import json
from datetime import date

from conftest import DAY, FakeClient, race_enc

from keirin.collect import fetch_day
from keirin.parse import TABLES, parse_bundle


def _tables(client: FakeClient):
    return parse_bundle(fetch_day(client, date.fromisoformat(DAY)))


def test_fetch_day_follows_day_meeting_race(fake_client):
    bundle = fetch_day(fake_client, date.fromisoformat(DAY))
    assert [c[0] for c in fake_client.calls] == [
        "JSJ057",
        "JSJ001",
        "JSJ017",
        "JSJ018",
        "JSJ002",
        "JSJ012",
    ]
    assert fake_client.calls[0][1] == {"kday": "20260110"}
    assert bundle["date"] == DAY
    assert len(bundle["responses"]) == 6


def test_rows_have_exactly_the_declared_columns(fake_client):
    for name, rows in _tables(fake_client).items():
        for row in rows:
            assert set(row) <= set(TABLES[name]), name


def test_meeting_and_race(fake_client):
    tables = _tables(fake_client)
    assert tables["meetings"] == [
        {
            "date": DAY,
            "venue_code": "99",
            "venue_name": "テスト",
            "grade": "F1",
            "day_label": "初日",
            "time_slot": "night",
            "title": "テスト杯",
        }
    ]
    assert tables["races"] == [
        {
            "date": DAY,
            "venue_code": "99",
            "race_no": 1,
            "race_class": "Ａ級予選",
            "close_time": "15:20",
            "start_time": "15:25",
            "weather": "晴",
            "wind_speed": 1.5,
            "entries": 3,
        }
    ]


def test_entries_merge_card_and_result(fake_client):
    entries = {e["car_no"]: e for e in _tables(fake_client)["entries"]}
    assert sorted(entries) == [1, 2, 3]

    first = entries[1]
    assert first["racer_name"] == "山田 一郎"
    assert first["prefecture"] == "東京"
    assert first["score"] == 90.25
    assert first["back"] == 4
    assert first["finish"] == "2"
    assert first["finish_pos"] == 2
    assert first["margin"] == "1/2車輪"
    assert first["last_lap"] == 11.5
    assert first["bh"] == "HB"
    assert first["notes"] == ""

    # Car 3 has no detailed card: falls back to the entry list, then gets the result.
    third = entries[3]
    assert third["racer_id"] == "000003"
    assert third["style"] == "両"
    assert "score" not in third
    assert third["finish"] == "失"
    assert third["finish_pos"] is None
    assert third["notes"] == "失格/斜行"


def test_payouts_skip_unsold_and_parse_numbers(fake_client):
    payouts = _tables(fake_client)["payouts"]
    assert [(p["bet_type"], p["combination"], p["payout"], p["popularity"]) for p in payouts] == [
        ("2車単", "2-1", 1230, 4),
        ("ワイド", "1=2", 150, 1),
        ("ワイド", "1=3", 320, 3),
    ]


def test_meeting_held_on_another_day_is_skipped(fake_client):
    fake_client.responses[("JSJ018", "enc-meeting-A")]["kday"] = "20260111"
    tables = _tables(fake_client)
    assert all(rows == [] for rows in tables.values())


def test_missing_responses_do_not_crash():
    bundle = {
        "date": DAY,
        "responses": [
            {
                "type": "JSJ057",
                "params": {"kday": "20260110"},
                "body": {"kInfo": [{"encPrm": "x", "KeirinCd": "1"}]},
            }
        ],
    }
    tables = parse_bundle(bundle)
    assert len(tables["meetings"]) == 1
    assert tables["races"] == []


def test_parse_is_stable_across_raw_roundtrip(fake_client):
    bundle = fetch_day(fake_client, date.fromisoformat(DAY))
    # storage.write_raw serializes with sort_keys=True, which reorders dict keys.
    restored = json.loads(json.dumps(bundle, sort_keys=True))
    assert parse_bundle(restored) == parse_bundle(bundle)


def test_falls_back_to_per_race_cards_when_jsj002_is_empty(fake_client):
    fake_client.responses[("JSJ002", race_enc(1))] = {"resultCd": -1}
    bundle = fetch_day(fake_client, date.fromisoformat(DAY))
    assert [c[0] for c in fake_client.calls][-3:] == ["JSJ002", "JSJ006", "JSJ012"]
    entries = {e["car_no"]: e for e in parse_bundle(bundle)["entries"]}
    assert entries[1]["score"] == 90.25


def test_old_bundles_with_jsj006_parse_the_same(fake_client):
    new = fetch_day(fake_client, date.fromisoformat(DAY))
    # Bundles collected before 2026-10-04 have JSJ006 per race and no JSJ002.
    old = {**new, "responses": [r for r in new["responses"] if r["type"] != "JSJ002"]}
    old["responses"].insert(
        -1,
        {"type": "JSJ006", "params": {"encp": race_enc(1)},
         "body": fake_client.responses[("JSJ006", race_enc(1))]},
    )  # fmt: skip
    assert parse_bundle(old) == parse_bundle(new)
