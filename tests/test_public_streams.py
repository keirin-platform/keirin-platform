import json
from datetime import datetime

from keirin import cli
from keirin.timeutil import JST
from keirin.youtube import CHANNELS, Stream, YouTubeError


def test_public_streams_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    out = tmp_path / "streams.json"
    assert cli.main(["public-streams", "--out", str(out)]) == 2
    assert not out.exists()


def test_public_streams_writes_document(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    s = Stream(
        "vid00000001", CHANNELS["47"][0], "松阪", datetime(2026, 10, 21, 20, 45, tzinfo=JST), True
    )
    monkeypatch.setattr(cli.youtube, "find_all_streams", lambda client, day: {day: {"47": s}})
    out = tmp_path / "streams.json"
    assert cli.main(["public-streams", "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["streams"][0]["venue_name"] == "松阪"
    assert doc["streams"][0]["day"]  # the run's JST date
    assert "松阪" in out.read_text(encoding="utf-8")  # not escaped


def test_public_streams_api_error_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    def fail(client, day):
        raise YouTubeError("videos: HTTP 403")

    monkeypatch.setattr(cli.youtube, "find_all_streams", fail)
    out = tmp_path / "streams.json"
    assert cli.main(["public-streams", "--out", str(out)]) == 1
    assert not out.exists()
