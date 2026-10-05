"""Win / top-2 / top-3 probabilities of the riders of a race. See docs/prediction.md.

A race's finishing order is modelled with a Plackett–Luce (rank-ordered logit)
model: the winner is drawn among all riders with probability proportional to
exp(utility), the second among the others, then the third. The utility is linear
in the race card (score, rates, style, B/H/S counts, ...) and in the rider's
position in the line formation. Line positions weigh differently for the win
(gamma1) and for the 2nd/3rd places (gamma23): a third rider of a line seldom
wins but often comes third. The 2nd and 3rd draws scale the utility by the
temperatures t2 and t3, since lower places are more random.

S級, A級 and L級 (girls) are fitted separately. Training uses a race's captured
formation (tables/lines) when there is one, and otherwise guesses it from the
riders' regions (`lines.guess_formation`).
"""

from __future__ import annotations

import csv
import math
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from keirin.lines import Formation, guess_formation
from keirin.storage import table_path

CARD_FEATURES = (
    "score",  # race score minus the race average (missing: 3 below the lowest)
    "no_score",
    "win",  # win / top-2 / top-3 rates, 0..1
    "top2",
    "top3",
    "nige_style",  # 脚質 逃
    "ryo_style",  # 脚質 両
    "back",  # log1p of the B / H / S counts
    "home",
    "start",
    "attack",  # log1p(逃げ + 捲り) - log1p(差し + マーク)
    "age",  # (age - 40) / 10
    "prev_s",  # S級 in the previous term
)
LINE_FEATURES = (
    "second",  # 番手
    "third_plus",  # 3番手 or further back
    "solo",  # 単騎
    "second_x_head",  # 番手 x the line leader's (centered) score
    "third_x_head",
    "head_size",  # leader x (line size - 2)
    "head_chaser",  # leader whose style is 追
)
FEATURES = CARD_FEATURES + LINE_FEATURES
KC, KL = len(CARD_FEATURES), len(LINE_FEATURES)
K = KC + KL
N_PARAMS = K + KL + 2  # beta, gamma1, gamma23, log t2, log t3
MIN_RACES = 100
L2 = 1e-3


def group_of(race_class: str | None) -> str | None:
    """S / A / L from the race class (Ｓ級特選, Ａ級一般, Ｌ級ガ予, ...)."""
    first = unicodedata.normalize("NFKC", race_class or "")[:1]
    return first if first in ("S", "A", "L") else None


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scratched(row: dict[str, Any]) -> bool:
    return "欠" in (row.get("notes") or "")


# --- features --------------------------------------------------------------------------


def feature_matrix(riders: list[dict[str, Any]], by_car: dict[int, dict[str, Any]]) -> np.ndarray:
    """One row per rider (card features, then line features).

    `riders` are race card rows (numbers or CSV strings); `by_car` maps a car number to
    its line_no / line_pos / line_size. Riders missing from it count as 単騎.
    """
    col = {name: i for i, name in enumerate(FEATURES)}
    x = np.zeros((len(riders), K))
    scores = [s if s and s > 0 else None for s in (_num(r.get("score")) for r in riders)]
    known = [s for s in scores if s is not None]
    floor = min(known) - 3 if known else 0.0
    filled = np.array([floor if s is None else s for s in scores])
    centered = filled - filled.mean() if len(filled) else filled

    for i, r in enumerate(riders):

        def num(key: str, row: dict[str, Any] = r) -> float:
            return _num(row.get(key)) or 0.0

        age = _num(r.get("age"))
        x[i, col["score"]] = centered[i]
        x[i, col["no_score"]] = scores[i] is None
        x[i, col["win"]] = num("win_rate") / 100
        x[i, col["top2"]] = num("top2_rate") / 100
        x[i, col["top3"]] = num("top3_rate") / 100
        x[i, col["nige_style"]] = r.get("style") == "逃"
        x[i, col["ryo_style"]] = r.get("style") == "両"
        x[i, col["back"]] = math.log1p(num("back"))
        x[i, col["home"]] = math.log1p(num("home"))
        x[i, col["start"]] = math.log1p(num("start"))
        x[i, col["attack"]] = math.log1p(num("nige") + num("makuri")) - math.log1p(
            num("sashi") + num("mark")
        )
        x[i, col["age"]] = (age - 40) / 10 if age else 0.0
        x[i, col["prev_s"]] = str(r.get("prev_class") or "").startswith("S")

    lines = [by_car.get(int(r["car_no"])) for r in riders]
    head_score: dict[int, float] = {}
    for i, info in enumerate(lines):
        if info and int(info["line_pos"]) == 1 and int(info["line_size"]) >= 2:
            line_no = int(info["line_no"])
            head_score[line_no] = max(head_score.get(line_no, -math.inf), centered[i])
    for i, info in enumerate(lines):
        size = int(info["line_size"]) if info else 1
        if size == 1:
            x[i, col["solo"]] = 1
            continue
        pos, head = int(info["line_pos"]), head_score.get(int(info["line_no"]), 0.0)
        if pos == 1:
            x[i, col["head_size"]] = size - 2
            x[i, col["head_chaser"]] = riders[i].get("style") == "追"
        elif pos == 2:
            x[i, col["second"]] = 1
            x[i, col["second_x_head"]] = head
        else:
            x[i, col["third_plus"]] = 1
            x[i, col["third_x_head"]] = head
    return x


def _by_car(formation: Formation) -> dict[int, dict[str, Any]]:
    return {
        row["car_no"]: {k: row[k] for k in ("line_no", "line_pos", "line_size", "contested")}
        for row in formation.rows({}, "guess")
    }


def _guess_input(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "car_no": int(row["car_no"]),
        "prefecture": row.get("prefecture") or "",
        "style": row.get("style") or "",
        "score": _num(row.get("score")),
        "back": _num(row.get("back")),
    }


def _close_gaps(by_car: dict[int, dict[str, Any]], scratched: set[int]) -> dict[int, dict]:
    """Line positions once the scratched riders are taken out."""
    members: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for car, info in by_car.items():
        if car not in scratched:
            members[int(info["line_no"])].append((int(info["line_pos"]), car))
    out = {}
    for line_no, cars in members.items():
        positions = sorted({pos for pos, _ in cars})
        for pos, car in cars:
            out[car] = {
                "line_no": line_no,
                "line_pos": positions.index(pos) + 1,
                "line_size": len(cars),
                "contested": by_car[car].get("contested", False),
            }
    return out


# --- the model -------------------------------------------------------------------------


def _utilities(theta: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    base = x[..., :KC] @ theta[:KC]
    u1 = base + x[..., KC:] @ theta[KC:K]
    u23 = base + x[..., KC:] @ theta[K : K + KL]
    return u1, u23, np.exp(theta[-2:])


def _objective(
    theta: np.ndarray, x: np.ndarray, mask: np.ndarray, order: np.ndarray, l2: float
) -> tuple[float, np.ndarray]:
    """Mean negative log-likelihood of the top-3 orders (+ L2) and its gradient.

    x: (races, width, K), mask: rider present, order: rows of the 1st..3rd (-1: none).
    """
    u1, u23, (t2, t3) = _utilities(theta, x)
    available = mask.copy()
    loglik, grad = 0.0, np.zeros_like(theta)
    for stage, (u, t) in enumerate(((u1, 1.0), (u23, t2), (u23, t3))):
        valid = order[:, stage] >= 0
        rows, chosen = np.flatnonzero(valid), order[valid, stage]
        picks = np.arange(len(rows))
        v = t * u[valid]
        z = np.where(available[valid], v, -np.inf)
        lse = logsumexp(z, axis=1)
        resid = -np.exp(z - lse[:, None])
        resid[picks, chosen] += 1.0
        loglik += np.sum(v[picks, chosen] - lse)
        grad[:KC] += t * np.einsum("rw,rwk->k", resid, x[valid][..., :KC])
        line_grad = t * np.einsum("rw,rwk->k", resid, x[valid][..., KC:])
        if stage == 0:
            grad[KC:K] += line_grad
        else:
            grad[K : K + KL] += line_grad
            grad[stage - 3] += t * np.sum(resid * u[valid])  # d(t u)/d(log t) = t u
        available[rows, chosen] = False
    n = len(x)
    weights = theta[:-2]
    grad = -grad / n
    grad[:-2] += 2 * l2 * weights
    return -loglik / n + l2 * float(weights @ weights), grad


def race_probabilities(theta: np.ndarray, x: np.ndarray) -> np.ndarray:
    """(riders, 3): probabilities of winning, of the top 2 and of the top 3."""
    u1, u23, (t2, t3) = _utilities(theta, x)
    n = len(x)

    def draw(u: np.ndarray, left: np.ndarray) -> np.ndarray:
        z = np.where(left, u, -np.inf)
        return np.exp(z - logsumexp(z))

    p1 = draw(u1, np.ones(n, bool))
    top2, top3 = p1.copy(), p1.copy()
    for a in range(n):
        left = np.ones(n, bool)
        left[a] = False
        if not left.any():
            continue
        p2 = draw(t2 * u23, left)
        top2 += p1[a] * p2
        top3 += p1[a] * p2
        for b in np.flatnonzero(left):
            rest = left.copy()
            rest[b] = False
            if rest.any():
                top3 += p1[a] * p2[b] * draw(t3 * u23, rest)
    return np.column_stack([p1, np.minimum(top2, 1.0), np.minimum(top3, 1.0)])


@dataclass(frozen=True)
class Race:
    group: str
    x: np.ndarray  # (riders, K)
    order: tuple[int, ...]  # rows of the 1st, 2nd and 3rd
    day: date | None


@dataclass
class Model:
    params: dict[str, np.ndarray]  # group -> parameter vector
    races: dict[str, int]  # group -> number of training races
    first_day: date | None
    last_day: date | None

    def probabilities(self, group: str, x: np.ndarray) -> np.ndarray | None:
        theta = self.params.get(group)
        return None if theta is None else race_probabilities(theta, x)


def _pack(races: list[Race], keep: np.ndarray | None):
    width = max(len(r.x) for r in races)
    x = np.zeros((len(races), width, K))
    mask = np.zeros((len(races), width), bool)
    order = np.full((len(races), 3), -1)
    for i, r in enumerate(races):
        x[i, : len(r.x)] = r.x
        mask[i, : len(r.x)] = True
        order[i, : len(r.order[:3])] = r.order[:3]
    if keep is not None:
        x[..., ~keep] = 0.0
    return x, mask, order


def fit(
    races: list[Race],
    *,
    min_races: int = MIN_RACES,
    l2: float = L2,
    columns: tuple[str, ...] | None = None,
) -> Model:
    """Maximum likelihood per group (groups with fewer than `min_races` are left out).

    `columns` restricts the model to those features (the others get a weight of 0),
    e.g. ("score", "no_score") for a score-only baseline.
    """
    keep = None if columns is None else np.array([f in columns for f in FEATURES])
    groups: dict[str, list[Race]] = defaultdict(list)
    for race in races:
        groups[race.group].append(race)
    params, counts = {}, {}
    for group, members in sorted(groups.items()):
        if len(members) < min_races:
            continue
        x, mask, order = _pack(members, keep)
        result = minimize(
            _objective, np.zeros(N_PARAMS), args=(x, mask, order, l2), jac=True, method="L-BFGS-B"
        )
        theta = result.x
        if keep is not None:
            theta[:KC][~keep[:KC]] = 0.0
            theta[KC:K][~keep[KC:]] = 0.0
            theta[K : K + KL][~keep[KC:]] = 0.0
        params[group], counts[group] = theta, len(members)
    days = [r.day for r in races if r.day is not None]
    return Model(params, counts, min(days, default=None), max(days, default=None))


# --- training data -----------------------------------------------------------------------


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_races(data_dir: Path, start: date | None = None, end: date | None = None) -> list[Race]:
    """Collected races of start <= day < end with a full card and at least three placed."""
    out = []
    for path in sorted((data_dir / "tables" / "entries").glob("*/*.csv")):
        day = date.fromisoformat(path.stem)
        if (start and day < start) or (end and day >= end):
            continue
        classes = {
            (r["venue_code"], r["race_no"]): r["race_class"]
            for r in _read(table_path(data_dir, "races", day))
        }
        formations: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
        for r in _read(table_path(data_dir, "lines", day)):
            formations[(r["venue_code"], r["race_no"])][int(r["car_no"])] = {
                "line_no": int(r["line_no"]),
                "line_pos": int(r["line_pos"]),
                "line_size": int(r["line_size"]),
            }
        races: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in _read(path):
            races[(r["venue_code"], r["race_no"])].append(r)
        for key in sorted(races, key=lambda k: (k[0], int(k[1]))):
            group = group_of(classes.get(key))
            riders = [r for r in races[key] if not _scratched(r)]
            if group is None or len(riders) < 3 or any(r["score"] == "" for r in riders):
                continue
            placed = sorted(
                (int(r["finish_pos"]), int(r["car_no"]), i)
                for i, r in enumerate(riders)
                if r["finish_pos"]
            )
            if len(placed) < 3:
                continue
            by_car = formations.get(key) or _by_car(
                guess_formation([_guess_input(r) for r in riders])
            )
            order = tuple(i for _, _, i in placed[:3])
            out.append(Race(group, feature_matrix(riders, by_car), order, day))
    return out


# --- evaluation ----------------------------------------------------------------------------

WIN_EDGES = (0.0, 0.05, 0.1, 0.2, 0.3, 0.45, 1.0)
TOP3_EDGES = (0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 1.0)


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    n: int
    predicted: float  # mean predicted probability
    observed: float  # share that happened


@dataclass(frozen=True)
class Evaluation:
    races: int
    log_loss: float  # mean -log P(winner)
    brier_win: float  # mean over riders of (P(win) - won)^2
    brier_top3: float
    win_bins: list[Bin]
    top3_bins: list[Bin]


def _bins(p: np.ndarray, y: np.ndarray, edges: tuple[float, ...]) -> list[Bin]:
    out = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        inside = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if inside.any():
            out.append(
                Bin(lo, hi, int(inside.sum()), float(p[inside].mean()), float(y[inside].mean()))
            )
    return out


def evaluate(model: Model, races: list[Race]) -> dict[str, Evaluation]:
    """Scores per group and over all races ("all"); races of groups without a model are skipped."""
    parts: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for race in races:
        p = model.probabilities(race.group, race.x)
        if p is None:
            continue
        won, top3 = np.zeros(len(race.x)), np.zeros(len(race.x))
        won[race.order[0]] = 1
        top3[list(race.order[:3])] = 1
        for group in (race.group, "all"):
            part = parts[group]
            part["loss"].append(-math.log(max(p[race.order[0], 0], 1e-12)))
            part["p1"].append(p[:, 0])
            part["y1"].append(won)
            part["p3"].append(p[:, 2])
            part["y3"].append(top3)
    result = {}
    for group, part in parts.items():
        p1, y1 = np.concatenate(part["p1"]), np.concatenate(part["y1"])
        p3, y3 = np.concatenate(part["p3"]), np.concatenate(part["y3"])
        result[group] = Evaluation(
            races=len(part["loss"]),
            log_loss=float(np.mean(part["loss"])),
            brier_win=float(np.mean((p1 - y1) ** 2)),
            brier_top3=float(np.mean((p3 - y3) ** 2)),
            win_bins=_bins(p1, y1, WIN_EDGES),
            top3_bins=_bins(p3, y3, TOP3_EDGES),
        )
    return result


# --- predicting a race card ------------------------------------------------------------


@dataclass(frozen=True)
class Prediction:
    group: str
    cars: list[int]
    probs: np.ndarray  # (riders, 3): win, top 2, top 3
    by_car: dict[int, dict[str, Any]]  # line positions used
    guessed: bool  # the formation was guessed from the regions
    scratched: list[int]


def predict_race(
    model: Model,
    race_class: str | None,
    rows: list[dict[str, Any]],
    by_car: dict[int, dict[str, Any]] | None,
) -> Prediction | None:
    """Probabilities for a race card; None when the race's group has no model."""
    group = group_of(race_class)
    if group is None or group not in model.params:
        return None
    riders = [r for r in rows if not _scratched(r)]
    if len(riders) < 2:
        return None
    scratched = sorted(int(r["car_no"]) for r in rows if _scratched(r))
    if by_car:
        lines, guessed = _close_gaps({int(c): v for c, v in by_car.items()}, set(scratched)), False
    else:
        lines, guessed = _by_car(guess_formation([_guess_input(r) for r in riders])), True
    probs = model.probabilities(group, feature_matrix(riders, lines))
    return Prediction(group, [int(r["car_no"]) for r in riders], probs, lines, guessed, scratched)
