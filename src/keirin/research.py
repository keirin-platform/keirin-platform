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
