from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from keirin.youtube import (
    CHANNELS,
    Stream,
    YouTubeClient,
    YouTubeError,
    embed_url,
    find_streams,
    match_venue,
    pick_streams,
    video_id_from_url,
)

JST = timezone(timedelta(hours=9))
TODAY = {"61", "47", "13", "11", "75", "71", "87", "81", "44", "56", "48"}


@pytest.mark.parametrize(
    ("title", "channel_venue", "expected"),
    [
        ("高松市営玉野競輪 F2 初日", "71", "61"),  # 借り上げ: 施行者を無視
        ("【富山市営】松阪競輪F2ミッドナイト 初日", "47", "47"),
        ("【函館inいわき平 実況中継】1日目", "13", "13"),  # X in Y は Y
        ("【競輪予想】熊本G3 決勝 前日予想", "11", None),  # 予想は除外
        ("前検日コメント杯 F2【1日目】", "44", "44"),  # 場名なし → チャンネルの場
        ("モーニング競輪 ラブ杯 in松山 1日目", "71", "75"),
        ("熊本けいりんLIVE 初日 解説 小倉太郎", "87", "87"),  # チャンネル自身の場を優先
        ("高松宮記念杯競輪 岸和田 G1 初日", "56", "56"),  # 高松宮は高松ではない
        ("四日市KEIRIN LIVE 初日", "48", "48"),  # KEIRIN の in は区切りではない
    ],
)
def test_match_venue(title, channel_venue, expected):
    assert match_venue(title, channel_venue, TODAY) == expected


def test_channels_cover_all_venues():
    assert len(CHANNELS) == 43
    assert len(CHANNELS["28"]) == len(CHANNELS["56"]) == len(CHANNELS["62"]) == 2


WINDOW = (
    datetime(2026, 10, 21, 21, 0, tzinfo=JST),
    datetime(2026, 10, 21, 23, 50, tzinfo=JST),
)


def _stream(vid: str, venue: str, title: str, start: datetime, live: bool = False) -> Stream:
    return Stream(vid, CHANNELS[venue][0], title, start, live)


def test_pick_prefers_own_channel_and_window():
    before = WINDOW[0] - timedelta(minutes=30)
    own = _stream("own00000001", "61", "玉野競輪 初日", before)
    other = _stream("oth00000001", "71", "高松市営玉野競輪 初日", before, live=True)
    yesterday = _stream("old00000001", "61", "玉野競輪 最終日", WINDOW[0] - timedelta(days=1))
    early = _stream("erl00000001", "61", "玉野競輪 モーニング", WINDOW[0] - timedelta(hours=14))
    got = pick_streams([other, own, yesterday, early], {"61": WINDOW}, date(2026, 10, 21))
    assert got == {"61": own}


def test_pick_falls_back_to_other_channel():
    other = _stream("oth00000001", "71", "高松市営玉野競輪 初日", WINDOW[0], live=True)
    assert pick_streams([other], {"61": WINDOW}, date(2026, 10, 21)) == {"61": other}


def test_pick_without_schedule_accepts_any_stream_today():
    today = _stream("tdy00000001", "61", "玉野競輪 初日", datetime(2026, 10, 21, 8, 0, tzinfo=JST))
    tomorrow = _stream("tmr00000001", "61", "玉野競輪 2日目", datetime(2026, 10, 22, 8, tzinfo=JST))
    got = pick_streams([tomorrow, today], {"61": None}, date(2026, 10, 21))
    assert got == {"61": today}


def test_pick_ignores_venues_not_racing_today():
    s = _stream("own00000001", "61", "玉野競輪 初日", WINDOW[0])
    assert pick_streams([s], {"47": WINDOW}, date(2026, 10, 21)) == {}


# --- API client -------------------------------------------------------------------------


def _api(
    videos: dict[str, dict], uploads: dict[str, list[str]], fail: dict[str, int] | None = None
):
    """A fake Data API: `uploads` maps channel id -> video ids, `videos` id -> resource."""
    calls: list[str] = []
    fail = fail or {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "test-key"
        kind = request.url.path.rsplit("/", 1)[-1]
        calls.append(kind)
        if kind in fail:
            return httpx.Response(fail[kind], json={"error": {"code": fail[kind]}})
        if kind == "playlistItems":
            channel = "UC" + request.url.params["playlistId"][2:]
            if channel in fail:
                return httpx.Response(fail[channel], json={"error": {"code": 404}})
            ids = uploads.get(channel, [])
            return httpx.Response(
                200, json={"items": [{"contentDetails": {"videoId": v}} for v in ids]}
            )
        assert kind == "videos"
        ids = request.url.params["id"].split(",")
        return httpx.Response(200, json={"items": [videos[v] for v in ids if v in videos]})

    return httpx.MockTransport(handler), calls


def _video(vid: str, channel: str, title: str, start: str, **live) -> dict:
    details = {"scheduledStartTime": start, **live}
    return {
        "id": vid,
        "snippet": {"channelId": channel, "title": title},
        "liveStreamingDetails": details,
    }


TAMANO, TAKAMATSU, MATSUSAKA = CHANNELS["61"][0], CHANNELS["71"][0], CHANNELS["47"][0]
MEETINGS = {"61": WINDOW, "47": WINDOW}


def test_find_streams_request_count():
    transport, calls = _api(
        {"vid00000001": _video("vid00000001", TAMANO, "玉野競輪 初日", "2026-10-21T11:00:00Z")},
        {TAMANO: ["vid00000001"]},
    )
    client = YouTubeClient("test-key", transport=transport)
    got = find_streams(client, date(2026, 10, 21), {"61": WINDOW, "71": WINDOW})
    assert calls.count("playlistItems") == 2  # 玉野 and 高松 each have one channel
    assert calls.count("videos") == 1
    assert got["61"].video_id == "vid00000001"
    assert got["61"].live is False


def test_find_streams_skips_finished_and_vod():
    videos = {
        "vod00000001": {"id": "vod00000001", "snippet": {"channelId": TAMANO, "title": "玉野"}},
        "end00000001": _video(
            "end00000001", TAMANO, "玉野競輪 初日", "2026-10-21T11:00:00Z",
            actualStartTime="2026-10-21T11:00:00Z", actualEndTime="2026-10-21T12:00:00Z",
        ),
        "liv00000001": _video(
            "liv00000001", TAMANO, "玉野競輪 初日", "2026-10-21T11:30:00Z",
            actualStartTime="2026-10-21T11:31:00Z",
        ),
    }  # fmt: skip
    transport, _ = _api(videos, {TAMANO: list(videos)})
    got = find_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21), MEETINGS)
    assert got["61"].video_id == "liv00000001"
    assert got["61"].live is True
    assert got["61"].start == datetime(2026, 10, 21, 20, 31, tzinfo=JST)


def test_find_streams_partial_failure():
    transport, _ = _api(
        {"mat00000001": _video("mat00000001", MATSUSAKA, "松阪競輪 初日", "2026-10-21T11:00:00Z")},
        {MATSUSAKA: ["mat00000001"]},
        fail={TAMANO: 404},
    )
    got = find_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21), MEETINGS)
    assert set(got) == {"47"}


def test_find_streams_quota_error():
    transport, _ = _api({}, {TAMANO: ["vid00000001"]}, fail={"videos": 403})
    with pytest.raises(YouTubeError):
        find_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21), MEETINGS)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=abcDEF12345&t=3", "abcDEF12345"),
        ("https://youtu.be/abcDEF12345", "abcDEF12345"),
        ("https://www.youtube.com/live/abcDEF12345?si=x", "abcDEF12345"),
        ("https://www.youtube.com/embed/abcDEF12345", "abcDEF12345"),
        ("https://www.youtube.com/@toride/live", None),
        ("not a url", None),
    ],
)
def test_video_id_from_url(url, expected):
    assert video_id_from_url(url) == expected


def test_embed_url_starts_muted():
    assert embed_url("abcDEF12345") == (
        "https://www.youtube.com/embed/abcDEF12345?autoplay=1&mute=1&playsinline=1"
    )
