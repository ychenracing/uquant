#!/usr/bin/env python3
"""Complete a sealed date window without replacing any existing market row.

This is an operational data-repair utility, not a strategy evaluator.  It
downloads the requested bounded Tencent window, verifies every overlapping
OHLC anchor, appends only dates after each file's frozen last row, and writes a
new staging directory.  It never mutates the source directory and never emits
prices or strategy metrics.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Sequence

from backfill_tencent_history import ENDPOINT, _fetch, _format_volume

from unified_ai_quant.data import DataStore

PRICE_FIELDS = ("open", "high", "low", "close")
QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Completion:
    symbol: str
    fetched_sessions: int
    existing_sessions: int
    appended_sessions: int
    anchor_max_relative_difference: float
    splice_scale: float


def _price(value: Decimal) -> str:
    rendered = format(value.quantize(QUANTUM, rounding=ROUND_HALF_UP), "f")
    rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _complete_one(
    task: tuple[Path, Path, str, str, int],
) -> Completion:
    source, destination, start, end, expected_sessions = task
    symbol = source.stem
    fetched = _fetch(symbol, start, end, count=max(40, expected_sessions + 5))
    fetched_by_date = {row[0]: row for row in fetched}
    if len(fetched_by_date) != expected_sessions:
        raise RuntimeError(
            f"{symbol} returned {len(fetched_by_date)} sessions, "
            f"expected {expected_sessions}"
        )

    existing_text = source.read_text(encoding="utf-8")
    existing_rows = list(csv.DictReader(existing_text.splitlines()))
    existing_by_date = {row["date"]: row for row in existing_rows}
    existing_window = {
        date: row for date, row in existing_by_date.items() if start <= date <= end
    }
    overlap = sorted(set(fetched_by_date) & set(existing_window))
    if not overlap:
        raise RuntimeError(f"{symbol} has no overlap anchor in the completion window")

    anchor_date = overlap[-1]
    fetched_anchor = fetched_by_date[anchor_date]
    fetched_anchor_prices = dict(
        zip(PRICE_FIELDS, map(Decimal, fetched_anchor[1:5]), strict=True)
    )
    existing_anchor = existing_window[anchor_date]
    scale = Decimal(existing_anchor["close"]) / fetched_anchor_prices["close"]
    if not scale.is_finite() or scale <= 0:
        raise RuntimeError(f"{symbol} produced an invalid splice scale")

    relative_differences: list[float] = []
    for date in overlap:
        raw = fetched_by_date[date]
        fetched_prices = dict(zip(PRICE_FIELDS, map(Decimal, raw[1:5]), strict=True))
        for field in PRICE_FIELDS:
            old = Decimal(existing_window[date][field])
            adjusted = fetched_prices[field] * scale
            relative_differences.append(
                float(abs(old - adjusted) / max(abs(old), Decimal("0.000001")))
            )
    anchor_difference = max(relative_differences, default=0.0)
    if anchor_difference > 0.005:
        raise RuntimeError(
            f"{symbol} scaled overlap changed by {anchor_difference:.4%}; refusing splice"
        )

    last_existing = existing_rows[-1]["date"]
    missing = [
        fetched_by_date[date]
        for date in sorted(fetched_by_date)
        if date not in existing_by_date
    ]
    if any(row[0] <= last_existing for row in missing):
        raise RuntimeError(f"{symbol} completion would insert inside frozen history")

    additions: list[str] = []
    for date, open_, high, low, close, volume in missing:
        adjusted = {
            field: Decimal(value) * scale
            for field, value in zip(
                PRICE_FIELDS,
                (open_, high, low, close),
                strict=True,
            )
        }
        values = [float(adjusted[field]) for field in PRICE_FIELDS]
        if (
            not all(math.isfinite(value) and value > 0 for value in values)
            or values[1] < max(values[0], values[2], values[3])
            or values[2] > min(values[0], values[1], values[3])
        ):
            raise RuntimeError(f"{symbol} returned invalid adjusted OHLC on {date}")
        additions.append(
            ",".join(
                (
                    date,
                    _price(adjusted["open"]),
                    _price(adjusted["high"]),
                    _price(adjusted["low"]),
                    _price(adjusted["close"]),
                    _format_volume(symbol, volume),
                    "",
                )
            )
            + "\n"
        )

    destination.write_text(existing_text + "".join(additions), encoding="utf-8")
    return Completion(
        symbol=symbol,
        fetched_sessions=len(fetched_by_date),
        existing_sessions=len(existing_window),
        appended_sessions=len(additions),
        anchor_max_relative_difference=anchor_difference,
        splice_scale=float(scale),
    )


def _update_metadata(
    source_dir: Path,
    output_dir: Path,
    completions: list[Completion],
    *,
    start: str,
    end: str,
) -> None:
    manifest_path = source_dir / "DATA_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc)
    completion_by_symbol = {item.symbol: item for item in completions}
    results = []
    sums = []
    for row in manifest["results"]:
        symbol = str(row["symbol"])
        path = output_dir / f"{symbol}.csv"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(encoding="utf-8") as stream:
            data_rows = sum(1 for _ in stream) - 1
        updated = dict(row)
        updated.update(
            {
                "last_date": end,
                "total_rows": data_rows,
                "sha256": digest,
                "holdback_rows_appended": completion_by_symbol[
                    symbol
                ].appended_sessions,
            }
        )
        results.append(updated)
        sums.append(f"{digest}  {symbol}.csv\n")
    manifest.update(
        {
            "snapshot_id": generated_at.strftime(
                "%Y%m%dT%H%M%SZ-holdback-coverage-completion"
            ),
            "generated_at_utc": generated_at.isoformat(),
            "endpoint": ENDPOINT,
            "incremental_completion": {
                "window": {"start": start, "end": end},
                "policy": "append missing provider rows only; preserve every existing CSV byte",
                "strategy_metrics_observed": False,
            },
            "results": results,
        }
    )
    (output_dir / "DATA_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "SHA256SUMS").write_text("".join(sums), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--expected-sessions", type=int, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error("output directory must not already exist")
    if args.source_dir.resolve() == args.output_dir.resolve():
        parser.error("source and output directories must differ")
    if args.expected_sessions < 2 or args.workers < 1:
        parser.error("expected sessions and workers must be positive")

    csv_paths = sorted(args.source_dir.glob("*.csv"))
    if not csv_paths:
        parser.error("source directory contains no CSV files")
    shutil.copytree(args.source_dir, args.output_dir)
    tasks = [
        (
            source,
            args.output_dir / source.name,
            args.start,
            args.end,
            args.expected_sessions,
        )
        for source in csv_paths
    ]
    try:
        with ThreadPoolExecutor(max_workers=min(args.workers, len(tasks))) as pool:
            completions = list(pool.map(_complete_one, tasks))
        _update_metadata(
            args.source_dir,
            args.output_dir,
            completions,
            start=args.start,
            end=args.end,
        )
        store = DataStore(args.output_dir)
        for path in csv_paths:
            store.load(path.stem)
    except Exception:
        shutil.rmtree(args.output_dir, ignore_errors=True)
        raise

    for item in sorted(completions, key=lambda value: value.symbol):
        print(
            f"{item.symbol}: existing={item.existing_sessions}, "
            f"appended={item.appended_sessions}, "
            f"anchor_diff={item.anchor_max_relative_difference:.6%}, "
            f"scale={item.splice_scale:.9f}"
        )
    print(
        f"completed {len(completions)} files; "
        f"appended {sum(item.appended_sessions for item in completions)} rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
