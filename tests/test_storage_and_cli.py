import csv
from datetime import date

from conftest import DAY

from keirin import cli, storage
from keirin.collect import fetch_day


def test_raw_roundtrip_is_deterministic(tmp_path, fake_client):
    bundle = fetch_day(fake_client, date.fromisoformat(DAY))
    path = storage.write_raw(tmp_path, bundle)
    first = path.read_bytes()
    storage.write_raw(tmp_path, bundle)
    assert path.read_bytes() == first
    assert path == tmp_path / "raw" / "2026" / f"{DAY}.json.gz"
    assert storage.read_raw(path) == bundle


def test_rebuild_writes_all_tables(tmp_path, fake_client):
    storage.write_raw(tmp_path, fetch_day(fake_client, date.fromisoformat(DAY)))
    assert cli.main(["rebuild", "--data-dir", str(tmp_path)]) == 0

    day = date.fromisoformat(DAY)
    for table in ("meetings", "races", "entries", "payouts"):
        assert storage.table_path(tmp_path, table, day).exists()
    with storage.table_path(tmp_path, "entries", day).open() as f:
        rows = list(csv.DictReader(f))
    assert [r["car_no"] for r in rows] == ["1", "2", "3"]
    assert rows[2]["score"] == ""
    assert rows[2]["finish"] == "失"


def test_collect_refuses_today(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "today_jst", lambda: date(2026, 1, 10))
    assert cli.main(["collect", "--date", "2026-01-10", "--data-dir", str(tmp_path)]) == 2


def test_collect_skips_days_already_collected(tmp_path, monkeypatch, fake_client):
    monkeypatch.setattr(cli, "today_jst", lambda: date(2026, 1, 11))
    storage.write_raw(tmp_path, fetch_day(fake_client, date.fromisoformat(DAY)))

    class ExplodingClient:
        def __init__(self, **kwargs):
            self.request_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def get(self, *args, **kwargs):
            raise AssertionError("must not hit the network")

    monkeypatch.setattr(cli, "KeirinClient", ExplodingClient)
    assert cli.main(["collect", "--date", DAY, "--data-dir", str(tmp_path)]) == 0
