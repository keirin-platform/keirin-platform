"""Streamlit viewer: race cards with class-change corrected scores.

Deployed on Streamlit Community Cloud from the private data repository, whose
``streamlit_app.py`` just calls :func:`main` (see infra/data-repo/). Access is
restricted with Community Cloud's viewer allow-list. Locally:

    KEIRIN_DATA_DIR=../keirin-data uv run streamlit run src/keirin/viewer.py
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
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
    # Interactive use: give up quickly and show an error instead of spinning for minutes.
    client = KeirinClient(min_interval=1.0, timeout=8.0, max_retries=2, backoff=2.0)
    return Store(Path(data_dir), CachedClient(client))


def _fetch[T](store: Store, day: date, what: str, fn: Callable[..., T], *args) -> T:
    if store.is_collected(day):
        return fn(*args)
    with st.spinner(f"KEIRIN.JP から{what}を取得しています…"):
        return fn(*args)


CAR_COLORS = {  # car number: (background, text)
    1: ("#ffffff", "#222222"),
    2: ("#222222", "#ffffff"),
    3: ("#e33b3b", "#ffffff"),
    4: ("#2f6fde", "#ffffff"),
    5: ("#f2cf2c", "#222222"),
    6: ("#2e9e4f", "#ffffff"),
    7: ("#f08a24", "#222222"),
    8: ("#e66fb2", "#222222"),
    9: ("#7b3fc4", "#ffffff"),
}


def position_label(info: dict | None) -> str:
    """先頭 / 番手 / 3番手 … / 単騎, with （競り） for a contested position."""
    if not info:
        return ""
    if info["line_size"] == 1:
        return "単騎"
    label = {1: "先頭", 2: "番手"}.get(info["line_pos"], f"{info['line_pos']}番手")
    return label + ("（競り）" if info["contested"] else "")


def _car_badge(car: int) -> str:
    bg, fg = CAR_COLORS.get(car, ("#888888", "#ffffff"))
    return (
        f'<span style="display:inline-block;min-width:1.9em;padding:2px 0;text-align:center;'
        f"border-radius:5px;border:1px solid rgba(128,128,128,.6);font-weight:700;"
        f'background:{bg};color:{fg}">{car}</span>'
    )


def formation_html(formation: dict) -> str:
    """Lines as boxes of car badges, front first; a contested position is stacked."""
    boxes = []
    for line in formation["lines"]:
        cells = []
        for position in line:
            if len(position) == 1:
                cells.append(_car_badge(position[0]))
            else:
                stacked = "".join(_car_badge(c) for c in position)
                cells.append(
                    '<span style="display:inline-flex;flex-direction:column;gap:2px;'
                    'padding:2px;border:1px dashed rgba(128,128,128,.8);border-radius:6px" '
                    f'title="競り">{stacked}</span>'
                )
        boxes.append(
            '<span style="display:inline-flex;align-items:center;gap:4px;padding:4px 6px;'
            'border:1px solid rgba(128,128,128,.45);border-radius:8px">'
            + "".join(cells)
            + "</span>"
        )
    return (
        '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:4px 0 2px">'
        '<span style="opacity:.6;font-size:.85rem">← 進行方向</span>' + "".join(boxes) + "</div>"
    )


def card_frame(rows: list[dict], with_results: bool, formation: dict | None = None) -> pd.DataFrame:
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
        "ライン": [
            ((formation or {}).get("by_car", {}).get(r["car_no"]) or {}).get("line_no")
            for r in rows
        ],
        "位置": [
            position_label((formation or {}).get("by_car", {}).get(r["car_no"])) for r in rows
        ],
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
        .format({"年齢": "{:.0f}", "期": "{:.0f}", "ライン": "{:.0f}"}, na_rep="")
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
        meetings = _fetch(store, day, "開催情報", store.meetings, day)
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
        races = _fetch(store, day, "レース一覧", store.races, day, venue)
        if not races:
            st.info("レース情報がありません。")
            return
        race_labels = {r["race_no"]: f"{r['race_no']}R" for r in races}
        race_no = st.segmented_control(
            "レース", list(race_labels), format_func=race_labels.get, default=races[0]["race_no"]
        )
        if race_no is None:
            return
        meeting, race, rows = _fetch(store, day, "出走表", store.card, day, venue, race_no)
    except NotFound as e:
        st.error(f"表示できませんでした: {e}")
        return
    except KeirinApiError as e:
        st.error(
            f"KEIRIN.JP から取得できませんでした（{e}）。時間をおいて再読み込みしてください。"
            "収集済みの日（左の日付で過去の日）は表示できます。"
        )
        return

    st.markdown(
        f"### {meeting['venue_name']} {race_no}R　{race['race_class']}　{race['start_time']} 発走"
    )
    try:
        formation = _fetch(store, day, "並び", store.formation, day, venue, race_no)
    except KeirinApiError:
        formation = None
    if formation:
        st.markdown(formation_html(formation), unsafe_allow_html=True)
        st.caption(f"並び: {formation['text']}（{formation['label']}）")
    else:
        st.caption("並び: 未公開または未取得")
    tab_card, tab_result = st.tabs(["出走表", "結果"])
    with tab_card:
        _render_card(store, day, rows, formation)
    with tab_result:
        try:
            result = _fetch(store, day, "結果", store.result, day, venue, race_no)
        except KeirinApiError as e:
            st.error(f"KEIRIN.JP から結果を取得できませんでした（{e}）。")
        else:
            _render_result(result, rows)
    st.caption("データ出典: KEIRIN.JP（個人利用）。補正得点は推定値です。")


def _render_card(store: Store, day: date, rows: list[dict], formation: dict | None) -> None:
    start = window_start(day)
    if rows and not all(r["complete"] for r in rows):
        st.warning(
            f"補正に必要な期間（{start.month}/{start.day}〜前日）の一部がまだ収集されていません"
            f"（データは {store.last_collected or '未収集'} まで）。補正は不完全です。"
        )
    frame = card_frame(rows, with_results=store.is_collected(day), formation=formation)
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


def result_frame(result: dict, card_rows: list[dict]) -> pd.DataFrame:
    """Finishing order, with the card's scores to compare against the result."""
    card = {r["car_no"]: r for r in card_rows}

    def rank(r: dict | None) -> str:
        if not r or r["rank_official"] is None:
            return "-"
        if r["rank_corrected"] != r["rank_official"]:
            return f"{r['rank_official']}→{r['rank_corrected']}"
        return str(r["rank_official"])

    entries = sorted(
        result["entries"],
        key=lambda e: (e["finish_pos"] is None, e["finish_pos"] or 0, e["car_no"] or 0),
    )
    return pd.DataFrame(
        {
            "着": [e["finish"] or "-" for e in entries],
            "車": [e["car_no"] for e in entries],
            "選手": [e["racer_name"] for e in entries],
            "着差": [e["margin"] for e in entries],
            "上がり": [e["last_lap"] for e in entries],
            "決まり手": [e["kimarite"] for e in entries],
            "B/H": [e["bh"] for e in entries],
            "競走得点": [(card.get(e["car_no"]) or {}).get("score") for e in entries],
            "補正得点": [(card.get(e["car_no"]) or {}).get("corrected") for e in entries],
            "得点順位": [rank(card.get(e["car_no"])) for e in entries],
            "状況": [e["notes"] for e in entries],
        }
    )


def payout_frame(result: dict) -> pd.DataFrame:
    payouts = result["payouts"]
    return pd.DataFrame(
        {
            "券種": [p["bet_type"] for p in payouts],
            "組番": [p["combination"] for p in payouts],
            "払戻金": [f"{p['payout']:,}円" if p["payout"] is not None else "-" for p in payouts],
            "人気": [p["popularity"] for p in payouts],
        }
    )


def _render_result(result: dict | None, card_rows: list[dict]) -> None:
    if result is None:
        st.info("結果はまだありません。レースが終わると表示されます（最大5分ほど遅れます）。")
        return
    weather = "　".join(
        filter(
            None,
            [
                f"天候 {result['weather']}" if result["weather"] else "",
                f"風速 {result['wind_speed']:.1f}m" if result["wind_speed"] is not None else "",
            ],
        )
    )
    if weather:
        st.markdown(weather)
    frame = result_frame(result, card_rows)
    st.dataframe(
        frame.style.format(
            {"上がり": "{:.1f}", "競走得点": "{:.2f}", "補正得点": "{:.2f}"}, na_rep=""
        ),
        hide_index=True,
        width="stretch",
    )
    if result["payouts"]:
        st.markdown("#### 払戻金")
        st.dataframe(payout_frame(result), hide_index=True, width="stretch")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    st.set_page_config(page_title="競輪 補正得点", page_icon="🚴", layout="wide")
    data_dir = os.environ.get("KEIRIN_DATA_DIR", ".")
    render(_store(data_dir, _data_version(Path(data_dir))), today_jst())


if __name__ == "__main__":
    main()
