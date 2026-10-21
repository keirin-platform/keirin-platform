from datetime import date, datetime, timedelta

from keirin.multiview import (
    VenueStatus,
    assign_slots,
    default_selection,
    highlighted,
    start_datetimes,
    venue_status,
)
from keirin.timeutil import JST

NOW = datetime(2026, 10, 21, 23, 40, tzinfo=JST)


def test_start_datetimes_skips_missing_times():
    races = [
        {"race_no": 1, "start_time": "21:05"},
        {"race_no": 2, "start_time": ""},
        {"race_no": 3, "start_time": "21:58"},
    ]
    assert start_datetimes(date(2026, 10, 21), races) == [
        (1, datetime(2026, 10, 21, 21, 5, tzinfo=JST)),
        (3, datetime(2026, 10, 21, 21, 58, tzinfo=JST)),
    ]


def test_venue_status_boundaries():
    starts = [(9, NOW), (10, NOW + timedelta(minutes=11))]
    assert venue_status("22", starts, NOW).state == "waiting"  # 予定ちょうど
    assert venue_status("22", starts, NOW + timedelta(minutes=4, seconds=59)).race_no == 9
    s = venue_status("22", starts, NOW + timedelta(minutes=5))  # 5分ちょうどで次へ
    assert (s.race_no, s.state) == (10, "upcoming")
    assert venue_status("22", starts, NOW + timedelta(minutes=16)).state == "finished"
    assert venue_status("22", None, NOW).state == "unknown"
    assert venue_status("22", [], NOW).state == "unknown"


def _status(code: str, minutes: int | None, state: str = "upcoming") -> VenueStatus:
    start = None if minutes is None else NOW + timedelta(minutes=minutes)
    return VenueStatus(code, None if minutes is None else 9, start, state)


def test_highlighted_picks_the_earliest_waiting_or_upcoming():
    statuses = [_status("47", 4), _status("22", -2, "waiting"), _status("13", 8)]
    assert highlighted(statuses) == "22"


def test_highlighted_skips_finished_and_unknown():
    assert highlighted([_status("47", None, "finished"), _status("22", None, "unknown")]) is None
    assert highlighted([]) is None


def test_default_selection_orders_by_next_start():
    statuses = [
        _status("11", 30),
        _status("47", 4),
        _status("81", None, "finished"),
        _status("13", 8),
        _status("22", 1),
    ]
    assert default_selection(statuses) == ["22", "47", "13"]


def test_assign_slots_keeps_positions():
    assert assign_slots([None] * 4, ["22", "47", "13"]) == ["22", "47", "13", None]
    assert assign_slots(["22", "47", "13", None], ["22", "13", "81"]) == ["22", "81", "13", None]
    assert assign_slots(["22", "81", "13", None], ["22", "81", "13", "11"]) == [
        "22",
        "81",
        "13",
        "11",
    ]
    assert assign_slots(["22", "81", "13", "11"], []) == [None] * 4
