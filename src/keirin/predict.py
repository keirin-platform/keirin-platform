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
riders' regions (`lines.guess_formation`). Girls' keirin has no lines: every
rider of an L級 race rides alone.

A fit uses DEFAULT_FEATURES. The other line features are candidates that
`walk_forward` and `compare` score against it on later days (`keirin predict-compare`).
"""

from __future__ import annotations

import csv
import itertools
import logging
import math
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp

from keirin.lines import Formation, guess_formation
from keirin.storage import table_path

log = logging.getLogger(__name__)

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
    # candidates (docs/prediction.md, 2026-11-17): a fit leaves them out unless asked
    "second_size",  # 番手 x (line size - 2)
    "third_size",  # 3番手 x (line size - 3)
    "fourth_plus",  # 4番手 or further back (on top of third_plus)
    "contested",  # shares the place with another rider (競り)
    "head_contested",  # leader of a line with a contested place behind
    "second_x_head_back",  # 番手 x log1p(the leader's B count)
)
FEATURES = CARD_FEATURES + LINE_FEATURES
KC, KL = len(CARD_FEATURES), len(LINE_FEATURES)
K = KC + KL
N_PARAMS = K + KL + 2  # beta, gamma1, gamma23, log t2, log t3
MIN_RACES = 100
L2 = 1e-3
TRAINING_DAYS = 120  # the viewer fits on the latest collected days only (fit time)

CANDIDATES = {  # groups of line features a comparison adds to the base model
    "size": ("second_size", "third_size", "fourth_plus"),
    "contest": ("contested", "head_contested"),
    "lead": ("second_x_head_back",),
}
BASE_FEATURES = tuple(f for f in FEATURES if not any(f in group for group in CANDIDATES.values()))
DEFAULT_FEATURES = BASE_FEATURES  # what `fit` uses (the viewer's model)


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


def is_scratched(row: dict[str, Any]) -> bool:
    """The rider is scratched (欠場, 欠車) on the race card. research.py uses it too."""
    return "欠" in (row.get("notes") or "")


# --- features --------------------------------------------------------------------------


def feature_matrix(riders: list[dict[str, Any]], by_car: dict[int, dict[str, Any]]) -> np.ndarray:
    """One row per rider (card features, then line features).

    `riders` are race card rows (numbers or CSV strings); `by_car` maps a car number to
    its line_no / line_pos / line_size (and contested). Riders missing from it count as 単騎.
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
    head_back: dict[int, float] = {}
    fought: set[int] = set()  # lines with a contested place
    for i, info in enumerate(lines):
        if not info or int(info["line_size"]) < 2:
            continue
        line_no = int(info["line_no"])
        if info.get("contested"):
            fought.add(line_no)
        if int(info["line_pos"]) == 1:
            head_score[line_no] = max(head_score.get(line_no, -math.inf), centered[i])
            head_back[line_no] = max(head_back.get(line_no, 0.0), x[i, col["back"]])
    for i, info in enumerate(lines):
        size = int(info["line_size"]) if info else 1
        if size == 1:
            x[i, col["solo"]] = 1
            continue
        line_no, pos = int(info["line_no"]), int(info["line_pos"])
        head = head_score.get(line_no, 0.0)
        x[i, col["contested"]] = bool(info.get("contested"))
        if pos == 1:
            x[i, col["head_size"]] = size - 2
            x[i, col["head_chaser"]] = riders[i].get("style") == "追"
            x[i, col["head_contested"]] = line_no in fought and not info.get("contested")
        elif pos == 2:
            x[i, col["second"]] = 1
            x[i, col["second_x_head"]] = head
            x[i, col["second_size"]] = size - 2
            x[i, col["second_x_head_back"]] = head_back.get(line_no, 0.0)
        else:
            x[i, col["third_plus"]] = 1
            x[i, col["third_x_head"]] = head
            if pos == 3:
                x[i, col["third_size"]] = size - 3
            else:
                x[i, col["fourth_plus"]] = 1
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


def close_gaps(by_car: dict[int, dict[str, Any]], scratched: set[int]) -> dict[int, dict]:
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
                "contested": sum(p == pos for p, _ in cars) > 1,
            }
    return out


def _alone(by_car: dict[int, dict[str, Any]], cars: list[int]) -> dict[int, dict[str, Any]]:
    """`by_car` with every rider it misses riding alone (in a line of their own)."""
    out = dict(by_car)
    line_no = max((int(info["line_no"]) for info in by_car.values()), default=0)
    for car in cars:
        if car not in out:
            line_no += 1
            out[car] = {"line_no": line_no, "line_pos": 1, "line_size": 1, "contested": False}
    return out


# --- the model -------------------------------------------------------------------------


def utilities(theta: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """u1 (1st), u23 (2nd and 3rd) and the temperatures (t2, t3). research.py uses it too."""
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
    u1, u23, (t2, t3) = utilities(theta, x)
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
    u1, u23, (t2, t3) = utilities(theta, x)
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


def top3_order_probabilities(theta: np.ndarray, x: np.ndarray) -> np.ndarray:
    """(n, n, n): probability that riders a, b and c finish 1st, 2nd and 3rd.

    The model of `race_probabilities` as one array (three or more riders).
    """
    u1, u23, (t2, t3) = utilities(theta, x)
    n = len(x)
    off = ~np.eye(n, dtype=bool)
    p1 = np.exp(u1 - logsumexp(u1))
    e2 = np.exp(t2 * u23 - (t2 * u23).max())
    e3 = np.exp(t3 * u23 - (t3 * u23).max())
    p2 = np.where(off, e2[None, :], 0.0)
    p2 /= p2.sum(axis=1, keepdims=True)  # (a, b): b is second once a has won
    free = off[:, :, None] & off[:, None, :] & off[None, :, :]  # a, b and c all differ
    p3 = np.where(free, e3[None, None, :], 0.0)
    left = p3.sum(axis=2, keepdims=True)
    p3 = np.divide(p3, left, out=np.zeros_like(p3), where=left > 0)
    return p1[:, None, None] * p2[:, :, None] * p3


def expected_places(theta: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(n, 3) win / top 2 / top 3 and (n, n) both in the top 3, under the model."""
    t = top3_order_probabilities(theta, x)
    first, second, third = t.sum(axis=(1, 2)), t.sum(axis=(0, 2)), t.sum(axis=(0, 1))
    m = t.sum(axis=2) + t.sum(axis=1) + t.sum(axis=0)
    return np.column_stack([first, first + second, first + second + third]), m + m.T


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


def _pack(races: list[Race], keep: np.ndarray):
    width = max(len(r.x) for r in races)
    x = np.zeros((len(races), width, K))
    mask = np.zeros((len(races), width), bool)
    order = np.full((len(races), 3), -1)
    for i, r in enumerate(races):
        x[i, : len(r.x)] = r.x
        mask[i, : len(r.x)] = True
        order[i, : len(r.order[:3])] = r.order[:3]
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
    e.g. ("score", "no_score") for a score-only baseline. The default is DEFAULT_FEATURES.
    """
    keep = np.array([f in (DEFAULT_FEATURES if columns is None else columns) for f in FEATURES])
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
        if not result.success:
            log.warning("%s: the fit did not converge (%s)", group, result.message)
        theta = result.x
        theta[:KC][~keep[:KC]] = 0.0
        theta[KC:K][~keep[KC:]] = 0.0
        theta[K : K + KL][~keep[KC:]] = 0.0
        params[group], counts[group] = theta, len(members)
    days = [r.day for r in races if r.day is not None]
    return Model(params, counts, min(days, default=None), max(days, default=None))


# --- training data -----------------------------------------------------------------------


def read_rows(path: Path) -> list[dict[str, str]]:
    """Rows of a collected CSV table (empty when the file is missing)."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def recent_start(data_dir: Path, days: int) -> date | None:
    """First day of the latest `days` days of collected data (None without data)."""
    files = sorted((data_dir / "tables" / "entries").glob("*/*.csv"))
    return date.fromisoformat(files[-1].stem) - timedelta(days=days - 1) if files else None


def load_races(data_dir: Path, start: date | None = None, end: date | None = None) -> list[Race]:
    """Collected races of start <= day < end with a full card and at least three placed."""
    out = []
    for path in sorted((data_dir / "tables" / "entries").glob("*/*.csv")):
        day = date.fromisoformat(path.stem)
        if (start and day < start) or (end and day >= end):
            continue
        classes = {
            (r["venue_code"], r["race_no"]): r["race_class"]
            for r in read_rows(table_path(data_dir, "races", day))
        }
        formations: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
        for r in read_rows(table_path(data_dir, "lines", day)):
            formations[(r["venue_code"], r["race_no"])][int(r["car_no"])] = {
                "line_no": int(r["line_no"]),
                "line_pos": int(r["line_pos"]),
                "line_size": int(r["line_size"]),
            }  # close_gaps works out the contests from the shared places
        races: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for r in read_rows(path):
            races[(r["venue_code"], r["race_no"])].append(r)
        for key in sorted(races, key=lambda k: (k[0], int(k[1]))):
            group = group_of(classes.get(key))
            riders = [r for r in races[key] if not is_scratched(r)]
            if group is None or len(riders) < 3 or any(r["score"] == "" for r in riders):
                continue
            scratched = {int(r["car_no"]) for r in races[key] if is_scratched(r)}
            placed = sorted(
                (int(r["finish_pos"]), int(r["car_no"]), i)
                for i, r in enumerate(riders)
                if r["finish_pos"]
            )
            if len(placed) < 3:
                continue
            if group == "L":
                by_car = {}  # no lines in girls' keirin
            elif key in formations:
                by_car = close_gaps(formations[key], scratched)
            else:
                by_car = _by_car(guess_formation([_guess_input(r) for r in riders]))
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
    brier_top2: float
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
        won, top2, top3 = (np.zeros(len(race.x)) for _ in range(3))
        won[race.order[0]] = 1
        top2[list(race.order[:2])] = 1
        top3[list(race.order[:3])] = 1
        for group in (race.group, "all"):
            part = parts[group]
            part["loss"].append(-math.log(max(p[race.order[0], 0], 1e-12)))
            part["p1"].append(p[:, 0])
            part["y1"].append(won)
            part["p2"].append(p[:, 1])
            part["y2"].append(top2)
            part["p3"].append(p[:, 2])
            part["y3"].append(top3)
    result = {}
    for group, part in parts.items():
        p1, y1 = np.concatenate(part["p1"]), np.concatenate(part["y1"])
        p2, y2 = np.concatenate(part["p2"]), np.concatenate(part["y2"])
        p3, y3 = np.concatenate(part["p3"]), np.concatenate(part["y3"])
        result[group] = Evaluation(
            races=len(part["loss"]),
            log_loss=float(np.mean(part["loss"])),
            brier_win=float(np.mean((p1 - y1) ** 2)),
            brier_top2=float(np.mean((p2 - y2) ** 2)),
            brier_top3=float(np.mean((p3 - y3) ** 2)),
            win_bins=_bins(p1, y1, WIN_EDGES),
            top3_bins=_bins(p3, y3, TOP3_EDGES),
        )
    return result


# --- comparing feature sets on later days ----------------------------------------------

PRACTICAL_LOG_LOSS = 0.002  # per race; fixed before measuring (docs/prediction.md)
SEGMENTS = ("men", "A", "S", "S7", "S9", "contested")


def variants(candidates: dict[str, tuple[str, ...]] | None = None) -> dict[str, tuple[str, ...]]:
    """The base model ("now") and every combination of candidate groups added to it."""
    candidates = CANDIDATES if candidates is None else candidates
    out = {"now": BASE_FEATURES}
    for r in range(1, len(candidates) + 1):
        for combo in itertools.combinations(candidates, r):
            out["+".join(combo)] = BASE_FEATURES + tuple(f for g in combo for f in candidates[g])
    return out


def cells_of(x: np.ndarray) -> list[str]:
    """Each rider's place in the line, read back from the feature matrix: head / second /
    third with the line size (4: four or more), fourth_plus, solo, contested (the riders
    who share a place) and head_contested (the head they fight behind)."""
    col = {name: i for i, name in enumerate(FEATURES)}

    def sized(row: np.ndarray, name: str, column: str, least: int) -> str:
        return f"{name}_{min(int(round(row[col[column]])) + least, 4)}"

    out = []
    for row in x:
        if row[col["contested"]] > 0.5:
            out.append("contested")
        elif row[col["solo"]] > 0.5:
            out.append("solo")
        elif row[col["head_contested"]] > 0.5:
            out.append("head_contested")
        elif row[col["fourth_plus"]] > 0.5:
            out.append("fourth_plus")
        elif row[col["third_plus"]] > 0.5:
            out.append(sized(row, "third", "third_size", 3))
        elif row[col["second"]] > 0.5:
            out.append(sized(row, "second", "second_size", 2))
        else:
            out.append(sized(row, "head", "head_size", 2))
    return out


@dataclass(frozen=True)
class Scored:
    """A race predicted by every variant, each fitted on earlier days only."""

    race: Race
    probs: dict[str, np.ndarray]  # variant -> (riders, 3): win, top 2, top 3


def walk_forward(
    races: list[Race],
    start: date,
    end: date,
    *,
    columns: dict[str, tuple[str, ...]],
    days: int = TRAINING_DAYS,
    step: int = 14,
    groups: tuple[str, ...] = ("A", "S"),
    min_races: int = MIN_RACES,
) -> list[Scored]:
    """Predicts the races of start <= day < end in blocks of `step` days; every block uses
    models fitted on the `days` days before it. Races of a group some variant has no model
    for are left out."""
    dated = [r for r in races if r.day is not None and r.group in groups]
    out = []
    block = start
    while block < end:
        following = min(block + timedelta(days=step), end)
        train = [r for r in dated if block - timedelta(days=days) <= r.day < block]
        models = {name: fit(train, min_races=min_races, columns=c) for name, c in columns.items()}
        for race in dated:
            if block <= race.day < following and all(
                race.group in m.params for m in models.values()
            ):
                probs = {
                    n: expected_places(m.params[race.group], race.x)[0] for n, m in models.items()
                }
                out.append(Scored(race, probs))
        block = following
    return out


def _race_scores(scored: Scored, names: list[str]) -> list[float]:
    """Per variant: -log P(winner) and the squared errors of win / top 2 / top 3 summed
    over the riders; then the number of riders."""
    n = len(scored.race.x)
    hit = np.zeros((n, 3))
    hit[scored.race.order[0], 0] = 1
    hit[list(scored.race.order[:2]), 1] = 1
    hit[list(scored.race.order[:3]), 2] = 1
    row = []
    for name in names:
        p = scored.probs[name]
        row.append(-math.log(max(p[scored.race.order[0], 0], 1e-12)))
        row.extend(((p - hit) ** 2).sum(axis=0))
    return [*row, float(n)]


def _measures(totals: np.ndarray, races: Any, names: list[str]) -> dict[str, dict[str, Any]]:
    """variant -> log_loss / brier_win / brier_top2 / brier_top3 from column sums."""
    riders = totals[..., -1]
    out = {}
    for k, name in enumerate(names):
        out[name] = {"log_loss": totals[..., 4 * k] / races} | {
            f"brier_{place}": totals[..., 4 * k + 1 + j] / riders
            for j, place in enumerate(("win", "top2", "top3"))
        }
    return out


def decide_feature(
    value: float,
    ci: tuple[float, float],
    halves: tuple[float, float],
    by_group: tuple[float, ...],
    practical: float = PRACTICAL_LOG_LOSS,
) -> str:
    """入れる / 入れない / 保留 for a change of the win log loss (negative: better).

    入れない first when the interval lies inside +-practical (negligible) or above 0
    (worse). 入れる needs an interval below 0, a point estimate of at least `practical`,
    both halves better and no group worse.
    """
    lo, hi = ci
    if (-practical < lo and hi < practical) or lo > 0:
        return "入れない"
    better = hi < 0 and value <= -practical
    if better and all(h < 0 for h in halves) and all(g <= 0 for g in by_group):
        return "入れる"
    return "保留"


def compare(
    scored: list[Scored], *, base: str = "now", n_boot: int = 2000, seed: int = 0
) -> dict[str, Any]:
    """How the variants score the same races, against `base` (aggregates only).

    Intervals resample races (paired across variants); the halves split at the median
    race day. A candidate group is judged by `decide_feature` on the men's win log loss
    of base + that group alone; "adopted" is the variant made of the groups to add.
    """
    names = list(scored[0].probs)
    table = np.array([_race_scores(s, names) for s in scored])
    days = np.array([s.race.day.toordinal() for s in scored])
    group = np.array([s.race.group for s in scored])
    field = np.array([len(s.race.x) for s in scored])
    cells = [cells_of(s.race.x) for s in scored]
    masks = {
        "men": np.ones(len(scored), bool),
        "A": group == "A",
        "S": group == "S",
        "S7": (group == "S") & (field == 7),
        "S9": (group == "S") & (field == 9),
        "contested": np.array(["contested" in c for c in cells]),
    }
    rng = np.random.default_rng(seed)

    def differences(rows: np.ndarray) -> dict[str, dict[str, dict[str, Any]]]:
        """variant -> measure -> change against base (value, ci95, halves)."""
        part, n = table[rows], int(rows.sum())
        if n == 0:
            return {}
        early = days[rows] <= np.median(days[rows])
        whole = _measures(part.sum(axis=0), n, names)
        halves = [
            _measures(part[half].sum(axis=0), int(half.sum()), names) if half.any() else None
            for half in (early, ~early)
        ]
        boots = []
        for _ in range(0, n_boot, 250):  # in chunks: the weights are (replicates, races)
            w = rng.multinomial(n, np.full(n, 1 / n), size=min(250, n_boot))
            boots.append(w @ part)
        boot = _measures(np.concatenate(boots)[:n_boot], n, names) if n_boot else None
        out = {}
        for name in names:
            out[name] = {}
            for measure in whole[name]:
                entry: dict[str, Any] = {
                    "value": round(float(whole[name][measure]), 5),
                    "change": round(float(whole[name][measure] - whole[base][measure]), 5),
                }
                if name != base:
                    if boot is not None:
                        diff = boot[name][measure] - boot[base][measure]
                        entry["ci95"] = [
                            round(float(q), 5) for q in np.quantile(diff, [0.025, 0.975])
                        ]
                    entry["halves"] = [
                        None if h is None else round(float(h[name][measure] - h[base][measure]), 5)
                        for h in halves
                    ]
                out[name][measure] = entry
        return out

    by_segment = {segment: differences(mask) for segment, mask in masks.items()}
    decisions, adopted = {}, []
    for candidate in CANDIDATES:
        if candidate not in names:
            continue
        men = by_segment["men"][candidate]["log_loss"]
        by_group = tuple(
            by_segment[g][candidate]["log_loss"]["change"] for g in ("A", "S") if by_segment[g]
        )
        halves = tuple(h for h in men["halves"] if h is not None)
        verdict = (
            decide_feature(men["change"], tuple(men["ci95"]), halves, by_group)
            if "ci95" in men and len(halves) == 2
            else "保留"
        )
        decisions[candidate] = {
            "change": men["change"],
            "ci95": men.get("ci95"),
            "halves": men["halves"],
            "by_group": dict(zip(("A", "S"), by_group, strict=False)),
            "decision": verdict,
        }
        if verdict == "入れる":
            adopted.append(candidate)
    adopted_name = "+".join(adopted) if adopted else base

    def residuals(name: str, rows: np.ndarray) -> dict[str, dict[str, Any]]:
        """cell -> riders and the mean of (happened - predicted) for the win and the top 3."""
        sums: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(3))
        for i in np.flatnonzero(rows):
            s = scored[i]
            p = s.probs[name]
            for k, cell in enumerate(cells[i]):
                won, top3 = k == s.race.order[0], k in s.race.order[:3]
                sums[cell] += (1.0, won - p[k, 0], top3 - p[k, 2])
        return {
            cell: {
                "riders": int(v[0]),
                "win": round(float(v[1] / v[0]), 4),
                "top3": round(float(v[2] / v[0]), 4),
            }
            for cell, v in sorted(sums.items())
        }

    def calibration(name: str) -> dict[str, list[dict[str, Any]]]:
        p1 = np.concatenate([s.probs[name][:, 0] for s in scored])
        p3 = np.concatenate([s.probs[name][:, 2] for s in scored])
        y1 = np.concatenate([np.arange(len(s.race.x)) == s.race.order[0] for s in scored])
        y3 = np.concatenate([np.isin(np.arange(len(s.race.x)), s.race.order[:3]) for s in scored])
        return {
            place: [
                {"from": b.lo, "to": b.hi, "riders": b.n,
                 "predicted": round(b.predicted, 4), "observed": round(b.observed, 4)}
                for b in _bins(p, y.astype(float), edges)
            ]
            for place, p, y, edges in (("win", p1, y1, WIN_EDGES), ("top3", p3, y3, TOP3_EDGES))
        }  # fmt: skip

    shown = list(dict.fromkeys([base, adopted_name, names[-1]]))  # base, adopted, everything
    return {
        "races": {segment: int(mask.sum()) for segment, mask in masks.items()},
        "practical": PRACTICAL_LOG_LOSS,
        "decisions": decisions,
        "adopted": adopted_name,
        "variants": by_segment,
        "residuals": {
            name: {g: residuals(name, masks[g]) for g in ("men", "A", "S")} for name in shown
        },
        "calibration": {name: calibration(name) for name in shown},
    }


# --- predicting a race card ------------------------------------------------------------


@dataclass(frozen=True)
class Prediction:
    group: str
    cars: list[int]
    probs: np.ndarray  # (riders, 3): win, top 2, top 3
    by_car: dict[int, dict[str, Any]]  # line positions used (every rider)
    guessed: bool  # the formation was guessed from the regions
    scratched: list[int]
    no_score: list[int]  # riders without a score (counted 3 below the lowest)


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
    riders = [r for r in rows if not is_scratched(r)]
    if len(riders) < 2:
        return None
    scratched = sorted(int(r["car_no"]) for r in rows if is_scratched(r))
    cars = [int(r["car_no"]) for r in riders]
    if group == "L":
        lines, guessed = {}, False  # no lines in girls' keirin
    elif by_car:
        lines, guessed = close_gaps({int(c): v for c, v in by_car.items()}, set(scratched)), False
    else:
        lines, guessed = _by_car(guess_formation([_guess_input(r) for r in riders])), True
    lines = _alone(lines, cars)
    probs = model.probabilities(group, feature_matrix(riders, lines))
    no_score = [c for c, r in zip(cars, riders, strict=True) if not (_num(r.get("score")) or 0) > 0]
    return Prediction(group, cars, probs, lines, guessed, scratched, no_score)
