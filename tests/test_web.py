from datetime import date

import pytest

pytest.importorskip("fastapi")

from conftest import DAY, MEETING_ENC, FakeClient, race_enc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_score import write_entries  # noqa: E402

from keirin import storage  # noqa: E402
from keirin.collect import fetch_day  # noqa: E402
from keirin.parse import parse_bundle  # noqa: E402
from keirin.timeutil import date_range  # noqa: E402
from keirin.web.app import create_app  # noqa: E402
from keirin.web.store import CachedClient  # noqa: E402


@pytest.fixture
def live_client():
    client = FakeClient()
    client.responses[("JSJ014", MEETING_ENC)] = {
        "raceDayDataList": [
            {"strRaceNitiji": "初日", "raceNoDataList": [
                {"strRaceNo": "1R", "strLnkPrm": race_enc(1)}]},
        ]
    }  # fmt: skip
    return client


def test_live_day_meeting_and_race(tmp_path, live_client):
    # History: car 1 (000001) raced in A級 last month and is S級 now -> corrected upwards.
    for d in date_range(date(2025, 10, 1), date(2026, 1, 9)):
        write_entries(tmp_path, d, [])
    write_entries(
        tmp_path,
        date(2025, 12, 1),
        [{"racer_id": "000001", "car_no": 1, "class": "A1", "finish_pos": 1}],
    )
    live_client.responses[("JSJ006", race_enc(1))]["sensyuTypeInfo"][0]["kyuhan"] = "S2"
    client = TestClient(create_app(tmp_path, live_client))

    page = client.get(f"/d/{DAY}")
    assert page.status_code == 200
    assert "テスト" in page.text and "ナイター" in page.text

    page = client.get(f"/d/{DAY}/99")
    assert page.status_code == 200
    assert "Ａ級予選" in page.text and "山田 一郎" in page.text

    page = client.get(f"/d/{DAY}/99/1")
    assert page.status_code == 200
    assert "94.05" in page.text  # 90.25 + 3.80 * (1 A級 race / 1 race)
    assert "+3.80" in page.text


def test_upcoming_race_token_comes_from_the_meeting_header(tmp_path):
    client = FakeClient()  # no JSJ014 response: it fails for days not started yet
    client.responses[("JSJ001", MEETING_ENC)]["C0201data"]["C0201race"] = [
        {"encParaR": race_enc(1)}
    ]
    page = TestClient(create_app(tmp_path, client)).get(f"/d/{DAY}/99/1")
    assert page.status_code == 200
    assert "山田 一郎" in page.text
    assert ("JSJ014", {"encp": MEETING_ENC}) not in client.calls


def test_collected_day_is_served_from_tables(tmp_path):
    bundle = fetch_day(FakeClient(), date.fromisoformat(DAY))
    storage.write_tables(tmp_path, date.fromisoformat(DAY), parse_bundle(bundle))

    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError("collected days must not hit the network")

    client = TestClient(create_app(tmp_path, NoNetwork()))
    page = client.get(f"/d/{DAY}/99/1")
    assert page.status_code == 200
    assert "テスト杯" not in page.text  # title only on the meeting page
    assert "失格/斜行" in page.text  # results are shown for collected days
    assert client.get(f"/d/{DAY}/99").status_code == 200


def test_unknown_meeting_is_404(tmp_path, live_client):
    client = TestClient(create_app(tmp_path, live_client))
    assert client.get(f"/d/{DAY}/12").status_code == 404


def test_basic_auth(tmp_path, live_client):
    client = TestClient(create_app(tmp_path, live_client, username="me", password="pw"))
    assert client.get("/healthz").status_code == 200
    assert client.get(f"/d/{DAY}").status_code == 401
    assert client.get(f"/d/{DAY}", auth=("me", "wrong")).status_code == 401
    assert client.get(f"/d/{DAY}", auth=("me", "pw")).status_code == 200


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
