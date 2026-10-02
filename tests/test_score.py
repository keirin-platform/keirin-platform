from datetime import date

import pytest

from keirin import storage
from keirin.score import History, Race, correct, delta, tier, window_start
from keirin.timeutil import date_range


def test_tier():
    assert [tier(c) for c in ("SS", "S1", "S2", "A1", "A2", "A3", "L1", "")] == [
        "S", "S", "S", "A12", "A12", "A3", "L", None,
    ]  # fmt: skip


def test_delta_walks_the_ladder():
    assert delta("S", "S") == 0
    assert delta("A12", "S") == pytest.approx(3.80)
    assert delta("S", "A12") == pytest.approx(-3.69)
    assert delta("A3", "S") == pytest.approx(8.52 + 3.80)
    assert delta("S", "A3") == pytest.approx(-3.69 - 9.42)
    assert delta("L", "A12") is None
    assert delta(None, "S") is None


@pytest.mark.parametrize(
    ("day", "start"),
    [
        (date(2026, 10, 2), date(2026, 7, 1)),
        (date(2027, 1, 15), date(2026, 10, 1)),
        (date(2026, 3, 31), date(2025, 12, 1)),
    ],
)
def test_window_start_is_current_month_plus_three(day, start):
    assert window_start(day) == start


def test_correct_promoted_rider():
    day = date(2027, 1, 10)
    races = [
        Race(date(2026, 9, 30), "11", "A12", True),  # before the window
        *[Race(date(2026, 10, d), "21", "A12", True) for d in (1, 2, 3)],
        *[Race(date(2026, 11, d), "22", "A12", True) for d in (1, 2, 3)],
        *[Race(date(2026, 12, d), "23", "A12", True) for d in (1, 2, 3)],
        Race(date(2026, 12, 20), "24", "A12", False),  # 失格: not scoring
        Race(date(2027, 1, 3), "31", "S", True),
        Race(date(2027, 1, 8), "47", "S", True),  # same meeting as the card: excluded
    ]
    c = correct(95.0, "S", races, day=day, complete=True, exclude_venue="47")
    assert (c.races, c.other_tier_races) == (10, 9)
    assert c.adjustment == pytest.approx(round(9 * 3.80 / 10, 2))
    assert c.corrected == pytest.approx(95.0 + c.adjustment)
    assert c.other_tier_share == pytest.approx(0.9)


def test_correct_without_class_change_is_identity():
    races = [Race(date(2026, 9, d), "11", "S", True) for d in range(1, 10)]
    c = correct(101.5, "S", races, day=date(2026, 10, 2), complete=True)
    assert (c.corrected, c.adjustment, c.races, c.other_tier_races) == (101.5, 0.0, 9, 0)


def test_correct_handles_missing_official_score():
    c = correct(None, "S", [], day=date(2026, 10, 2), complete=False)
    assert c.corrected is None
    assert c.adjustment == 0.0


def write_entries(data_dir, day, rows):
    defaults = {"venue_code": "11", "race_no": 1, "racer_name": "テスト", "finish_pos": ""}
    storage.write_tables(
        data_dir,
        day,
        {"entries": [{**defaults, "date": day.isoformat(), **r} for r in rows]},
    )


def test_history_from_tables_and_coverage(tmp_path):
    for day in date_range(date(2026, 7, 1), date(2026, 10, 1)):
        write_entries(tmp_path, day, [])
    write_entries(
        tmp_path,
        date(2026, 6, 20),
        [{"racer_id": "000001", "car_no": 1, "class": "A1", "finish_pos": 1}],
    )
    write_entries(
        tmp_path,
        date(2026, 7, 5),
        [{"racer_id": "000001", "car_no": 1, "class": "S2", "finish_pos": 2}],
    )
    history = History.from_tables(tmp_path)
    assert [r.tier for r in history.races("000001")] == ["A12", "S"]
    assert history.covers(date(2026, 7, 1), date(2026, 10, 2))
    assert not history.covers(date(2026, 6, 1), date(2026, 10, 2))

    c = history.correct("000001", 96.0, "S2", date(2026, 10, 2))
    assert c.complete
    assert (c.races, c.other_tier_races) == (1, 0)  # June is outside the October window
