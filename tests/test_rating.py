from datetime import date, timedelta

import numpy as np
import pytest
from test_score import write_entries

from keirin import analysis, rating
from keirin.rating import RaceOrder, fit, load_orders
from keirin.score import History
from keirin.timeutil import date_range

AS_OF = date(2026, 6, 1)


def simulate(strengths: dict[str, float], races: int, field: int, seed: int = 0):
    """Plackett–Luce samples: each place drawn among the remaining riders ∝ exp(strength)."""
    rng = np.random.default_rng(seed)
    ids = list(strengths)
    orders = []
    for k in range(races):
        riders = list(rng.choice(ids, size=field, replace=False))
        finish = []
        while riders:
            w = np.exp([strengths[r] for r in riders])
            pick = rng.choice(len(riders), p=w / w.sum())
            finish.append(riders.pop(pick))
        day = AS_OF - timedelta(days=1 + k % 60)
        orders.append(RaceOrder(day, tuple(finish), tuple("S" for _ in finish)))
    return orders


def test_recovers_strength_order():
    truth = {f"r{i}": s for i, s in enumerate([1.5, 1.0, 0.6, 0.3, 0.0, -0.3, -0.7, -1.2, -1.5])}
    ratings = fit(simulate(truth, races=800, field=7), AS_OF, half_life=1e9)
    fitted = [ratings.theta[r] for r in truth]
    assert np.corrcoef(fitted, list(truth.values()))[0, 1] > 0.97
    assert max(ratings.theta, key=ratings.theta.get) == "r0"
    assert min(ratings.theta, key=ratings.theta.get) == "r8"


def test_beating_strong_riders_counts_more():
    # X and Y both win 1 of their 2 races, but X's opponent is much stronger.
    orders = [RaceOrder(AS_OF - timedelta(days=1), ("STRONG", "WEAK"), ("S", "S"))] * 30
    orders += [
        RaceOrder(AS_OF - timedelta(days=2), ("X", "STRONG"), ("S", "S")),
        RaceOrder(AS_OF - timedelta(days=2), ("STRONG", "X"), ("S", "S")),
        RaceOrder(AS_OF - timedelta(days=2), ("Y", "WEAK"), ("S", "S")),
        RaceOrder(AS_OF - timedelta(days=2), ("WEAK", "Y"), ("S", "S")),
    ]
    ratings = fit(orders, AS_OF)
    assert ratings.theta["X"] > ratings.theta["Y"]


def test_recent_races_weigh_more():
    old = [RaceOrder(AS_OF - timedelta(days=300), ("A", "B"), ("S", "S"))] * 20
    recent = [RaceOrder(AS_OF - timedelta(days=5), ("B", "A"), ("S", "S"))] * 10
    short = fit(old + recent, AS_OF, half_life=30)
    assert short.theta["B"] > short.theta["A"]
    flat = fit(old + recent, AS_OF, half_life=1e9)
    assert flat.theta["A"] > flat.theta["B"]  # 20 wins vs 10 when age does not matter
    assert short.weight["A"] == pytest.approx(20 * 0.5**10 + 10 * 0.5 ** (5 / 30))


def test_races_on_or_after_as_of_are_ignored():
    orders = [
        RaceOrder(AS_OF - timedelta(days=1), ("A", "B"), ("S", "S")),
        RaceOrder(AS_OF, ("C", "A"), ("S", "S")),
    ]
    ratings = fit(orders, AS_OF)
    assert set(ratings.theta) == {"A", "B"}
    assert fit([], AS_OF).theta == {}


def test_calibration_maps_theta_to_score_per_pool():
    ratings = rating.Ratings(
        AS_OF,
        theta={f"m{i}": i / 10 for i in range(12)} | {f"l{i}": i / 10 for i in range(12)},
        weight={f"{p}{i}": 10.0 for p in "ml" for i in range(12)},
        tier={f"m{i}": "S" for i in range(12)} | {f"l{i}": "L" for i in range(12)},
    )
    reference = {f"m{i}": 90 + 20 * (i / 10) for i in range(12)} | {
        f"l{i}": 50 + 5 * (i / 10) for i in range(12)
    }
    maps = ratings.calibration(reference)
    assert maps["men"] == pytest.approx((90.0, 20.0))
    assert maps["L"] == pytest.approx((50.0, 5.0))
    assert ratings.score_equivalent("m5", maps) == pytest.approx(100.0)
    assert ratings.score_equivalent("unknown", maps) is None


def test_load_orders_from_tables(tmp_path):
    write_entries(
        tmp_path,
        date(2026, 5, 1),
        [
            {"racer_id": "B", "car_no": 1, "class": "S2", "finish_pos": 2},
            {"racer_id": "A", "car_no": 2, "class": "S1", "finish_pos": 1},
            {"racer_id": "C", "car_no": 3, "class": "S2", "finish_pos": ""},  # 失格
            {"racer_id": "D", "car_no": 1, "class": "A1", "finish_pos": 1, "race_no": 2},
        ],
    )
    orders = load_orders(tmp_path)
    assert orders == [RaceOrder(date(2026, 5, 1), ("A", "B"), ("S", "S"))]  # race 2: 1 rider


def test_evaluate_with_ratings(tmp_path):
    day = date(2026, 1, 10)
    for d in date_range(date(2025, 10, 1), date(2026, 1, 9)):
        write_entries(tmp_path, d, [])
    write_entries(
        tmp_path,
        day,
        [
            {"racer_id": "P", "car_no": 1, "class": "S2", "score": 95.0, "finish_pos": 1},
            {"racer_id": "Q", "car_no": 2, "class": "S2", "score": 97.0, "finish_pos": 2},
            {"racer_id": "R", "car_no": 3, "class": "S2", "score": 99.0, "finish_pos": 3},
        ],
    )
    history = History.from_tables(tmp_path)
    result = analysis.evaluate(
        tmp_path, day, day, history, ratings_for=lambda d: {"P": 1.0, "Q": 0.0}
    )
    assert result["all"]["rating"].pairs == 1  # R has no rating: only the P-Q pair counts
    assert result["all"]["rating"].rate == 1.0
    assert result["all"]["official"].rate == 0.0
