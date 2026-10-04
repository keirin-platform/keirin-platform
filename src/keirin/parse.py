"""Turn a raw bundle (see ``collect.py``) into flat tables.

Parsing is pure: it only reads the bundle, so tables can always be rebuilt from
stored raw data. Field names on the API side are romanized Japanese
(``syaban`` = car number, ``tyaku`` = finishing position, ...); the table
columns use English names documented in ``docs/data-schema.md``.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

TABLES: dict[str, list[str]] = {
    "meetings": [
        "date",
        "venue_code",
        "venue_name",
        "grade",
        "day_label",
        "time_slot",
        "title",
    ],
    "races": [
        "date",
        "venue_code",
        "race_no",
        "race_class",
        "close_time",
        "start_time",
        "weather",
        "wind_speed",
        "entries",
    ],
    "entries": [
        "date",
        "venue_code",
        "race_no",
        "car_no",
        "racer_id",
        "racer_name",
        "prefecture",
        "age",
        "term",
        "class",
        "prev_class",
        "style",
        "score",
        "nige",
        "makuri",
        "sashi",
        "mark",
        "back",
        "home",
        "start",
        "win_rate",
        "top2_rate",
        "top3_rate",
        "finish",
        "finish_pos",
        "margin",
        "last_lap",
        "kimarite",
        "bh",
        "notes",
    ],
    "payouts": [
        "date",
        "venue_code",
        "race_no",
        "bet_type",
        "combination",
        "payout",
        "popularity",
    ],
}

# kaisaiIconChar on JSJ057
TIME_SLOTS = {"1": "day", "3": "night", "5": "midnight", "8": "morning"}

# Prefix of "<code>HaraiGakuDispItemSubData" keys on JSJ012
BET_TYPES = {
    "WH2": "2枠複",
    "WT2": "2枠単",
    "SH2": "2車複",
    "ST2": "2車単",
    "W": "ワイド",
    "RH3": "3連複",
    "RT3": "3連単",
}
_PAYOUT_SUFFIX = "HaraiGakuDispItemSubData"

Row = dict[str, Any]


def parse_bundle(bundle: dict[str, Any]) -> dict[str, list[Row]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    top: dict[str, Any] = {}
    for resp in bundle.get("responses", []):
        body = resp.get("body") or {}
        if resp["type"] == "JSJ057":
            top = body
        else:
            index[(resp["type"], resp["params"].get("encp", ""))] = body

    day = date.fromisoformat(bundle["date"])
    tables: dict[str, list[Row]] = {name: [] for name in TABLES}

    for meeting in top.get("kInfo") or []:
        enc = meeting.get("encPrm", "")
        header = (index.get(("JSJ001", enc)) or {}).get("C0201data") or {}
        entry_list = index.get(("JSJ017", enc)) or {}
        result_list = index.get(("JSJ018", enc)) or {}

        held_on = _yyyymmdd(result_list.get("kday") or entry_list.get("kaisaihi"))
        if held_on and held_on != day:
            log.warning("skip meeting %s: held on %s, not %s", meeting.get("jyoName"), held_on, day)
            continue

        base = {"date": day.isoformat(), "venue_code": _text(meeting.get("KeirinCd"))}
        tables["meetings"].append(
            {
                **base,
                "venue_name": _text(meeting.get("jyoName")),
                "grade": _text(meeting.get("gradeIconChar")),
                "day_label": _text(meeting.get("nitijiIconChar")),
                "time_slot": TIME_SLOTS.get(
                    meeting.get("kaisaiIconChar"), _text(meeting.get("kaisaiIconChar"))
                ),
                "title": _text(header.get("raceName")),
            }
        )

        cards_by_race = {r.get("raceNo"): r for r in entry_list.get("rInfo") or []}
        results_by_race = {
            _race_no(r.get("rclblRaceNo")): r for r in result_list.get("resultList") or []
        }
        meeting_cards = _meeting_cards(index, results_by_race.values())
        for race_no in sorted((set(cards_by_race) | set(results_by_race)) - {None}):
            summary = cards_by_race.get(race_no) or {}
            result_row = results_by_race.get(race_no) or {}
            race_enc = result_row.get("raceRVPrm") or result_row.get("raceTanpyoPrm") or ""
            card = meeting_cards.get(race_no) or index.get(("JSJ006", race_enc)) or {}
            result = index.get(("JSJ012", race_enc)) or {}
            race_base = {**base, "race_no": race_no}

            entries = _entries(race_base, summary, card, result)
            tables["entries"].extend(entries)
            tables["payouts"].extend(_payouts(race_base, result))
            tables["races"].append(
                {
                    **race_base,
                    "race_class": _text(
                        summary.get("syumoku") or result_row.get("rclblSyumokuName")
                    ),
                    "close_time": _text(summary.get("denTime")),
                    "start_time": _text(summary.get("stTime")),
                    "weather": _text(result.get("tenki")),
                    "wind_speed": _float(result.get("husoku")),
                    "entries": len(entries),
                }
            )

    for rows in tables.values():
        rows.sort(key=lambda r: (r["venue_code"], r.get("race_no", 0), r.get("car_no", 0)))
    return tables


def _meeting_cards(index: dict[tuple[str, str], dict[str, Any]], result_rows) -> dict:
    """Race cards of a meeting from its JSJ002 response (keyed by race number)."""
    for row in result_rows:
        body = index.get(("JSJ002", row.get("raceRVPrm") or row.get("raceTanpyoPrm") or ""))
        if body and body.get("raceInfo"):
            return meeting_race_cards(body)
    return {}


def meeting_race_cards(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """JSJ002 lists the cards of every race of a meeting: race number -> card."""
    return {
        no: race
        for race in body.get("raceInfo") or []
        if (no := _race_no(race.get("raceNo"))) is not None
    }


def card_entries(card: dict[str, Any], summary: dict[str, Any] | None = None) -> list[Row]:
    """Entries of one race card (a JSJ002 `raceInfo` item or JSJ006), e.g. fetched live.

    `summary` is the race's item of the meeting entry list (JSJ017 rInfo), used as a
    fallback for riders missing from the card.
    """
    return _entries({}, summary or {}, card, {})


def race_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Finishing order and payouts of one race (JSJ012); None before the result is out."""
    entries = _entries({}, {}, {}, result)
    if not any(e.get("finish") or e.get("notes") for e in entries):
        return None
    return {
        "weather": _text(result.get("tenki")),
        "wind_speed": _float(result.get("husoku")),
        "entries": entries,
        "payouts": _payouts({}, result),
    }


def _entries(race_base: Row, summary: dict, card: dict, result: dict) -> list[Row]:
    rows: dict[int, Row] = {}
    for s in card.get("sensyuTypeInfo") or []:
        car_no = _int(s.get("syaban"))
        if car_no is None:
            continue
        rows[car_no] = {
            "racer_id": _text(s.get("sensyuRegistNo")),
            "racer_name": _text(s.get("sensyuName")),
            "prefecture": _pref(s.get("huKen")),
            "age": _int(s.get("age")),
            "term": _int(s.get("sotugyouki")),
            "class": _text(s.get("kyuhan")),
            "prev_class": _text(s.get("prevKyuhan")),
            "style": _text(s.get("kyakusitu")),
            "score": _float(s.get("heikinTokuten")),
            "nige": _int(s.get("nigeCnt")),
            "makuri": _int(s.get("makuriCnt")),
            "sashi": _int(s.get("sasiCnt")),
            "mark": _int(s.get("markCnt")),
            "back": _int(s.get("backCnt")),
            "home": _int(s.get("homeTori")),
            "start": _int(s.get("stTori")),
            "win_rate": _float(s.get("syouritu")),
            "top2_rate": _float(s.get("rentairitu2")),
            "top3_rate": _float(s.get("rentairitu3")),
        }
    # Fall back to the (less detailed) meeting entry list when the card is missing.
    for s in summary.get("sInfo") or []:
        car_no = _int(s.get("syaban"))
        if car_no is None or car_no in rows:
            continue
        rows[car_no] = {
            "racer_id": _text(s.get("senNo")),
            "racer_name": _text(s.get("senName")),
            "prefecture": _pref(s.get("huken")),
            "style": _text(s.get("kyaku")),
        }
    for t in result.get("tyakujyunItemSubData") or []:
        car_no = _int(t.get("syaban"))
        if car_no is None:
            continue
        row = rows.setdefault(
            car_no,
            {
                "racer_id": _text(t.get("sensyuRegistNo")),
                "racer_name": _text(t.get("sensyuName")),
                "prefecture": _pref(t.get("huken")),
                "age": _int(t.get("age")),
                "term": _int(t.get("sotugyouki")),
                "class": _text(t.get("kyuhan")),
            },
        )
        finish = _text(t.get("tyaku"))
        notes = [
            _text(k.get(field))
            for k in t.get("kojinStateItemSubData") or []
            for field in ("kojinState", "tyakuNote")
            if _text(k.get(field))
        ]
        row.update(
            {
                "finish": finish,
                "finish_pos": int(finish) if finish.isdigit() else None,
                "margin": _text(t.get("tyakusa")),
                "last_lap": _float(t.get("agari")),
                "kimarite": _text(t.get("kimarite")),
                "bh": _text(t.get("BH")),
                "notes": "/".join(notes),
            }
        )
    return [{**race_base, "car_no": car_no, **row} for car_no, row in sorted(rows.items())]


def _payouts(race_base: Row, result: dict) -> list[Row]:
    rows = []
    payouts = result.get("haraiGakuSubData") or {}
    codes = [k[: -len(_PAYOUT_SUFFIX)] for k in payouts if k.endswith(_PAYOUT_SUFFIX)]
    # Fixed bet type order: key order is not preserved once raw is stored with sort_keys.
    order = list(BET_TYPES)
    codes.sort(key=lambda c: (order.index(c) if c in order else len(order), c))
    for code in codes:
        items = payouts[code + _PAYOUT_SUFFIX]
        if not isinstance(items, list):
            continue
        for item in items:
            combination = _text(item.get("kumiBan"))
            if not combination:  # 未発売 etc.
                continue
            rows.append(
                {
                    **race_base,
                    "bet_type": BET_TYPES.get(code, code),
                    "combination": combination,
                    "payout": _int(item.get("haraiGaku")),
                    "popularity": _int(item.get("ninki")),
                }
            )
    return rows


def _text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("&nbsp;", " ")).strip()


def _pref(value: Any) -> str:
    return re.sub(r"\s", "", _text(value))


def _int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    digits = re.sub(r"[,()円\s]", "", _text(value))
    return int(digits) if digits.isdigit() else None


def _float(value: Any) -> float | None:
    try:
        return float(_text(value))
    except ValueError:
        return None


def _race_no(value: Any) -> int | None:
    m = re.match(r"\s*(\d+)", _text(value))
    return int(m.group(1)) if m else None


def _yyyymmdd(value: Any) -> date | None:
    text = _text(value)
    if not re.fullmatch(r"\d{8}", text):
        return None
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))
