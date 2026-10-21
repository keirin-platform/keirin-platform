"""Streamlit script used by test_multiview.py (AppTest runs scripts, not functions)."""

import os
from datetime import date, datetime, timedelta
from pathlib import Path

from keirin.client import KeirinApiError
from keirin.multiview import render_multiview
from keirin.timeutil import JST
from keirin.youtube import CHANNELS, Stream, YouTubeError

DAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 21, 30, tzinfo=JST)
# Made-up meetings: 前橋 races soonest, then 松阪 and いわき平; 小倉 has finished.
SCHEDULES = {
    "22": ["21:33", "21:58"],
    "47": ["21:36", "22:01"],
    "13": ["21:40", "22:05"],
    "81": ["12:00", "12:30"],
}
NAMES = {"22": "前橋", "47": "松阪", "13": "いわき平", "81": "小倉"}


class FakeStore:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)

    def meetings(self, day: date) -> list[dict]:
        return [
            {"venue_code": code, "venue_name": NAMES[code], "time_slot": "midnight"}
            for code in SCHEDULES
        ]

    def races(self, day: date, venue_code: str) -> list[dict]:
        if venue_code == os.environ.get("KEIRIN_TEST_SCHEDULE_FAIL"):
            raise KeirinApiError("JSJ017: HTTP 403")
        return [{"race_no": i + 1, "start_time": t} for i, t in enumerate(SCHEDULES[venue_code])]


def streams(meetings):
    mode = os.environ.get("KEIRIN_TEST_STREAMS", "one")
    if mode == "error":
        raise YouTubeError("videos: HTTP 403")
    # Only 前橋 is found.
    return {"22": Stream("found000001", CHANNELS["22"][0], "前橋競輪 初日", NOW, True)}


def clock() -> datetime:
    return NOW + timedelta(minutes=int(os.environ.get("KEIRIN_TEST_MINUTES", "0")))


render_multiview(
    FakeStore(os.environ["KEIRIN_TEST_DATA_DIR"]),
    clock,
    None if os.environ.get("KEIRIN_TEST_STREAMS") == "none" else streams,
)
