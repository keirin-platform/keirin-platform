import csv
import inspect
import json
import math
import re
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


def test_research_uses_only_public_predict_names():
    """research.py shares predict's helpers; private ones could change without notice."""
    assert re.findall(r"predict\._\w+", inspect.getsource(research)) == []


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
        ((0.01, 0.04), (0.02, 0.03), "実質的な差なし"),  # excludes 0 but negligible
        ((-0.04, -0.01), (-0.02, -0.03), "実質的な差なし"),
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


# --- car numbers ---------------------------------------------------------------------------


def write_cards(data_dir, day, meetings):
    """meetings: {venue: (time_slot, {race_no: (race_class, [entry rows])})}."""
    meeting_rows, race_rows, entries = [], [], []
    for venue, (slot, races) in meetings.items():
        meeting_rows.append({"date": day.isoformat(), "venue_code": venue, "time_slot": slot})
        for race_no, (race_class, rows) in races.items():
            race_rows.append(
                {"date": day.isoformat(), "venue_code": venue, "race_no": race_no,
                 "race_class": race_class, "entries": len(rows)}
            )  # fmt: skip
            entries += [
                {"date": day.isoformat(), "venue_code": venue, "race_no": race_no, **r}
                for r in rows
            ]
    storage.write_tables(
        data_dir, day, {"meetings": meeting_rows, "races": race_rows, "entries": entries}
    )


def card(scores, finish=None):
    """Riders 1..n with these scores; `finish` lists the finishing car numbers in order."""
    order = finish or list(range(1, len(scores) + 1))
    pos = {car: i + 1 for i, car in enumerate(order)}
    return [full_card(car, s, pos.get(car)) for car, s in enumerate(scores, 1)]


def cards_of(tmp_path, meetings):
    write_cards(tmp_path, DAY, meetings)
    return research.load_cards(tmp_path, DAY, DAY + timedelta(days=1))


def test_score_order_summary(tmp_path):
    cards = cards_of(
        tmp_path,
        {
            "01": ("midnight", {1: ("Ａ級一般", card([95, 90, 85, 80, 75, 70, 65])),
                                2: ("Ａ級一般", card([90, 95, 85, 80, 75, 70, 65]))}),
            "02": ("night", {1: ("Ａ級一般", card([95, 90, 85, 80, 75, 70, 65]))}),
        },
    )  # fmt: skip
    summary = research.score_order_summary(cards)
    assert summary == {"races": 2, "car_order_equals_score_rank": 0.5, "car1_is_top_score": 0.5}


def test_convention_summary(tmp_path):
    nine = card([90, 89, 88, 80, 85, 80, 85, 90, 85])  # 4<5, 6<7 hold; 8<9 does not
    seven_cars = card([90, 89, 88, 87, 86, 80, 85])  # 5<6 does not hold, 6<7 holds
    cards = cards_of(
        tmp_path,
        {
            "01": ("day", {1: ("Ｓ級予選", nine), 2: ("Ａ級予選", seven_cars)}),
            "02": ("midnight", {1: ("Ａ級一般", card([95, 90, 85, 80, 75, 70, 65]))}),
        },
    )
    summary = research.convention_summary(cards)
    assert summary["9車"] == {
        "4<5": {"races": 1, "share": 1.0},
        "6<7": {"races": 1, "share": 1.0},
        "8<9": {"races": 1, "share": 0.0},
    }
    assert summary["7車"] == {"5<6": {"races": 1, "share": 0.0}, "6<7": {"races": 1, "share": 1.0}}


def test_car1_effect_counts_top_scorer_only(tmp_path):
    cards = cards_of(
        tmp_path,
        {
            "01": ("day", {
                1: ("Ａ級予選", card([95, 90, 85, 80, 75, 70, 65])),  # car 1 tops and wins
                2: ("Ａ級予選", card([85, 90, 95, 80, 75, 70, 65], [2, 3, 1, 4, 5, 6, 7])),
                3: ("Ａ級予選", card([95, 95, 85, 80, 75, 70, 65])),  # tie at the top
                4: ("Ｌ級ガ予", card([95, 90, 85, 80, 75, 70, 65])),  # girls
            }),
            "02": ("midnight", {1: ("Ａ級一般", card([95, 90, 85, 80, 75, 70, 65]))}),
        },
    )  # fmt: skip
    effect = research.car1_effect(cards, n_boot=50)
    assert (effect["car1_races"], effect["other_races"]) == (1, 1)
    assert (effect["car1_win"], effect["other_win"], effect["diff"]) == (1.0, 0.0, 1.0)


def test_car1_effect_leaves_scratched_riders_out(tmp_path):
    riders = card([95, 90, 85, 80, 75, 70, 65], [2, 3, 4, 5, 6, 7])
    riders[0]["notes"] = "欠場"
    cards = cards_of(tmp_path, {"01": ("day", {1: ("Ａ級予選", riders)})})
    effect = research.car1_effect(cards, n_boot=50)
    assert (effect["car1_races"], effect["other_races"], effect["other_win"]) == (0, 1, 1.0)


def test_car1_effect_skips_races_without_three_placed(tmp_path):
    scores = [95, 90, 85, 80, 75, 70, 65]
    cancelled = [full_card(car, s, None) for car, s in enumerate(scores, 1)]
    cards = cards_of(
        tmp_path,
        {
            "01": ("day", {
                1: ("Ａ級予選", cancelled),
                2: ("Ａ級予選", card(scores, [2, 3])),  # two placed, the rest fell
                3: ("Ａ級予選", card(scores, [2, 3, 1])),  # car 1 tops and finishes 3rd
            }),
        },
    )  # fmt: skip
    effect = research.car1_effect(cards, n_boot=50)
    assert (effect["car1_races"], effect["car1_win"]) == (1, 0.0)


def test_card_stats_keep_races_without_a_result(tmp_path):
    scores = [95, 90, 85, 80, 75, 70, 65]
    cancelled = [full_card(car, s, None) for car, s in enumerate(scores, 1)]
    cards = cards_of(tmp_path, {"01": ("midnight", {1: ("Ａ級一般", cancelled)})})
    assert research.score_order_summary(cards)["races"] == 1  # the card is set either way


def test_cli_car_order_prints_json(tmp_path, capsys):
    write_cards(
        tmp_path,
        DAY,
        {"01": ("midnight", {1: ("Ａ級一般", card([95, 90, 85, 80, 75, 70, 65]))}),
         "02": ("day", {1: ("Ａ級予選", card([85, 90, 95, 80, 75, 70, 65]))})},
    )  # fmt: skip
    args = ["car-order", "--date", DAY.isoformat(), "--to", DAY.isoformat(), "--boot", "50"]
    assert cli.main([*args, "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    result = json.loads(out)
    assert set(result) == {"period", "score_order", "convention", "car1_effect"}
    assert result["score_order"]["races"] == 1
    assert "000001" not in out and "テスト" not in out


# --- chase break ---------------------------------------------------------------------------


def chase_day(tmp_path, text, riders, race_class="Ａ級予選"):
    write_day(tmp_path, DAY, {("01", 1): (race_class, riders)}, formation_rows("01", 1, text))
    return research.load_chase_rows(tmp_path, DAY, DAY + timedelta(days=1))


def makuri_winner(**finish):
    riders = seven(**finish)
    riders[0]["kimarite"] = "捲り"
    return riders


def test_chase_rows_are_the_riders_behind_the_winner(tmp_path):
    rows = chase_day(tmp_path, "123/45/67", makuri_winner())
    assert [r.winner_kimarite for r in rows] == ["捲り", "捲り"]
    assert [r.strong for r in rows] == [False, False]  # cars 2 and 3 finish 2nd and 3rd
    assert rows[0].stratum != rows[1].stratum  # one and two places behind


def test_chase_row_is_strong_when_outside_the_top_3(tmp_path):
    rows = chase_day(tmp_path, "123/45/67", makuri_winner(c2=6, c3=2, c6=3))
    assert [r.strong for r in rows] == [True, False]


def test_chase_skips_girls_contested_and_unplaced(tmp_path):
    assert chase_day(tmp_path, "1/2/3/4/5/6/7", makuri_winner(), "Ｌ級ガ予") == []
    assert chase_day(tmp_path, "(12)3/45/67", makuri_winner()) == []  # winner contests
    riders = makuri_winner(c3=None)
    riders[2]["finish"] = riders[2]["finish_pos"] = ""
    rows = chase_day(tmp_path, "123/45/67", riders)
    assert len(rows) == 1  # car 3 has no placed finish


def test_chase_closes_the_gap_of_a_scratched_rider(tmp_path):
    riders = makuri_winner(c2=None)
    riders[1]["notes"] = "欠場"
    rows = chase_day(tmp_path, "123/45/67", riders)
    assert len(rows) == 1  # car 3 moves up to one place behind the winner


def synthetic_chase(p_makuri, p_nige, *, races=1500, rider_effect=None, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(races):
        kimarite = "捲り" if i % 2 == 0 else "逃げ"
        rider = f"R{rng.integers(0, 150)}"
        p = p_makuri if kimarite == "捲り" else p_nige
        if rider_effect and kimarite == "捲り":
            p = min(max(p + rider_effect[rider], 0.01), 0.99)
        stratum = int(rng.integers(0, 4))
        out.append(
            research.ChaseRow(
                DAY + timedelta(days=i % 100), i, rider, kimarite, stratum, bool(rng.random() < p)
            )
        )
    return out


def test_chase_effect_detects_a_makuri_difference():
    result = research.chase_effect(synthetic_chase(0.55, 0.40), n_boot=300)
    assert result["decision"] == "正の効果"
    assert result["standardized_difference"] == pytest.approx(0.15, abs=0.05)
    assert result["first_half"] > 0 and result["second_half"] > 0


def test_chase_effect_without_a_difference_is_not_positive():
    result = research.chase_effect(synthetic_chase(0.45, 0.45), n_boot=300)
    assert result["decision"] != "正の効果"


def test_chase_individual_finds_rider_differences():
    rng = np.random.default_rng(1)
    effects = {f"R{i}": float(rng.normal(0, 0.12)) for i in range(150)}
    rows = synthetic_chase(0.5, 0.5, races=9000, rider_effect=effects)
    result = research.chase_individual(rows, n_perm=300)
    assert result["decision"] == "個人差あり"
    assert result["split_half"]["r"] > 0


def test_chase_individual_without_differences_is_pending():
    result = research.chase_individual(synthetic_chase(0.5, 0.5, races=9000), n_perm=300)
    assert result["decision"] == "保留"


def test_cli_chase_break_prints_aggregates_only(tmp_path, capsys):
    write_day(
        tmp_path, DAY, {("01", 1): ("Ａ級予選", makuri_winner())},
        formation_rows("01", 1, "123/45/67"),
    )  # fmt: skip
    code = cli.main(
        ["chase-break", "--date", DAY.isoformat(), "--to", DAY.isoformat(),
         "--boot", "20", "--data-dir", str(tmp_path)]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "effect" in json.loads(out) and "individual" in json.loads(out)
    assert "000001" not in out and "テスト" not in out  # no rider ids or names


def synthetic_model(*, own=0.0, opp=0.0, gap=0.0, races=9000, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(races):
        own_size = int(rng.integers(2, 5))
        opp_max = int(rng.integers(1, 5))
        score_gap = float(rng.normal(0, 6))
        rank = int(rng.integers(1, 10))
        eta = -0.3 + 0.2 * (rank - 5) + own * own_size + opp * opp_max + gap * score_gap / 5
        strong = bool(rng.random() < 1 / (1 + math.exp(-eta)))
        out.append(
            research.ChaseRow(
                DAY + timedelta(days=i % 100), i, f"R{i % 150}", "捲り", 0, strong,
                1, rank, bool(i % 2), own_size, opp_max, score_gap,
            )
        )  # fmt: skip
    return out


def test_chase_model_separates_the_factors():
    rows = synthetic_model(own=0.0, opp=0.35, gap=-0.3)
    result = research.chase_model(rows, n_boot=100)
    factors = result["factors"]
    assert factors["opp_max"]["decision"] == "正の効果"
    assert factors["gap"]["decision"] == "負の効果"
    assert factors["own_size"]["decision"] == "実質的な差なし"
    assert factors["opp_max"]["effect_per_unit"] == pytest.approx(0.07, abs=0.03)


def test_chase_model_drops_cases_without_an_opponent_line():
    rows = synthetic_model(races=300)
    rows += [research.ChaseRow(DAY, 9999, "X", "捲り", 0, True, 1, 1, False, 2, 0, math.nan)]
    assert research.chase_model(rows, n_boot=5)["cases"] == 300


def test_loader_fills_the_line_factors(tmp_path):
    rows = chase_day(tmp_path, "123/45/67", makuri_winner())
    assert {r.own_size for r in rows} == {3}
    assert {r.opp_max for r in rows} == {2}
    assert rows[0].gap == pytest.approx(3.0)  # winner scores 89, best outside the line 86


# --- line positions --------------------------------------------------------------------------


def test_order_probabilities_match_the_prediction_model():
    rng = np.random.default_rng(5)
    theta = rng.normal(scale=0.3, size=N_PARAMS)
    for n in (3, 7, 9):
        x = rng.normal(size=(n, K))
        orders = predict.top3_order_probabilities(theta, x)
        assert orders.sum() == pytest.approx(1.0)
        assert orders[0, 0].sum() == 0.0 and orders[1, :, 1].sum() == 0.0  # no rider twice
        prob, pairs = predict.expected_places(theta, x)
        assert np.allclose(prob, predict.race_probabilities(theta, x), atol=1e-9)
        assert np.allclose(pairs, research.pair_top3_probabilities(theta, x), atol=1e-9)


def write_payouts(data_dir, day, rows):
    path = storage.table_path(data_dir, "payouts", day)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=storage.TABLES["payouts"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"date": day.isoformat(), "combination": "1-2", **row})


def line_day(tmp_path, text, riders, race_class="Ａ級予選", payouts=()):
    write_day(tmp_path, DAY, {("01", 1): (race_class, riders)}, formation_rows("01", 1, text))
    write_payouts(tmp_path, DAY, [{"venue_code": "01", "race_no": 1, **p} for p in payouts])
    return research.load_line_races(tmp_path, DAY, DAY + timedelta(days=1), model_for("A", "S"))


def test_line_race_reads_the_formation_the_result_and_the_payouts(tmp_path):
    riders = seven()
    riders[0]["bh"], riders[3]["bh"] = "H", "B"
    payouts = [
        {"bet_type": "2車単", "payout": 1230, "popularity": 4},
        {"bet_type": "3連単", "payout": 9870, "popularity": 30},
        {"bet_type": "ワイド", "payout": 200, "popularity": 1},
    ]
    [race] = line_day(tmp_path, "123/45/6", riders, "Ａ級決勝", payouts)
    assert research._cells(race) == [
        "head_3", "second_3", "third_3", "head_2", "second_2", "solo", "solo",
    ]  # fmt: skip
    assert len(set(race.line)) == 4  # car 7 is not in the formation: alone, a line of its own
    assert race.order == (0, 1, 2) and race.final and race.source == "keirin.jp"
    assert (race.home, race.back) == (0, 3)
    assert race.exacta == (1230.0,) and race.trifecta == (9870.0,)
    assert race.prob.sum(axis=0) == pytest.approx([1.0, 2.0, 3.0])


def test_line_race_cells_of_a_contested_line(tmp_path):
    [race] = line_day(tmp_path, "1(23)4/567", seven(), "Ａ級準決勝")
    assert research._cells(race) == [
        "contested_head", "contested", "contested", "contested_other",
        "head_3", "second_3", "third_3",
    ]  # fmt: skip
    assert not race.final and race.back is None


def test_line_race_closes_gaps_and_skips_girls(tmp_path):
    riders = seven(c2=None)
    riders[1]["notes"] = "欠場"
    riders[0]["bh"] = riders[2]["bh"] = "B"  # two riders with B: unknown
    [race] = line_day(tmp_path, "1234/567", riders)
    assert research._cells(race)[:3] == ["head_3", "second_3", "third_3"]
    assert race.back is None and race.order == (0, 1, 2)
    assert line_day(tmp_path, "1/2/3/4/5/6/7", seven(), "Ｌ級ガ予") == []


def made(text, order, *, day=DAY, group="A", back=None, home=None, prefectures=None, **fields):
    """A LineRace for a formation such as "1(23)4/56/7"; entries are the cars 1..n in order
    and every rider is equally strong."""
    rows = sorted(formation_rows("01", 1, text), key=lambda r: r["car_no"])
    n = len(rows)
    pair = np.full((n, n), 6 / (n * (n - 1)))
    np.fill_diagonal(pair, 0.0)
    return research.LineRace(
        day, group, fields.pop("final", False), fields.pop("source", "oddspark"),
        tuple(r["line_pos"] for r in rows), tuple(r["line_size"] for r in rows),
        tuple(r["contested"] for r in rows), tuple(r["line_no"] for r in rows),
        fields.pop("scores", tuple(90.0 - i for i in range(n))),
        fields.pop("riders", tuple(f"R{i}" for i in range(n))),
        prefectures or ("東京",) * n,
        np.tile([1 / n, 2 / n, 3 / n], (n, 1)), pair, order, back, home, **fields,
    )  # fmt: skip


def test_race_columns_count_positions_and_the_back_line():
    race = made(
        "123/45/67", (1, 0, 3), back=0, home=0,
        prefectures=("東京", "東京", "埼玉", "大阪", "福岡", "イギリ", "東京"),
        exacta=(1000.0,),
    )  # fmt: skip
    c = research._race_columns(race)
    assert c["n:head_3"] == 1 and c["n:head_2"] == 2 and c["n:third_3"] == 1
    assert c["win:second_3"] == 1 and c["top3:head_2"] == 1 and c["top2:head_3"] == 1
    assert c["ewin:second_2"] == pytest.approx(2 / 7)
    assert (c["ot:same12"], c["ot:same123"]) == (1, 0)
    assert (c["b:n"], c["b:line_win"], c["b:self_win"], c["b:multi_n"]) == (1, 1, 0, 1)
    assert (c["bl:n"], c["bl:second_win"], c["bl:head_win"], c["bl:head_top3"]) == (1, 1, 0, 1)
    assert c["ah:same:n"] == 1 and c["ah:same:second_win"] == 1
    assert c["pr:prefecture:n"] == 1 and c["pr:prefecture:obs"] == 1  # 東京 - 東京
    assert c["pr:other:n"] == 1 and c["pr:other:obs"] == 0  # 大阪 - 福岡
    assert c["pr:region:n"] == 0  # the foreign rider's pair is left out
    assert c["x2:A7:pn"] == 1 and c["x2:A7:plog"] == pytest.approx(math.log(1000))
    assert c["fav:p:n"] == 1 and c["fav:p:win"] == 0  # entry 0 has the top score
    assert c["rk:second:2:win"] == pytest.approx(1 - 1 / 7)


def test_race_columns_tell_who_took_the_back_after_the_home():
    def who(back):
        c = research._race_columns(made("123/45/67", (0, 1, 2), back=back, home=0))
        return [name for name in ("same", "second", "behind", "other") if c[f"ah:{name}:n"]]

    assert [who(0), who(1), who(2), who(3)] == [["same"], ["second"], ["behind"], ["other"]]
    c = research._race_columns(made("(12)3/45/67", (0, 1, 2), back=0, home=3, exacta=(500.0,)))
    assert c["bl:n"] == 0 and c["b:line_win"] == 1  # a contested head is not a clean head
    assert c["x2:A7:cn"] == 1 and c["fav:c:win"] == 1


def simulated(n=400, **changes):
    """Seconds always win, heads come 2nd, the third of the long line 3rd; solos never place."""
    return [
        made("123/45/6/7", (1, 0, 2), day=DAY + timedelta(days=i // 20), back=0, **changes)
        for i in range(n)
    ]


def test_summary_decides_the_position_effects():
    summary = research.line_position_summary(simulated(), n_boot=100)
    decisions = summary["decisions"]
    assert decisions["second_minus_head_win"]["value"] == pytest.approx(0.5)
    assert decisions["second_minus_head_win"]["decision"] == "正の効果"
    assert decisions["back_line_second_minus_head_win"]["value"] == pytest.approx(1.0)
    assert decisions["third_win"]["decision"] == "負の効果"
    assert decisions["third_top3"]["value"] == pytest.approx(1 - 3 / 7, abs=1e-4)
    assert decisions["solo_top3"]["decision"] == "負の効果"
    assert decisions["solo_win"]["value"] == pytest.approx(-1 / 7, abs=1e-4)
    assert decisions["long_line_front_win"]["decision"] == "正の効果"  # 123 beats 45
    assert decisions["long_line_front_top3"]["value"] == pytest.approx(1.0)
    assert decisions["back_line_winner_share"]["decision"] == "主張と違う"  # always: 1.0
    assert decisions["contested_log_exacta"]["decision"] == "保留"  # no payouts
    assert summary["positions"]["second"]["win"]["rate"] == pytest.approx(0.5)
    assert summary["positions"]["second"]["riders"] == 800
    assert summary["by_line_size"]["second_3"]["win"]["excess"]["value"] > 0.8
    assert summary["winner_share"] == {
        "head": 0.0, "second": 1.0, "third": 0.0, "fourth_plus": 0.0, "solo": 0.0,
        "contested_line": 0.0,
    }  # fmt: skip
    assert summary["one_two"] == {"same_line_first_two": 1.0, "same_line_first_three": 1.0}
    assert summary["back_line"]["lines"] == 400 and summary["back_line"]["second_win"] == 1.0
    assert summary["segments"]["A級"]["races"] == 400
    assert summary["segments"]["S級9車"]["races"] == 0
    assert summary["segments"]["オッズパーク"]["decisions"]["solo_top3"]["decision"] == "負の効果"
    text = json.dumps(summary)
    assert "NaN" not in text and "Infinity" not in text


def test_summary_without_effects_stays_near_zero():
    rng = np.random.default_rng(11)
    races = [
        made(
            "123/45/6/7",
            tuple(int(i) for i in rng.permutation(7)[:3]),
            day=DAY + timedelta(days=i // 50),
            back=int(rng.integers(0, 7)),
        )
        for i in range(2000)
    ]
    summary = research.line_position_summary(races, n_boot=200)
    for name in ("second_minus_head_win", "third_win", "solo_top3", "long_line_front_top3"):
        assert abs(summary["decisions"][name]["value"]) < 0.04
        lo, hi = summary["decisions"][name]["ci95"]
        assert lo < hi and hi - lo < 0.12
    standardized = summary["score_rank"]["standardized"]
    assert abs(standardized["solo"]["top3"]["value"]) < 0.01  # a rank holds one position here
    assert summary["score_rank"]["model_fit"]["7"]["riders"] == 2000


def test_summary_compares_contested_races_and_home_regions():
    races = []
    for i in range(80):
        day = DAY + timedelta(days=i // 4)
        if i % 2:
            races.append(made("1(23)/45/6/7", (3, 4, 5), day=day, exacta=(2000.0,)))
        else:
            races.append(
                made(
                    "12/34/5/6/7",
                    (0, 1, 2),
                    day=day,
                    exacta=(1000.0,),
                    trifecta=(5000.0,),
                    prefectures=("東京", "東京", "大阪", "福岡", "東京", "東京", "東京"),
                )  # fmt: skip
            )
    summary = research.line_position_summary(races, n_boot=100)
    exacta = summary["decisions"]["contested_log_exacta"]
    assert exacta["value"] == pytest.approx(math.log(2), abs=1e-4)
    assert exacta["decision"] == "正の効果"
    assert summary["contested"]["races"] == {"contested": 40, "other": 40}
    assert summary["contested"]["median_exacta"]["7車"]["contested"] == {
        "races": 40, "median": 2000.0,
    }  # fmt: skip
    assert summary["contested"]["log_trifecta"]["value"] is None  # no contested trifecta
    assert summary["contested"]["favourite_win_excess"]["difference"]["value"] == pytest.approx(-1)
    pair = summary["decisions"]["same_prefecture_pair"]
    assert pair["value"] == pytest.approx(1.0) and pair["decision"] == "正の効果"
    assert summary["pairs"]["prefecture"]["pairs"] == 80  # 12 of one race, 45 of the other
    assert summary["pairs"]["other"]["pairs"] == 40
    assert summary["positions"]["contested"]["riders"] == 80
    assert summary["positions"]["contested_head"]["top3"]["rate"] == 0.0


@pytest.mark.parametrize(
    "ci, halves, expected",
    [
        ((0.78, 0.82), (0.79, 0.81), "主張どおり"),
        ((0.60, 0.65), (0.62, 0.63), "主張と違う"),
        ((0.90, 0.95), (0.91, 0.93), "主張と違う"),
        ((0.60, 0.65), (0.62, 0.80), "保留"),
        ((0.72, 0.78), (0.74, 0.76), "保留"),
    ],
)
def test_decide_share(ci, halves, expected):
    assert research.decide_share(ci, halves) == expected


def test_within_rider_compares_the_same_rider_in_two_positions():
    races = []
    for i in range(60):
        names = [f"R{k}" for k in range(7)]
        if i % 2:  # R5 rides alone and loses
            races.append(made("12/34/5/6/7", (0, 1, 2), riders=tuple(names)))
        else:  # R5 heads a line and wins
            names[0], names[5] = names[5], names[0]
            races.append(made("12/34/5/6/7", (0, 1, 2), riders=tuple(names)))
    result = research.within_rider(
        races, research.POSITIONS["solo"], research.POSITIONS["head"], n_boot=50
    )
    assert result["riders"] == 2  # R5 and R0 swap; too few riders for an estimate
    extra = [
        made("12/34/5/6/7", (0, 1, 2), riders=("X", "a", "b", "c", "Y", "d", "e")) for _ in range(6)
    ] + [
        made("12/34/5/6/7", (0, 1, 2), riders=("Y", "a", "b", "c", "X", "d", "e")) for _ in range(6)
    ]
    result = research.within_rider(
        races + extra, research.POSITIONS["solo"], research.POSITIONS["head"], n_boot=50
    )
    assert result["riders"] == 4 and result["min_rides"] == 5
    assert result["win"]["value"] == pytest.approx(-1.0)  # alone: never; as a head: always
    assert result["top3"]["ci95"] == [pytest.approx(-1.0), pytest.approx(-1.0)]


def test_cli_line_positions_prints_aggregates_only(tmp_path, capsys):
    train_day = DAY - timedelta(days=1)
    write_day(tmp_path, train_day, {("01", r): ("Ａ級予選", seven()) for r in range(1, 6)})
    write_day(
        tmp_path,
        DAY,
        {("01", 1): ("Ａ級予選", seven()), ("01", 2): ("Ａ級予選", seven(c1=4, c4=1))},
        formation_rows("01", 1, "123/45/67") + formation_rows("01", 2, "14/23/567"),
    )
    args = ["line-positions", "--date", DAY.isoformat(), "--to", DAY.isoformat(), "--days", "3"]
    assert cli.main([*args, "--min-races", "1", "--boot", "20", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    result = json.loads(out)
    assert result["races"] == 2 and result["model"]["races"] == {"A": 5}
    assert set(result["decisions"]) == {*research.PRIMARY, "back_line_winner_share"}
    assert "000001" not in out and "テスト" not in out and "東京" not in out
    assert "NaN" not in out and "Infinity" not in out
