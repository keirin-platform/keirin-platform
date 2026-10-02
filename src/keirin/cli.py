"""Command line entry point: ``keirin <command>`` (see ``keirin --help``)."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import date
from pathlib import Path

from keirin import analysis, github_commit, rating, storage
from keirin.client import KeirinApiError, KeirinClient
from keirin.collect import fetch_day
from keirin.parse import parse_bundle
from keirin.score import History, window_start
from keirin.timeutil import date_range, today_jst, yesterday_jst

log = logging.getLogger("keirin")


def cmd_collect(args: argparse.Namespace) -> int:
    start = args.date or yesterday_jst()
    end = args.to or start
    if end >= today_jst():
        log.error("refusing to collect %s or later: results are not final yet", today_jst())
        return 2
    data_dir: Path = args.data_dir
    collected = 0
    pending = []
    for day in date_range(start, end):
        if args.force or not storage.raw_path(data_dir, day).exists():
            pending.append(day)
        else:
            collected += 1
    # Newest first so that recent data lands first when --max-days spreads a backfill
    # over several runs.
    pending.sort(reverse=True)
    if args.max_days is not None:
        pending = pending[: args.max_days]
    log.info("%s..%s: %d days to fetch, %d already collected", start, end, len(pending), collected)
    if not pending:
        return 0
    with KeirinClient(min_interval=args.min_interval) as client:
        for day in pending:
            try:
                bundle = fetch_day(client, day)
            except KeirinApiError as e:
                # Stop entirely instead of hammering a site that is failing or blocking us.
                log.error("%s: %s", day, e)
                return 1
            storage.write_raw(data_dir, bundle)
            tables = parse_bundle(bundle)
            storage.write_tables(data_dir, day, tables)
            log.info(
                "%s: wrote %s", day, ", ".join(f"{n}={len(rows)}" for n, rows in tables.items())
            )
        log.info("total requests: %d", client.request_count)
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    count = 0
    for path in storage.iter_raw_paths(args.data_dir):
        bundle = storage.read_raw(path)
        day = date.fromisoformat(bundle["date"])
        storage.write_tables(args.data_dir, day, parse_bundle(bundle))
        count += 1
    log.info("rebuilt tables for %d days", count)
    return 0


def cmd_github_commit(args: argparse.Namespace) -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log.error("GITHUB_TOKEN is not set")
        return 2
    github_commit.commit_changes(
        args.repo_dir, repo=args.repo, branch=args.branch, message=args.message, token=token
    )
    return 0


def cmd_corrections(args: argparse.Namespace) -> int:
    history = History.from_tables(args.data_dir, since=window_start(args.date))
    rows = analysis.corrected_card_rows(args.data_dir, args.date, history)
    if args.changed_only:
        rows = [r for r in rows if r["adjustment"]]
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]) if rows else ["date"])
    writer.writeheader()
    writer.writerows(rows)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    history = History.from_tables(args.data_dir, since=window_start(args.date))
    ratings_for = None
    if args.rating:
        orders = rating.load_orders(args.data_dir)
        state: dict[str, dict[str, float]] = {}

        def ratings_for(day: date) -> dict[str, float]:
            fitted = rating.fit(orders, day, half_life=args.half_life, init=state.get("theta"))
            state["theta"] = fitted.theta  # warm start for the next day
            return fitted.theta

    result = analysis.evaluate(
        args.data_dir,
        args.date,
        args.to,
        history,
        ratings_for=ratings_for,
        require_complete=not args.include_incomplete,
    )
    for group, scores in result.items():
        pairs = next(iter(scores.values())).pairs
        rates = " ".join(f"{m}={c.rate:.4f}" for m, c in scores.items())
        print(f"{group:9s} pairs={pairs:8.0f} {rates}")
    return 0


def cmd_rating(args: argparse.Namespace) -> int:
    as_of = args.as_of or today_jst()
    fitted = rating.fit(rating.load_orders(args.data_dir), as_of, half_life=args.half_life)
    cards = rating.latest_cards(args.data_dir, as_of)
    maps = fitted.calibration({r: float(c["score"]) for r, c in cards.items()})
    rows = sorted(fitted.theta.items(), key=lambda kv: kv[1], reverse=True)
    for pool in ("men", "L"):
        members = [
            (r, t) for r, t in rows
            if (fitted.tier.get(r) == "L") == (pool == "L") and fitted.weight[r] >= args.min_races
        ]  # fmt: skip
        print(f"== {pool} ({len(members)} riders, calibration={maps.get(pool)})")
        for r, t in members[: args.top]:
            card = cards.get(r, {})
            print(
                f"{card.get('racer_name', r):10s} {card.get('class', ''):3s} "
                f"theta={t:+.3f} score_eq={fitted.score_equivalent(r, maps)} "
                f"official={card.get('score', '')} races={fitted.weight[r]:.1f}"
            )
    return 0


def cmd_estimate_deltas(args: argparse.Namespace) -> int:
    estimates = analysis.estimate_deltas(args.data_dir, args.boundary, args.min_races)
    if not estimates:
        log.error("not enough data around %s", args.boundary)
        return 1
    for e in estimates:
        print(
            f"{e.from_tier:>3s} -> {e.to_tier:<3s} delta={e.delta:+6.2f} "
            f"(stderr {e.stderr:.2f}, movers={e.movers}, stayers={e.stayers})"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keirin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="fetch race days from keirin.jp into the data dir")
    p.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD (default: yesterday in JST)")
    p.add_argument("--to", type=date.fromisoformat, help="last day of a range (inclusive)")
    p.add_argument("--data-dir", type=Path, default=Path("."))
    p.add_argument("--force", action="store_true", help="re-fetch days already collected")
    p.add_argument(
        "--max-days", type=int, help="fetch at most N days this run (newest missing days first)"
    )
    p.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="minimum seconds between requests (default: 1.0)",
    )
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("rebuild", help="regenerate tables from stored raw data")
    p.add_argument("--data-dir", type=Path, default=Path("."))
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser(
        "github-commit", help="commit working tree changes via the GitHub API (signed)"
    )
    p.add_argument("--repo-dir", type=Path, default=Path("."))
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--branch", required=True)
    p.add_argument("--message", required=True)
    p.set_defaults(func=cmd_github_commit)

    p = sub.add_parser("corrections", help="print the day's race cards with corrected scores (CSV)")
    p.add_argument("--date", type=date.fromisoformat, required=True)
    p.add_argument("--data-dir", type=Path, default=Path("."))
    p.add_argument("--changed-only", action="store_true", help="only riders with a correction")
    p.set_defaults(func=cmd_corrections)

    p = sub.add_parser("evaluate", help="compare official vs corrected scores against results")
    p.add_argument("--date", type=date.fromisoformat, required=True, help="first day")
    p.add_argument("--to", type=date.fromisoformat, required=True, help="last day (inclusive)")
    p.add_argument("--data-dir", type=Path, default=Path("."))
    p.add_argument("--rating", action="store_true", help="also evaluate the rating")
    p.add_argument("--half-life", type=float, default=rating.DEFAULT_HALF_LIFE)
    p.add_argument(
        "--include-incomplete",
        action="store_true",
        help="also count races whose score windows are not fully collected",
    )
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("rating", help="fit and print rider ratings (opponent-aware)")
    p.add_argument("--as-of", type=date.fromisoformat, help="use races before this day")
    p.add_argument("--half-life", type=float, default=rating.DEFAULT_HALF_LIFE)
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-races", type=float, default=3.0)
    p.add_argument("--data-dir", type=Path, default=Path("."))
    p.set_defaults(func=cmd_rating)

    p = sub.add_parser("estimate-deltas", help="estimate tier deltas from a class change")
    p.add_argument("--boundary", type=date.fromisoformat, required=True, help="e.g. 2026-07-01")
    p.add_argument("--min-races", type=int, default=6)
    p.add_argument("--data-dir", type=Path, default=Path("."))
    p.set_defaults(func=cmd_estimate_deltas)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
