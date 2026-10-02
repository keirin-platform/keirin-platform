"""Data access for the viewer: collected tables for past days, keirin.jp for the rest."""

from __future__ import annotations

import csv
import logging
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from keirin.parse import TIME_SLOTS, card_entries
from keirin.score import History
from keirin.storage import table_path

log = logging.getLogger(__name__)

Row = dict[str, Any]


class ApiClient(Protocol):
    def get(self, type_: str, **params: str) -> dict[str, Any]: ...


class NotFound(LookupError):
    pass


class CachedClient:
    """Serializes and caches API calls so page views stay polite to keirin.jp."""

    def __init__(self, client: ApiClient, ttl: float = 300.0, clock=time.monotonic) -> None:
        self._client = client
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, dict[str, Any]]] = {}

    def get(self, type_: str, **params: str) -> dict[str, Any]:
        key = (type_, tuple(sorted(params.items())))
        with self._lock:
            now = self._clock()
            hit = self._cache.get(key)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
            started = time.monotonic()
            try:
                body = self._client.get(type_, **params)
            except Exception as e:
                log.warning("%s failed after %.1fs: %s", type_, time.monotonic() - started, e)
                raise
            log.info("%s fetched in %.1fs", type_, time.monotonic() - started)
            self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self._ttl}
            self._cache[key] = (now, body)
            return body


def _read_table(data_dir: Path, name: str, day: date) -> list[Row] | None:
    path = table_path(data_dir, name, day)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


class Store:
    def __init__(self, data_dir: Path, client: ApiClient | None) -> None:
        self.data_dir = data_dir
        self._client = client
        self.history = History.from_tables(data_dir)

    def reload(self) -> None:
        self.history = History.from_tables(self.data_dir)

    @property
    def last_collected(self) -> date | None:
        return max(self.history.days) if self.history.days else None

    def is_collected(self, day: date) -> bool:
        return table_path(self.data_dir, "meetings", day).exists()

    # --- meetings -------------------------------------------------------------------

    def meetings(self, day: date) -> list[Row]:
        rows = _read_table(self.data_dir, "meetings", day)
        if rows is not None:
            return rows
        return [
            {
                "venue_code": m.get("KeirinCd", ""),
                "venue_name": m.get("jyoName", ""),
                "grade": m.get("gradeIconChar", ""),
                "day_label": m.get("nitijiIconChar", ""),
                "time_slot": TIME_SLOTS.get(m.get("kaisaiIconChar"), ""),
                "title": "",
                "enc": m.get("encPrm", ""),
            }
            for m in self._live("JSJ057", kday=day.strftime("%Y%m%d")).get("kInfo") or []
        ]

    def meeting(self, day: date, venue_code: str) -> Row:
        for m in self.meetings(day):
            if m["venue_code"] == venue_code:
                return m
        raise NotFound(f"no meeting at venue {venue_code} on {day}")

    # --- races ----------------------------------------------------------------------

    def races(self, day: date, venue_code: str) -> list[Row]:
        meeting = self.meeting(day, venue_code)
        if self.is_collected(day):
            entries = [
                e for e in _read_table(self.data_dir, "entries", day) or []
                if e["venue_code"] == venue_code
            ]  # fmt: skip
            return [
                {
                    "race_no": int(r["race_no"]),
                    "race_class": r["race_class"],
                    "start_time": r["start_time"],
                    "riders": [
                        (int(e["car_no"]), e["racer_name"], e["style"])
                        for e in entries
                        if e["race_no"] == r["race_no"]
                    ],
                }
                for r in _read_table(self.data_dir, "races", day) or []
                if r["venue_code"] == venue_code
            ]
        return [
            {
                "race_no": r.get("raceNo"),
                "race_class": r.get("syumoku", ""),
                "start_time": r.get("stTime", ""),
                "riders": [
                    (s.get("syaban"), (s.get("senName") or "").replace("　", " "),
                     s.get("kyaku", ""))
                    for s in r.get("sInfo") or []
                ],
            }
            for r in self._live("JSJ017", encp=meeting["enc"]).get("rInfo") or []
        ]  # fmt: skip

    # --- race card ------------------------------------------------------------------

    def card(self, day: date, venue_code: str, race_no: int) -> tuple[Row, Row, list[Row]]:
        meeting = self.meeting(day, venue_code)
        race = next((r for r in self.races(day, venue_code) if r["race_no"] == race_no), None)
        if race is None:
            raise NotFound(f"no race {race_no} at venue {venue_code} on {day}")
        if self.is_collected(day):
            entries = [
                e for e in _read_table(self.data_dir, "entries", day) or []
                if e["venue_code"] == venue_code and int(e["race_no"]) == race_no
            ]  # fmt: skip
        else:
            entries = self._live_card(meeting, race_no)
        rows = [self._with_correction(e, day, venue_code) for e in entries]
        _rank(rows, "score", "rank_official")
        _rank(rows, "corrected", "rank_corrected")
        return meeting, race, rows

    def _live_card(self, meeting: Row, race_no: int) -> list[Row]:
        token = self._race_token(meeting, race_no)
        summary = next(
            (r for r in self._live("JSJ017", encp=meeting["enc"]).get("rInfo") or []
             if r.get("raceNo") == race_no),
            None,
        )  # fmt: skip
        return card_entries(self._live("JSJ006", encp=token), summary)

    def _race_token(self, meeting: Row, race_no: int) -> str:
        # The meeting header lists the race tokens in race order, also for upcoming days
        # (JSJ014 answers resultCd=-1 for days that have not started yet).
        header = self._live("JSJ001", encp=meeting["enc"]).get("C0201data") or {}
        races = header.get("C0201race") or []
        if 0 < race_no <= len(races) and races[race_no - 1].get("encParaR"):
            return races[race_no - 1]["encParaR"]
        label = f"{race_no}R"
        for day in self._live("JSJ014", encp=meeting["enc"]).get("raceDayDataList") or []:
            if day.get("strRaceNitiji") != meeting["day_label"]:
                continue
            for race in day.get("raceNoDataList") or []:
                if race.get("strRaceNo") == label and race.get("strLnkPrm"):
                    return race["strLnkPrm"]
        for race in self._live("JSJ018", encp=meeting["enc"]).get("resultList") or []:
            if race.get("rclblRaceNo") == label and race.get("raceRVPrm"):
                return race["raceRVPrm"]
        raise NotFound(f"no race token for {label}")

    def _with_correction(self, entry: Row, day: date, venue_code: str) -> Row:
        score = _num(entry.get("score"))
        c = self.history.correct(
            str(entry.get("racer_id", "")),
            score,
            entry.get("class"),
            day,
            exclude_venue=venue_code,
        )
        return {
            "car_no": _int(entry.get("car_no")),
            "racer_id": entry.get("racer_id", ""),
            "racer_name": entry.get("racer_name", ""),
            "prefecture": entry.get("prefecture", ""),
            "age": _int(entry.get("age")),
            "term": _int(entry.get("term")),
            "class": entry.get("class") or "",
            "prev_class": entry.get("prev_class") or "",
            "style": entry.get("style") or "",
            "score": score,
            "corrected": c.corrected,
            "adjustment": c.adjustment,
            "races": c.races,
            "other_tier_races": c.other_tier_races,
            "complete": c.complete,
            "win_rate": _num(entry.get("win_rate")),
            "top2_rate": _num(entry.get("top2_rate")),
            "top3_rate": _num(entry.get("top3_rate")),
            "finish": entry.get("finish") or "",
            "notes": entry.get("notes") or "",
        }

    def _live(self, type_: str, **params: str) -> dict[str, Any]:
        if self._client is None:
            raise NotFound("live data is disabled")
        return self._client.get(type_, **params)


def _rank(rows: list[Row], key: str, out: str) -> None:
    values = sorted({r[key] for r in rows if r[key] is not None}, reverse=True)
    for r in rows:
        r[out] = values.index(r[key]) + 1 if r[key] is not None else None
