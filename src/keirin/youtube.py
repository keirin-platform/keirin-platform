"""Finds each venue's live stream on the venues' official YouTube channels (docs/multiview.md).

Only the YouTube Data API is used (``playlistItems`` and ``videos``, 1 unit each); the
YouTube pages themselves are never scraped. A stream is matched to a venue by its title:
meetings held at another venue's track carry both names ("高松市営玉野競輪",
"函館inいわき平"), and channels sometimes carry other venues' meetings, so the
channel alone does not decide the venue.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from keirin.timeutil import JST

log = logging.getLogger(__name__)

# Venue code -> official channels (checked 2026-10-21 against the K-Dreams and
# netkeirin live lists). Tachikawa, Kishiwada and Hiroshima have two each.
CHANNELS: dict[str, tuple[str, ...]] = {
    "11": ("UC7Ehq9agmTeznt6XjZ5faLw",),  # 函館
    "12": ("UCosOaqq60s3QAenTONNxUuQ",),  # 青森
    "13": ("UCTrT1fwMS9cOePzW_Z0TQfw",),  # いわき平
    "21": ("UCT6ae6rMY1bP-A_6EKEOLtw",),  # 弥彦
    "22": ("UCtn0XkksabeXWF2Omyg6JEQ",),  # 前橋
    "23": ("UCDeV-cyqnTnoCCjz2Dm7VNA",),  # 取手
    "24": ("UCXKIA4ppNI_5lYFS3Eke39A",),  # 宇都宮
    "25": ("UCjdy4VH5HnofysPTcqiwTdg",),  # 大宮
    "26": ("UCPqtPpQwwWArIS_zTl7A9XQ",),  # 西武園
    "27": ("UCHdHXMKeFSZ3PxSUtpp90ng",),  # 京王閣
    "28": ("UCqm3b6_IkDSBezhVrJUmt1A", "UCvG1ViOoCDUqLrjbJsPhwzg"),  # 立川
    "31": ("UCEYoRewzhZWzP9OHaAFE8gA",),  # 松戸
    "32": ("UCxehdZntrAfpHnaNGlg_WJQ",),  # 千葉
    "34": ("UCtU7gR5VCpvQjtVPykL4EUA",),  # 川崎
    "35": ("UCvfTQBD1nQ8kAqwdrnTN-_g",),  # 平塚
    "36": ("UCNgccovrnvwUqSBaqoqrtfA",),  # 小田原
    "37": ("UC3CT-xL9H2UW4PMA2Hg1FjA",),  # 伊東
    "38": ("UCoPaxOy8ch2ydfgL1cpwhvQ",),  # 静岡
    "42": ("UCgf3cKAiLdTAset5YVGN4Rw",),  # 名古屋
    "43": ("UCs2ed7DZGQ-YKsCOX6NGyZQ",),  # 岐阜
    "44": ("UCcDTb_mchV4oxxQ2CfGSHFA",),  # 大垣
    "45": ("UC8mS4Ao44gjBbi8nFrP4fNg",),  # 豊橋
    "46": ("UCvVIZOHj843xo_QSnjY62Lg",),  # 富山
    "47": ("UCOZRGa_L_saD0ObvDtzG22Q",),  # 松阪
    "48": ("UCPibXjT_mINGifM1gi7ja0g",),  # 四日市
    "51": ("UCypKxtT2sGParvK54JMeWfw",),  # 福井
    "53": ("UCAHgvI8-zib9yOA2suyjGcg",),  # 奈良
    "54": ("UCdBuL72WRnKDIvAekxS2NxQ",),  # 向日町
    "55": ("UCu_WLHra6mfVBHj5qVz5Rbw",),  # 和歌山
    "56": ("UChHOZZ-KJgahUia5vxQnvNw", "UCng42-AiA4Km1N6fM-qq69A"),  # 岸和田
    "61": ("UCiKs6agRQVxz1RRPHSX-Oaw",),  # 玉野
    "62": ("UCC-MHu8wH3xWu8M93nzszBQ", "UCyi7rKGMOJSzCNT8Vt4-Cbg"),  # 広島
    "63": ("UC42l0MgmrF_tIfaMAPVvUKA",),  # 防府
    "71": ("UCWFvocB4sfX4wVXWaB4-SBg",),  # 高松
    "73": ("UCnfVl6Q_xov3Wf45NdWE1Eg",),  # 小松島
    "74": ("UCcnE1XfjoFEDL5GeWRk2DgQ",),  # 高知
    "75": ("UCX3xb9FwC7jL_ZnVmoyzgDg",),  # 松山
    "81": ("UCgOTgI9mi1XXYWs-J50FvHw",),  # 小倉
    "83": ("UCh7mcNayAkEXneLmnFZwzbQ",),  # 久留米
    "84": ("UClqmxHvzzOOWnpNdznfM28A",),  # 武雄
    "85": ("UCHsrH9sBqz9uDKggEKGsOFw",),  # 佐世保
    "86": ("UCxUAWJYLRXK2GFl9Nz7lJXA",),  # 別府
    "87": ("UCuhDz3wM73NDTg4IPMNlKhQ",),  # 熊本
}

# Venue code -> how the venue appears in stream titles (the first is the official name).
VENUE_NAMES: dict[str, tuple[str, ...]] = {
    "11": ("函館",),
    "12": ("青森",),
    "13": ("いわき平",),
    "21": ("弥彦",),
    "22": ("前橋",),
    "23": ("取手",),
    "24": ("宇都宮",),
    "25": ("大宮",),
    "26": ("西武園",),
    "27": ("京王閣",),
    "28": ("立川",),
    "31": ("松戸",),
    "32": ("千葉", "JPFドーム", "JPF ドーム"),
    "34": ("川崎",),
    "35": ("平塚", "湘南バンク"),
    "36": ("小田原",),
    "37": ("伊東温泉", "伊東"),
    "38": ("静岡",),
    "42": ("名古屋",),
    "43": ("岐阜",),
    "44": ("大垣",),
    "45": ("豊橋",),
    "46": ("富山",),
    "47": ("松阪",),
    "48": ("四日市",),
    "51": ("福井",),
    "53": ("奈良",),
    "54": ("京都向日町", "向日町"),
    "55": ("和歌山",),
    "56": ("ブッキースタジアム岸和田", "岸和田"),
    "61": ("玉野",),
    "62": ("広島",),
    "63": ("防府",),
    "71": ("高松",),
    "73": ("小松島",),
    "74": ("高知",),
    "75": ("松山",),
    "81": ("小倉",),
    "83": ("久留米",),
    "84": ("武雄",),
    "85": ("佐世保",),
    "86": ("別府",),
    "87": ("熊本",),
}

_CHANNEL_VENUE = {cid: code for code, cids in CHANNELS.items() for cid in cids}
# The organizer of a meeting held at another track ("高松市営玉野競輪", "【富山市営】松阪").
_ORGANIZER = re.compile(r"[^\s【】\[\]]{1,6}?(?:市営|県営|町営)")
# "函館inいわき平": held at the venue after "in" (but not the "in" of "KEIRIN").
_HELD_AT = re.compile(r"(?<![A-Za-z])in(?![A-Za-z])", re.IGNORECASE)
# How long before the first race a venue's stream may start.
LEAD_TIME = timedelta(hours=4)
API = "https://www.googleapis.com/youtube/v3"
_VIDEO_ID = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/|embed/)|youtu\.be/)"
    r"([\w-]{11})(?![\w-])"
)


class YouTubeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Stream:
    video_id: str
    channel_id: str
    title: str
    start: datetime  # actual start, or the scheduled start while upcoming
    live: bool


def _position(text: str, venue_code: str) -> int | None:
    found = [i for name in VENUE_NAMES[venue_code] if (i := text.find(name)) >= 0]
    return min(found) if found else None


def match_venue(title: str, channel_venue: str, venues_today: set[str]) -> str | None:
    """The venue whose races a stream shows, or None for a stream that is not a race."""
    if "予想" in title:
        return None
    text = _ORGANIZER.sub("", title).replace("高松宮", "")
    if held_at := _HELD_AT.search(text):
        text = text[held_at.end() :]
    if _position(text, channel_venue) is not None:
        return channel_venue
    found = [(pos, code) for code in venues_today if (pos := _position(text, code)) is not None]
    return min(found)[1] if found else channel_venue


def _in_window(start: datetime, window: tuple[datetime, datetime] | None, day: date) -> bool:
    if window is None:
        return start.astimezone(JST).date() == day
    first, last = window
    return first - LEAD_TIME <= start <= last


def pick_streams(
    streams: Iterable[Stream],
    meetings: Mapping[str, tuple[datetime, datetime] | None],
    day: date,
) -> dict[str, Stream]:
    """One stream per venue racing today: the venue's own channel first, then live, then earliest.

    `meetings` maps the venues racing today to (first, last) scheduled start, or None
    when the schedule is unknown (then any stream starting today counts).
    """
    best: dict[str, tuple[tuple, Stream]] = {}
    for s in streams:
        channel_venue = _CHANNEL_VENUE.get(s.channel_id)
        if channel_venue is None:
            continue
        venue = match_venue(s.title, channel_venue, set(meetings))
        if venue not in meetings or not _in_window(s.start, meetings[venue], day):
            continue
        rank = (s.channel_id not in CHANNELS[venue], not s.live, s.start)
        if venue not in best or rank < best[venue][0]:
            best[venue] = (rank, s)
    return {venue: s for venue, (_, s) in best.items()}


class YouTubeClient:
    """The two Data API calls we need. The key never appears in logs or errors."""

    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None) -> None:
        self._key = api_key
        self._http = httpx.Client(timeout=10, transport=transport)

    def _get(self, kind: str, **params: str) -> dict[str, Any]:
        try:
            response = self._http.get(f"{API}/{kind}", params={**params, "key": self._key})
        except httpx.HTTPError as e:
            raise YouTubeError(f"{kind}: {type(e).__name__}") from None
        if response.status_code != 200:
            raise YouTubeError(f"{kind}: HTTP {response.status_code}")
        return response.json()

    def recent_streams(self, channel_ids: Iterable[str], per_channel: int = 8) -> list[Stream]:
        """Live and upcoming streams among each channel's latest uploads.

        A channel that fails is skipped (it may have been renamed or removed), except
        for 403, which means the key or the daily quota is the problem.
        """
        video_ids: list[str] = []
        for cid in channel_ids:
            try:
                body = self._get(
                    "playlistItems", part="contentDetails", maxResults=str(per_channel),
                    playlistId="UU" + cid[2:],
                )  # fmt: skip
            except YouTubeError as e:
                if "HTTP 403" in str(e):
                    raise
                log.warning("skipped channel %s: %s", cid, e)
                continue
            video_ids += [item["contentDetails"]["videoId"] for item in body.get("items", [])]
        streams = []
        for i in range(0, len(video_ids), 50):
            body = self._get(
                "videos", part="snippet,liveStreamingDetails", id=",".join(video_ids[i : i + 50])
            )
            streams += [s for item in body.get("items", []) if (s := _stream(item))]
        return streams


def _stream(item: dict[str, Any]) -> Stream | None:
    """A live or upcoming stream; None for ordinary videos and finished streams."""
    details = item.get("liveStreamingDetails")
    if not details or details.get("actualEndTime"):
        return None
    started = details.get("actualStartTime")
    start = started or details.get("scheduledStartTime")
    if not start:
        return None
    snippet = item.get("snippet", {})
    return Stream(
        video_id=item["id"],
        channel_id=snippet.get("channelId", ""),
        title=snippet.get("title", ""),
        start=datetime.fromisoformat(start).astimezone(JST),
        live=bool(started),
    )


def find_streams(
    client: YouTubeClient, day: date, meetings: Mapping[str, tuple[datetime, datetime] | None]
) -> dict[str, Stream]:
    """Streams for the venues racing on `day` (see :func:`pick_streams` for `meetings`)."""
    channels = [cid for venue in meetings for cid in CHANNELS.get(venue, ())]
    return pick_streams(client.recent_streams(channels), meetings, day)


def video_id_from_url(url: str) -> str | None:
    """The video id of a pasted watch / youtu.be / live / embed URL."""
    match = _VIDEO_ID.match(url.strip())
    return match.group(1) if match else None


def embed_url(video_id: str) -> str:
    # Browsers autoplay only muted videos; the viewer turns the sound on in the player.
    return f"https://www.youtube.com/embed/{video_id}?autoplay=1&mute=1&playsinline=1"
