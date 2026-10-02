from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")


def today_jst() -> date:
    return datetime.now(JST).date()


def yesterday_jst() -> date:
    return today_jst() - timedelta(days=1)


def date_range(start: date, end: date) -> Iterator[date]:
    """Inclusive range; yields nothing when end < start."""
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)
