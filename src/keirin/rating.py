"""Rider ratings from finishing orders, accounting for the strength of the opponents.

The official race score averages per-race points that depend on the programme
and the finishing position, not on who the opponents were. Here every race's
finishing order is modelled with a Plackett–Luce model: rider i has a strength
gamma_i and, at each place, the next finisher is drawn among the remaining
riders with probability proportional to gamma. Beating strong riders therefore
counts more than beating weak ones, and riders who changed class link the
S級 / A級 scales through the races they ran in both.

Fitting is the MAP estimate under a Gamma(a, b) prior with the MM algorithm
(Hunter 2004; Caron & Doucet 2012), each race weighted by its age with an
exponential decay (half-life in days). Only races before `as_of` are used, so a
rating can be evaluated against that day's results without leakage.

theta = log(gamma) is the rating; `Ratings.calibration` maps it onto the
official score scale for display ("score equivalent").
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from keirin.score import tier

DEFAULT_HALF_LIFE = 90.0  # days
DEFAULT_PRIOR = (2.0, 1.0)  # Gamma(a, b): one pseudo win and one pseudo exposure


@dataclass(frozen=True)
class RaceOrder:
    date: date
    order: tuple[str, ...]  # racer ids of the finishers, winner first
    tiers: tuple[str | None, ...]  # each finisher's tier at race time


def load_orders(data_dir: Path) -> list[RaceOrder]:
    """Finishing orders of every collected race (riders without a place are left out)."""
    orders = []
    for path in sorted((data_dir / "tables" / "entries").glob("*/*.csv")):
        day = date.fromisoformat(path.stem)
        races: dict[tuple[str, str], list[tuple[int, str, str | None]]] = defaultdict(list)
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["finish_pos"] and row["racer_id"]:
                    races[(row["venue_code"], row["race_no"])].append(
                        (int(row["finish_pos"]), row["racer_id"], tier(row["class"]))
                    )
        for key in sorted(races):
            finishers = sorted(races[key])
            if len(finishers) >= 2:
                orders.append(
                    RaceOrder(
                        day,
                        tuple(r for _, r, _ in finishers),
                        tuple(t for _, _, t in finishers),
                    )
                )
    return orders


@dataclass
class Ratings:
    as_of: date
    theta: dict[str, float]  # racer_id -> log strength
    weight: dict[str, float]  # racer_id -> time-weighted number of races
    tier: dict[str, str | None]  # racer_id -> tier in the latest race

    def calibration(
        self, reference: dict[str, float], min_weight: float = 5.0
    ) -> dict[str, tuple[float, float]]:
        """Linear maps theta -> score, one per pool (men's tiers share a scale; L級 apart).

        Fitted by least squares on riders with a known official score
        (`reference`, racer_id -> score) and enough races.
        """
        pools: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for racer_id, score in reference.items():
            if racer_id in self.theta and self.weight[racer_id] >= min_weight:
                pools[_pool(self.tier.get(racer_id))].append((self.theta[racer_id], score))
        maps = {}
        for pool, points in pools.items():
            if len(points) < 10:
                continue
            x, y = np.array(points).T
            slope, intercept = np.polyfit(x, y, 1)
            maps[pool] = (float(intercept), float(slope))
        return maps

    def score_equivalent(self, racer_id: str, maps: dict[str, tuple[float, float]]) -> float | None:
        if racer_id not in self.theta:
            return None
        coef = maps.get(_pool(self.tier.get(racer_id)))
        if coef is None:
            return None
        return round(coef[0] + coef[1] * self.theta[racer_id], 2)


def _pool(t: str | None) -> str:
    return "L" if t == "L" else "men"


def fit(
    orders: list[RaceOrder],
    as_of: date,
    *,
    half_life: float = DEFAULT_HALF_LIFE,
    prior: tuple[float, float] = DEFAULT_PRIOR,
    init: dict[str, float] | None = None,
    max_iter: int = 2000,
    tol: float = 1e-6,
) -> Ratings:
    use = [o for o in orders if o.date < as_of]
    ids = sorted({r for o in use for r in o.order})
    if not use:
        return Ratings(as_of, {}, {}, {})
    index = {r: i for i, r in enumerate(ids)}
    n, width = len(ids), max(len(o.order) for o in use)

    order = np.full((len(use), width), -1, dtype=np.int64)
    for row, o in enumerate(use):
        order[row, : len(o.order)] = [index[r] for r in o.order]
    finishers = (order >= 0).sum(axis=1)
    age = np.array([(as_of - o.date).days for o in use], dtype=float)
    race_weight = 0.5 ** (age / half_life)

    present = order >= 0
    # Stage s picks the s-th finisher among the riders still remaining; the last pick is forced.
    stage = np.arange(width)[None, :] < (finishers[:, None] - 1)
    weights = np.broadcast_to(race_weight[:, None], order.shape)
    wins = np.bincount(order[stage], weights=weights[stage], minlength=n)
    exposure = np.bincount(order[present], weights=weights[present], minlength=n)

    a, b = prior
    gamma = np.ones(n)
    if init:
        gamma = np.array([np.exp(init.get(r, 0.0)) for r in ids])
    safe = np.where(present, order, 0)
    for _ in range(max_iter):
        g = np.where(present, gamma[safe], 0.0)
        remaining = np.cumsum(g[:, ::-1], axis=1)[:, ::-1]
        inv = np.where(stage, race_weight[:, None] / np.where(remaining > 0, remaining, 1.0), 0.0)
        # A rider finishing at position t took part in the stages 0..t.
        denom = np.bincount(order[present], weights=np.cumsum(inv, axis=1)[present], minlength=n)
        new = (a - 1 + wins) / (b + denom)
        done = np.max(np.abs(np.log(new) - np.log(gamma))) < tol
        gamma = new
        if done:
            break

    latest_tier: dict[str, str | None] = {}
    for o in use:  # chronological, so the last assignment is the latest race
        for r, t in zip(o.order, o.tiers, strict=True):
            latest_tier[r] = t
    theta = np.log(gamma)
    return Ratings(
        as_of,
        {r: float(theta[i]) for i, r in enumerate(ids)},
        {r: float(exposure[i]) for i, r in enumerate(ids)},
        latest_tier,
    )


def latest_cards(data_dir: Path, before: date) -> dict[str, dict[str, str]]:
    """Each rider's latest race card row (name, class, official score) before a day."""
    latest: dict[str, dict[str, str]] = {}
    for path in sorted((data_dir / "tables" / "entries").glob("*/*.csv")):
        if date.fromisoformat(path.stem) >= before:
            continue
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["racer_id"] and row["score"]:
                    latest[row["racer_id"]] = row
    return latest
