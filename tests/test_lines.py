import csv
from datetime import date

import httpx
import pytest

from keirin import lines, storage
from keirin.client import KeirinApiError
from keirin.lines import Formation, from_ninfo, from_oddspark

DAY = date(2026, 10, 4)


def oddspark_block(tokens: str) -> str:
    """Markup shaped like Oddspark's line block: digits = cars, "(" ")" = contest, "/" = break."""
    items = ['<li class="sirusi"><span class="hidari">←</span></li>']
    for t in tokens:
        if t.isdigit():
            items.append(f'<li><span class="no{t}">{t}</span>追込</li>')
        else:
            items.append(f'<li><span class="no0">{"&nbsp;" if t == "/" else t}</span></li>')
    return '<ul class="keirinRyosouline">' + "".join(items) + "</ul>"


@pytest.mark.parametrize(
    ("tokens", "text"),
    [
        ("2(13)56/74", "2(13)56/74"),  # contested 番手 behind 2, two lines
        ("52/36/4(17)", "52/36/4(17)"),
        ("137/42/86/5", "137/42/86/5"),  # 単騎 at the end
    ],
)
def test_from_oddspark(tokens, text):
    assert from_oddspark(oddspark_block(tokens)).text() == text


def test_from_oddspark_without_block():
    assert from_oddspark(None) is None
    assert lines.extract_oddspark_block("<html>no formation</html>") is None
    page = "<div>" + oddspark_block("12/3") + '<div class="keirinRyosousouhyo">短評</div></div>'
    assert from_oddspark(lines.extract_oddspark_block(page)).text() == "12/3"


def test_from_ninfo_reads_positions_gaps_and_contests():
    # 奈良10R on 2026-10-05: cars 1 and 7 share narabiX 8 (contested), gaps split lines.
    ninfo = [
        {"syaban": 1, "narabiX": 8, "narabiY": 1},
        {"syaban": 2, "narabiX": 2, "narabiY": 1},
        {"syaban": 3, "narabiX": 4, "narabiY": 1},
        {"syaban": 4, "narabiX": 7, "narabiY": 1},
        {"syaban": 5, "narabiX": 1, "narabiY": 1},
        {"syaban": 6, "narabiX": 5, "narabiY": 1},
        {"syaban": 7, "narabiX": 8, "narabiY": 2},
    ]
    assert from_ninfo(ninfo).text() == "52/36/4(17)"
    assert from_ninfo([]) is None


def test_rows():
    rows = Formation((((2,), (1, 3), (5,), (6,)), ((7,), (4,)))).rows(
        {"date": "2026-10-04", "venue_code": "84", "race_no": 3}, "oddspark"
    )
    by_car = {r["car_no"]: r for r in rows}
    assert [r["car_no"] for r in rows] == [1, 2, 3, 4, 5, 6, 7]
    assert (by_car[2]["line_no"], by_car[2]["line_pos"], by_car[2]["line_size"]) == (1, 1, 5)
    assert by_car[1]["contested"] and by_car[3]["contested"] and not by_car[2]["contested"]
    assert (by_car[1]["line_pos"], by_car[5]["line_pos"]) == (2, 3)
    assert (by_car[4]["line_no"], by_car[4]["line_pos"], by_car[4]["line_size"]) == (2, 2, 2)
    assert {r["formation"] for r in rows} == {"2(13)56/74"}


class FakeKeirin:
    def __init__(self, ninfo_by_race):
        self.ninfo_by_race = ninfo_by_race
        self.calls = []

    def get(self, type_, **params):
        self.calls.append(type_)
        if type_ == "JSJ057":
            return {"kInfo": [{"encPrm": "m", "KeirinCd": "53"}]}
        return {
            "rInfo": [
                {"raceNo": no, "nInfo": ninfo, "line": "二分戦", "seri": 0}
                for no, ninfo in self.ninfo_by_race.items()
            ]
        }


NINFO = [{"syaban": 1, "narabiX": 1, "narabiY": 1}, {"syaban": 2, "narabiX": 2, "narabiY": 1}]


def read_lines(data_dir, day):
    with storage.table_path(data_dir, "lines", day).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_collect_upcoming_keeps_the_first_capture(tmp_path):
    assert lines.collect_upcoming(FakeKeirin({1: NINFO, 2: []}), tmp_path, DAY) == 1
    assert [r["car_no"] for r in read_lines(tmp_path, DAY)] == ["1", "2"]
    # Later the finished race 1 has no formation any more and race 2 got one.
    later = [{"syaban": 2, "narabiX": 1, "narabiY": 1}, {"syaban": 1, "narabiX": 3, "narabiY": 1}]
    assert lines.collect_upcoming(FakeKeirin({1: [], 2: later}), tmp_path, DAY) == 1
    rows = read_lines(tmp_path, DAY)
    assert {(r["race_no"], r["formation"]) for r in rows} == {("1", "12"), ("2", "2/1")}


def test_collect_upcoming_writes_nothing_before_publication(tmp_path):
    assert lines.collect_upcoming(FakeKeirin({1: []}), tmp_path, DAY) == 0
    assert not lines.lines_raw_path(tmp_path, DAY).exists()


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def write_races(data_dir, day, races):
    storage.write_tables(
        data_dir,
        day,
        {"races": [{"date": day.isoformat(), "venue_code": v, "race_no": r} for v, r in races]},
    )


def test_backfill_fetches_missing_races_with_crawl_delay(tmp_path):
    write_races(tmp_path, DAY, [("84", 1), ("84", 2), ("84", 3)])
    lines.save_captures(tmp_path, DAY, {"84-1": {"source": "keirin.jp", "data": {"nInfo": NINFO}}})
    pages = {"2": oddspark_block("12/3"), "3": "<html>no formation</html>"}
    seen = []

    def handler(request):
        seen.append(request.url.params["raceNo"])
        assert request.url.params["joCode"] == "84" and request.url.params["kaisaiBi"] == "20261004"
        return httpx.Response(200, text=pages[request.url.params["raceNo"]])

    fake = FakeTime()
    client = lines.OddsparkClient(
        transport=httpx.MockTransport(handler), sleep=fake.sleep, clock=fake.clock
    )
    assert lines.backfill(client, tmp_path, [DAY], budget_seconds=3600, clock=fake.clock) == 2
    assert seen == ["2", "3"]
    assert fake.now == pytest.approx(10.0)  # one Crawl-delay between the two pages
    rows = read_lines(tmp_path, DAY)
    assert {(r["race_no"], r["source"]) for r in rows} == {("1", "keirin.jp"), ("2", "oddspark")}
    captures = lines.read_captures(tmp_path, DAY)
    assert captures["84-3"]["data"] is None  # remembered as unavailable: not fetched again
    assert lines.backfill(client, tmp_path, [DAY], budget_seconds=3600, clock=fake.clock) == 0


def test_backfill_stops_at_the_time_budget(tmp_path):
    write_races(tmp_path, DAY, [("84", r) for r in range(1, 8)])
    fake = FakeTime()
    client = lines.OddsparkClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=oddspark_block("1/2"))),
        sleep=fake.sleep,
        clock=fake.clock,
    )
    # Pages at t=0, 10, 20, 30 s; a fifth would end after the 35 s budget.
    assert lines.backfill(client, tmp_path, [DAY], budget_seconds=35, clock=fake.clock) == 4
    assert len(lines.read_captures(tmp_path, DAY)) == 4  # progress is saved


def test_oddspark_refusal_stops(tmp_path):
    client = lines.OddsparkClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(KeirinApiError, match="403"):
        client.race_page("84", DAY, 1)


def test_missing_main_days(tmp_path):
    storage.write_raw(tmp_path, {"date": "2026-10-03", "responses": []})
    assert lines.missing_main_days(tmp_path, date(2026, 10, 2), date(2026, 10, 3)) == [
        date(2026, 10, 2)
    ]
