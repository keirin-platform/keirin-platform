"""Line formations (並び) of every race. See docs/lines.md.

Results never keep the formation, so it is captured from two sources:

- keirin.jp (going forward): the meeting entry list (JSJ017) carries the predicted
  formation (`nInfo`) until the race is over. One request per meeting, taken for
  today and tomorrow by every run of the daily job.
- Oddspark (gaps and the past): each race page has the formation in
  `<ul class="keirinRyosouline">`, also for old races. One page per race and its
  robots.txt asks for 10 seconds between requests, so the backfill only fetches
  races still missing, within a time budget per run.

Raw captures are merged per day: the first non-empty capture of a race is kept and
never overwritten (keirin.jp empties the formation once the race is over).

    raw/lines/YYYY/YYYY-MM-DD.json.gz        {"date", "races": {"<venue>-<race>": capture}}
    tables/lines/YYYY/YYYY-MM-DD.csv         one row per rider
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from keirin.client import USER_AGENT, KeirinApiError
from keirin.collect import ApiClient
from keirin.storage import raw_path, table_path
from keirin.timeutil import JST, date_range

log = logging.getLogger(__name__)

COLUMNS = [
    "date", "venue_code", "race_no", "car_no",
    "line_no", "line_pos", "line_size", "contested", "formation", "source",
]  # fmt: skip
ODDSPARK_RACE_URL = "https://www.oddspark.com/keirin/RaceList.do"
ODDSPARK_INTERVAL = 10.0  # robots.txt Crawl-delay

Position = tuple[int, ...]  # more than one car = contested position (競り)


# --- formations ----------------------------------------------------------------------


@dataclass(frozen=True)
class Formation:
    lines: tuple[tuple[Position, ...], ...]

    def text(self) -> str:
        """e.g. "2(13)56/74": lines split by "/", contested positions in parentheses."""
        return "/".join(
            "".join(str(p[0]) if len(p) == 1 else "(" + "".join(map(str, p)) + ")" for p in line)
            for line in self.lines
        )

    def rows(self, base: dict[str, Any], source: str) -> list[dict[str, Any]]:
        rows = []
        for line_no, line in enumerate(self.lines, 1):
            size = sum(len(p) for p in line)
            for pos, cars in enumerate(line, 1):
                for car in cars:
                    rows.append(
                        {
                            **base,
                            "car_no": car,
                            "line_no": line_no,
                            "line_pos": pos,
                            "line_size": size,
                            "contested": len(cars) > 1,
                            "formation": self.text(),
                            "source": source,
                        }
                    )
        return sorted(rows, key=lambda r: r["car_no"])


def from_ninfo(ninfo: list[dict[str, Any]] | None) -> Formation | None:
    """keirin.jp: same narabiX = same position (several narabiY = contested); gaps split lines."""
    cells = sorted(
        (int(n["narabiX"]), int(n.get("narabiY") or 1), int(n["syaban"]))
        for n in ninfo or []
        if n.get("narabiX") and n.get("syaban")
    )
    if not cells:
        return None
    lines: list[list[Position]] = []
    previous_x = None
    for x in sorted({c[0] for c in cells}):
        position = tuple(car for cx, _, car in cells if cx == x)
        if previous_x is None or x - previous_x > 1:
            lines.append([])
        lines[-1].append(position)
        previous_x = x
    return Formation(tuple(tuple(line) for line in lines))


_ODDSPARK_BLOCK = re.compile(r'<ul class="keirinRyosouline">.*?</ul>', re.S)
_ODDSPARK_TOKEN = re.compile(r'<span class="no(\d)">(.*?)</span>', re.S)


def extract_oddspark_block(page: str) -> str | None:
    m = _ODDSPARK_BLOCK.search(page)
    return m.group(0) if m else None


def from_oddspark(block: str | None) -> Formation | None:
    """Oddspark: noN = car N; no0 holds "(" / ")" around contested cars or a line break."""
    if not block:
        return None
    lines: list[list[Position]] = [[]]
    group: list[int] | None = None
    for number, text in _ODDSPARK_TOKEN.findall(block):
        if number != "0":
            if group is not None:
                group.append(int(number))
            else:
                lines[-1].append((int(number),))
        elif "(" in text:
            group = []
        elif ")" in text:
            if group:
                lines[-1].append(tuple(group))
            group = None
        elif lines[-1]:
            lines.append([])
    lines = [line for line in lines if line]
    return Formation(tuple(tuple(line) for line in lines)) if lines else None


def formation_of(capture: dict[str, Any]) -> Formation | None:
    if capture.get("source") == "keirin.jp":
        return from_ninfo((capture.get("data") or {}).get("nInfo"))
    if capture.get("source") == "oddspark":
        return from_oddspark(capture.get("data"))
    return None


# --- storage -------------------------------------------------------------------------


def lines_raw_path(data_dir: Path, day: date) -> Path:
    return data_dir / "raw" / "lines" / f"{day:%Y}" / f"{day.isoformat()}.json.gz"


def read_captures(data_dir: Path, day: date) -> dict[str, dict[str, Any]]:
    path = lines_raw_path(data_dir, day)
    if not path.exists():
        return {}
    with gzip.open(path, "rb") as gz:
        return json.loads(gz.read())["races"]


def save_captures(data_dir: Path, day: date, captures: dict[str, dict[str, Any]]) -> None:
    """Write the raw captures and regenerate the day's lines table (deterministic)."""
    path = lines_raw_path(data_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"date": day.isoformat(), "races": captures}, ensure_ascii=False, sort_keys=True
    ).encode()
    with path.open("wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
        gz.write(payload)
    write_lines_table(data_dir, day, captures)


def write_lines_table(data_dir: Path, day: date, captures: dict[str, dict[str, Any]]) -> None:
    rows = []
    for key, capture in captures.items():
        formation = formation_of(capture)
        if formation is None:
            continue
        venue, race = key.split("-")
        base = {"date": day.isoformat(), "venue_code": venue, "race_no": int(race)}
        rows.extend(formation.rows(base, capture["source"]))
    rows.sort(key=lambda r: (r["venue_code"], r["race_no"], r["car_no"]))
    path = table_path(data_dir, "lines", day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _merge(captures: dict[str, dict[str, Any]], key: str, capture: dict[str, Any]) -> bool:
    """Keep the first usable capture of a race; an unusable one never replaces anything."""
    if key in captures and formation_of(captures[key]) is not None:
        return False
    if key in captures and formation_of(capture) is None:
        return False
    captures[key] = capture
    return True


# --- keirin.jp (going forward) -------------------------------------------------------


def collect_upcoming(client: ApiClient, data_dir: Path, day: date) -> int:
    """Capture the formations keirin.jp shows for the day's races; returns new captures."""
    captures = read_captures(data_dir, day)
    added = 0
    now = datetime.now(JST).isoformat(timespec="seconds")
    for meeting in client.get("JSJ057", kday=day.strftime("%Y%m%d")).get("kInfo") or []:
        if not meeting.get("encPrm"):
            continue
        entry_list = client.get("JSJ017", encp=meeting["encPrm"])
        for race in entry_list.get("rInfo") or []:
            if not race.get("nInfo"):
                continue  # not published yet, or the race is already over
            key = f"{meeting.get('KeirinCd')}-{race.get('raceNo')}"
            capture = {
                "source": "keirin.jp",
                "fetched_at": now,
                "data": {k: race.get(k) for k in ("nInfo", "line", "seri", "narabiYCnt")},
            }
            added += _merge(captures, key, capture)
    if added or captures:
        save_captures(data_dir, day, captures)
    log.info("%s: %d new formations from keirin.jp (%d races captured)", day, added, len(captures))
    return added


# --- Oddspark (gaps and the past) ----------------------------------------------------


class OddsparkClient:
    """One page at a time with the 10 s Crawl-delay; stops on refusals."""

    def __init__(
        self,
        *,
        min_interval: float = ODDSPARK_INTERVAL,
        timeout: float = 30.0,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=timeout, transport=transport
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self.request_count = 0

    def race_page(self, venue_code: str, day: date, race_no: int) -> str:
        params = {"joCode": venue_code, "kaisaiBi": day.strftime("%Y%m%d"), "raceNo": race_no}
        for attempt in range(1, self._max_retries + 1):
            if self._last is not None:
                wait = self._min_interval - (self._clock() - self._last)
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            try:
                resp = self._http.get(ODDSPARK_RACE_URL, params=params)
            except httpx.TransportError as e:
                error = f"{type(e).__name__}: {e}"
            else:
                self.request_count += 1
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code < 500:
                    raise KeirinApiError(f"Oddspark: HTTP {resp.status_code}")
                error = f"HTTP {resp.status_code}"
            if attempt == self._max_retries:
                raise KeirinApiError(f"Oddspark: {error} (gave up after {attempt} attempts)")
            log.warning("Oddspark: %s, retrying", error)
        raise AssertionError("unreachable")


def _races_of(data_dir: Path, day: date) -> list[tuple[str, int]]:
    path = table_path(data_dir, "races", day)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [(r["venue_code"], int(r["race_no"])) for r in csv.DictReader(f)]


def missing_main_days(data_dir: Path, since: date, until: date) -> list[date]:
    return [d for d in date_range(since, until) if not raw_path(data_dir, d).exists()]


def backfill(
    client: OddsparkClient,
    data_dir: Path,
    days: Iterable[date],
    *,
    budget_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Fetch the formation of every collected race still missing, newest day first.

    Stops when the time budget would be exceeded. A race page without a formation is
    recorded as such so that it is not fetched again.
    """
    started = clock()
    fetched = 0
    for day in sorted(days, reverse=True):
        captures = read_captures(data_dir, day)
        todo = [
            (venue, race) for venue, race in _races_of(data_dir, day)
            if f"{venue}-{race}" not in captures
        ]  # fmt: skip
        if not todo:
            continue
        try:
            for venue, race in todo:
                if clock() - started + ODDSPARK_INTERVAL > budget_seconds:
                    log.info("time budget used up after %d pages", fetched)
                    return fetched
                block = extract_oddspark_block(client.race_page(venue, day, race))
                captures[f"{venue}-{race}"] = {
                    "source": "oddspark",
                    "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
                    "data": block,  # None: the page has no formation (kept to avoid refetching)
                }
                fetched += 1
        finally:
            save_captures(data_dir, day, captures)
        log.info("%s: formations complete (%d races)", day, len(captures))
    return fetched


def upcoming_days(today: date, days: int) -> list[date]:
    return [today + timedelta(days=i) for i in range(days)]
