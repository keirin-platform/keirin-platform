"""File layout of the (private) data repository.

    raw/YYYY/YYYY-MM-DD.json.gz          raw API responses for one day
    tables/<table>/YYYY/YYYY-MM-DD.csv   normalized tables derived from raw

Files are written deterministically (sorted rows, gzip mtime fixed to 0) so
that re-collecting or rebuilding an unchanged day produces no git diff.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from keirin.parse import TABLES


def raw_path(data_dir: Path, day: date) -> Path:
    return data_dir / "raw" / f"{day:%Y}" / f"{day.isoformat()}.json.gz"


def table_path(data_dir: Path, table: str, day: date) -> Path:
    return data_dir / "tables" / table / f"{day:%Y}" / f"{day.isoformat()}.csv"


def write_raw(data_dir: Path, bundle: dict[str, Any]) -> Path:
    path = raw_path(data_dir, date.fromisoformat(bundle["date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True).encode()
    with path.open("wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
        gz.write(payload)
    return path


def read_raw(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rb") as gz:
        return json.loads(gz.read())


def iter_raw_paths(data_dir: Path) -> Iterator[Path]:
    yield from sorted((data_dir / "raw").glob("*/*.json.gz"))


def write_tables(data_dir: Path, day: date, tables: dict[str, list[dict[str, Any]]]) -> None:
    for name, columns in TABLES.items():
        path = table_path(data_dir, name, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=columns, extrasaction="raise", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(tables.get(name, []))
