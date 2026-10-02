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
