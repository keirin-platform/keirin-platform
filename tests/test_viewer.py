from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402
from test_score import write_entries  # noqa: E402

from keirin.timeutil import date_range  # noqa: E402

SCRIPT = str(Path(__file__).with_name("viewer_script.py"))


def run_app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setenv("KEIRIN_TEST_DATA_DIR", str(tmp_path))
    at = AppTest.from_file(SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def test_live_card_shows_corrected_score(tmp_path, monkeypatch):
    for d in date_range(date(2025, 10, 1), date(2026, 1, 9)):
        write_entries(tmp_path, d, [])
    write_entries(
        tmp_path,
        date(2025, 12, 1),
        [{"racer_id": "000001", "car_no": 1, "class": "A1", "finish_pos": 1}],
    )
    at = run_app(tmp_path, monkeypatch)

    assert at.selectbox[0].value == "99"
    frame = at.dataframe[0].value
    first = frame[frame["車"] == 1].iloc[0]
    assert first["選手"] == "山田 一郎"
    assert first["級班"] == "A1→S2"
    assert first["競走得点"] == pytest.approx(90.25)
    assert first["補正得点"] == pytest.approx(94.05)
    assert first["補正"] == pytest.approx(3.80)
    assert first["他級走/走数"] == "1/1"
    assert not at.warning  # the whole window is collected


def test_incomplete_window_warns(tmp_path, monkeypatch):
    at = run_app(tmp_path, monkeypatch)
    assert at.warning and "収集されていません" in at.warning[0].value


def test_day_buttons_move_the_date(tmp_path, monkeypatch):
    at = run_app(tmp_path, monkeypatch)
    at.sidebar.button[2].click().run()  # 翌日
    assert at.session_state["day"] == date(2026, 1, 11)
    at.sidebar.button[1].click().run()  # 今日
    assert at.session_state["day"] == date(2026, 1, 10)


def test_keirin_jp_failure_is_shown_instead_of_hanging(tmp_path, monkeypatch):
    monkeypatch.setenv("KEIRIN_TEST_FAIL", "1")
    at = run_app(tmp_path, monkeypatch)
    assert at.error and "KEIRIN.JP から取得できませんでした" in at.error[0].value
