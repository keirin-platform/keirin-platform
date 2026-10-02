from datetime import date

import pytest
from test_score import write_entries

from keirin import analysis
from keirin.score import History
from keirin.timeutil import date_range


def test_evaluate_rewards_correct_promotion_adjustment(tmp_path):
    day = date(2026, 1, 10)
    for d in date_range(date(2025, 10, 1), date(2026, 1, 9)):
        write_entries(tmp_path, d, [])
    write_entries(
        tmp_path,
        date(2025, 12, 1),
        [
            {"racer_id": "P", "car_no": 1, "class": "A1", "finish_pos": 1},
            {"racer_id": "Q", "car_no": 1, "class": "S2", "finish_pos": 1, "venue_code": "22"},
        ],
    )
    # P was promoted: lower official score but finishes ahead of Q.
    write_entries(
        tmp_path,
        day,
        [
            {"racer_id": "P", "car_no": 1, "class": "S2", "score": 95.0, "finish_pos": 1},
            {"racer_id": "Q", "car_no": 2, "class": "S2", "score": 97.0, "finish_pos": 2},
        ],
    )
    history = History.from_tables(tmp_path)
    result = analysis.evaluate(tmp_path, day, day, history)
    assert result["all"]["official"].rate == 0.0
    assert result["all"]["corrected"].rate == 1.0
    assert result["affected"]["corrected"].pairs == 1


def test_evaluate_skips_races_with_incomplete_history(tmp_path):
    day = date(2026, 1, 10)
    write_entries(
        tmp_path,
        day,
        [
            {"racer_id": "P", "car_no": 1, "class": "S2", "score": 95.0, "finish_pos": 1},
            {"racer_id": "Q", "car_no": 2, "class": "S2", "score": 97.0, "finish_pos": 2},
        ],
    )
    result = analysis.evaluate(tmp_path, day, day, History.from_tables(tmp_path))
    assert result["all"]["official"].pairs == 0


def test_estimate_deltas_removes_regression_to_the_mean(tmp_path):
    def expected_change(old):  # what stayers do: regress towards 87
        return 0.5 - 0.1 * (old - 87)

    june, october = [], []
    for i in range(20):  # A1 stayers
        old = 80 + i * 0.75
        rid = f"S{i:02d}"
        june.append({"racer_id": rid, "car_no": 1, "class": "A1", "score": old})
        october.append(
            {"racer_id": rid, "car_no": 1, "class": "A2" if i % 2 else "A1",
             "score": round(old + expected_change(old), 2), "finish_pos": 1}
        )  # fmt: skip
    for i in range(5):  # promoted to S2: +3.0 on top of the stayers' trend
        old = 93 + i * 0.5
        rid = f"M{i:02d}"
        june.append({"racer_id": rid, "car_no": 1, "class": "A1", "score": old})
        october.append(
            {"racer_id": rid, "car_no": 1, "class": "S2",
             "score": round(old + expected_change(old) + 3.0, 2), "finish_pos": 1}
        )  # fmt: skip
    write_entries(tmp_path, date(2026, 6, 20), june)
    write_entries(tmp_path, date(2026, 10, 5), october)

    estimates = analysis.estimate_deltas(tmp_path, date(2026, 7, 1), min_races=1)
    assert len(estimates) == 1
    e = estimates[0]
    assert (e.from_tier, e.to_tier, e.movers, e.stayers) == ("A12", "S", 5, 20)
    assert e.delta == pytest.approx(3.0, abs=0.02)
