from datetime import date

import pytest
from conftest import DAY, MEETING_ENC, FakeClient, race_enc
from test_score import write_entries

from keirin import storage
from keirin.collect import fetch_day
from keirin.parse import parse_bundle
from keirin.store import CachedClient, NotFound, Store
from keirin.timeutil import date_range

D = date.fromisoformat(DAY)


@pytest.fixture
def live_client():
    client = FakeClient()
    client.responses[("JSJ001", MEETING_ENC)]["C0201data"]["C0201race"] = [
        {"encParaR": race_enc(1)}
    ]
    return client


def test_live_meeting_races_and_corrected_card(tmp_path, live_client):
    # 000001 raced in A級 last month and is S級 now -> corrected upwards.
    for d in date_range(date(2025, 10, 1), date(2026, 1, 9)):
        write_entries(tmp_path, d, [])
    write_entries(
        tmp_path,
        date(2025, 12, 1),
        [{"racer_id": "000001", "car_no": 1, "class": "A1", "finish_pos": 1}],
    )
    live_client.responses[("JSJ006", race_enc(1))]["sensyuTypeInfo"][0]["kyuhan"] = "S2"
    store = Store(tmp_path, live_client)

    [meeting] = store.meetings(D)
    assert (meeting["venue_name"], meeting["time_slot"]) == ("テスト", "night")
    [race] = store.races(D, "99")
    assert race["race_class"] == "Ａ級予選"
    assert race["riders"][0] == (1, "山田 一郎", "逃")

    _, _, rows = store.card(D, "99", 1)
    first = rows[0]
    assert (first["score"], first["corrected"], first["adjustment"]) == (90.25, 94.05, 3.80)
    assert (first["other_tier_races"], first["races"], first["complete"]) == (1, 1, True)
    assert first["rank_official"] == 1  # 2 riders share 90.25; car 3 has no score
    assert first["rank_corrected"] == 1
    # The meeting header provides the race token; JSJ014 is not needed.
    assert ("JSJ014", {"encp": MEETING_ENC}) not in live_client.calls


def test_race_token_falls_back_to_jsj014(tmp_path):
    client = FakeClient()  # header without race tokens
    client.responses[("JSJ014", MEETING_ENC)] = {
        "raceDayDataList": [
            {"strRaceNitiji": "初日",
             "raceNoDataList": [{"strRaceNo": "1R", "strLnkPrm": race_enc(1)}]}
        ]
    }  # fmt: skip
    _, _, rows = Store(tmp_path, client).card(D, "99", 1)
    assert [r["car_no"] for r in rows] == [1, 2, 3]


def test_collected_day_is_served_from_tables(tmp_path):
    storage.write_tables(tmp_path, D, parse_bundle(fetch_day(FakeClient(), D)))

    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError("collected days must not hit the network")

    store = Store(tmp_path, NoNetwork())
    assert store.meetings(D)[0]["title"] == "テスト杯"
    _, _, rows = store.card(D, "99", 1)
    assert rows[2]["notes"] == "失格/斜行"


def test_unknown_meeting_raises_not_found(tmp_path, live_client):
    with pytest.raises(NotFound):
        Store(tmp_path, live_client).races(D, "12")


def test_cached_client_reuses_responses():
    now = [0.0]
    inner = FakeClient()
    cached = CachedClient(inner, ttl=60, clock=lambda: now[0])
    cached.get("JSJ057", kday="20260110")
    cached.get("JSJ057", kday="20260110")
    assert len(inner.calls) == 1
    now[0] = 61
    cached.get("JSJ057", kday="20260110")
    assert len(inner.calls) == 2


def test_result_from_tables(tmp_path):
    storage.write_tables(tmp_path, D, parse_bundle(fetch_day(FakeClient(), D)))
    result = Store(tmp_path, None).result(D, "99", 1)
    assert (result["weather"], result["wind_speed"]) == ("晴", 1.5)
    order = {e["car_no"]: e for e in result["entries"]}
    assert (order[2]["finish_pos"], order[2]["kimarite"], order[2]["last_lap"]) == (1, "差し", 11.2)
    assert order[3]["finish_pos"] is None and order[3]["notes"] == "失格/斜行"
    assert [(p["bet_type"], p["combination"], p["payout"]) for p in result["payouts"]] == [
        ("2車単", "2-1", 1230),
        ("ワイド", "1=2", 150),
        ("ワイド", "1=3", 320),
    ]


def test_live_result_only_after_the_race(tmp_path, live_client):
    store = Store(tmp_path, live_client)
    assert store.result(D, "99", 1) is None  # header says the result is not in yet
    assert not any(call[0] == "JSJ012" for call in live_client.calls)

    live_client.responses[("JSJ001", MEETING_ENC)]["C0201data"]["C0201race"][0]["rcvKekka"] = "1"
    result = Store(tmp_path, live_client).result(D, "99", 1)
    assert [e["car_no"] for e in result["entries"] if e["finish_pos"] == 1] == [2]
    assert len(result["payouts"]) == 3


NINFO_CONTEST = [
    {"syaban": 1, "narabiX": 3, "narabiY": 1},
    {"syaban": 2, "narabiX": 1, "narabiY": 1},
    {"syaban": 3, "narabiX": 3, "narabiY": 2},
]


def test_formation_live_from_keirin_jp(tmp_path, live_client):
    live_client.responses[("JSJ017", MEETING_ENC)]["rInfo"][0].update(
        {"nInfo": NINFO_CONTEST, "line": "細切れ"}
    )
    f = Store(tmp_path, live_client).formation(D, "99", 1)
    assert (f["text"], f["label"], f["source"]) == ("2 / (13)", "細切れ", "keirin.jp")
    assert f["lines"] == [[[2]], [[1, 3]]]
    assert f["by_car"][1] == {"line_no": 2, "line_pos": 1, "line_size": 2, "contested": True}


def test_formation_falls_back_to_the_capture(tmp_path, live_client):
    # keirin.jp no longer shows it (race over): use what the collector captured earlier.
    from keirin import lines

    block = (
        '<ul class="keirinRyosouline"><li><span class="no2">2</span></li>'
        '<li><span class="no1">1</span></li><li><span class="no0">&nbsp;</span></li>'
        '<li><span class="no3">3</span></li></ul>'
    )
    lines.save_captures(tmp_path, D, {"99-1": {"source": "oddspark", "data": block}})
    f = Store(tmp_path, live_client).formation(D, "99", 1)
    assert (f["text"], f["label"], f["source"]) == ("21 / 3", "二分戦", "oddspark")


def test_formation_unknown(tmp_path, live_client):
    assert Store(tmp_path, live_client).formation(D, "99", 1) is None
