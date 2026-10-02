"""Class-change correction of the official race score (競走得点).

The official score shown on race cards is the plain average of per-race points
over the current month and the 3 previous calendar months. Per-race points
depend on the race programme, and S級 / A級1・2班 / A級3班 programmes sit on
different point levels, but there is no conversion when a rider changes class.
Right after a class change (January / July, or a special promotion) the score
therefore mixes points earned on another level.

The correction converts every scoring race of the window that was run in a
different tier than the rider's current one:

    corrected = official + sum(delta(tier_of_race -> tier_now)) / scoring_races

which is the same as replacing those races' points by their expected value in
the current tier. See docs/score-correction.md for the derivation and sources.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

TIER_OF_CLASS = {
    "SS": "S",
    "S1": "S",
    "S2": "S",
    "A1": "A12",
    "A2": "A12",
    "A3": "A3",
    "L1": "L",
}
# Tiers that riders move between, lowest first. L級 (girls) has no class changes.
LADDER = ["A3", "A12", "S"]

# Expected change of a rider's per-race points when the same rider races one tier
# up / down. PROVISIONAL: 遠山競輪研究所030 (Gamboo, 2020-12), estimated on the
# 2019-10..2020-01 class change. To be re-estimated on our own data
# (`keirin estimate-deltas`), see docs/score-correction.md.
STEP_DELTAS: dict[tuple[str, str], float] = {
    ("A12", "S"): 3.80,
    ("S", "A12"): -3.69,
    ("A3", "A12"): 8.52,
    ("A12", "A3"): -9.42,
}

# Races of the meeting a card belongs to are not in the card's score yet.
CURRENT_MEETING_DAYS = 6


def tier(class_: str | None) -> str | None:
    return TIER_OF_CLASS.get((class_ or "").strip())


def delta(
    from_tier: str | None,
    to_tier: str | None,
    deltas: dict[tuple[str, str], float] = STEP_DELTAS,
) -> float | None:
    """Points to add to a race run in `from_tier` to express it in `to_tier`."""
    if from_tier == to_tier:
        return 0.0
    if from_tier not in LADDER or to_tier not in LADDER:
        return None
    i, j = LADDER.index(from_tier), LADDER.index(to_tier)
    step = 1 if j > i else -1
    return sum(deltas[(LADDER[k], LADDER[k + step])] for k in range(i, j, step))


def window_start(day: date) -> date:
    """First day of the score window of a card on `day` (current month + 3 previous)."""
    month, year = day.month - 3, day.year
    if month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


@dataclass(frozen=True)
class Race:
    date: date
    venue_code: str
    tier: str | None
    scoring: bool  # counted in the average (not 失格 / 棄権 / 欠場)


@dataclass(frozen=True)
class Correction:
    official: float | None
    corrected: float | None
    adjustment: float
    races: int  # scoring races in the window
    other_tier_races: int  # ... of which run in another tier than the current one
    complete: bool  # our data covers the whole window

    @property
    def other_tier_share(self) -> float:
        return self.other_tier_races / self.races if self.races else 0.0


def correct(
    official: float | None,
    tier_now: str | None,
    races: list[Race],
    *,
    day: date,
    complete: bool,
    exclude_venue: str | None = None,
    deltas: dict[tuple[str, str], float] = STEP_DELTAS,
) -> Correction:
    start = window_start(day)
    in_window = [
        r
        for r in races
        if r.scoring
        and start <= r.date < day
        and not (
            exclude_venue
            and r.venue_code == exclude_venue
            and (day - r.date).days <= CURRENT_MEETING_DAYS
        )
    ]
    total = 0.0
    other = 0
    for r in in_window:
        if r.tier == tier_now:
            continue
        d = delta(r.tier, tier_now, deltas)
        if d is None:
            continue
        total += d
        other += 1
    adjustment = round(total / len(in_window), 2) if in_window else 0.0
    corrected = round(official + adjustment, 2) if official is not None else None
    return Correction(official, corrected, adjustment, len(in_window), other, complete)


class History:
    """Every rider's past races, loaded from the normalized `entries` tables."""

    def __init__(self, races: dict[str, list[Race]], days: set[date]) -> None:
        self._races = races
        self.days = days

    @classmethod
    def from_tables(cls, data_dir: Path, since: date | None = None) -> History:
        races: dict[str, list[Race]] = defaultdict(list)
        days: set[date] = set()
        for path in sorted((data_dir / "tables" / "entries").glob("*/*.csv")):
            day = date.fromisoformat(path.stem)
            if since and day < since:
                continue
            days.add(day)
            with path.open(encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if not row["racer_id"]:
                        continue
                    races[row["racer_id"]].append(
                        Race(day, row["venue_code"], tier(row["class"]), bool(row["finish_pos"]))
                    )
        for lst in races.values():
            lst.sort(key=lambda r: r.date)
        log.debug("loaded %d days, %d racers", len(days), len(races))
        return cls(dict(races), days)

    def races(self, racer_id: str) -> list[Race]:
        return self._races.get(racer_id, [])

    def covers(self, start: date, end: date) -> bool:
        """True when every day in [start, end) has been collected."""
        day = start
        while day < end:
            if day not in self.days:
                return False
            day += timedelta(days=1)
        return True

    def correct(
        self,
        racer_id: str,
        official: float | None,
        class_now: str | None,
        day: date,
        *,
        exclude_venue: str | None = None,
        deltas: dict[tuple[str, str], float] = STEP_DELTAS,
    ) -> Correction:
        return correct(
            official,
            tier(class_now),
            self.races(racer_id),
            day=day,
            complete=self.covers(window_start(day), day),
            exclude_venue=exclude_venue,
            deltas=deltas,
        )
