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


def test_result_tab(tmp_path, monkeypatch):
    at = run_app(tmp_path, monkeypatch)
    assert any("結果はまだありません" in i.value for i in at.info)

    monkeypatch.setenv("KEIRIN_TEST_FINISHED", "1")
    at = run_app(tmp_path, monkeypatch)
    order = at.dataframe[1].value
    assert list(order["車"]) == [2, 1, 3]  # finishers first, the disqualified rider last
    assert list(order["決まり手"])[:2] == ["差し", "逃げ"]
    payouts = at.dataframe[2].value
    assert list(payouts["払戻金"]) == ["1,230円", "150円", "320円"]
    assert any("天候 晴" in m.value for m in at.markdown)


def test_position_label():
    from keirin.viewer import position_label

    def info(pos, size, contested=False):
        return {"line_no": 1, "line_pos": pos, "line_size": size, "contested": contested}

    assert position_label(info(1, 3)) == "先頭"
    assert position_label(info(2, 3, True)) == "番手（競り）"
    assert position_label(info(4, 4)) == "4番手"
    assert position_label(info(1, 1)) == "単騎"
    assert position_label(None) == ""


def test_formation_is_shown(tmp_path, monkeypatch):
    monkeypatch.setenv("KEIRIN_TEST_NINFO", "1")
    at = run_app(tmp_path, monkeypatch)
    assert any("並び: 2 / (13)（細切れ）" in c.value for c in at.caption)
    assert any("競り" in m.value for m in at.markdown)  # stacked badges of the contest
    frame = at.dataframe[0].value
    # "2 / (13)": car 2 alone, cars 1 and 3 contest the head of the other line.
    assert list(frame.sort_values("車")["位置"]) == ["先頭（競り）", "単騎", "先頭（競り）"]
