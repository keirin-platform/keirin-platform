"""Fetch every response needed to describe one race day into a raw bundle.

Navigation follows the site itself: day -> meetings -> races. Meetings and
races are addressed by opaque ``encp`` tokens that come from the parent
response, so we never have to construct them.

    JSJ057 kday=YYYYMMDD  meetings held that day (each has encPrm)
      JSJ001 encp=<meeting>  meeting header (title)
      JSJ017 encp=<meeting>  entry list of every race (start times, classes)
      JSJ018 encp=<meeting>  result list (per-race encp in raceRVPrm)
      JSJ002 encp=<race>     detailed cards of EVERY race of the meeting (scores, styles,
                             win rates) in one request; JSJ006 per race is the fallback
        JSJ012 encp=<race>  detailed result (order, margins, payouts, weather)

Bundles collected before 2026-10-04 have one JSJ006 per race instead of JSJ002;
the parser reads both.

The bundle stores the raw responses untouched so that tables can be rebuilt
later (``keirin rebuild``) without hitting the site again.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Protocol

from keirin.timeutil import JST

log = logging.getLogger(__name__)

BUNDLE_VERSION = 1


class ApiClient(Protocol):
    def get(self, type_: str, **params: str) -> dict[str, Any]: ...


def fetch_day(client: ApiClient, day: date) -> dict[str, Any]:
    responses: list[dict[str, Any]] = []

    def call(type_: str, **params: str) -> dict[str, Any]:
        body = client.get(type_, **params)
        responses.append({"type": type_, "params": params, "body": body})
        return body

    top = call("JSJ057", kday=day.strftime("%Y%m%d"))
    meetings = top.get("kInfo") or []
    race_count = 0
    for meeting in meetings:
        enc = meeting.get("encPrm")
        if not enc:
            continue
        call("JSJ001", encp=enc)
        call("JSJ017", encp=enc)
        result_list = call("JSJ018", encp=enc)
        race_encs = [
            race.get("raceRVPrm") or race.get("raceTanpyoPrm")
            for race in result_list.get("resultList") or []
        ]
        race_encs = [e for e in race_encs if e]
        if not race_encs:
            continue
        cards = call("JSJ002", encp=race_encs[0])
        for race_enc in race_encs:
            if not cards.get("raceInfo"):
                call("JSJ006", encp=race_enc)
            call("JSJ012", encp=race_enc)
            race_count += 1
    log.info(
        "%s: %d meetings, %d races, %d requests", day, len(meetings), race_count, len(responses)
    )
    return {
        "version": BUNDLE_VERSION,
        "date": day.isoformat(),
        "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
        "responses": responses,
    }
