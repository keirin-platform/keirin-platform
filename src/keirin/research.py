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
