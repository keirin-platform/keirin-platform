import csv
import json
import math
from datetime import date, timedelta

import numpy as np
import pytest

from keirin import cli, lines, predict, research, storage
from keirin.predict import N_PARAMS, K, Model
from keirin.research import LinkageRace

DAY = date(2026, 10, 5)


def full_card(car, score, finish_pos, **fields):
    return {
        "car_no": car, "racer_id": f"{car:06d}", "racer_name": "テスト", "prefecture": "東京",
        "age": 30, "class": "A1", "prev_class": "A1", "style": "追", "score": score,
        "nige": 0, "makuri": 0, "sashi": 1, "mark": 1, "back": 0, "home": 0, "start": 0,
        "win_rate": 10, "top2_rate": 20, "top3_rate": 30, "finish": str(finish_pos or ""),
        "finish_pos": finish_pos or "", "notes": "", **fields,
    }  # fmt: skip


def write_day(data_dir, day, races, formations=()):
    """races: {(venue, race_no): (race_class, [entry rows])}; formations: lines table rows."""
    entries, race_rows = [], []
    for (venue, race_no), (race_class, rows) in races.items():
        race_rows.append(
            {"date": day.isoformat(), "venue_code": venue, "race_no": race_no,
             "race_class": race_class, "entries": len(rows)}
        )  # fmt: skip
        entries += [
            {"date": day.isoformat(), "venue_code": venue, "race_no": race_no, **r} for r in rows
        ]
    storage.write_tables(data_dir, day, {"entries": entries, "races": race_rows})
    if formations:
        path = storage.table_path(data_dir, "lines", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=lines.COLUMNS)
            writer.writeheader()
            for row in formations:
                writer.writerow({"date": day.isoformat(), **row})


def formation_rows(venue, race_no, text):
    """Lines table rows for a formation such as "2(13)56/74"."""
    rows = []
    for line_no, line in enumerate(text.split("/"), 1):
        positions, i = [], 0
        while i < len(line):
            if line[i] == "(":
                j = line.index(")", i)
                positions.append([int(c) for c in line[i + 1 : j]])
                i = j + 1
            else:
                positions.append([int(line[i])])
                i += 1
        size = sum(len(p) for p in positions)
        for pos, cars in enumerate(positions, 1):
            for car in cars:
                rows.append(
                    {"venue_code": venue, "race_no": race_no, "car_no": car, "line_no": line_no,
                     "line_pos": pos, "line_size": size, "contested": len(cars) > 1,
                     "formation": text, "source": "keirin.jp"}
                )  # fmt: skip
    return rows


def seven(**finish):
    """Seven riders (cars 1..7) finishing in car order unless `finish` says otherwise."""
    return [full_card(car, 90 - car, finish.get(f"c{car}", car)) for car in range(1, 8)]


def model_for(*groups):
    theta = np.zeros(N_PARAMS)
    theta[predict.CARD_FEATURES.index("score")] = 0.1
    return Model({g: theta for g in groups}, {}, None, None)


# --- pair probabilities -----------------------------------------------------------------


def test_pair_probabilities_match_top3():
    rng = np.random.default_rng(3)
    theta = rng.normal(scale=0.3, size=N_PARAMS)
    x = rng.normal(size=(7, K))
    p = research.pair_top3_probabilities(theta, x)
    assert np.allclose(p, p.T)
    assert np.allclose(np.diag(p), 0.0)
    assert p[np.triu_indices(7, 1)].sum() == pytest.approx(3.0, abs=1e-9)
    top3 = predict.race_probabilities(theta, x)[:, 2]
    assert np.allclose(p.sum(axis=1), 2 * top3, atol=1e-9)


def test_pair_probabilities_of_three_riders_are_one():
    rng = np.random.default_rng(4)
    p = research.pair_top3_probabilities(rng.normal(size=N_PARAMS), rng.normal(size=(3, K)))
    assert np.allclose(p, np.ones((3, 3)) - np.eye(3))


# --- loading races with real lines ------------------------------------------------------


def test_load_uses_real_lines_only(tmp_path):
    races = {
        ("01", 1): ("Ａ級予選", seven()),
        ("01", 2): ("Ａ級予選", seven()),
        ("01", 3): ("Ｌ級ガ予", seven()),
    }
    formations = formation_rows("01", 1, "123/45/67") + formation_rows("01", 3, "1/2/3/4/5/6/7")
    write_day(tmp_path, DAY, races, formations)
    loaded = research.load_linkage_races(
        tmp_path, DAY, DAY + timedelta(days=1), model_for("A", "S", "L")
    )
    assert len(loaded) == 1
    assert loaded[0].group == "A" and loaded[0].day == DAY


def load_one(tmp_path, text, riders):
    write_day(tmp_path, DAY, {("01", 1): ("Ａ級予選", riders)}, formation_rows("01", 1, text))
    [race] = research.load_linkage_races(tmp_path, DAY, DAY + timedelta(days=1), model_for("A"))
    return race


def test_contested_riders_are_not_line_mates(tmp_path):
    race = load_one(tmp_path, "2(13)56/74", seven())

    def mates(a, b):  # cars -> rows (riders are in car order)
        return bool(race.same_line[a - 1, b - 1])

    assert mates(2, 1) and mates(2, 3) and mates(5, 6) and mates(7, 4)
    assert not mates(1, 3)  # both contest the position behind car 2
    assert not mates(2, 7)
    assert np.array_equal(race.same_line, race.same_line.T)
    assert race.top3.tolist() == [True, True, True, False, False, False, False]


def test_scratched_rider_is_left_out(tmp_path):
    riders = seven(c2=None)
    riders[1]["notes"] = "欠場"
    race = load_one(tmp_path, "123/45/67", riders)
    assert len(race.top3) == 6
    assert race.same_line[0, 1]  # cars 1 and 3 close the gap left by car 2
    assert race.top3.tolist() == [True, True, True, False, False, False]


# --- statistics ----------------------------------------------------------------------------


def handmade(n, pairs, top3, prob=0.1, day=DAY, group="A"):
    same = np.zeros((n, n), bool)
    for a, b in pairs:
        same[a, b] = same[b, a] = True
    p = np.full((n, n), prob)
    np.fill_diagonal(p, 0.0)
    return LinkageRace(day, group, p, same, np.array(top3))


def test_linkage_excess_counts_observed_minus_expected():
    race = handmade(4, [(0, 1), (2, 3)], [True, True, False, True])
    assert research.linkage_excess(race) == pytest.approx(0.8)


def test_placebo_equals_actual_when_everyone_is_one_line():
    race = handmade(
        5, [(a, b) for a in range(5) for b in range(a + 1, 5)], [True] * 3 + [False] * 2
    )
    rng = np.random.default_rng(0)
    assert research.placebo_excess(race, rng) == pytest.approx(research.linkage_excess(race))


@pytest.mark.parametrize(
    "ci, halves, expected",
    [
        ((0.1, 0.3), (0.2, 0.1), "正の効果"),
        ((-0.3, -0.1), (-0.2, -0.1), "負の効果"),
        ((-0.02, 0.03), (0.01, -0.01), "実質的な差なし"),
        ((0.01, 0.3), (0.2, -0.1), "保留"),
        ((0.1, 0.3), (0.2, -0.1), "保留"),
    ],
)
def test_decide(ci, halves, expected):
    assert research.decide(ci, halves, 0.05) == expected


def test_bootstrap_ci_brackets_the_mean():
    values = np.random.default_rng(1).normal(loc=1.0, size=500)
    lo, hi = research.bootstrap_ci(values, n_boot=500)
    assert lo < values.mean() < hi
    assert hi - lo < 0.3


UNIFORM_PAIR = 1 / 7  # 7 riders, equal strength: P(both in the top 3) = 5 / 35
LINES_7 = [(0, 1), (0, 2), (1, 2), (3, 4), (5, 6)]  # 123/45/67


def test_summary_detects_simulated_linkage():
    races = []
    for i in range(400):
        top3 = [True, True, True, False, False, False, False]  # line 123 sweeps
        if i % 2:
            top3 = [False, False, False, True, True, True, False]  # 45 together, 6 alone
        races.append(handmade(7, LINES_7, top3, UNIFORM_PAIR, DAY + timedelta(days=i // 20)))
    summary = research.linkage_summary(races, n_boot=300)
    assert summary["groups"]["all"]["decision"] == "正の効果"
    assert summary["groups"]["all"]["excess_per_race"] == pytest.approx(2 - 5 / 7, abs=1e-4)
    assert summary["groups"]["all"]["races"] == 400
    assert summary["groups"]["all"]["pairs"] == 2000


def test_summary_without_linkage_is_not_positive():
    rng = np.random.default_rng(7)
    races = []
    for i in range(400):
        top3 = np.zeros(7, bool)
        top3[rng.choice(7, 3, replace=False)] = True
        races.append(
            handmade(7, LINES_7, top3.tolist(), UNIFORM_PAIR, DAY + timedelta(days=i // 20))
        )
    summary = research.linkage_summary(races, n_boot=300)
    assert summary["groups"]["all"]["decision"] != "正の効果"
    assert abs(summary["groups"]["all"]["placebo_excess_per_race"]) < 0.15


def test_summary_splits_groups_and_halves():
    races = [handmade(4, [(0, 1)], [True, True, True, False], 0.5, DAY, "A")]
    races += [handmade(4, [(0, 1)], [True, False, True, True], 0.5, DAY + timedelta(days=9), "S")]
    groups = research.linkage_summary(races, n_boot=50)["groups"]
    assert set(groups) == {"all", "A", "S"}
    assert groups["all"]["first_half"] == pytest.approx(0.5)
    assert groups["all"]["second_half"] == pytest.approx(-0.5)
    assert groups["A"]["second_half"] is None  # no races after the median day
    json.dumps(groups)  # no NaN in the output


# --- command line --------------------------------------------------------------------------


def test_cli_line_linkage_prints_aggregates_only(tmp_path, capsys):
    train_day = DAY - timedelta(days=1)
    write_day(tmp_path, train_day, {("01", r): ("Ａ級予選", seven()) for r in range(1, 6)})
    write_day(
        tmp_path,
        DAY,
        {("01", 1): ("Ａ級予選", seven()), ("01", 2): ("Ａ級予選", seven(c1=4, c4=1))},
        formation_rows("01", 1, "123/45/67") + formation_rows("01", 2, "14/23/567"),
    )
    args = ["line-linkage", "--date", DAY.isoformat(), "--to", DAY.isoformat(), "--days", "3"]
    assert cli.main([*args, "--min-races", "1", "--boot", "50", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["groups"]["all"]["races"] == 2
    assert result["period"] == {"from": DAY.isoformat(), "to": DAY.isoformat()}
    assert "000001" not in out and "テスト" not in out
    assert not math.isnan(result["groups"]["all"]["expected"])
