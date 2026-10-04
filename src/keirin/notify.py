"""Discord notifications when watched riders race in a matching situation.

The watchlist lives in the private data repository (`watchlist.toml`), e.g.

    [[watch]]
    racer_id = "014779"                       # registration number (required)
    name = "名川 豊"                           # display name
    reason = "決勝以外で番手捲り率が高い"        # shown in the notification
    positions = ["番手"]                       # 先頭 / 番手 / 3番手 … / 単騎 / 競り; empty = any
    leader_styles = ["逃", "両"]               # style of the leader of the rider's line (optional)
    exclude_finals = true                      # skip races whose class contains 決勝

Matching uses the races collected by `lines.collect_upcoming` (today / tomorrow),
so it costs no extra request. A rule with positions needs the predicted
formation, which keirin.jp publishes some time before the race; it is matched
again on the next run until then. Each (race, rider, rule) is notified once;
the sent keys are kept in `notify/sent.json` of the data repository.
"""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from keirin.lines import UpcomingRace, position_label

log = logging.getLogger(__name__)

WATCHLIST_FILE = "watchlist.toml"
SENT_FILE = Path("notify") / "sent.json"
MAX_EMBEDS = 10  # Discord limit per message
WEEKDAYS = "月火水木金土日"


class WatchlistError(ValueError):
    pass


@dataclass(frozen=True)
class WatchRule:
    racer_id: str
    name: str = ""
    reason: str = ""
    positions: tuple[str, ...] = ()
    leader_styles: tuple[str, ...] = ()
    exclude_finals: bool = False

    @property
    def key(self) -> str:
        return "|".join(
            [self.racer_id, ",".join(self.positions), ",".join(self.leader_styles),
             str(self.exclude_finals)]
        )  # fmt: skip


def load_watchlist(path: Path) -> list[WatchRule]:
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise WatchlistError(f"{path}: {e}") from e
    rules = []
    for i, item in enumerate(data.get("watch", []), 1):
        racer_id = str(item.get("racer_id", "")).strip()
        if not racer_id:
            raise WatchlistError(f"{path}: watch #{i} needs a racer_id")
        rules.append(
            WatchRule(
                racer_id=racer_id,
                name=str(item.get("name", "")),
                reason=str(item.get("reason", "")),
                positions=tuple(str(p) for p in item.get("positions", [])),
                leader_styles=tuple(str(s) for s in item.get("leader_styles", [])),
                exclude_finals=bool(item.get("exclude_finals", False)),
            )
        )
    return rules


@dataclass
class Match:
    race: UpcomingRace
    rule: WatchRule
    rider: dict[str, Any]
    position: str  # "" when the formation is not needed / unknown

    @property
    def key(self) -> str:
        r = self.race
        return f"{r.day.isoformat()}|{r.venue_code}|{r.race_no}|{self.rule.key}"


def _line_info(race: UpcomingRace, car_no: int) -> tuple[dict | None, list[int]]:
    """The rider's position info and the cars leading their line."""
    if race.formation is None:
        return None, []
    for line in race.formation.lines:
        if any(car_no in position for position in line):
            size = sum(len(p) for p in line)
            for pos, cars in enumerate(line, 1):
                if car_no in cars:
                    info = {
                        "line_pos": pos,
                        "line_size": size,
                        "contested": len(cars) > 1,
                    }
                    return info, list(line[0])
    return None, []


def match_race(race: UpcomingRace, rules: list[WatchRule]) -> list[Match]:
    matches = []
    by_id = {r["racer_id"]: r for r in race.riders}
    for rule in rules:
        rider = by_id.get(rule.racer_id)
        if rider is None:
            continue
        if rule.exclude_finals and "決勝" in race.race_class:
            continue
        info, leaders = _line_info(race, rider["car_no"])
        label = position_label(info)
        if rule.positions:
            if info is None:
                continue  # formation not published yet: try again on the next run
            wanted = set(rule.positions)
            if not (
                label.replace("（競り）", "") in wanted or ("競り" in wanted and info["contested"])
            ):
                continue
        if rule.leader_styles:
            styles = {r["style"] for r in race.riders if r["car_no"] in leaders}
            if info is None or not styles & set(rule.leader_styles):
                continue
        matches.append(Match(race, rule, rider, label))
    return matches


@dataclass
class SentLog:
    path: Path
    keys: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, data_dir: Path) -> SentLog:
        path = data_dir / SENT_FILE
        keys = set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
        return cls(path, keys)

    def save(self, keep_since: date) -> None:
        """Write the keys, forgetting races before `keep_since`."""
        kept = sorted(k for k in self.keys if k[:10] >= keep_since.isoformat())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(kept, ensure_ascii=False, indent=1) + "\n", "utf-8")


def embed(match: Match) -> dict[str, Any]:
    race, rule, rider = match.race, match.rule, match.rider
    day = race.day
    title = (
        f"{race.venue_name} {race.race_no}R　{race.race_class}　"
        f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）{race.start_time} 発走"
    )
    fields = [
        {"name": "選手", "value": f"{rider['car_no']}番 {rider['name']}（{rider['style']}）",
         "inline": True},
        {"name": "位置", "value": match.position or "（並び未公開）", "inline": True},
    ]  # fmt: skip
    if race.formation is not None:
        fields.append(
            {"name": "並び", "value": race.formation.text().replace("/", " / "), "inline": False}
        )
    if rule.reason:
        fields.append({"name": "理由", "value": rule.reason, "inline": False})
    return {"title": title, "color": 0x2557A7, "fields": fields}


class Discord:
    def __init__(self, webhook_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self._url = webhook_url
        self._http = httpx.Client(timeout=30, transport=transport)

    def send(self, content: str, embeds: list[dict[str, Any]] | None = None) -> None:
        embeds = embeds or []
        for i in range(0, max(len(embeds), 1), MAX_EMBEDS):
            resp = self._http.post(
                self._url,
                json={
                    "username": "keirin-platform",
                    "content": content if i == 0 else "",
                    "embeds": embeds[i : i + MAX_EMBEDS],
                    "allowed_mentions": {"parse": []},
                },
            )
            resp.raise_for_status()


def notify(
    races: list[UpcomingRace], rules: list[WatchRule], sent: SentLog, discord: Discord | None
) -> list[Match]:
    """Send the new matches (oldest race first); returns them."""
    new = [
        m
        for race in sorted(races, key=lambda r: (r.day, r.start_time, r.venue_code, r.race_no))
        for m in match_race(race, rules)
        if m.key not in sent.keys
    ]
    if new and discord is not None:
        discord.send(f"注目選手の出走が {len(new)} 件あります。", [embed(m) for m in new])
        # Only what was actually sent counts; without a webhook this is a dry run.
        sent.keys.update(m.key for m in new)
    for m in new:
        log.info("match: %s %dR %s %s", m.race.venue_name, m.race.race_no, m.rider["name"],
                 m.position)  # fmt: skip
    return new
