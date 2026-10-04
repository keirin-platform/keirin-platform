import json
from datetime import date

import httpx
import pytest

from keirin import cli, notify
from keirin.lines import Formation, UpcomingRace
from keirin.notify import Discord, SentLog, WatchlistError, WatchRule, load_watchlist, match_race

DAY = date(2026, 10, 6)


def race(formation=None, race_class="Ａ級予選", race_no=3, day=DAY):
    riders = [
        {"car_no": c, "racer_id": f"00000{c}", "name": f"選手{c}", "style": s}
        for c, s in [(1, "追"), (2, "逃"), (3, "追"), (4, "両"), (5, "追"), (6, "追"), (7, "逃")]
    ]
    return UpcomingRace(day, "84", "武雄", race_no, race_class, "15:20", riders, formation)


# 2(13)56 / 74: cars 1 and 3 contest the 番手 behind 2, 7 leads 4.
FORMATION = Formation((((2,), (1, 3), (5,), (6,)), ((7,), (4,))))


def test_load_watchlist(tmp_path):
    path = tmp_path / "watchlist.toml"
    assert load_watchlist(path) == []
    path.write_text(
        '[[watch]]\nracer_id = "000005"\nname = "選手5"\nreason = "番手捲り"\n'
        'positions = ["3番手"]\nleader_styles = ["逃"]\nexclude_finals = true\n',
        encoding="utf-8",
    )
    [rule] = load_watchlist(path)
    assert rule == WatchRule("000005", "選手5", "番手捲り", ("3番手",), ("逃",), True)
    path.write_text('[[watch]]\nname = "no id"\n', encoding="utf-8")
    with pytest.raises(WatchlistError, match="racer_id"):
        load_watchlist(path)


@pytest.mark.parametrize(
    ("rule", "matched"),
    [
        (WatchRule("000005", positions=("3番手",)), True),
        (WatchRule("000005", positions=("番手",)), False),
        (WatchRule("000001", positions=("番手",)), True),  # contested 番手 is still 番手
        (WatchRule("000003", positions=("競り",)), True),
        (WatchRule("000004", positions=("番手",), leader_styles=("逃",)), True),
        (WatchRule("000004", positions=("番手",), leader_styles=("追",)), False),
        (WatchRule("000009"), False),  # not in the race
        (WatchRule("000006"), True),  # no condition: any race of the rider
    ],
)
def test_match_race(rule, matched):
    assert bool(match_race(race(FORMATION), [rule])) == matched


def test_finals_and_unpublished_formations():
    assert match_race(race(FORMATION, "Ａ級決勝"), [WatchRule("000006", exclude_finals=True)]) == []
    # A position rule waits for the formation; a plain rule does not.
    assert match_race(race(None), [WatchRule("000005", positions=("3番手",))]) == []
    [m] = match_race(race(None), [WatchRule("000005")])
    assert m.position == ""


class FakeDiscord:
    def __init__(self):
        self.messages = []

    def send(self, content, embeds=None):
        self.messages.append((content, embeds or []))


def test_notify_once_and_dry_run(tmp_path):
    rules = [WatchRule("000001", reason="番手捲り率が高い", positions=("番手",))]
    sent = SentLog.load(tmp_path)

    # Without a webhook nothing is marked as sent.
    assert len(notify.notify([race(FORMATION)], rules, sent, None)) == 1
    assert sent.keys == set()

    discord = FakeDiscord()
    assert len(notify.notify([race(FORMATION)], rules, sent, discord)) == 1
    content, [embed] = discord.messages[0]
    assert "1 件" in content
    assert embed["title"].startswith("武雄 3R　Ａ級予選　10/6（火）15:20")
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["選手"] == "1番 選手1（追）"
    assert fields["位置"] == "番手（競り）"
    assert fields["並び"] == "2(13)56 / 74"
    assert fields["理由"] == "番手捲り率が高い"

    # The next run does not repeat it.
    assert notify.notify([race(FORMATION)], rules, sent, discord) == []
    assert len(discord.messages) == 1


def test_sent_log_prunes_old_races(tmp_path):
    sent = SentLog.load(tmp_path)
    sent.keys = {"2026-09-20|84|1|x", "2026-10-06|84|3|x"}
    sent.save(keep_since=date(2026, 9, 29))
    assert json.loads((tmp_path / "notify" / "sent.json").read_text()) == ["2026-10-06|84|3|x"]
    assert SentLog.load(tmp_path).keys == {"2026-10-06|84|3|x"}


def test_discord_splits_more_than_ten_embeds():
    posts = []

    def handler(request):
        posts.append(json.loads(request.content))
        return httpx.Response(204)

    Discord("https://discord.example/webhook", transport=httpx.MockTransport(handler)).send(
        "見出し", [{"title": str(i)} for i in range(12)]
    )
    assert [len(p["embeds"]) for p in posts] == [10, 2]
    assert posts[0]["content"] == "見出し" and posts[1]["content"] == ""
    assert posts[0]["allowed_mentions"] == {"parse": []}


def test_cli_collect_lines_notifies(tmp_path, monkeypatch):
    (tmp_path / "watchlist.toml").write_text(
        '[[watch]]\nracer_id = "015000"\nreason = "テスト"\npositions = ["番手"]\n',
        encoding="utf-8",
    )

    class FakeKeirin:
        request_count = 0

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def get(self, type_, **params):
            if type_ == "JSJ057":
                return {"kInfo": [{"encPrm": "m", "KeirinCd": "84", "jyoName": "武雄"}]}
            return {
                "rInfo": [
                    {
                        "raceNo": 3,
                        "syumoku": "Ａ級予選",
                        "stTime": "15:20",
                        "sInfo": [
                            {
                                "syaban": 1,
                                "senNo": "014000",
                                "senName": "先頭　選手",
                                "kyaku": "逃",
                            },
                            {
                                "syaban": 2,
                                "senNo": "015000",
                                "senName": "番手　選手",
                                "kyaku": "追",
                            },
                        ],
                        "nInfo": [
                            {"syaban": 1, "narabiX": 1, "narabiY": 1},
                            {"syaban": 2, "narabiX": 2, "narabiY": 1},
                        ],
                    }
                ]
            }

    discord = FakeDiscord()
    monkeypatch.setattr(cli, "KeirinClient", FakeKeirin)
    monkeypatch.setattr(cli, "_discord", lambda: discord)
    monkeypatch.setattr(cli, "today_jst", lambda: DAY)
    args = ["collect-lines", "--days", "1", "--data-dir", str(tmp_path)]
    assert cli.main([*args, "--watchlist", str(tmp_path / "watchlist.toml")]) == 0
    [(content, [embed])] = discord.messages
    assert {f["name"]: f["value"] for f in embed["fields"]}["選手"] == "2番 番手 選手（追）"
    assert (tmp_path / "notify" / "sent.json").exists()
