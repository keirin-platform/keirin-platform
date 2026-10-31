"""Verification statistics for keirin-research (the public keirin knowledge base).

The LAW entries of keirin-research cite these functions by commit as their verification
record. Every output is an aggregate: no rider ids, names or race rows leave this module.
The decision rule follows keirin-research COMMIT-002 (95% bootstrap interval over races,
the same direction in both halves of the period, a practical size fixed before measuring).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import logsumexp

from keirin import predict
from keirin.storage import table_path

LINKAGE_PRACTICAL = 0.05  # same-line pairs both in the top 3, per race


def bootstrap_ci(values: np.ndarray, *, n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% interval of the mean, resampling the values (one per race) with replacement."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    means = np.array(
        [values[rng.integers(0, len(values), len(values))].mean() for _ in range(n_boot)]
    )
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def decide(ci: tuple[float, float], halves: tuple[float, float], practical: float) -> str:
    """正の効果 / 負の効果 / 実質的な差なし / 保留 (keirin-research COMMIT-002)."""
    lo, hi = ci
    if lo > 0 and all(h > 0 for h in halves):
        return "正の効果"
    if hi < 0 and all(h < 0 for h in halves):
        return "負の効果"
    if -practical < lo and hi < practical:
        return "実質的な差なし"
    return "保留"


# --- line linkage ------------------------------------------------------------------------


def pair_top3_probabilities(theta: np.ndarray, x: np.ndarray) -> np.ndarray:
    """(riders, riders): probability that both riders finish in the top 3 (zero diagonal).

    The same stages as `predict.race_probabilities`: 1st from u1, 2nd from t2 * u23 and
    3rd from t3 * u23, summed over every ordered top 3.
    """
    u1, u23, (t2, t3) = predict._utilities(theta, x)
    n = len(x)

    def draw(u: np.ndarray, left: np.ndarray) -> np.ndarray:
        z = np.where(left, u, -np.inf)
        return np.exp(z - logsumexp(z))

    pairs = np.zeros((n, n))
    p1 = draw(u1, np.ones(n, bool))
    for a in range(n):
        left = np.ones(n, bool)
        left[a] = False
        p2 = draw(t2 * u23, left)
        for b in np.flatnonzero(left):
            rest = left.copy()
            rest[b] = False
            p_ab = p1[a] * p2[b]
            pairs[a, b] += p_ab
            pairs[b, a] += p_ab
            if rest.any():
                p3 = p_ab * draw(t3 * u23, rest)  # zero at a and b
                for r in (a, b):
                    pairs[r] += p3
                    pairs[:, r] += p3
    return pairs


@dataclass(frozen=True)
class LinkageRace:
    day: date
    group: str
    pair_prob: np.ndarray  # (n, n): both in the top 3 under the score-only model
    same_line: np.ndarray  # (n, n) bool: same line, not contesting the same position
    top3: np.ndarray  # (n,) bool


def load_linkage_races(
    data_dir: Path, start: date, end: date, model: predict.Model
) -> list[LinkageRace]:
    """Races of start <= day < end that have real lines (the lines table), men's only."""
    out = []
    for path in sorted((data_dir / "tables" / "lines").glob("*/*.csv")):
        day = date.fromisoformat(path.stem)
        if day < start or day >= end:
            continue
        formations: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
        for r in predict._read(path):
            formations[(r["venue_code"], r["race_no"])][int(r["car_no"])] = {
                "line_no": int(r["line_no"]),
                "line_pos": int(r["line_pos"]),
            }
        classes = {
            (r["venue_code"], r["race_no"]): r["race_class"]
            for r in predict._read(table_path(data_dir, "races", day))
        }
        entries: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in predict._read(table_path(data_dir, "entries", day)):
            entries[(r["venue_code"], r["race_no"])].append(r)
        for key in sorted(formations, key=lambda k: (k[0], int(k[1]))):
            group = predict.group_of(classes.get(key))
            if group not in ("A", "S") or group not in model.params:
                continue
            race = _linkage_race(day, group, entries[key], formations[key], model.params[group])
            if race is not None:
                out.append(race)
    return out


def _linkage_race(
    day: date,
    group: str,
    rows: list[dict[str, str]],
    formation: dict[int, dict[str, Any]],
    theta: np.ndarray,
) -> LinkageRace | None:
    riders = [r for r in rows if not predict._scratched(r)]
    if len(riders) < 3 or any(r["score"] == "" for r in riders):
        return None
    placed = sorted(
        (int(r["finish_pos"]), int(r["car_no"]), i) for i, r in enumerate(riders) if r["finish_pos"]
    )
    if len(placed) < 3:
        return None
    scratched = {int(r["car_no"]) for r in rows if predict._scratched(r)}
    by_car = predict._close_gaps(formation, scratched)
    cars = [int(r["car_no"]) for r in riders]
    n = len(riders)
    same = np.zeros((n, n), bool)
    for i, ci in enumerate(cars):
        for j, cj in enumerate(cars):
            a, b = by_car.get(ci), by_car.get(cj)
            if i != j and a and b and a["line_no"] == b["line_no"]:
                same[i, j] = a["line_pos"] != b["line_pos"]
    top3 = np.zeros(n, bool)
    top3[[i for *_, i in placed[:3]]] = True
    x = predict.feature_matrix(riders, {})
    return LinkageRace(day, group, pair_top3_probabilities(theta, x), same, top3)


def _excess(same: np.ndarray, race: LinkageRace) -> tuple[int, float, float]:
    upper = np.triu(same, 1)
    observed = (upper & np.outer(race.top3, race.top3)).sum()
    return int(upper.sum()), float(observed), float(race.pair_prob[upper].sum())


def linkage_excess(race: LinkageRace) -> float:
    """Same-line pairs both in the top 3, observed minus expected."""
    _, observed, expected = _excess(race.same_line, race)
    return observed - expected


def placebo_excess(race: LinkageRace, rng: np.random.Generator) -> float:
    """The same quantity with the riders shuffled over the line positions of the race."""
    p = rng.permutation(len(race.top3))
    _, observed, expected = _excess(race.same_line[p][:, p], race)
    return observed - expected


def _value(x: float) -> float | None:
    return None if math.isnan(x) else round(float(x), 4)


def _mean(values: np.ndarray) -> float:
    return float(values.mean()) if len(values) else math.nan


def linkage_summary(
    races: list[LinkageRace], *, n_boot: int = 2000, seed: int = 0
) -> dict[str, Any]:
    by_group: dict[str, list[LinkageRace]] = defaultdict(list)
    for race in races:
        by_group["all"].append(race)
        by_group[race.group].append(race)
    groups = {}
    for name in sorted(by_group, key=lambda g: (g != "all", g)):
        members = by_group[name]
        counts = [_excess(r.same_line, r) for r in members]
        excess = np.array([obs - exp for _, obs, exp in counts])
        days = np.array([r.day for r in members])
        median = statistics.median_low(days.tolist())
        first, second = _mean(excess[days <= median]), _mean(excess[days > median])
        ci = bootstrap_ci(excess, n_boot=n_boot, seed=seed)
        rng = np.random.default_rng(seed)
        placebo = np.array([placebo_excess(r, rng) for r in members])
        groups[name] = {
            "races": len(members),
            "pairs": sum(c[0] for c in counts),
            "observed": _value(sum(c[1] for c in counts)),
            "expected": _value(sum(c[2] for c in counts)),
            "excess_per_race": _value(_mean(excess)),
            "ci95": [_value(v) for v in ci],
            "first_half": _value(first),
            "second_half": _value(second),
            "placebo_excess_per_race": _value(_mean(placebo)),
            "placebo_ci95": [_value(v) for v in bootstrap_ci(placebo, n_boot=n_boot, seed=seed)],
            "decision": decide(ci, (first, second), LINKAGE_PRACTICAL),
        }
    return {"groups": groups}


# --- car numbers ---------------------------------------------------------------------------

CAR1_PRACTICAL = 0.03  # win rate of the top scorer, car 1 minus the other cars
CONVENTION_PAIRS = {9: ((4, 5), (6, 7), (8, 9)), 7: ((5, 6), (6, 7))}


@dataclass(frozen=True)
class Card:
    day: date
    time_slot: str
    field_size: int
    group: str | None
    cars: tuple[int, ...]
    scores: tuple[float, ...]
    finish_pos: tuple[int | None, ...]  # None: scratched, disqualified, fell, ...
    scratched: tuple[bool, ...]


def load_cards(data_dir: Path, start: date, end: date) -> list[Card]:
    """Race cards of start <= day < end in which every rider has a score."""
    out = []
    for path in sorted((data_dir / "tables" / "entries").glob("*/*.csv")):
        day = date.fromisoformat(path.stem)
        if day < start or day >= end:
            continue
        slots = {
            r["venue_code"]: r["time_slot"]
            for r in predict._read(table_path(data_dir, "meetings", day))
        }
        races = {
            (r["venue_code"], r["race_no"]): r
            for r in predict._read(table_path(data_dir, "races", day))
        }
        entries: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in predict._read(path):
            entries[(r["venue_code"], r["race_no"])].append(r)
        for key in sorted(entries, key=lambda k: (k[0], int(k[1]))):
            rows = sorted(entries[key], key=lambda r: int(r["car_no"]))
            race = races.get(key)
            if race is None or any(r["score"] == "" for r in rows):
                continue
            out.append(
                Card(
                    day,
                    slots.get(key[0], ""),
                    int(race["entries"]),
                    predict.group_of(race["race_class"]),
                    tuple(int(r["car_no"]) for r in rows),
                    tuple(float(r["score"]) for r in rows),
                    tuple(int(r["finish_pos"]) if r["finish_pos"] else None for r in rows),
                    tuple(predict._scratched(r) for r in rows),
                )
            )
    return out


def _share(hits: list[bool]) -> float | None:
    return _value(sum(hits) / len(hits)) if hits else None


def score_order_summary(cards: list[Card]) -> dict[str, Any]:
    """Midnight cards: car numbers in score order (ties either way) and car 1 on top."""
    midnight = [c for c in cards if c.time_slot == "midnight"]
    ordered = [all(a >= b for a, b in zip(c.scores, c.scores[1:], strict=False)) for c in midnight]
    top = [c.scores[c.cars.index(1)] == max(c.scores) for c in midnight if 1 in c.cars]
    return {
        "races": len(midnight),
        "car_order_equals_score_rank": _share(ordered),
        "car1_is_top_score": _share(top),
    }


def convention_summary(cards: list[Card]) -> dict[str, Any]:
    """Outside midnight: share of cards where the lower car of a pair has the lower score."""
    out: dict[str, Any] = {}
    for size, pairs in CONVENTION_PAIRS.items():
        out[f"{size}車"] = {}
        for low, high in pairs:
            hits = [
                c.scores[c.cars.index(low)] < c.scores[c.cars.index(high)]
                for c in cards
                if c.time_slot != "midnight"
                and c.field_size == size
                and low in c.cars
                and high in c.cars
            ]
            out[f"{size}車"][f"{low}<{high}"] = {"races": len(hits), "share": _share(hits)}
    return out


def _diff_ci(a: np.ndarray, b: np.ndarray, *, n_boot: int, seed: int) -> tuple[float, float]:
    if len(a) == 0 or len(b) == 0:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    diffs = [
        a[rng.integers(0, len(a), len(a))].mean() - b[rng.integers(0, len(b), len(b))].mean()
        for _ in range(n_boot)
    ]
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi)


def car1_effect(cards: list[Card], *, n_boot: int = 2000, seed: int = 0) -> dict[str, Any]:
    """Men's 7-car races outside midnight with one clear top scorer (scratched riders out):
    does the top scorer win more often from car 1 than from the other cars?"""
    rows = []  # (day, from car 1, won)
    for c in cards:
        if c.time_slot == "midnight" or c.field_size != 7 or c.group not in ("A", "S"):
            continue
        active = [i for i in range(len(c.cars)) if not c.scratched[i]]
        best = max(c.scores[i] for i in active)
        tops = [i for i in active if c.scores[i] == best]
        if len(tops) == 1:
            rows.append((c.day, c.cars[tops[0]] == 1, c.finish_pos[tops[0]] == 1))
    days = np.array([d for d, _, _ in rows], dtype=object)
    car1 = np.array([one for _, one, _ in rows], dtype=bool)
    won = np.array([w for _, _, w in rows], dtype=float)

    def diff(mask: np.ndarray) -> float:
        a, b = won[mask & car1], won[mask & ~car1]
        return _mean(a) - _mean(b) if len(a) and len(b) else math.nan

    everyone = np.ones(len(rows), bool)
    if rows:
        median = statistics.median_low(days.tolist())
        first, second = diff(days <= median), diff(days > median)
    else:
        first = second = math.nan
    ci = _diff_ci(won[car1], won[~car1], n_boot=n_boot, seed=seed)
    return {
        "races": len(rows),
        "car1_races": int(car1.sum()),
        "other_races": int((~car1).sum()),
        "car1_win": _value(_mean(won[car1])),
        "other_win": _value(_mean(won[~car1])),
        "diff": _value(diff(everyone)),
        "ci95": [_value(v) for v in ci],
        "first_half": _value(first),
        "second_half": _value(second),
        "decision": decide(ci, (first, second), CAR1_PRACTICAL),
    }
