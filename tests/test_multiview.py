from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from keirin.multiview import (
    VenueStatus,
    assign_slots,
    default_selection,
    highlighted,
    start_datetimes,
    venue_status,
)
from keirin.timeutil import JST
from keirin.youtube import embed_url

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


# --- page -------------------------------------------------------------------------------

SCRIPT = str(Path(__file__).with_name("multiview_script.py"))


def run_page(tmp_path, monkeypatch, **env: str):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("KEIRIN_TEST_DATA_DIR", str(tmp_path))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    at = AppTest.from_file(SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def iframes(at) -> list[str]:
    return [e.proto.src for e in at.get("iframe")]


def texts(at) -> str:
    return " ".join(
        str(e.value)
        for kind in ("markdown", "warning", "info", "error", "caption")
        for e in at.get(kind)
    )


def test_page_defaults_and_missing_stream(tmp_path, monkeypatch):
    at = run_page(tmp_path, monkeypatch)
    assert at.multiselect[0].value == ["22", "47", "13"]
    assert iframes(at) == [embed_url("found000001")]
    assert texts(at).count("配信が見つかりません") == 2
    assert len(at.text_input) == 3  # every tile can take a pasted URL


def test_page_without_api_key(tmp_path, monkeypatch):
    at = run_page(tmp_path, monkeypatch, KEIRIN_TEST_STREAMS="none")
    assert "API キーが未設定" in texts(at)
    assert iframes(at) == []
    assert len(at.text_input) == 3


def test_page_api_error_falls_back(tmp_path, monkeypatch):
    at = run_page(tmp_path, monkeypatch, KEIRIN_TEST_STREAMS="error")
    assert iframes(at) == []
    assert texts(at).count("配信が見つかりません") == 3


def test_page_schedule_failure(tmp_path, monkeypatch):
    at = run_page(tmp_path, monkeypatch, KEIRIN_TEST_SCHEDULE_FAIL="47")
    assert "松阪 ミッドナイト 発走予定を取得できません" in texts(at)
    assert (
        "47" in at.multiselect[0].value
    )  # still shown; only the highlight and countdown are missing


def test_manual_url_overrides(tmp_path, monkeypatch):
    at = run_page(tmp_path, monkeypatch)
    at.text_input[0].input("https://youtu.be/manual00001").run()
    assert not at.exception, at.exception
    assert iframes(at)[0] == embed_url("manual00001")
    at.run()  # streams are looked up again; the pasted one stays
    assert iframes(at)[0] == embed_url("manual00001")


def test_page_keeps_tiles_when_a_venue_is_swapped(tmp_path, monkeypatch):
    at = run_page(tmp_path, monkeypatch)
    at.multiselect[0].set_value(["22", "13", "81"]).run()
    assert not at.exception, at.exception
    tiles = [m.value for m in at.markdown if "R" in m.value or "終了" in m.value]
    assert "いわき平" in " ".join(tiles)
    assert iframes(at) == [embed_url("found000001")]


def test_selection_survives_time_passing(tmp_path, monkeypatch):
    # The picker's options must not change with the clock: Streamlit resets a widget whose
    # options change, which emptied the page in the browser when a race passed its start.
    at = run_page(tmp_path, monkeypatch)
    before = at.multiselect[0].options
    monkeypatch.setenv("KEIRIN_TEST_MINUTES", "4")  # 前橋 1R is now waiting, the order changes
    at.run()
    assert not at.exception, at.exception
    assert at.multiselect[0].options == before
    assert at.multiselect[0].value == ["22", "47", "13"]


def iframe_paths(at) -> dict[str, tuple[int, ...]]:
    """Where each video sits in the element tree; a moved video is reloaded by the browser."""
    paths: dict[str, tuple[int, ...]] = {}

    def walk(node, path: tuple[int, ...]) -> None:
        if getattr(node, "type", None) == "iframe":
            paths[node.proto.src] = path
        for i, child in getattr(node, "children", {}).items():
            walk(child, (*path, i))

    walk(at._tree, ())
    return paths


def test_videos_stay_put_when_a_fourth_venue_is_added(tmp_path, monkeypatch):
    at = run_page(tmp_path, monkeypatch)
    before = iframe_paths(at)
    assert list(before) == [embed_url("found000001")]
    at.multiselect[0].set_value(["22", "47", "13", "81"]).run()
    assert not at.exception, at.exception
    assert iframe_paths(at) == before


def _fixed_height_ancestor(node, target, fixed: bool = False) -> bool | None:
    """Whether `target` sits inside a block with a fixed pixel height (None if not found)."""
    if node is target:
        return fixed
    proto = getattr(node, "proto", None)
    height = getattr(proto, "height_config", None) if proto is not None else None
    fixed = fixed or bool(height is not None and height.pixel_height)
    for child in getattr(node, "children", {}).values():
        found = _fixed_height_ancestor(child, target, fixed)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize("venues", [["22", "47", "13"], ["22", "47", "13", "81"]])
def test_changing_text_above_the_videos_has_a_fixed_height(tmp_path, monkeypatch, venues):
    # The countdowns change every 30 seconds and venues drop out as they finish; their
    # lines above the grid must not change its height, or the videos move on screen.
    at = run_page(tmp_path, monkeypatch)
    at.multiselect[0].set_value(venues).run()
    changing = [e for e in at.caption if "あと" in e.value]
    if len(venues) == 4:
        changing += [e for e in at.markdown if "次の発走" in e.value]
    assert len(changing) == (2 if len(venues) == 4 else 1)
    for element in changing:
        assert _fixed_height_ancestor(at._tree, element) is True
