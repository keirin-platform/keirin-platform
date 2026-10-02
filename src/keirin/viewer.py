"""Streamlit viewer: race cards with class-change corrected scores.

Deployed on Streamlit Community Cloud from the private data repository, whose
``streamlit_app.py`` just calls :func:`main` (see infra/data-repo/). Access is
restricted with Community Cloud's viewer allow-list. Locally:

    KEIRIN_DATA_DIR=../keirin-data uv run streamlit run src/keirin/viewer.py
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from keirin.client import KeirinApiError, KeirinClient
from keirin.score import STEP_DELTAS, window_start
from keirin.store import CachedClient, NotFound, Store
from keirin.timeutil import today_jst

WEEKDAYS = "月火水木金土日"
TIME_SLOTS = {
    "day": "デイ",
    "night": "ナイター",
    "midnight": "ミッドナイト",
    "morning": "モーニング",
}
UP_BG, DOWN_BG = (
    "background-color: rgba(46, 160, 67, .22)",
    "background-color: rgba(218, 54, 51, .2)",
)


def _data_version(data_dir: Path) -> str:
    """Changes whenever new tables arrive, so the cached store is rebuilt."""
    files = sorted((data_dir / "tables" / "entries").glob("*/*.csv"))
    return f"{len(files)}:{files[-1].name if files else ''}"


@st.cache_resource(show_spinner="データを読み込んでいます…")
def _store(data_dir: str, version: str) -> Store:
    return Store(Path(data_dir), CachedClient(KeirinClient(min_interval=1.0)))


def card_frame(rows: list[dict], with_results: bool) -> pd.DataFrame:
    def rank(r: dict) -> str:
        if r["rank_official"] is None:
            return "-"
        if r["rank_corrected"] != r["rank_official"]:
            return f"{r['rank_official']}→{r['rank_corrected']}"
        return str(r["rank_official"])

    data = {
        "車": [r["car_no"] for r in rows],
        "選手": [r["racer_name"] for r in rows],
        "府県": [r["prefecture"] for r in rows],
        "年齢": [r["age"] for r in rows],
        "期": [r["term"] for r in rows],
        "級班": [
            f"{r['prev_class']}→{r['class']}"
            if r["prev_class"] and r["prev_class"] != r["class"]
            else r["class"]
            for r in rows
        ],
        "脚質": [r["style"] for r in rows],
        "競走得点": [r["score"] for r in rows],
        "補正得点": [r["corrected"] for r in rows],
        "補正": [r["adjustment"] or None for r in rows],
        "得点順位": [rank(r) for r in rows],
        "他級走/走数": [f"{r['other_tier_races']}/{r['races']}" for r in rows],
        "勝率": [r["win_rate"] for r in rows],
        "2連対率": [r["top2_rate"] for r in rows],
        "3連対率": [r["top3_rate"] for r in rows],
    }
    if with_results:
        data["着"] = [" ".join(filter(None, [r["finish"], r["notes"]])) for r in rows]
    return pd.DataFrame(data)


def _style(frame: pd.DataFrame):
    def highlight(row: pd.Series) -> list[str]:
        adj = row["補正"]
        color = "" if pd.isna(adj) else UP_BG if adj > 0 else DOWN_BG
        return [color if col in ("補正得点", "補正") else "" for col in row.index]

    return (
        frame.style.apply(highlight, axis=1)
        .format({"競走得点": "{:.2f}", "補正得点": "{:.2f}", "補正": "{:+.2f}"}, na_rep="")
        .format({"勝率": "{:.0f}", "2連対率": "{:.0f}", "3連対率": "{:.0f}"}, na_rep="-")
        .format({"年齢": "{:.0f}", "期": "{:.0f}"}, na_rep="")
    )


def render(store: Store, today: date) -> None:
    st.title("競輪 補正得点")
    st.caption(f"データ: {store.last_collected or '未収集'} まで収集済み")

    if "day" not in st.session_state:
        st.session_state["day"] = today

    def shift(delta: int | None) -> None:
        st.session_state["day"] = (
            today if delta is None else st.session_state["day"] + timedelta(days=delta)
        )

    with st.sidebar:
        st.date_input("開催日", key="day", format="YYYY/MM/DD")
        cols = st.columns(3)
        for col, label, delta in zip(cols, ("前日", "今日", "翌日"), (-1, None, 1), strict=True):
            col.button(label, on_click=shift, args=(delta,), width="stretch")
    day = st.session_state["day"]

    st.subheader(f"{day.month}月{day.day}日（{WEEKDAYS[day.weekday()]}）")
    try:
        meetings = store.meetings(day)
        if not meetings:
            st.info("開催はありません。")
            return
        labels = {
            m["venue_code"]: " ".join(
                filter(None, [m["venue_name"], m["grade"], m["day_label"],
                              TIME_SLOTS.get(m["time_slot"], "")])
            )
            for m in meetings
        }  # fmt: skip
        venue = st.selectbox("開催", list(labels), format_func=labels.get)
        races = store.races(day, venue)
        if not races:
            st.info("レース情報がありません。")
            return
        race_labels = {r["race_no"]: f"{r['race_no']}R" for r in races}
        race_no = st.segmented_control(
            "レース", list(race_labels), format_func=race_labels.get, default=races[0]["race_no"]
        )
        if race_no is None:
            return
        meeting, race, rows = store.card(day, venue, race_no)
    except (NotFound, KeirinApiError) as e:
        st.error(f"表示できませんでした: {e}")
        return

    st.markdown(
        f"### {meeting['venue_name']} {race_no}R　{race['race_class']}　{race['start_time']} 発走"
    )
    start = window_start(day)
    if rows and not all(r["complete"] for r in rows):
        st.warning(
            f"補正に必要な期間（{start.month}/{start.day}〜前日）の一部がまだ収集されていません"
            f"（データは {store.last_collected or '未収集'} まで）。補正は不完全です。"
        )
    frame = card_frame(rows, with_results=store.is_collected(day))
    st.dataframe(_style(frame), hide_index=True, width="stretch")

    d = STEP_DELTAS
    st.caption(
        "補正得点 = 競走得点 + Σ（別の級班で走ったレースの換算値）÷ 走数。"
        f"得点の窓は {start.month}/{start.day} から（当月＋前3ヶ月）。"
        f"換算値（1走あたり、暫定）: A級1・2班→S級 {d[('A12', 'S')]:+.2f}、"
        f"S級→A級1・2班 {d[('S', 'A12')]:+.2f}、A級3班→A級1・2班 {d[('A3', 'A12')]:+.2f}、"
        f"A級1・2班→A級3班 {d[('A12', 'A3')]:+.2f}。"
        "「他級走/走数」は窓の中で別の級班で走った回数と、得点対象の走数。"
    )
    st.caption("データ出典: KEIRIN.JP（個人利用）。補正得点は推定値です。")


def main() -> None:
    st.set_page_config(page_title="競輪 補正得点", page_icon="🚴", layout="wide")
    data_dir = os.environ.get("KEIRIN_DATA_DIR", ".")
    render(_store(data_dir, _data_version(Path(data_dir))), today_jst())


if __name__ == "__main__":
    main()
