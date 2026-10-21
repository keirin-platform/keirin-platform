"""Multiview: several venues' live streams on one page (docs/multiview.md).

The page keeps the tiles where they are; only the colour of the next venue to race
moves, and only from the scheduled start times (keirin.jp is not polled for results).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from keirin.timeutil import JST
from keirin.youtube import Stream, YouTubeError, embed_url, video_id_from_url

log = logging.getLogger(__name__)

# Minutes after a scheduled start before the highlight moves on (races are rarely late).
WAIT_MINUTES = 5
MAX_TILES = 4
TILE_HEIGHT = 360  # 16:9 at about half the width of a laptop screen
HIGHLIGHT = "background-color: rgba(232, 163, 61, .35)"
SLOT_ORDER = {"morning": 0, "day": 1, "night": 2, "midnight": 3}

State = Literal["upcoming", "waiting", "finished", "unknown"]


@dataclass(frozen=True)
class VenueStatus:
    venue_code: str
    race_no: int | None  # the next race, None when finished or unknown
    start: datetime | None
    state: State


def start_datetimes(day: date, races: list[dict]) -> list[tuple[int, datetime]]:
    """(race number, scheduled start in JST) of `Store.races` rows that have a start time."""
    starts = []
    for r in races:
        if not r.get("start_time"):
            continue
        hour, minute = map(int, r["start_time"].split(":"))
        starts.append(
            (int(r["race_no"]), datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST))
        )
    return starts


def venue_status(
    code: str, starts: list[tuple[int, datetime]] | None, now: datetime
) -> VenueStatus:
    if not starts:
        return VenueStatus(code, None, None, "unknown")
    wait = timedelta(minutes=WAIT_MINUTES)
    for race_no, start in sorted(starts, key=lambda s: s[1]):
        if now < start + wait:
            return VenueStatus(code, race_no, start, "waiting" if now >= start else "upcoming")
    return VenueStatus(code, None, None, "finished")


def _racing(statuses: list[VenueStatus]) -> list[VenueStatus]:
    return sorted(
        (s for s in statuses if s.state in ("upcoming", "waiting")), key=lambda s: s.start
    )


def highlighted(statuses: list[VenueStatus]) -> str | None:
    """The venue that races next, or None when none of them has a race to come."""
    racing = _racing(statuses)
    return racing[0].venue_code if racing else None


def default_selection(statuses: list[VenueStatus], n: int = 3) -> list[str]:
    """The `n` venues that race soonest; venues without a schedule come after them."""
    unknown = [s for s in statuses if s.state == "unknown"]
    return [s.venue_code for s in _racing(statuses) + unknown][:n]


def assign_slots(previous: list[str | None], selected: list[str]) -> list[str | None]:
    """Tiles for `selected`: venues kept stay in their tile, new ones fill the free tiles."""
    slots = [code if code in selected else None for code in previous[:MAX_TILES]]
    slots += [None] * (MAX_TILES - len(slots))
    for code in selected:
        if code not in slots and None in slots:
            slots[slots.index(None)] = code
    return slots


# --- page -----------------------------------------------------------------------------

Schedule = list[tuple[int, datetime]]
Windows = dict[str, tuple[datetime, datetime] | None]


def _schedule(store: Any, day: date, code: str) -> Schedule | None:
    """Scheduled starts, fetched once per meeting and day (they do not change)."""
    import streamlit as st

    @st.cache_data(ttl=86400, show_spinner=False)
    def fetch(data_dir: str, day: date, code: str) -> Schedule:
        return start_datetimes(day, store.races(day, code))

    try:
        return fetch(str(store.data_dir), day, code)
    except Exception as e:  # keirin.jp down or the meeting missing: show the video anyway
        log.warning("no schedule for %s on %s: %s", code, day, e)
        return None


def _until(start: datetime, now: datetime) -> str:
    minutes = int((start - now).total_seconds() // 60)
    return "まもなく" if minutes < 1 else f"あと{minutes}分"


def _status_text(status: VenueStatus, now: datetime) -> str:
    if status.state == "unknown":
        return "発走予定を取得できません"
    if status.state == "finished":
        return "本日終了"
    text = f"{status.race_no}R 予定 {status.start:%H:%M}"
    return (
        f"{text} 発走待ち"
        if status.state == "waiting"
        else f"{text}（{_until(status.start, now)}）"
    )


def render_multiview(
    store: Any,
    clock: Callable[[], datetime],
    streams: Callable[[Windows], dict[str, Stream]] | None,
) -> None:
    """The multiview page. `streams` finds today's streams (None when there is no API key)."""
    import streamlit as st

    from keirin.viewer import TIME_SLOTS

    now = clock()
    day = now.date()
    try:
        meetings = {m["venue_code"]: m for m in store.meetings(day)}
    except Exception as e:
        st.error(f"KEIRIN.JP から開催情報を取得できませんでした（{e}）。")
        return
    if not meetings:
        st.info("今日の開催はありません。")
        return
    schedules = {code: _schedule(store, day, code) for code in meetings}

    def statuses(at: datetime) -> dict[str, VenueStatus]:
        return {code: venue_status(code, schedules[code], at) for code in meetings}

    def name(code: str) -> str:
        m = meetings[code]
        return " ".join(filter(None, [m["venue_name"], TIME_SLOTS.get(m.get("time_slot"), "")]))

    # The options and their labels must not change during the day: Streamlit resets a
    # multiselect whose options change, which would empty the page when a race starts.
    # The changing part (the next race of each venue) goes in the line below it.
    order = sorted(meetings, key=lambda c: (SLOT_ORDER.get(meetings[c].get("time_slot"), 9), c))
    if "mv_selected" not in st.session_state:
        st.session_state["mv_selected"] = default_selection(list(statuses(now).values()))
    selected = st.multiselect(
        "映す場（最大4場）", order, key="mv_selected", max_selections=MAX_TILES, format_func=name
    )

    @st.fragment(run_every=30)
    def all_venues() -> None:
        at = clock()
        live = statuses(at)
        st.caption("　／　".join(f"{name(c)} {_status_text(live[c], at)}" for c in order))

    all_venues()
    slots = assign_slots(st.session_state.get("mv_slots", [None] * MAX_TILES), selected)
    st.session_state["mv_slots"] = slots
    manual: dict[str, str] = st.session_state.setdefault("mv_manual", {})

    found: dict[str, Stream] = {}
    notices = st.container()  # always there, so a notice does not move the videos
    if streams is None:
        notices.info("YouTube の API キーが未設定です。各枠に配信の URL を貼ると表示できます。")
    else:
        windows: Windows = {
            code: (min(t for _, t in s), max(t for _, t in s)) if s else None
            for code, s in schedules.items()
        }
        try:
            found = streams(windows)
        except YouTubeError as e:
            log.warning("stream lookup failed: %s", e)
            notices.warning("YouTube の配信を探せませんでした。各枠に配信の URL を貼ってください。")

    @st.fragment(run_every=30)
    def header(code: str) -> None:
        at = clock()
        live = statuses(at)
        on_screen = [live[c] for c in slots if c]
        style = HIGHLIGHT if highlighted(on_screen) == code else ""
        st.markdown(
            f'<div style="{style}; padding: 2px 6px; border-radius: 4px">'
            f"<b>{name(code)}</b>　{_status_text(live[code], at)}</div>",
            unsafe_allow_html=True,
        )

    @st.fragment(run_every=30)
    def next_starts() -> None:
        at = clock()
        upcoming = _racing([statuses(at)[c] for c in slots if c])
        lines = [
            f"- {s.start:%H:%M} {name(s.venue_code)} {s.race_no}R"
            + ("（発走待ち）" if s.state == "waiting" else f"（{_until(s.start, at)}）")
            for s in upcoming
        ]
        st.markdown("**次の発走**\n\n" + ("\n".join(lines) or "この後のレースはありません。"))

    def set_manual(code: str) -> None:
        url = st.session_state.get(f"mv_url_{code}", "")
        if video_id := video_id_from_url(url):
            manual[code] = video_id

    def tile(code: str) -> None:
        header(code)
        video_id = manual.get(code) or (found[code].video_id if code in found else None)
        if video_id:
            st.iframe(embed_url(video_id), height=TILE_HEIGHT)
        else:
            st.warning("配信が見つかりません。下の欄に YouTube の URL を貼ってください。")
        with st.popover("配信を差し替え" if video_id else "配信の URL を入力"):
            st.text_input(
                "YouTube の URL", key=f"mv_url_{code}", on_change=set_manual, args=(code,),
                placeholder="https://www.youtube.com/watch?v=…",
            )  # fmt: skip
            if st.session_state.get(f"mv_url_{code}") and code not in manual:
                st.error("YouTube の動画の URL ではありません。")

    # Always create this block, filled or not: the videos below must keep their place in
    # the page, or the browser reloads them.
    above = st.container()
    full = None not in slots
    if full:
        with above:
            next_starts()
    panel = None if full else slots.index(None)
    for row in range(MAX_TILES // 2):
        for col, i in zip(st.columns(2), (2 * row, 2 * row + 1), strict=True):
            with col:
                if slots[i]:
                    tile(slots[i])
                elif i == panel:
                    next_starts()
