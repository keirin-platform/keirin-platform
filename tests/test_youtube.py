from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from keirin.youtube import (
    CHANNELS,
    Stream,
    YouTubeClient,
    YouTubeError,
    embed_url,
    find_all_streams,
    find_streams,
    match_venue,
    pick_streams,
    streams_document,
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
            ids = uploads.get(channel, [])[: int(request.url.params["maxResults"])]
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


def test_find_streams_sees_streams_scheduled_long_ago():
    # Uploads are ordered by creation, so a stream booked days ahead sits behind newer
    # interview clips (いわき平 had tonight's stream at #49 of its uploads on 2026-10-21).
    clips = [f"clp{i:08d}" for i in range(48)]
    videos = {
        v: {"id": v, "snippet": {"channelId": TAMANO, "title": "インタビュー"}} for v in clips
    }
    videos["old00000001"] = _video("old00000001", TAMANO, "玉野競輪 初日", "2026-10-21T11:00:00Z")
    transport, calls = _api(videos, {TAMANO: [*clips, "old00000001"]})
    got = find_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21), MEETINGS)
    assert got["61"].video_id == "old00000001"
    assert calls.count("playlistItems") == 2  # still one call per channel


def test_find_streams_invalid_key_is_an_error():
    # An invalid key gets 400 (keyInvalid), not 403: say so instead of "not found" everywhere.
    transport, _ = _api({}, {}, fail={"playlistItems": 400})
    with pytest.raises(YouTubeError):
        find_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21), MEETINGS)


def test_find_streams_network_down_stops_at_once():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("down")

    client = YouTubeClient("test-key", transport=httpx.MockTransport(handler))
    with pytest.raises(YouTubeError):
        find_streams(client, date(2026, 10, 21), MEETINGS)
    assert len(calls) == 1  # not once per channel


# --- public multiviewer (all venues, no keirin.jp) ---------------------------------------


def test_pick_any_live_keeps_yesterdays_live_stream():
    start = datetime(2026, 10, 20, 23, 50, tzinfo=JST)
    s = Stream("vid00000009", TAMANO, "玉野競輪 2日目", start, True)
    day = date(2026, 10, 21)
    assert pick_streams([s], {"61": None}, day) == {}
    assert pick_streams([s], {"61": None}, day, any_live=True) == {"61": s}


def test_find_all_streams_searches_every_channel():
    transport, calls = _api(
        {
            "vid00000001": _video(
                "vid00000001", TAKAMATSU, "モーニング競輪 in松山 1日目", "2026-10-20T23:30:00Z"
            )
        },
        {TAKAMATSU: ["vid00000001"]},
    )
    got = find_all_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21))
    assert calls.count("playlistItems") == sum(len(c) for c in CHANNELS.values())
    assert set(got) == {"75"}  # 高松のチャンネルの松山の開催は、松山になる


def test_find_all_streams_non_race_live_on_idle_channel():
    # Known weak spot (design doc): a non-race live without a venue name on an idle
    # venue's channel shows as that venue. Only titles with "予想" are dropped.
    maebashi = CHANNELS["22"][0]
    transport, _ = _api(
        {
            "vid00000003": _video(
                "vid00000003", maebashi, "トークショー生配信", "2026-10-21T09:00:00Z"
            ),
            "vid00000004": _video(
                "vid00000004", maebashi, "【競輪予想】今夜の予想", "2026-10-21T10:00:00Z"
            ),
        },
        {maebashi: ["vid00000003", "vid00000004"]},
    )
    got = find_all_streams(YouTubeClient("test-key", transport=transport), date(2026, 10, 21))
    assert {k: v.video_id for k, v in got.items()} == {"22": "vid00000003"}


def test_streams_document_shape_and_order():
    late = Stream(
        "vid00000002", MATSUSAKA, "松阪", datetime(2026, 10, 21, 20, 45, tzinfo=JST), False
    )
    early = Stream("vid00000001", TAMANO, "玉野", datetime(2026, 10, 21, 10, 0, tzinfo=JST), True)
    doc = streams_document({"47": late, "61": early}, datetime(2026, 10, 21, 19, 13, 5, tzinfo=JST))
    assert doc["generated_at"] == "2026-10-21T19:13:05+09:00"
    assert [s["venue_code"] for s in doc["streams"]] == ["61", "47"]
    assert doc["streams"][1] == {
        "venue_code": "47",
        "venue_name": "松阪",
        "video_id": "vid00000002",
        "title": "松阪",
        "start": "2026-10-21T20:45:00+09:00",
        "live": False,
    }
