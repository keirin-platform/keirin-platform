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
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import logsumexp

from keirin import predict
from keirin.lines import REGIONS
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
    """正の効果 / 負の効果 / 実質的な差なし / 保留 (keirin-research COMMIT-002).

    An interval inside ±practical is 実質的な差なし even when it excludes 0: the effect
    may exist, but it is too small to use.
    """
    lo, hi = ci
    if -practical < lo and hi < practical:
        return "実質的な差なし"
    if lo > 0 and all(h > 0 for h in halves):
        return "正の効果"
    if hi < 0 and all(h < 0 for h in halves):
        return "負の効果"
    return "保留"


# --- line linkage ------------------------------------------------------------------------


def pair_top3_probabilities(theta: np.ndarray, x: np.ndarray) -> np.ndarray:
    """(riders, riders): probability that both riders finish in the top 3 (zero diagonal).

    The same stages as `predict.race_probabilities`: 1st from u1, 2nd from t2 * u23 and
    3rd from t3 * u23, summed over every ordered top 3.
    """
    u1, u23, (t2, t3) = predict.utilities(theta, x)
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
        for r in predict.read_rows(path):
            formations[(r["venue_code"], r["race_no"])][int(r["car_no"])] = {
                "line_no": int(r["line_no"]),
                "line_pos": int(r["line_pos"]),
            }
        classes = {
            (r["venue_code"], r["race_no"]): r["race_class"]
            for r in predict.read_rows(table_path(data_dir, "races", day))
        }
        entries: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in predict.read_rows(table_path(data_dir, "entries", day)):
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
    riders = [r for r in rows if not predict.is_scratched(r)]
    if len(riders) < 3 or any(r["score"] == "" for r in riders):
        return None
    placed = sorted(
        (int(r["finish_pos"]), int(r["car_no"]), i) for i, r in enumerate(riders) if r["finish_pos"]
    )
    if len(placed) < 3:
        return None
    scratched = {int(r["car_no"]) for r in rows if predict.is_scratched(r)}
    by_car = predict.close_gaps(formation, scratched)
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
            for r in predict.read_rows(table_path(data_dir, "meetings", day))
        }
        races = {
            (r["venue_code"], r["race_no"]): r
            for r in predict.read_rows(table_path(data_dir, "races", day))
        }
        entries: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in predict.read_rows(path):
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
                    tuple(predict.is_scratched(r) for r in rows),
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
    does the top scorer win more often from car 1 than from the other cars?

    Only races with at least three placed count (as in `predict.load_races`): a cancelled
    race has no winner, so it would count the top scorer as a loss.
    """
    rows = []  # (day, from car 1, won)
    for c in cards:
        if c.time_slot == "midnight" or c.field_size != 7 or c.group not in ("A", "S"):
            continue
        if sum(p is not None for p in c.finish_pos) < 3:
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


# --- chase break (追走の千切れ) ---------------------------------------------------------------

CHASE_PRACTICAL = 0.05  # strong-break rate, makuri win minus nige win, fixed before measuring
CHASE_MIN_CELL = 10  # cases per side in a stratum
CHASE_MIN_RIDER = 10  # cases for a rider to enter the individual-difference test
CHASE_MIN_HALF_RIDER = 6  # the same, within one half of the period
CHASE_SPLIT_MIN = 5  # cases in each half for the split-half correlation
SCORE_RANK_BINS = 7  # score rank 1, 2, 3, 4, 5, 6, 7 or worse
_STRATA = 2 * SCORE_RANK_BINS * 2  # rel (1, 2) x score rank x nine-car field


@dataclass(frozen=True)
class ChaseRow:
    """A rider behind the winner in the winner's line (not contested, placed)."""

    day: date
    race: int  # index of the race
    rider: str  # kept in memory only; never part of an output
    winner_kimarite: str
    stratum: int  # (rel - 1) * 14 + rank bin * 2 + nine-car field
    strong: bool  # finished outside the top 3
    rel: int = 1  # places behind the winner
    rank: int = 1  # score rank in the race
    nine: bool = False
    own_size: int = 2  # riders in the winner's line
    opp_max: int = 0  # riders in the longest other line (0: no other line)
    gap: float = math.nan  # winner's score minus the best score outside the line


def _stratum(rel: int, score_rank: int, nine: bool) -> int:
    return (rel - 1) * SCORE_RANK_BINS * 2 + (min(score_rank, SCORE_RANK_BINS) - 1) * 2 + int(nine)


def load_chase_rows(data_dir: Path, start: date, end: date) -> list[ChaseRow]:
    """Followers (1 or 2 places behind) of a winner in a line of two or more, men's only.

    Only races with real lines count; scratched riders are taken out of the line, and a
    follower must have a placed finish (a fall or a disqualification has none).
    """
    out: list[ChaseRow] = []
    race_index = 0
    for path in sorted((data_dir / "tables" / "lines").glob("*/*.csv")):
        day = date.fromisoformat(path.stem)
        if day < start or day >= end:
            continue
        formations: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
        for r in predict.read_rows(path):
            formations[(r["venue_code"], r["race_no"])][int(r["car_no"])] = {
                "line_no": int(r["line_no"]),
                "line_pos": int(r["line_pos"]),
            }
        classes = {
            (r["venue_code"], r["race_no"]): r["race_class"]
            for r in predict.read_rows(table_path(data_dir, "races", day))
        }
        entries: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in predict.read_rows(table_path(data_dir, "entries", day)):
            entries[(r["venue_code"], r["race_no"])].append(r)
        for key in sorted(formations, key=lambda k: (k[0], int(k[1]))):
            if predict.group_of(classes.get(key)) not in ("A", "S"):
                continue
            rows = entries[key]
            riders = [r for r in rows if not predict.is_scratched(r)]
            if any(r["score"] == "" for r in riders):
                continue
            scratched = {int(r["car_no"]) for r in rows if predict.is_scratched(r)}
            by_car = predict.close_gaps(formations[key], scratched)
            scores = [float(r["score"]) for r in riders]
            scores_of = {int(r["car_no"]): float(r["score"]) for r in riders}
            before = len(out)
            winners = [r for r in riders if r["finish_pos"] == "1"]
            if len(winners) != 1 or int(winners[0]["car_no"]) not in by_car:
                continue
            win = winners[0]
            w = by_car[int(win["car_no"])]
            if w["contested"] or w["line_size"] < 2:
                continue
            others = {
                i["line_no"]: i["line_size"]
                for c, i in by_car.items()
                if i["line_no"] != w["line_no"]
            }
            outside = [scores_of[c] for c, i in by_car.items() if i["line_no"] != w["line_no"]]
            gap = float(win["score"]) - max(outside) if outside else math.nan
            for r in riders:
                info = by_car.get(int(r["car_no"]))
                if not info or info["line_no"] != w["line_no"] or info["contested"]:
                    continue
                rel = info["line_pos"] - w["line_pos"]
                if rel not in (1, 2) or not r["finish_pos"]:
                    continue
                rank = 1 + sum(s > float(r["score"]) for s in scores)
                out.append(
                    ChaseRow(
                        day,
                        race_index,
                        r["racer_id"],
                        win["kimarite"],
                        _stratum(rel, rank, len(riders) >= 9),
                        int(r["finish_pos"]) > 3,
                        rel,
                        rank,
                        len(riders) >= 9,
                        w["line_size"],
                        max(others.values(), default=0),
                        gap,
                    )
                )
            if len(out) > before:
                race_index += 1
    return out


def _stratified_arrays(rows: list[ChaseRow], races: int) -> np.ndarray:
    """(races, 4, strata): makuri n, makuri strong, nige n, nige strong per race."""
    a = np.zeros((races, 4, _STRATA))
    for r in rows:
        if r.winner_kimarite == "捲り":
            a[r.race, 0, r.stratum] += 1
            a[r.race, 1, r.stratum] += r.strong
        elif r.winner_kimarite == "逃げ":
            a[r.race, 2, r.stratum] += 1
            a[r.race, 3, r.stratum] += r.strong
    return a


def _standardized_diff(total: np.ndarray, min_cell: int = CHASE_MIN_CELL) -> float:
    """Makuri minus nige strong-break rate, weighted by makuri cases of the usable strata."""
    an, ak, bn, bk = total
    ok = (an >= min_cell) & (bn >= min_cell)
    if not ok.any():
        return math.nan
    diff = ak[ok] / an[ok] - bk[ok] / bn[ok]
    return float((an[ok] * diff).sum() / an[ok].sum())


def chase_effect(rows: list[ChaseRow], *, n_boot: int = 2000, seed: int = 0) -> dict[str, Any]:
    races = sorted({r.race for r in rows})
    index = {race: i for i, race in enumerate(races)}
    rows = [replace(r, race=index[r.race]) for r in rows]
    arrays = _stratified_arrays(rows, len(races))
    point = _standardized_diff(arrays.sum(axis=0))
    rng = np.random.default_rng(seed)
    flat = arrays.reshape(len(races), -1)
    boots = []
    for _ in range(n_boot):
        counts = rng.multinomial(len(races), np.full(len(races), 1 / len(races)))
        boots.append(_standardized_diff((counts @ flat).reshape(4, _STRATA)))
    ci = tuple(np.nanpercentile(boots, [2.5, 97.5]))
    median = statistics.median_low(sorted({r.day for r in rows}))
    halves = []
    for keep in (lambda d: d <= median, lambda d: d > median):
        half = [r for r in rows if keep(r.day)]
        halves.append(_standardized_diff(_stratified_arrays(half, len(races)).sum(axis=0), 5))
    kinds = ("捲り", "逃げ")
    rate = {
        k: {
            "n": sum(r.winner_kimarite == k for r in rows),
            "strong_break_rate": _value(
                _mean(np.array([r.strong for r in rows if r.winner_kimarite == k], float))
            ),
        }
        for k in kinds
    }
    return {
        "followers": rate,
        "races": len(races),
        "standardized_difference": _value(point),
        "ci95": [_value(v) for v in ci],
        "first_half": _value(halves[0]),
        "second_half": _value(halves[1]),
        "practical": CHASE_PRACTICAL,
        "decision": decide(ci, (halves[0], halves[1]), CHASE_PRACTICAL),
    }


def _rider_statistic(riders: np.ndarray, residual: np.ndarray, size: int) -> float:
    sums = np.bincount(riders, weights=residual, minlength=size)
    counts = np.bincount(riders, minlength=size)
    keep = counts > 0
    return float((sums[keep] ** 2 / counts[keep]).sum())


def _permutation_test(
    riders: np.ndarray, residual: np.ndarray, size: int, *, n_perm: int, seed: int
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    observed = _rider_statistic(riders, residual, size)
    null = np.array(
        [_rider_statistic(riders, rng.permutation(residual), size) for _ in range(n_perm)]
    )
    p = (int((null >= observed).sum()) + 1) / (n_perm + 1)
    return {
        "statistic": _value(observed),
        "null_mean": _value(null.mean()),
        "p": _value(p),
        "between_rider_sd": _value(
            math.sqrt(max(observed - null.mean(), 0.0) / max(len(residual), 1))
        ),
    }


def chase_individual(rows: list[ChaseRow], *, n_perm: int = 2000, seed: int = 0) -> dict[str, Any]:
    """Are riders different in breaking when they follow a makuri winner?

    The residual of each case is its outcome minus the stratum rate (rank, place behind
    the winner, nine-car field). See `_individual_report`.
    """
    makuri = [r for r in rows if r.winner_kimarite == "捲り"]
    strata = np.array([r.stratum for r in makuri])
    strong = np.array([r.strong for r in makuri], float)
    rate = np.zeros(_STRATA)
    for s in range(_STRATA):
        if (strata == s).any():
            rate[s] = strong[strata == s].mean()
    return _individual_report(makuri, strong - rate[strata], n_perm=n_perm, seed=seed)


def _individual_report(
    makuri: list[ChaseRow], residual: np.ndarray, *, n_perm: int, seed: int
) -> dict[str, Any]:
    """The statistic is the sum over riders of cases * mean^2 of the residual; the null
    permutes the residuals across riders. Only counts and p-values leave."""
    ids = {rider: i for i, rider in enumerate(sorted({r.rider for r in makuri}))}
    riders = np.array([ids[r.rider] for r in makuri])
    days = np.array([r.day for r in makuri])
    median = statistics.median_low(sorted(set(days.tolist())))
    cases = np.bincount(riders, minlength=len(ids))

    def test(mask: np.ndarray, minimum: int) -> dict[str, Any]:
        sub_cases = np.bincount(riders[mask], minlength=len(ids))
        keep = mask & (sub_cases[riders] >= minimum)
        out = _permutation_test(riders[keep], residual[keep], len(ids), n_perm=n_perm, seed=seed)
        out["riders"] = int((sub_cases >= minimum).sum())
        out["cases"] = int(keep.sum())
        return out

    whole = test(np.ones(len(makuri), bool), CHASE_MIN_RIDER)
    first = test(days <= median, CHASE_MIN_HALF_RIDER)
    second = test(days > median, CHASE_MIN_HALF_RIDER)
    # split-half: the same riders' mean residual in the two halves
    sums = {}
    for name, mask in (("a", days <= median), ("b", days > median)):
        sums[name] = (
            np.bincount(riders[mask], weights=residual[mask], minlength=len(ids)),
            np.bincount(riders[mask], minlength=len(ids)),
        )
    both = (sums["a"][1] >= CHASE_SPLIT_MIN) & (sums["b"][1] >= CHASE_SPLIT_MIN)
    mean_a = sums["a"][0][both] / sums["a"][1][both]
    mean_b = sums["b"][0][both] / sums["b"][1][both]
    split: dict[str, Any] = {"riders": int(both.sum())}
    if both.sum() >= 3:
        r_obs = float(np.corrcoef(mean_a, mean_b)[0, 1])
        rng = np.random.default_rng(seed)
        null = np.array([np.corrcoef(mean_a, rng.permutation(mean_b))[0, 1] for _ in range(n_perm)])
        split |= {"r": _value(r_obs), "p": _value((int((null >= r_obs).sum()) + 1) / (n_perm + 1))}
    return {
        "riders": len(ids),
        "cases": len(makuri),
        "cases_per_rider": {
            "median": float(np.median(cases[cases > 0])),
            "p90": float(np.percentile(cases[cases > 0], 90)),
            "max": int(cases.max()),
        },
        "whole_period": whole,
        "first_half": first,
        "second_half": second,
        "split_half": split,
        "decision": "個人差あり" if all(t["p"] < 0.05 for t in (whole, first, second)) else "保留",
    }


# --- chase break: all factors together -------------------------------------------------------

CHASE_MODEL_PRACTICAL = 0.03  # change in the strong-break rate per unit of a factor
GAP_UNIT = 5.0  # score points
GAP_CLIP = 20.0
FACTORS = ("own_size", "opp_max", "gap")  # +1 rider, +1 rider, +5 score points


def _design(rows: list[ChaseRow]) -> np.ndarray:
    """Intercept, 2 places behind, nine-car field, score-rank dummies (2..7+), then the
    three factors: own line size (cap 4), longest other line (cap 4), score gap / 5."""
    ranks = np.array([min(r.rank, SCORE_RANK_BINS) for r in rows])
    cols = [
        np.ones(len(rows)),
        np.array([r.rel == 2 for r in rows], float),
        np.array([r.nine for r in rows], float),
        *[(ranks == k).astype(float) for k in range(2, SCORE_RANK_BINS + 1)],
        np.array([min(r.own_size, 4) for r in rows], float),
        np.array([min(r.opp_max, 4) for r in rows], float),
        np.clip(np.array([r.gap for r in rows]), -GAP_CLIP, GAP_CLIP) / GAP_UNIT,
    ]
    return np.column_stack(cols)


def _logit(x: np.ndarray, y: np.ndarray, *, ridge: float = 1e-6) -> np.ndarray:
    """Logistic regression by Newton steps (a tiny ridge keeps sparse strata finite)."""
    beta = np.zeros(x.shape[1])
    for _ in range(25):
        eta = np.clip(x @ beta, -30, 30)
        p = 1 / (1 + np.exp(-eta))
        w = p * (1 - p) + 1e-9
        grad = x.T @ (y - p) - ridge * beta
        hess = (x * w[:, None]).T @ x + ridge * np.eye(x.shape[1])
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.abs(step).max() < 1e-8:
            break
    return beta


def _sigmoid(eta: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(eta, -30, 30)))


def _marginal_effects(x: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """Average change in the strong-break rate for one unit of each factor (own line size
    +1 rider, longest other line +1 rider, score gap +5 points)."""
    base = _sigmoid(x @ beta)
    out = []
    for k in (-3, -2, -1):  # the last three columns
        up = x.copy()
        up[:, k] += 1.0
        out.append(float((_sigmoid(up @ beta) - base).mean()))
    return np.array(out)


def chase_model(rows: list[ChaseRow], *, n_boot: int = 2000, seed: int = 0) -> dict[str, Any]:
    """Own line size, longest other line and score gap together, for followers of a makuri
    winner. Reports each factor's average marginal effect with a race-clustered bootstrap
    interval and the same fit in each half of the period; the individual-difference test
    is repeated on what the factors leave over."""
    cases = [
        r for r in rows if r.winner_kimarite == "捲り" and r.opp_max >= 1 and not math.isnan(r.gap)
    ]
    if len(cases) < 3 * len(FACTORS) + 20:  # too few to fit
        return {"cases": len(cases), "practical": CHASE_MODEL_PRACTICAL, "factors": {}}
    x = _design(cases)
    y = np.array([r.strong for r in cases], float)
    beta = _logit(x, y)
    point = _marginal_effects(x, beta)
    race = np.array([r.race for r in cases])
    order = np.argsort(race, kind="stable")
    bounds = np.flatnonzero(np.diff(race[order])) + 1
    groups = np.split(order, bounds)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        idx = np.concatenate([groups[i] for i in pick])
        boots.append(_marginal_effects(x[idx], _logit(x[idx], y[idx])))
    boots = np.array(boots)
    days = np.array([r.day for r in cases])
    median = statistics.median_low(sorted(set(days.tolist())))
    halves = []
    for mask in (days <= median, days > median):
        halves.append(_marginal_effects(x[mask], _logit(x[mask], y[mask])))
    factors = {}
    for k, name in enumerate(FACTORS):
        ci = tuple(np.percentile(boots[:, k], [2.5, 97.5]))
        factors[name] = {
            "effect_per_unit": _value(point[k]),
            "ci95": [_value(v) for v in ci],
            "first_half": _value(halves[0][k]),
            "second_half": _value(halves[1][k]),
            "decision": decide(ci, (halves[0][k], halves[1][k]), CHASE_MODEL_PRACTICAL),
        }
    residual = y - _sigmoid(x @ beta)
    return {
        "cases": len(cases),
        "practical": CHASE_MODEL_PRACTICAL,
        "units": {"own_size": "+1 rider", "opp_max": "+1 rider", "gap": f"+{GAP_UNIT:g} points"},
        "factors": factors,
        "individual_after_factors": _individual_report(cases, residual, n_perm=n_boot, seed=seed),
    }


# --- line positions (位置・人数・競り・地区) ---------------------------------------------------
# docs/line-positions.md: the measures and the practical sizes were fixed before measuring.

POSITION_WIN_PRACTICAL = 0.02  # win rate minus the score-only model
POSITION_TOP3_PRACTICAL = 0.03  # top-3 rate minus the score-only model
BACK_LINE_PRACTICAL = 0.03  # win rate, second minus head of a line whose head took the back
PAIR_PRACTICAL = 0.02  # head and second both in the top 3 minus the model, per pair
PAYOUT_PRACTICAL = 0.10  # log exacta payout, contested minus other races
BACK_LINE_CLAIM = (0.75, 0.85)  # "80% of the winners come from the line that took the back"
PAYOUT_MIN_CELL = 10  # races per side in a stratum
WITHIN_RIDER_MIN = 5  # rides in each of the two kinds of position

OUTCOMES = ("win", "top2", "top3")
_PLACE_NAMES = {1: "head", 2: "second", 3: "third", 4: "fourth"}
POSITIONS = {  # cells of `_cells`; the suffix is the line size (4: four or more)
    "head": ("head_2", "head_3", "head_4"),
    "second": ("second_2", "second_3", "second_4"),
    "third": ("third_3", "third_4"),
    "fourth_plus": ("fourth_4",),
    "solo": ("solo",),
    "contested": ("contested",),
    "contested_head": ("contested_head",),
}
_LONG_FRONT = ("head_3", "head_4", "second_3", "second_4")
_SHORT_FRONT = ("head_2", "second_2")
_ROLES = ("head", "second", "third", "solo")
_PAYOUT_STRATA = tuple(f"{g}{f}" for g in "AS" for f in ("7", "9", "x"))
_NEARNESS = ("prefecture", "region", "other")
_AFTER_HOME = ("same", "second", "behind", "other")


@dataclass(frozen=True)
class LineRace:
    """A men's race with a real formation; one entry per rider who started."""

    day: date
    group: str  # A / S
    final: bool  # 決勝
    source: str  # where the formation comes from
    pos: tuple[int, ...]  # place in the line (1: head; a rider alone is 1)
    size: tuple[int, ...]  # riders in the line
    fight: tuple[bool, ...]  # contests the place with another rider (競り)
    line: tuple[int, ...]  # line number (a rider alone has their own)
    scores: tuple[float, ...]
    riders: tuple[str, ...]  # kept in memory only; never part of an output
    prefectures: tuple[str, ...]
    prob: np.ndarray  # (n, 3): win / top 2 / top 3 under the score-only model
    pair_prob: np.ndarray  # (n, n): both in the top 3
    order: tuple[int, int, int]  # entries of the 1st, 2nd and 3rd
    back: int | None = None  # entry of the rider with B (None unless exactly one)
    home: int | None = None
    exacta: tuple[float, ...] = ()  # 2車単 payouts per 100 yen (several on a dead heat)
    trifecta: tuple[float, ...] = ()


def load_line_races(data_dir: Path, start: date, end: date, model: predict.Model) -> list[LineRace]:
    """Men's races of start <= day < end with real lines, a score for every rider who
    started and three placed riders. `model` is the score-only model of each group."""
    out = []
    for path in sorted((data_dir / "tables" / "lines").glob("*/*.csv")):
        day = date.fromisoformat(path.stem)
        if day < start or day >= end:
            continue
        formations: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
        sources = {}
        for r in predict.read_rows(path):
            key = (r["venue_code"], r["race_no"])
            formations[key][int(r["car_no"])] = {
                "line_no": int(r["line_no"]),
                "line_pos": int(r["line_pos"]),
            }
            sources[key] = r["source"]
        classes = {
            (r["venue_code"], r["race_no"]): r["race_class"]
            for r in predict.read_rows(table_path(data_dir, "races", day))
        }
        entries: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in predict.read_rows(table_path(data_dir, "entries", day)):
            entries[(r["venue_code"], r["race_no"])].append(r)
        payouts: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for r in predict.read_rows(table_path(data_dir, "payouts", day)):
            if r["bet_type"] in ("2車単", "3連単") and float(r["payout"] or 0) > 0:
                payouts[(r["venue_code"], r["race_no"])][r["bet_type"]].append(float(r["payout"]))
        for key in sorted(formations, key=lambda k: (k[0], int(k[1]))):
            race_class = classes.get(key) or ""
            group = predict.group_of(race_class)
            if group not in ("A", "S") or group not in model.params:
                continue
            race = _line_race(
                day, group, race_class, sources[key], entries[key], formations[key],
                model.params[group], payouts.get(key, {}),
            )  # fmt: skip
            if race is not None:
                out.append(race)
    return out


def _line_race(
    day: date,
    group: str,
    race_class: str,
    source: str,
    rows: list[dict[str, str]],
    formation: dict[int, dict[str, Any]],
    theta: np.ndarray,
    payouts: dict[str, list[float]],
) -> LineRace | None:
    riders = [r for r in rows if not predict.is_scratched(r)]
    if len(riders) < 3 or any(r["score"] == "" for r in riders):
        return None
    placed = sorted(
        (int(r["finish_pos"]), int(r["car_no"]), i) for i, r in enumerate(riders) if r["finish_pos"]
    )
    if len(placed) < 3:
        return None
    scratched = {int(r["car_no"]) for r in rows if predict.is_scratched(r)}
    by_car = predict.close_gaps(formation, scratched)
    line_no = max((info["line_no"] for info in by_car.values()), default=0)
    infos = []
    for r in riders:
        info = by_car.get(int(r["car_no"]))
        if info is None:  # not in the formation: rides alone
            line_no += 1
            info = {"line_no": line_no, "line_pos": 1, "line_size": 1, "contested": False}
        infos.append(info)

    def only(flag: str) -> int | None:
        marked = [i for i, r in enumerate(riders) if flag in (r.get("bh") or "")]
        return marked[0] if len(marked) == 1 else None

    prob, pair_prob = predict.expected_places(theta, predict.feature_matrix(riders, {}))
    return LineRace(
        day,
        group,
        "決勝" in race_class and "準決" not in race_class,
        source,
        tuple(i["line_pos"] for i in infos),
        tuple(i["line_size"] for i in infos),
        tuple(bool(i["contested"]) for i in infos),
        tuple(i["line_no"] for i in infos),
        tuple(float(r["score"]) for r in riders),
        tuple(r["racer_id"] for r in riders),
        tuple(r["prefecture"] for r in riders),
        prob,
        pair_prob,
        tuple(i for *_, i in placed[:3]),
        only("B"),
        only("H"),
        tuple(payouts.get("2車単", ())),
        tuple(payouts.get("3連単", ())),
    )


def _cells(race: LineRace) -> list[str]:
    """Where each rider is: head_2 .. fourth_4 in a line nobody contests (the suffix is the
    line size, 4 for four or more), solo, and in a line with a contested place contested
    (the riders fighting for it), contested_head or contested_other."""
    fought = {line for line, fight in zip(race.line, race.fight, strict=True) if fight}
    out = []
    for pos, size, fight, line in zip(race.pos, race.size, race.fight, race.line, strict=True):
        if size == 1:
            out.append("solo")
        elif fight:
            out.append("contested")
        elif line in fought:
            out.append("contested_head" if pos == 1 else "contested_other")
        else:
            out.append(f"{_PLACE_NAMES[min(pos, 4)]}_{min(size, 4)}")
    return out


def _placed(race: LineRace) -> np.ndarray:
    """(n, 3) bool: won, in the top 2, in the top 3."""
    placed = np.zeros((len(race.pos), 3), bool)
    placed[race.order[0], 0] = True
    placed[list(race.order[:2]), 1] = True
    placed[list(race.order), 2] = True
    return placed


def _nearness(a: str, b: str) -> str | None:
    """Same prefecture, same region (lines.REGIONS) or another region; None when unknown."""
    if a not in REGIONS or b not in REGIONS:
        return None
    if a == b:
        return "prefecture"
    return "region" if REGIONS[a] == REGIONS[b] else "other"


def _race_columns(race: LineRace) -> dict[str, float]:
    """Everything the summary needs from one race, as sums it can add up over races."""
    c: dict[str, float] = defaultdict(float)
    n = len(race.pos)
    placed, cells = _placed(race), _cells(race)
    winner = race.order[0]
    c["races"] = 1
    for i, cell in enumerate(cells):
        c[f"n:{cell}"] += 1
        for k, outcome in enumerate(OUTCOMES):
            c[f"{outcome}:{cell}"] += placed[i, k]
            c[f"e{outcome}:{cell}"] += race.prob[i, k]
        rank = min(1 + sum(s > race.scores[i] for s in race.scores), SCORE_RANK_BINS)
        role = cell.split("_")[0] if cell != "solo" else "solo"
        for who in ("all", role) if role in _ROLES and "contested" not in cell else ("all",):
            c[f"rk:{who}:{rank}:n"] += 1
            c[f"rk:{who}:{rank}:win"] += placed[i, 0] - race.prob[i, 0]
            c[f"rk:{who}:{rank}:top3"] += placed[i, 2] - race.prob[i, 2]

    def second_of(head: int) -> int:
        return next(i for i in range(n) if race.line[i] == race.line[head] and race.pos[i] == 2)

    def mates(i: int) -> list[int]:
        return [k for k in range(n) if race.line[k] == race.line[i]]

    # the head and the second of each line nobody contests, by how near their homes are
    for i, cell in enumerate(cells):
        if cell.startswith("head_"):
            j = second_of(i)
            near = _nearness(race.prefectures[i], race.prefectures[j])
            if near:
                c[f"pr:{near}:n"] += 1
                c[f"pr:{near}:obs"] += placed[i, 2] and placed[j, 2]
                c[f"pr:{near}:exp"] += race.pair_prob[i, j]

    if race.back is not None:
        b = race.back
        c["b:n"] += 1
        c["b:line_win"] += winner in mates(b)
        c["b:self_win"] += winner == b
        if race.size[b] >= 2:
            c["b:multi_n"] += 1
            c["b:multi_line_win"] += winner in mates(b)
        if cells[b].startswith("head_"):  # the head of an uncontested line led at the back
            j = second_of(b)
            c["bl:n"] += 1
            for name, i in (("head", b), ("second", j)):
                c[f"bl:{name}_win"] += placed[i, 0]
                c[f"bl:{name}_ewin"] += race.prob[i, 0]
                c[f"bl:{name}_top3"] += placed[i, 2]
    if race.home is not None:
        h = race.home
        c["h:n"] += 1
        c["h:line_win"] += winner in mates(h)
        if cells[h].startswith("head_") and race.back is not None:
            b = race.back
            if b == h:
                who = "same"
            elif race.line[b] == race.line[h]:
                who = "second" if race.pos[b] == 2 else "behind"
            else:
                who = "other"
            c[f"ah:{who}:n"] += 1
            for name, i in (("head", h), ("second", second_of(h))):
                c[f"ah:{who}:{name}_win"] += placed[i, 0]
                c[f"ah:{who}:{name}_top3"] += placed[i, 2]

    first, second, third = race.order
    same12 = race.line[first] == race.line[second]
    c["ot:same12"] += same12
    c["ot:same123"] += same12 and race.line[third] == race.line[first]

    kind = "c" if any(race.fight) else "p"  # contested or plain
    stratum = f"{race.group}{n if n in (7, 9) else 'x'}"
    for name, paid in (("x2", race.exacta), ("x3", race.trifecta)):
        if paid:
            c[f"{name}:{stratum}:{kind}n"] += 1
            c[f"{name}:{stratum}:{kind}log"] += float(np.mean(np.log(paid)))
    best = max(race.scores)
    tops = [i for i, s in enumerate(race.scores) if s == best]
    if len(tops) == 1:
        c[f"fav:{kind}:n"] += 1
        c[f"fav:{kind}:win"] += placed[tops[0], 0]
        c[f"fav:{kind}:ewin"] += race.prob[tops[0], 0]
    return c


def _ratio(a: Any, b: Any) -> Any:
    """a / b, NaN where b is 0."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return np.where(b > 0, a / np.where(b > 0, b, 1.0), math.nan)


class _Totals:
    """Column sums over the races: the whole period, each half (split at the median race
    day) and bootstrap replicates that resample races."""

    def __init__(self, races: list[LineRace], *, n_boot: int, seed: int) -> None:
        rows = [_race_columns(r) for r in races]
        self.index = {name: i for i, name in enumerate(sorted({k for row in rows for k in row}))}
        table = np.zeros((len(rows), len(self.index)))
        for i, row in enumerate(rows):
            for name, value in row.items():
                table[i, self.index[name]] = value
        days = np.array([r.day.toordinal() for r in races], dtype=int)
        median = statistics.median_low(days.tolist()) if len(days) else 0
        self.point = table.sum(axis=0)
        self.halves = (table[days <= median].sum(axis=0), table[days > median].sum(axis=0))
        rng = np.random.default_rng(seed)
        n = len(rows)
        boots = [np.zeros((0, len(self.index)))]
        for first in range(0, n_boot if n else 0, 100):
            draws = rng.multinomial(n, np.full(n, 1 / n), size=min(100, n_boot - first))
            boots.append(draws @ table)
        self.boots = np.vstack(boots)

    def _view(self, sums: np.ndarray):
        def total(*names: str) -> np.ndarray:
            out = np.zeros(sums.shape[:-1])
            for name in names:
                if name in self.index:
                    out = out + sums[..., self.index[name]]
            return out

        return total

    def value(self, stat) -> float | None:
        with np.errstate(divide="ignore", invalid="ignore"):
            return _value(float(stat(self._view(self.point))))

    def estimate(self, stat, practical: float | None = None) -> dict[str, Any]:
        """`stat` maps a function giving column totals to the statistic."""
        with np.errstate(divide="ignore", invalid="ignore"):
            point = float(stat(self._view(self.point)))
            boots = np.asarray(stat(self._view(self.boots)), float)
            halves = tuple(float(stat(self._view(h))) for h in self.halves)
        ci = (math.nan, math.nan)
        if np.isfinite(boots).any():
            ci = tuple(float(v) for v in np.nanpercentile(boots, [2.5, 97.5]))
        out = {
            "value": _value(point),
            "ci95": [_value(v) for v in ci],
            "first_half": _value(halves[0]),
            "second_half": _value(halves[1]),
        }
        if practical is not None:
            out |= {"practical": practical, "decision": decide(ci, halves, practical)}
        return out


def _rate_excess(cells: tuple[str, ...], outcome: str):
    """Observed minus expected rate of the riders in these cells."""

    def stat(t) -> Any:
        observed = t(*[f"{outcome}:{c}" for c in cells])
        expected = t(*[f"e{outcome}:{c}" for c in cells])
        return _ratio(observed - expected, t(*[f"n:{c}" for c in cells]))

    return stat


def _minus(a, b):
    return lambda t: a(t) - b(t)


def _pair_excess(near: str):
    return lambda t: _ratio(t(f"pr:{near}:obs") - t(f"pr:{near}:exp"), t(f"pr:{near}:n"))


def _payout_gap(name: str):
    """Mean log payout, contested minus plain races, within group x field size strata and
    weighted by the contested races of the strata with enough races on both sides."""

    def stat(t) -> Any:
        cn, cl, pn, pl = (
            np.stack([t(f"{name}:{s}:{col}") for s in _PAYOUT_STRATA], axis=-1)
            for col in ("cn", "clog", "pn", "plog")
        )
        ok = (cn >= PAYOUT_MIN_CELL) & (pn >= PAYOUT_MIN_CELL)
        diff = np.where(ok, cl / np.where(ok, cn, 1.0) - pl / np.where(ok, pn, 1.0), 0.0)
        weight = np.where(ok, cn, 0.0)
        return _ratio((weight * diff).sum(axis=-1), weight.sum(axis=-1))

    return stat


def _rank_standardized(role: str, outcome: str):
    """Excess of a role over all riders of the same score rank, weighted by the role."""

    def stat(t) -> Any:
        total, count = 0.0, 0.0
        for rank in range(1, SCORE_RANK_BINS + 1):
            n = t(f"rk:{role}:{rank}:n")
            everyone = _ratio(t(f"rk:all:{rank}:{outcome}"), t(f"rk:all:{rank}:n"))
            total = total + t(f"rk:{role}:{rank}:{outcome}") - np.where(n > 0, n * everyone, 0.0)
            count = count + n
        return _ratio(total, count)

    return stat


def _back_line_share(t) -> Any:
    return _ratio(t("b:line_win"), t("b:n"))


PRIMARY = {  # name: (statistic, practical size); docs/line-positions.md
    "second_minus_head_win": (
        _minus(_rate_excess(POSITIONS["second"], "win"), _rate_excess(POSITIONS["head"], "win")),
        POSITION_WIN_PRACTICAL,
    ),
    "back_line_second_minus_head_win": (
        lambda t: _ratio(t("bl:second_win") - t("bl:head_win"), t("bl:n")),
        BACK_LINE_PRACTICAL,
    ),
    "third_win": (_rate_excess(POSITIONS["third"], "win"), POSITION_WIN_PRACTICAL),
    "third_top3": (_rate_excess(POSITIONS["third"], "top3"), POSITION_TOP3_PRACTICAL),
    "solo_top3": (_rate_excess(POSITIONS["solo"], "top3"), POSITION_TOP3_PRACTICAL),
    "solo_win": (_rate_excess(POSITIONS["solo"], "win"), POSITION_WIN_PRACTICAL),
    "contested_log_exacta": (_payout_gap("x2"), PAYOUT_PRACTICAL),
    "long_line_front_win": (
        _minus(_rate_excess(_LONG_FRONT, "win"), _rate_excess(_SHORT_FRONT, "win")),
        POSITION_WIN_PRACTICAL,
    ),
    "long_line_front_top3": (
        _minus(_rate_excess(_LONG_FRONT, "top3"), _rate_excess(_SHORT_FRONT, "top3")),
        POSITION_TOP3_PRACTICAL,
    ),
    "same_prefecture_pair": (
        _minus(_pair_excess("prefecture"), _pair_excess("other")),
        PAIR_PRACTICAL,
    ),
}


def decide_share(ci: tuple[float, float], halves: tuple[float, float]) -> str:
    """主張どおり / 主張と違う / 保留 for a claimed share (BACK_LINE_CLAIM)."""
    low, high = BACK_LINE_CLAIM
    lo, hi = ci
    if low <= lo and hi <= high:
        return "主張どおり"
    if (hi < low and all(h < low for h in halves)) or (lo > high and all(h > high for h in halves)):
        return "主張と違う"
    return "保留"


def _decisions(totals: _Totals) -> dict[str, Any]:
    out = {name: totals.estimate(stat, practical) for name, (stat, practical) in PRIMARY.items()}
    share = totals.estimate(_back_line_share)
    ci = tuple(math.nan if v is None else v for v in share["ci95"])
    halves = tuple(
        math.nan if v is None else v for v in (share["first_half"], share["second_half"])
    )
    out["back_line_winner_share"] = share | {
        "claim": list(BACK_LINE_CLAIM),
        "decision": decide_share(ci, halves),
    }
    return out


def _cell_report(totals: _Totals, cells: tuple[str, ...]) -> dict[str, Any]:
    riders = [f"n:{c}" for c in cells]
    out: dict[str, Any] = {"riders": int(totals.value(lambda t: t(*riders)) or 0)}
    for outcome in OUTCOMES:
        observed = [f"{outcome}:{c}" for c in cells]
        expected = [f"e{outcome}:{c}" for c in cells]
        out[outcome] = {
            "rate": totals.value(lambda t, o=observed: _ratio(t(*o), t(*riders))),
            "expected": totals.value(lambda t, e=expected: _ratio(t(*e), t(*riders))),
            "excess": totals.estimate(_rate_excess(cells, outcome)),
        }
    return out


def within_rider(
    races: list[LineRace],
    first: tuple[str, ...],
    second: tuple[str, ...],
    *,
    n_boot: int = 2000,
    seed: int = 0,
) -> dict[str, Any]:
    """The same rider in two kinds of position: the excess (observed minus the score-only
    model) in the cells `first` minus that in `second`, averaged over the riders with
    enough rides in both. The interval resamples riders. Only aggregates leave."""
    sums: dict[str, np.ndarray] = defaultdict(lambda: np.zeros((2, 3)))  # n, win, top 3
    for race in races:
        placed = _placed(race)
        for i, cell in enumerate(_cells(race)):
            if cell in first or cell in second:
                s = sums[race.riders[i]][0 if cell in first else 1]
                s += (1, placed[i, 0] - race.prob[i, 0], placed[i, 2] - race.prob[i, 2])
    kept = np.array(
        [s for s in sums.values() if s[:, 0].min() >= WITHIN_RIDER_MIN], dtype=float
    ).reshape(-1, 2, 3)
    out: dict[str, Any] = {"riders": len(kept), "min_rides": WITHIN_RIDER_MIN}
    if len(kept) < 3:
        return out
    n_first, n_second = kept[:, 0, 0], kept[:, 1, 0]
    weight = n_first * n_second / (n_first + n_second)
    diff = kept[:, 0, 1:] / n_first[:, None] - kept[:, 1, 1:] / n_second[:, None]
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(kept), len(kept))
        boots.append((weight[pick, None] * diff[pick]).sum(axis=0) / weight[pick].sum())
    point = (weight[:, None] * diff).sum(axis=0) / weight.sum()
    out["rides"] = [int(n_first.sum()), int(n_second.sum())]
    for k, outcome in enumerate(("win", "top3")):
        ci = np.percentile(np.array(boots)[:, k], [2.5, 97.5]) if boots else (math.nan,) * 2
        out[outcome] = {"value": _value(point[k]), "ci95": [_value(v) for v in ci]}
    return out


SEGMENTS = {
    "A級": lambda r: r.group == "A",
    "S級7車": lambda r: r.group == "S" and len(r.pos) == 7,
    "S級9車": lambda r: r.group == "S" and len(r.pos) == 9,
    "決勝": lambda r: r.final,
    "決勝以外": lambda r: not r.final,
    "オッズパーク": lambda r: r.source == "oddspark",
    "KEIRIN.JP": lambda r: r.source == "keirin.jp",
}


def line_position_summary(
    races: list[LineRace], *, n_boot: int = 2000, seed: int = 0
) -> dict[str, Any]:
    """Aggregates for docs/line-positions.md (keirin-research CAND-002..009)."""
    totals = _Totals(races, n_boot=n_boot, seed=seed)

    def value(stat) -> float | None:
        return totals.value(stat)

    def count(*names: str) -> int:
        return int(value(lambda t: t(*names)) or 0)

    in_fought_line = ("contested", "contested_head", "contested_other")
    winner_share = {
        name: value(lambda t, c=cells: _ratio(t(*[f"win:{x}" for x in c]), t("races")))
        for name, cells in (POSITIONS | {"contested_line": in_fought_line}).items()
        if not name.startswith("contested") or name == "contested_line"
    }
    back_line = {
        "lines": count("bl:n"),
        **{
            f"{who}_{what}": value(lambda t, k=f"bl:{who}_{what}": _ratio(t(k), t("bl:n")))
            for who in ("head", "second")
            for what in ("win", "ewin", "top3")
        },
        "races_with_back": count("b:n"),
        "winner_is_back_rider": value(lambda t: _ratio(t("b:self_win"), t("b:n"))),
        "back_rider_in_a_line": {
            "races": count("b:multi_n"),
            "winner_from_the_line": totals.estimate(
                lambda t: _ratio(t("b:multi_line_win"), t("b:multi_n"))
            ),
        },
        "home": {
            "races": count("h:n"),
            "winner_from_the_line": totals.estimate(lambda t: _ratio(t("h:line_win"), t("h:n"))),
        },
    }
    after_home = {
        who: {
            "lines": count(f"ah:{who}:n"),
            **{
                f"{rider}_{what}": value(
                    lambda t, k=f"ah:{who}:{rider}_{what}", n=f"ah:{who}:n": _ratio(t(k), t(n))
                )
                for rider in ("head", "second")
                for what in ("win", "top3")
            },
        }
        for who in _AFTER_HOME
    }
    pairs: dict[str, Any] = {
        near: {
            "pairs": count(f"pr:{near}:n"),
            "both_top3": value(lambda t, k=near: _ratio(t(f"pr:{k}:obs"), t(f"pr:{k}:n"))),
            "expected": value(lambda t, k=near: _ratio(t(f"pr:{k}:exp"), t(f"pr:{k}:n"))),
            "excess": totals.estimate(_pair_excess(near)),
        }
        for near in _NEARNESS
    }
    pairs["region_minus_other"] = totals.estimate(
        _minus(_pair_excess("region"), _pair_excess("other"))
    )

    def favourite(kind: str):
        return lambda t: _ratio(t(f"fav:{kind}:win") - t(f"fav:{kind}:ewin"), t(f"fav:{kind}:n"))

    fought = [r for r in races if any(r.fight)]
    plain = [r for r in races if not any(r.fight)]

    def median_exacta(members: list[LineRace], field: int) -> dict[str, Any]:
        paid = [float(np.mean(r.exacta)) for r in members if r.exacta and len(r.pos) == field]
        return {"races": len(paid), "median": _value(statistics.median(paid)) if paid else None}

    contested = {
        "races": {"contested": len(fought), "other": len(plain)},
        "log_trifecta": totals.estimate(_payout_gap("x3")),
        "favourite_win_excess": {
            "contested": totals.estimate(favourite("c")),
            "other": totals.estimate(favourite("p")),
            "difference": totals.estimate(_minus(favourite("c"), favourite("p"))),
        },
        "median_exacta": {
            f"{field}車": {
                "contested": median_exacta(fought, field),
                "other": median_exacta(plain, field),
            }
            for field in (7, 9)
        },
    }
    score_rank = {
        "model_fit": {
            str(rank): {
                "riders": count(f"rk:all:{rank}:n"),
                **{
                    outcome: value(
                        lambda t, r=rank, o=outcome: _ratio(
                            t(f"rk:all:{r}:{o}"), t(f"rk:all:{r}:n")
                        )
                    )
                    for outcome in ("win", "top3")
                },
            }
            for rank in range(1, SCORE_RANK_BINS + 1)
        },
        "standardized": {
            role: {o: totals.estimate(_rank_standardized(role, o)) for o in ("win", "top3")}
            for role in _ROLES
        },
        "second_minus_head_win": totals.estimate(
            _minus(_rank_standardized("second", "win"), _rank_standardized("head", "win"))
        ),
    }
    in_line = tuple(
        c for name in ("head", "second", "third", "fourth_plus") for c in POSITIONS[name]
    )
    segments = {}
    for name, keep in SEGMENTS.items():
        members = [r for r in races if keep(r)]
        segments[name] = {
            "races": len(members),
            "decisions": _decisions(_Totals(members, n_boot=n_boot, seed=seed)),
        }
    return {
        "races": len(races),
        "practical": {
            "win": POSITION_WIN_PRACTICAL,
            "top3": POSITION_TOP3_PRACTICAL,
            "back_line": BACK_LINE_PRACTICAL,
            "pair": PAIR_PRACTICAL,
            "log_payout": PAYOUT_PRACTICAL,
        },
        "decisions": _decisions(totals),
        "positions": {name: _cell_report(totals, cells) for name, cells in POSITIONS.items()},
        "by_line_size": {
            cell: _cell_report(totals, (cell,))
            for name in POSITIONS
            for cell in POSITIONS[name]
            if "_" in cell and not cell.startswith("contested")
        },
        "winner_share": winner_share,
        "one_two": {
            "same_line_first_two": value(lambda t: _ratio(t("ot:same12"), t("races"))),
            "same_line_first_three": value(lambda t: _ratio(t("ot:same123"), t("races"))),
        },
        "back_line": back_line,
        "after_home": after_home,
        "pairs": pairs,
        "contested": contested,
        "score_rank": score_rank,
        "within_rider": {
            "solo_minus_in_line": within_rider(
                races, POSITIONS["solo"], in_line, n_boot=n_boot, seed=seed
            ),
            "second_minus_head": within_rider(
                races, POSITIONS["second"], POSITIONS["head"], n_boot=n_boot, seed=seed
            ),
        },
        "segments": segments,
    }
