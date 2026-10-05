import csv
import math
from datetime import date

import numpy as np
import pytest

from keirin import lines, predict, storage
from keirin.predict import CARD_FEATURES, FEATURES, LINE_FEATURES, Model, Race

K = len(FEATURES)
COL = {name: i for i, name in enumerate(FEATURES)}
LN2 = math.log(2)


def params(**values: float) -> np.ndarray:
    """A parameter vector: b_<card feature>, g1_<line feature>, g23_<line feature>, t2, t3."""
    p = np.zeros(K + len(LINE_FEATURES) + 2)
    for name, value in values.items():
        kind, _, feature = name.partition("_")
        if kind == "b":
            p[CARD_FEATURES.index(feature)] = value
        elif kind == "g1":
            p[len(CARD_FEATURES) + LINE_FEATURES.index(feature)] = value
        elif kind == "g23":
            p[K + LINE_FEATURES.index(feature)] = value
        elif name == "t2":
            p[-2] = math.log(value)
        elif name == "t3":
            p[-1] = math.log(value)
    return p


def matrix(n: int, **columns: list[float]) -> np.ndarray:
    x = np.zeros((n, K))
    for name, values in columns.items():
        x[:, COL[name]] = values
    return x


# --- features --------------------------------------------------------------------------


def card(car, **fields):
    base = {
        "car_no": car, "score": None, "win_rate": None, "top2_rate": None, "top3_rate": None,
        "style": "", "back": None, "home": None, "start": None, "nige": None, "makuri": None,
        "sashi": None, "mark": None, "age": None, "prev_class": "", "prefecture": "",
        "notes": "",
    }  # fmt: skip
    return {**base, **fields}


def test_group_of_race_class():
    assert [predict.group_of(c) for c in ("Ａ級一般", "Ｓ級特選", "Ｌ級ガ予", "", None)] == [
        "A", "S", "L", None, None,
    ]  # fmt: skip


def test_feature_matrix_card_features():
    riders = [
        card(1, score=90.0, win_rate=20.0, top2_rate=40.0, top3_rate=50.0, style="逃",
             back=3, home=1, start=0, nige=2, makuri=1, sashi=0, mark=0, age=30,
             prev_class="S2"),
        card(2, score=87.0, win_rate=10.0, top2_rate=20.0, top3_rate=30.0, style="追",
             back=0, home=0, start=1, nige=0, makuri=0, sashi=1, mark=2, age=50,
             prev_class="A1"),
        card(3, style="両"),  # no card stats: imputed 3 points below the lowest score
    ]  # fmt: skip
    by_car = {
        1: {"line_no": 1, "line_pos": 1, "line_size": 2},
        2: {"line_no": 1, "line_pos": 2, "line_size": 2},
        3: {"line_no": 2, "line_pos": 1, "line_size": 1},
    }
    x = predict.feature_matrix(riders, by_car)
    log4, log2 = math.log(4), math.log(2)
    expected = [
        # score no  win  top2 top3 逃 両 back  home  start attack age  prevS
        [3.0, 0, 0.2, 0.4, 0.5, 1, 0, log4, log2, 0.0, log4, -1.0, 1,
         # 2nd 3rd+ solo 2nd*head 3rd*head head_size head_chaser
         0, 0, 0, 0.0, 0.0, 0, 0],
        [0.0, 0, 0.1, 0.2, 0.3, 0, 0, 0.0, 0.0, log2, -log4, 1.0, 0,
         1, 0, 0, 3.0, 0.0, 0, 0],
        [-3.0, 1, 0.0, 0.0, 0.0, 0, 1, 0.0, 0.0, 0.0, 0.0, 0.0, 0,
         0, 0, 1, 0.0, 0.0, 0, 0],
    ]  # fmt: skip
    np.testing.assert_allclose(x, expected, atol=1e-9)


def test_feature_matrix_reads_csv_strings():
    riders = [
        card(1, score="90.0", win_rate="20.0", back="3", age="30", style="逃"),
        card(2, score="0.0", win_rate="", back="", age=""),  # 0.0 = no score yet
    ]
    x = predict.feature_matrix(riders, {})
    np.testing.assert_allclose(x[:, COL["score"]], [1.5, -1.5])
    np.testing.assert_allclose(x[:, COL["no_score"]], [0, 1])
    np.testing.assert_allclose(x[:, COL["win"]], [0.2, 0.0])
    np.testing.assert_allclose(x[:, COL["back"]], [math.log(4), 0.0])
    np.testing.assert_allclose(x[:, COL["age"]], [-1.0, 0.0])


def test_feature_matrix_line_features():
    riders = [
        card(1, score=90.0, style="追"),
        card(2, score=88.0),
        card(3, score=86.0),
        card(4, score=84.0),
        card(5, score=82.0),  # not in the formation: alone
    ]
    by_car = {c: {"line_no": 1, "line_pos": c, "line_size": 4} for c in (1, 2, 3, 4)}
    x = predict.feature_matrix(riders, by_car)
    lines_part = x[:, len(CARD_FEATURES) :]
    # head score (centered) is 90 - 86 = 4
    np.testing.assert_allclose(
        lines_part,
        [
            [0, 0, 0, 0, 0, 2, 1],  # head of a 4-rider line, a chaser
            [1, 0, 0, 4, 0, 0, 0],
            [0, 1, 0, 0, 4, 0, 0],
            [0, 1, 0, 0, 4, 0, 0],
            [0, 0, 1, 0, 0, 0, 0],
        ],
    )


# --- probabilities ---------------------------------------------------------------------


def test_probabilities_of_three_riders():
    x = matrix(3, score=[LN2, 0, 0])  # strengths 2 : 1 : 1
    p = predict.race_probabilities(params(b_score=1.0), x)
    np.testing.assert_allclose(p[:, 0], [0.5, 0.25, 0.25])
    np.testing.assert_allclose(p[:, 1], [5 / 6, 7 / 12, 7 / 12])
    np.testing.assert_allclose(p[:, 2], [1, 1, 1])


def test_probabilities_of_four_riders():
    x = matrix(4, score=[LN2, 0, 0, 0])
    p = predict.race_probabilities(params(b_score=1.0), x)
    np.testing.assert_allclose(p[:, 0], [0.4, 0.2, 0.2, 0.2])
    np.testing.assert_allclose(p[:, 2], [0.9, 0.7, 0.7, 0.7])


def test_second_stage_temperature_flattens_the_second_place():
    x = matrix(4, score=[LN2, 0, 0, 0])
    p = predict.race_probabilities(params(b_score=1.0, t2=0.5), x)
    # P(1 second) = 0.6 * sqrt(2) / (sqrt(2) + 3) instead of 0.6 * 2 / 4
    assert p[0, 1] == pytest.approx(0.4 + 0.6 * math.sqrt(2) / (math.sqrt(2) + 2))


def test_place_line_effects_do_not_change_the_win():
    x = matrix(4, third_plus=[0, 0, 0, 1])
    p = predict.race_probabilities(params(g23_third_plus=math.log(3)), x)
    np.testing.assert_allclose(p[:, 0], [0.25] * 4)
    # rider 4 misses the top 3 only if it is picked last: 0.75 * 2/5 * 1/4
    np.testing.assert_allclose(p[:, 2], [(3 - 0.925) / 3] * 3 + [0.925])


# --- fitting ---------------------------------------------------------------------------


def test_objective_gradient_matches_finite_differences():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(6, 5, K))
    mask = np.ones((6, 5), bool)
    mask[0, 4] = mask[3, 3:] = False
    order = np.array([[0, 2, 1], [4, 3, -1], [1, 0, 2], [2, 1, 0], [3, 4, 2], [0, 1, 4]])
    theta = rng.normal(scale=0.3, size=K + len(LINE_FEATURES) + 2)
    _, grad = predict._objective(theta, x, mask, order, 1e-3)
    eps = 1e-6
    numeric = []
    for i in range(len(theta)):
        step = np.zeros_like(theta)
        step[i] = eps
        up, _ = predict._objective(theta + step, x, mask, order, 1e-3)
        down, _ = predict._objective(theta - step, x, mask, order, 1e-3)
        numeric.append((up - down) / (2 * eps))
    np.testing.assert_allclose(grad, numeric, rtol=1e-5, atol=1e-7)


def simulate(truth: np.ndarray, races: int, field: int, seed: int = 0) -> list[Race]:
    """Draws finishing orders from the model itself (stage by stage)."""
    kc, kl = len(CARD_FEATURES), len(LINE_FEATURES)
    beta, g1, g23 = truth[:kc], truth[kc:K], truth[K : K + kl]
    t2, t3 = np.exp(truth[-2:])
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(races):
        x = rng.normal(size=(field, K))
        u1, u23 = x @ np.concatenate([beta, g1]), x @ np.concatenate([beta, g23])
        left, order = list(range(field)), []
        for u in (u1, t2 * u23, t3 * u23):
            w = np.exp(u[left] - u[left].max())
            order.append(left.pop(rng.choice(len(left), p=w / w.sum())))
        out.append(Race("A", x, tuple(order), date(2026, 9, 1)))
    return out


def test_fit_recovers_the_simulated_effects():
    truth = params(b_score=1.0, b_win=-0.5, g23_second=0.8, t3=0.5)
    model = predict.fit(simulate(truth, races=1500, field=7))
    fitted = model.params["A"]
    assert fitted[CARD_FEATURES.index("score")] == pytest.approx(1.0, abs=0.12)
    assert fitted[CARD_FEATURES.index("win")] == pytest.approx(-0.5, abs=0.12)
    assert fitted[K + LINE_FEATURES.index("second")] == pytest.approx(0.8, abs=0.25)
    assert math.exp(fitted[-1]) == pytest.approx(0.5, abs=0.15)
    assert model.races == {"A": 1500}


def test_fit_skips_groups_with_too_few_races():
    truth = params(b_score=1.0)
    races = simulate(truth, races=150, field=7)
    races += [Race("L", r.x, r.order, r.day) for r in simulate(truth, races=20, field=7)]
    model = predict.fit(races, min_races=100)
    assert set(model.params) == {"A"}
    assert model.probabilities("L", races[0].x) is None


def test_fit_with_selected_columns_ignores_the_others():
    truth = params(b_score=1.0, b_win=1.0)
    model = predict.fit(simulate(truth, races=300, field=7), columns=("score",))
    fitted = model.params["A"]
    assert fitted[CARD_FEATURES.index("score")] > 0.5
    assert fitted[CARD_FEATURES.index("win")] == 0


# --- training data from the tables -----------------------------------------------------


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


def test_load_races_from_tables(tmp_path):
    day = date(2026, 9, 1)
    races = {
        ("11", 1): ("Ａ級一般", [full_card(1, 90.0, 2, style="逃"), full_card(2, 88.0, 1),
                               full_card(3, 86.0, 3), full_card(4, 84.0, 4)]),
        ("11", 2): ("Ａ級一般", [full_card(1, 90.0, 1), full_card(2, 88.0, None, notes="欠場"),
                               full_card(3, 86.0, 2), full_card(4, 84.0, 3)]),
        ("11", 3): ("Ａ級一般", [full_card(1, "", 1), full_card(2, 88.0, 2),
                               full_card(3, 86.0, 3)]),  # card missing
        ("11", 4): ("Ｌ級ガ予", [full_card(1, 52.0, 3), full_card(2, 51.0, 1),
                               full_card(3, 50.0, 2)]),
        ("11", 5): ("Ａ級一般", [full_card(1, 90.0, 1), full_card(2, 88.0, 2),
                               full_card(3, 86.0, None, notes="落車")]),  # 2 placed
        ("11", 6): ("", [full_card(1, 90.0, 1), full_card(2, 88.0, 2), full_card(3, 86.0, 3)]),
    }  # fmt: skip
    # Race 1's captured formation: car 2 in front, car 1 behind it (the guess would lead with 1).
    formation = [
        {"venue_code": "11", "race_no": 1, "car_no": c, "line_no": ln, "line_pos": lp,
         "line_size": ls, "contested": False, "formation": "21/3/4", "source": "keirin.jp"}
        for c, ln, lp, ls in ((1, 1, 2, 2), (2, 1, 1, 2), (3, 2, 1, 1), (4, 3, 1, 1))
    ]  # fmt: skip
    write_day(tmp_path, day, races, formation)
    write_day(tmp_path, date(2026, 9, 2), {("11", 1): races[("11", 1)]})

    loaded = predict.load_races(tmp_path, end=date(2026, 9, 2))
    assert [(r.group, len(r.x), r.order) for r in loaded] == [
        ("A", 4, (1, 0, 2)),
        ("A", 3, (0, 1, 2)),  # the scratched rider is left out
        ("L", 3, (1, 2, 0)),
    ]
    first = loaded[0].x
    assert first[0, COL["second"]] == 1 and first[1, COL["second"]] == 0  # from the lines table
    # race 2 has no captured formation: guessed (all 東京 chasers; car 1 leads, then by score)
    assert loaded[1].x[:, COL["second"]].tolist() == [0, 1, 0]
    assert all(r.day == day for r in loaded)
    assert len(predict.load_races(tmp_path, start=date(2026, 9, 2))) == 1


# --- evaluation ------------------------------------------------------------------------


def test_evaluate_scores_the_predictions():
    x = matrix(3, score=[LN2, 0, 0])  # 0.5 / 0.25 / 0.25 to win
    model = Model({"A": params(b_score=1.0)}, {"A": 2}, None, None)
    races = [Race("A", x, (0, 1, 2), None), Race("A", x, (1, 0, 2), None)]
    result = predict.evaluate(model, races)
    assert result["A"].races == 2
    assert result["A"].log_loss == pytest.approx((-math.log(0.5) - math.log(0.25)) / 2)
    # (0.25 + 0.0625 + 0.0625) + (0.25 + 0.5625 + 0.0625) over 6 riders
    assert result["A"].brier_win == pytest.approx(1.25 / 6)
    assert result["A"].brier_top3 == pytest.approx(0.0)
    assert result["all"].races == 2
    win = {b.lo: b for b in result["A"].win_bins}
    assert (win[0.45].n, win[0.45].predicted, win[0.45].observed) == (2, 0.5, 0.5)


def test_evaluate_leaves_out_groups_without_a_model():
    model = Model({"A": params(b_score=1.0)}, {"A": 2}, None, None)
    races = [Race("S", matrix(3, score=[1, 0, 0]), (0, 1, 2), None)]
    assert predict.evaluate(model, races) == {}


# --- predicting a card ------------------------------------------------------------------


def test_predict_race_leaves_out_scratched_riders_and_closes_the_line():
    model = Model({"A": params(b_score=1.0)}, {"A": 1}, date(2026, 9, 1), date(2026, 9, 30))
    rows = [
        card(1, score=90.0, style="逃"),
        card(2, score=88.0),
        card(3, score=86.0),
        card(4, score=84.0, notes="欠場"),
    ]
    by_car = {
        1: {"line_no": 1, "line_pos": 1, "line_size": 3, "contested": False},
        4: {"line_no": 1, "line_pos": 2, "line_size": 3, "contested": False},
        2: {"line_no": 1, "line_pos": 3, "line_size": 3, "contested": False},
        3: {"line_no": 2, "line_pos": 1, "line_size": 1, "contested": False},
    }
    pred = predict.predict_race(model, "Ａ級一般", rows, by_car)
    assert pred.cars == [1, 2, 3]
    assert pred.scratched == [4]
    assert not pred.guessed
    assert (pred.by_car[2]["line_pos"], pred.by_car[2]["line_size"]) == (2, 2)
    # scores 90 / 88 / 86 -> utilities 2 / 0 / -2
    e = np.exp([2.0, 0.0, -2.0])
    np.testing.assert_allclose(pred.probs[:, 0], e / e.sum())


def test_predict_race_guesses_an_unknown_formation():
    model = Model({"A": params(b_score=1.0)}, {"A": 1}, None, None)
    rows = [card(1, score=90.0, style="逃", prefecture="福岡"), card(2, score=88.0,
            prefecture="熊本"), card(3, score=86.0, prefecture="東京")]  # fmt: skip
    pred = predict.predict_race(model, "Ａ級一般", rows, None)
    assert pred.guessed
    assert pred.by_car[2]["line_pos"] == 2 and pred.by_car[3]["line_size"] == 1


def test_predict_race_needs_a_known_group_with_a_model():
    model = Model({"A": params(b_score=1.0)}, {"A": 1}, None, None)
    rows = [card(1, score=90.0), card(2, score=88.0)]
    assert predict.predict_race(model, "Ｓ級特選", rows, None) is None
    assert predict.predict_race(model, "", rows, None) is None
    assert predict.predict_race(model, "Ａ級一般", rows[:1], None) is None


# --- the predict-eval command ------------------------------------------------------------


def write_simple_days(data_dir, days):
    """Five riders a race; the better score finishes in front, except that every third race
    swaps the first two (so that the model has something to learn but is not certain)."""
    for d, day in enumerate(days):
        races = {}
        for race_no in (1, 2, 3):
            finish = [1, 2, 3, 4, 5]
            if (d * 3 + race_no) % 3 == 0:
                finish[:2] = [2, 1]
            rows = [
                full_card(car, 92.0 - 2 * car, pos, prefecture=pref)
                for car, pos, pref in zip(
                    (1, 2, 3, 4, 5), finish, ("東京", "大阪", "福岡", "宮城", "愛知"), strict=True
                )
            ]
            races[("11", race_no)] = ("Ａ級一般", rows)
        write_day(data_dir, day, races)


def test_predict_eval_command(tmp_path, capsys):
    from keirin import cli
    from keirin.timeutil import date_range

    write_simple_days(tmp_path, list(date_range(date(2026, 9, 1), date(2026, 9, 11))))
    code = cli.main(
        ["predict-eval", "--date", "2026-09-11", "--to", "2026-09-11",
         "--data-dir", str(tmp_path), "--min-races", "10"]
    )  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "2026-09-01..2026-09-10" in out  # trained on the days before --date only
    [row] = [line.split() for line in out.splitlines() if line.startswith("A ")]
    races, model_loss, uniform_loss = int(row[1]), float(row[2]), float(row[4])
    assert races == 3
    assert uniform_loss == pytest.approx(math.log(5), abs=1e-3)
    assert model_loss < uniform_loss


def test_predict_eval_without_training_data(tmp_path, capsys):
    from keirin import cli

    code = cli.main(["predict-eval", "--date", "2026-09-11", "--to", "2026-09-11",
                     "--data-dir", str(tmp_path)])  # fmt: skip
    assert code == 1
