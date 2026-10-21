"""Multiview: several venues' live streams on one page (docs/multiview.md).

The page keeps the tiles where they are; only the colour of the next venue to race
moves, and only from the scheduled start times (keirin.jp is not polled for results).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from keirin.timeutil import JST

# Minutes after a scheduled start before the highlight moves on (races are rarely late).
WAIT_MINUTES = 5
MAX_TILES = 4

State = Literal["upcoming", "waiting", "finished", "unknown"]


@dataclass(frozen=True)
class VenueStatus:
    venue_code: str
    race_no: int | None  # the next race, None when finished or unknown
    start: datetime | None
    state: State


def start_datetimes(day: date, races: list[dict]) -> list[tuple[int, datetime]]:
    """(race number, scheduled start in JST) of `Store.races` rows that have a start time."""
    starts = []
    for r in races:
        if not r.get("start_time"):
            continue
        hour, minute = map(int, r["start_time"].split(":"))
        starts.append(
            (int(r["race_no"]), datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST))
        )
    return starts


def venue_status(
    code: str, starts: list[tuple[int, datetime]] | None, now: datetime
) -> VenueStatus:
    if not starts:
        return VenueStatus(code, None, None, "unknown")
    wait = timedelta(minutes=WAIT_MINUTES)
    for race_no, start in sorted(starts, key=lambda s: s[1]):
        if now < start + wait:
            return VenueStatus(code, race_no, start, "waiting" if now >= start else "upcoming")
    return VenueStatus(code, None, None, "finished")


def _racing(statuses: list[VenueStatus]) -> list[VenueStatus]:
    return sorted(
        (s for s in statuses if s.state in ("upcoming", "waiting")), key=lambda s: s.start
    )


def highlighted(statuses: list[VenueStatus]) -> str | None:
    """The venue that races next, or None when none of them has a race to come."""
    racing = _racing(statuses)
    return racing[0].venue_code if racing else None


def default_selection(statuses: list[VenueStatus], n: int = 3) -> list[str]:
    """The `n` venues that race soonest; venues without a schedule come after them."""
    unknown = [s for s in statuses if s.state == "unknown"]
    return [s.venue_code for s in _racing(statuses) + unknown][:n]


def assign_slots(previous: list[str | None], selected: list[str]) -> list[str | None]:
    """Tiles for `selected`: venues kept stay in their tile, new ones fill the free tiles."""
    slots = [code if code in selected else None for code in previous[:MAX_TILES]]
    slots += [None] * (MAX_TILES - len(slots))
    for code in selected:
        if code not in slots and None in slots:
            slots[slots.index(None)] = code
    return slots
