"""Offline analyses on the collected tables: corrected cards, evaluation, delta estimation."""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from keirin.score import LADDER, STEP_DELTAS, History, tier
from keirin.storage import table_path
from keirin.timeutil import date_range


def read_entries(data_dir: Path, day: date) -> list[dict[str, Any]]:
    path = table_path(data_dir, "entries", day)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def corrected_card_rows(
    data_dir: Path,
    day: date,
    history: History,
    deltas: dict[tuple[str, str], float] = STEP_DELTAS,
) -> list[dict[str, Any]]:
    rows = []
    for e in read_entries(data_dir, day):
        c = history.correct(
            e["racer_id"],
            _float(e["score"]),
            e["class"],
            day,
            exclude_venue=e["venue_code"],
            deltas=deltas,
        )
        rows.append(
            {
                "date": e["date"],
                "venue_code": e["venue_code"],
                "race_no": int(e["race_no"]),
                "car_no": int(e["car_no"]),
                "racer_id": e["racer_id"],
                "racer_name": e["racer_name"],
                "class": e["class"],
                "prev_class": e["prev_class"],
                "score": c.official,
                "corrected": c.corrected,
                "adjustment": c.adjustment,
                "races": c.races,
                "other_tier_races": c.other_tier_races,
                "complete": c.complete,
                "finish_pos": int(e["finish_pos"]) if e["finish_pos"] else None,
            }
        )
    return rows


@dataclass
class Concordance:
    pairs: float = 0.0
    concordant: float = 0.0

    def add(self, a: float, b: float, finish_a: int, finish_b: int) -> None:
        self.pairs += 1
        if a == b:
            self.concordant += 0.5
        elif (a > b) == (finish_a < finish_b):
            self.concordant += 1

    @property
    def rate(self) -> float:
        return self.concordant / self.pairs if self.pairs else float("nan")


def evaluate(
    data_dir: Path,
    start: date,
    end: date,
    history: History,
    deltas: dict[tuple[str, str], float] = STEP_DELTAS,
) -> dict[str, dict[str, Concordance]]:
    """Pairwise concordance between score order and finishing order, per race.

    Returns {"all" | "affected": {"official" | "corrected": Concordance}} where
    "affected" are races with at least one rider whose score was corrected.
    Only races where every rider's window is fully covered by our data count.
    """
    result = {
        k: {"official": Concordance(), "corrected": Concordance()} for k in ("all", "affected")
    }
    for day in date_range(start, end):
        races: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for row in corrected_card_rows(data_dir, day, history, deltas):
            races[(row["venue_code"], row["race_no"])].append(row)
        for rows in races.values():
            rows = [r for r in rows if r["finish_pos"] is not None and r["score"] is not None]
            if len(rows) < 2 or not all(r["complete"] for r in rows):
                continue
            groups = ["all"] + (["affected"] if any(r["adjustment"] for r in rows) else [])
            for i, a in enumerate(rows):
                for b in rows[i + 1 :]:
                    for g in groups:
                        result[g]["official"].add(
                            a["score"], b["score"], a["finish_pos"], b["finish_pos"]
                        )
                        result[g]["corrected"].add(
                            a["corrected"], b["corrected"], a["finish_pos"], b["finish_pos"]
                        )
    return result


@dataclass
class DeltaEstimate:
    from_tier: str
    to_tier: str
    movers: int
    stayers: int
    delta: float
    stderr: float


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    return date(day.year + month // 12, month % 12 + 1, 1)


def estimate_deltas(data_dir: Path, boundary: date, min_races: int = 6) -> list[DeltaEstimate]:
    """Estimate per-race tier deltas from the class change at `boundary` (Jan 1 / Jul 1).

    old = official score on the rider's last card in the month before the boundary
    (its window lies entirely before the change); new = official score on the last
    card from boundary+3 months on (its window lies entirely after the change).
    Regression to the mean is removed with stayers of the same old tier:
    delta = mean over movers of (new - old) - fit_stayers(old).
    """
    before = (_add_months(boundary, -1), boundary)
    after = (_add_months(boundary, 3), _add_months(boundary, 4))
    old: dict[str, tuple[float, str]] = {}
    new: dict[str, tuple[float, str, date]] = {}
    tiers_after: dict[str, set[str]] = defaultdict(set)
    scoring_after: dict[str, int] = defaultdict(int)
    for day in date_range(before[0], after[1] - timedelta(days=1)):
        for e in read_entries(data_dir, day):
            score, t = _float(e["score"]), tier(e["class"])
            if score is None or t not in LADDER:
                continue
            if before[0] <= day < before[1]:
                old[e["racer_id"]] = (score, t)
            elif day >= boundary:
                tiers_after[e["racer_id"]].add(t)
                if e["finish_pos"]:
                    scoring_after[e["racer_id"]] += 1
                if day >= after[0]:
                    new[e["racer_id"]] = (score, t, day)

    samples: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for racer_id, (new_score, new_tier, _) in new.items():
        if racer_id not in old or len(tiers_after[racer_id]) != 1:
            continue  # unknown before, or special promotion after the boundary
        if scoring_after[racer_id] < min_races:
            continue
        old_score, old_tier = old[racer_id]
        samples[(old_tier, new_tier)].append((old_score, new_score - old_score))

    estimates = []
    for (from_tier, to_tier), movers in sorted(samples.items()):
        if from_tier == to_tier:
            continue
        stayers = samples.get((from_tier, from_tier), [])
        if len(stayers) < 10 or len(movers) < 3:
            continue
        xs, ys = zip(*stayers, strict=True)
        slope, intercept = statistics.linear_regression(xs, ys)
        residuals = [y - (intercept + slope * x) for x, y in movers]
        estimates.append(
            DeltaEstimate(
                from_tier,
                to_tier,
                len(movers),
                len(stayers),
                round(statistics.fmean(residuals), 2),
                round(statistics.stdev(residuals) / len(residuals) ** 0.5, 2)
                if len(residuals) > 1
                else float("nan"),
            )
        )
    return estimates
