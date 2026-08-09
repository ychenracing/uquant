#!/usr/bin/env python3
"""Run the three frozen legacy systems through one comparable evidence schema.

The script is intentionally outside the production package.  It requires
read-only checkouts of qwenquant, AQuant and trade, and writes only a benchmark
artifact.  Production code never imports or shells out to those projects.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import io
import json
import math
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from unified_ai_quant.validation.provenance import bounded_data_fingerprint

INITIAL_CASH = 2_000_000.0
POOLS: dict[str, tuple[str, ...]] = {
    "a": ("sz300308", "sz300502", "sz300394"),
    "b": ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    "c": (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688008",
        "sh603986",
        "sz002409",
        "sh688072",
        "sh688300",
        "sz300054",
    ),
    "d": (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688498",
        "sh601869",
        "sh688256",
        "sh688008",
        "sh603986",
        "sh688072",
        "sh688082",
        "sh688120",
        "sh688300",
        "sz300054",
        "sh688361",
        "sz300604",
    ),
    "e": (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688498",
        "sz002281",
        "sh601869",
        "sh600487",
        "sh688256",
        "sh688041",
        "sh688008",
        "sh603986",
        "sz300223",
        "sh688110",
        "sh688766",
        "sz002371",
        "sh688012",
        "sh688072",
        "sh688082",
        "sh688120",
        "sh688037",
        "sh688361",
        "sz300604",
        "sh688200",
        "sh688019",
        "sz300054",
        "sz002409",
        "sz300666",
        "sh688233",
        "sh688268",
        "sh688146",
        "sh688300",
        "sh603688",
    ),
}
WINDOWS: dict[str, tuple[str, str]] = {
    "bear_2018": ("2018-01-02", "2018-12-28"),
    "crash_2020": ("2020-01-02", "2020-12-31"),
    "rotation_2021": ("2021-01-04", "2021-12-31"),
    "bear_2022": ("2022-01-04", "2022-12-30"),
    "mixed_2023": ("2023-01-03", "2023-12-29"),
    "choppy_2024": ("2024-01-02", "2024-12-31"),
    "bull": ("2025-04-01", "2026-06-30"),
    "through_july": ("2025-04-01", "2026-07-20"),
    "continuous_full": ("2018-01-02", "2026-07-20"),
}
RISK_TOKENS = (
    "risk",
    "drawdown",
    "stop",
    "shock",
    "hazard",
    "sector",
    "regime",
    "defensive",
    "lock",
    "catastrophe",
    "choppy",
    "transition",
)

# The frozen Trade engine deliberately fails closed when a symbol is missing
# from its internal metadata table.  These labels feed its existing documented
# name-hint router; they do not change any strategy threshold or signal.
TRADE_NAME_HINTS = {
    "600487": "Hengtong optical communication",
    "603688": "Quartz silicon wafer",
    "688110": "Dosilicon memory",
    "688146": "electronic specialty gas",
    "688200": "Huafeng semiconductor equipment",
    "688233": "Shengong silicon wafer",
    "688766": "Primarius memory",
}


@dataclass(frozen=True, slots=True)
class Task:
    system: str
    pool: str
    window: str
    qwen_root: str
    aquant_root: str
    trade_root: str
    data_dir: str
    trade_data_dir: str


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, (np.integer, np.floating)):
        return _jsonable(value.item())
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(value).date())
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _equity_rows(series: pd.Series) -> list[dict[str, float | str]]:
    return [
        {"date": str(pd.Timestamp(date).date()), "equity": float(value)}
        for date, value in series.sort_index().items()
    ]


def _is_risk_reduction(side: str, reason: str) -> bool:
    normalized = reason.lower().replace("-", "_")
    return side.upper() == "SELL" and any(token in normalized for token in RISK_TOKENS)


def _visible_symbols(
    data_dir: Path, symbols: Sequence[str], as_of: str
) -> tuple[str, ...]:
    """Keep only securities visible at the window boundary.

    The frozen engines cannot model staggered listings without either leaking the
    future universe or delaying the whole portfolio start.  Excluding securities
    that list after the common start date is the conservative, causal adapter.
    """
    boundary = pd.Timestamp(as_of)
    visible: list[str] = []
    for symbol in symbols:
        candidates = (data_dir / f"{symbol}.csv", data_dir / f"{symbol[2:]}.csv")
        path = next((item for item in candidates if item.exists()), None)
        if path is None:
            raise FileNotFoundError(f"missing common-adapter data for {symbol}")
        dates = pd.read_csv(path, usecols=["date"])["date"]
        if not dates.empty and pd.Timestamp(dates.iloc[0]) <= boundary:
            visible.append(symbol)
    if not visible:
        raise RuntimeError("no symbols were visible in the requested legacy window")
    return tuple(visible)


def _standard(
    *,
    task: Task,
    effective_symbols: Sequence[str],
    final_wealth: float,
    max_drawdown: float,
    account_orders: int,
    equity_curve: list[dict[str, float | str]],
    fills: list[dict[str, Any]],
    risk_events: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reductions = [
        fill
        for fill in fills
        if _is_risk_reduction(str(fill.get("side", "")), str(fill.get("reason", "")))
    ]
    return {
        "system": task.system,
        "pool": task.pool,
        "window": task.window,
        "requested_symbols": list(POOLS[task.pool]),
        "effective_symbols": list(effective_symbols),
        "start": WINDOWS[task.window][0],
        "end": WINDOWS[task.window][1],
        "final_wealth": final_wealth,
        "total_return": final_wealth - 1.0,
        "max_drawdown": abs(max_drawdown),
        "account_orders": account_orders,
        "equity_curve": equity_curve,
        "fills": fills,
        "risk_reductions": reductions,
        "risk_events": risk_events,
        "replacements": replacements,
        "extra": extra or {},
    }


def _run_qwen(task: Task) -> dict[str, Any]:
    sys.path.insert(0, task.qwen_root)
    from qwenquant import BacktestEngine  # type: ignore[import-not-found]
    from qwenquant.optimize.presets import (  # type: ignore[import-not-found]
        preset_for_universe,
    )

    class NextOpenOnly(BacktestEngine):
        def _intraday_risk(
            self,
            panel: Any,
            features: Any,
            router: Any,
            i: int,
            positions: Any,
            cash: float,
            fills: Any,
            date: str,
        ) -> float:
            return cash

    start, end = WINDOWS[task.window]
    symbols = _visible_symbols(Path(task.data_dir), POOLS[task.pool], start)
    result = NextOpenOnly(
        preset_for_universe(len(symbols)),
        data_dir=task.data_dir,
    ).run(list(symbols), start, end, init_cash=INITIAL_CASH)
    fills = [
        {
            "fill_date": fill.date,
            "signal_date": None,
            "symbol": fill.symbol,
            "side": fill.side.value.upper(),
            "reason": fill.reason.value,
            "price": float(fill.price),
            "shares": int(fill.shares),
        }
        for fill in result.fills
    ]
    curve = [
        {
            "date": item.date,
            "equity": float(item.equity),
            "gross": float(item.gross_exposure),
        }
        for item in result.equity_curve
    ]
    replacements = [fill for fill in fills if fill["reason"] == "rotation"]
    return _standard(
        task=task,
        effective_symbols=symbols,
        final_wealth=float(result.final_equity / INITIAL_CASH),
        max_drawdown=float(result.max_drawdown),
        account_orders=int(result.n_trades),
        equity_curve=curve,
        fills=fills,
        risk_events=[],
        replacements=replacements,
        extra={"execution_model": "close_signal_next_open; intraday exits disabled"},
    )


def _run_aquant(task: Task) -> dict[str, Any]:
    os.environ["AQUANT_DATA_DIR"] = task.data_dir
    sys.path.insert(0, task.aquant_root)
    replay = importlib.import_module("aquant.auto_daily_replay")
    start, end = WINDOWS[task.window]
    requested = POOLS[task.pool]
    loaded_panel = replay._preload_panel(tuple(sorted(requested)))
    # AQuant preloads a fixed reference basket and then treats a security with no
    # row yet as corrupt input.  Under the common point-in-time contract, a
    # later listing is instead invisible.  Keep the start-date-visible slice for
    # the whole comparison window so the adapter neither leaks future members
    # nor delays the account start.
    panel = {
        symbol: frame
        for symbol, frame in loaded_panel.items()
        if pd.Timestamp(frame.index.min()) <= pd.Timestamp(start)
    }
    symbols = tuple(
        symbol
        for symbol in requested
        if symbol in panel
    )
    captured: dict[str, list[dict[str, float | str]]] = {}
    original_metrics = replay._performance_metrics
    original_route = replay.daily._automatic_route

    def capture_metrics(
        equity: pd.Series,
        fills: Any,
        leader_events: Any,
        loaded: Any,
        init_cash: float,
    ) -> dict[str, object]:
        captured["equity_curve"] = _equity_rows(equity)
        return original_metrics(equity, fills, leader_events, loaded, init_cash)

    def point_in_time_route(args: Any, *, panel: Any = None) -> Any:
        params, automatic, explanation = original_route(args, panel=panel)
        params = dict(params)
        params["sector_guard_symbols"] = tuple(
            symbol
            for symbol in params.get("sector_guard_symbols", ())
            if panel is not None and symbol in panel
        )
        if not params["sector_guard_symbols"]:
            params["sector_guard"] = False
        return params, automatic, explanation

    replay._performance_metrics = capture_metrics
    replay.daily._automatic_route = point_in_time_route
    try:
        result = replay.run_auto_daily_replay(
            symbols,
            start,
            end,
            init_cash=INITIAL_CASH,
            production_policy_enabled=True,
            panel=panel,
        )
    finally:
        replay._performance_metrics = original_metrics
        replay.daily._automatic_route = original_route
    metrics = result["metrics"]
    fills = [
        {
            "fill_date": str(fill["date"]),
            "signal_date": str(fill["signal_date"]),
            "symbol": str(fill["symbol"]),
            "side": str(fill["side"]).upper(),
            "reason": str(fill["reason"]),
            "price": float(fill["price"]),
            "shares": int(fill["shares"]),
        }
        for fill in result["fills"]
    ]
    order_keys = {
        (fill["fill_date"], fill["symbol"], fill["side"])
        for fill in fills
    }
    return _standard(
        task=task,
        effective_symbols=symbols,
        final_wealth=float(metrics["final_assets"]) / INITIAL_CASH,
        max_drawdown=float(metrics["max_drawdown"]),
        account_orders=len(order_keys),
        equity_curve=captured["equity_curve"],
        fills=fills,
        risk_events=[],
        replacements=_jsonable(result["leader_replacements"]),
        extra={
            "execution_model": "manual_close_next_open",
            "decision_digest": result["decision_digest"],
            "false_exit_regret_20d": metrics["average_false_exit_regret_20d"],
            "replacement_regret_20d": metrics[
                "average_leader_replacement_regret_20d"
            ],
        },
    )


def _run_trade(task: Task) -> dict[str, Any]:
    sys.path.insert(0, task.trade_root)
    route = importlib.import_module("regime_adaptive")
    start, end = WINDOWS[task.window]
    prefixed = _visible_symbols(Path(task.data_dir), POOLS[task.pool], start)
    symbols = tuple(symbol[2:] for symbol in prefixed)
    base_policy = route.qf.PortfolioPolicy()
    reference_symbols = tuple(
        ("sh" if symbol.startswith(("6", "9")) else "sz") + symbol
        for symbol in base_policy.regime_symbols
    )
    visible_references = _visible_symbols(
        Path(task.data_dir), reference_symbols, start
    )
    policy = replace(
        base_policy,
        regime_symbols=tuple(symbol[2:] for symbol in visible_references),
    )
    symbol_names = {
        symbol: TRADE_NAME_HINTS.get(symbol, symbol) for symbol in symbols
    }
    with contextlib.redirect_stdout(io.StringIO()):
        result = route.ProductionReplayEngine(INITIAL_CASH, policy=policy).run(
            symbol_names,
            start,
            end,
            data_dir=task.trade_data_dir,
            regime_data_dir=task.trade_data_dir,
            leader_data_dir=task.trade_data_dir,
            indicator_state="warm",
        )
    fills = [
        {
            "fill_date": str(fill.date),
            "signal_date": str(fill.signal_date),
            "symbol": str(fill.symbol),
            "side": str(fill.direction).upper(),
            "reason": str(fill.reason),
            "price": float(fill.price),
            "shares": int(fill.shares),
        }
        for fill in result["trades"]
    ]
    replacements = [
        fill
        for fill in fills
        if "rotation" in fill["reason"].lower()
        or "replacement" in fill["reason"].lower()
        or "sticky" in fill["reason"].lower()
    ]
    curve = _equity_rows(result["equity_curve"]["assets"])
    return _standard(
        task=task,
        effective_symbols=prefixed,
        final_wealth=float(result["final_assets"]) / INITIAL_CASH,
        max_drawdown=float(result["max_drawdown"]),
        account_orders=int(result["total_trades"]),
        equity_curve=curve,
        fills=fills,
        risk_events=_jsonable(result["risk_events"]),
        replacements=replacements,
        extra={
            "execution_model": "ProductionReplayEngine next-open",
            "name_hint_metadata": {
                symbol: name
                for symbol, name in symbol_names.items()
                if symbol in TRADE_NAME_HINTS
            },
            "sleeve_fill_count": int(result["sleeve_fill_count"]),
            "route_sequence": _jsonable(result["route_sequence"]),
        },
    )


def _run(task: Task) -> dict[str, Any]:
    try:
        row = {
            "qwenquant": _run_qwen,
            "aquant": _run_aquant,
            "trade": _run_trade,
        }[task.system](task)
    except Exception as exc:
        # Frozen packages define custom exception classes that are unavailable
        # in the parent process.  Re-raise a built-in error so failures remain
        # attributable instead of breaking ProcessPool deserialization.
        raise RuntimeError(
            f"{task.system}/{task.pool}/{task.window}: "
            f"{type(exc).__name__}: {exc}"
        ) from None
    return _jsonable(row)


def _stage_bounded_data(source: Path, destination: Path, *, end: str) -> int:
    """Copy canonical CSV bytes through ``end`` into an isolated temp snapshot."""
    destination.mkdir(parents=True, exist_ok=True)
    staged = 0
    cutoff = end.encode("ascii")
    for path in sorted(source.glob("*.csv")):
        target = destination / path.name
        with path.open("rb") as reader, target.open("wb") as writer:
            header = reader.readline()
            if not header.lower().startswith(b"date,"):
                raise RuntimeError(f"canonical CSV lacks a date header: {path}")
            writer.write(header)
            for line in reader:
                date, separator, _ = line.partition(b",")
                if not separator:
                    raise RuntimeError(f"malformed canonical CSV row: {path}")
                if date <= cutoff:
                    writer.write(line)
        staged += 1
    if staged == 0:
        raise RuntimeError(f"no canonical market-data CSVs found in {source}")
    return staged


def _stage_trade_data(source: Path, destination: Path) -> int:
    """Expose bounded prefixed CSVs under the legacy six-digit filenames."""
    destination.mkdir(parents=True, exist_ok=True)
    staged = 0
    for path in sorted(source.glob("*.csv")):
        if len(path.stem) != 8 or path.stem[:2] not in {"sh", "sz"}:
            continue
        target = destination / f"{path.stem[2:]}.csv"
        if target.exists():
            raise RuntimeError(f"duplicate legacy symbol filename: {target.name}")
        target.symlink_to(path.resolve())
        staged += 1
    if staged == 0:
        raise RuntimeError(f"no canonical market-data CSVs found in {source}")
    return staged


def _execute_matrix(
    args: argparse.Namespace,
    roots: dict[str, Path],
    canonical_data_dir: Path,
    trade_data_dir: Path,
) -> int:
    tasks = [
        Task(
            system,
            pool,
            window,
            str(args.qwen_root),
            str(args.aquant_root),
            str(args.trade_root),
            str(canonical_data_dir),
            str(trade_data_dir),
        )
        for system in args.systems
        for pool in POOLS
        for window in args.windows
    ]
    print(
        f"legacy adapter: executing {len(tasks)} cells with {args.workers} workers",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = []
        for index, row in enumerate(executor.map(_run, tasks, chunksize=1), start=1):
            rows.append(row)
            print(f"legacy adapter: {index}/{len(tasks)}", flush=True)
    rows.sort(key=lambda row: (row["system"], row["pool"], row["window"]))
    payload = {
        "schema_version": 1,
        "contract": {
            "initial_cash": INITIAL_CASH,
            "signal": "close_t",
            "execution": "next_tradable_open",
            "intraday_exit": False,
            "prelisting": "invisible until first observable row",
        },
        "source_hashes": {
            system: _source_hash(roots[system]) for system in args.systems
        },
        "data_provenance": {
            "through": max(end for _, end in WINDOWS.values()),
            "sha256": bounded_data_fingerprint(
                args.data_dir,
                end=max(end for _, end in WINDOWS.values()),
            ),
        },
        "systems": list(args.systems),
        "windows": list(args.windows),
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"legacy adapter: wrote {args.output}", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    workspace = root.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--systems",
        nargs="+",
        choices=("qwenquant", "aquant", "trade"),
        default=("qwenquant", "aquant", "trade"),
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        choices=tuple(WINDOWS),
        default=tuple(WINDOWS),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--qwen-root",
        type=Path,
        default=workspace / "frozen_benchmarks/qwenquant",
    )
    parser.add_argument(
        "--aquant-root",
        type=Path,
        default=workspace / "frozen_benchmarks/aquant",
    )
    parser.add_argument(
        "--trade-root",
        type=Path,
        default=workspace / "frozen_benchmarks/trade",
    )
    parser.add_argument("--data-dir", type=Path, default=root / "data/frozen")
    parser.add_argument(
        "--trade-data-dir",
        type=Path,
        help=(
            "optional directory of unprefixed six-digit CSVs; when omitted, "
            "the adapter stages read-only links from --data-dir"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "benchmarks/legacy_common_adapter.json",
    )
    args = parser.parse_args(argv)
    roots = {
        "qwenquant": args.qwen_root,
        "aquant": args.aquant_root,
        "trade": args.trade_root,
    }
    for system in args.systems:
        if not roots[system].exists():
            parser.error(f"frozen {system} checkout does not exist: {roots[system]}")
    if args.trade_data_dir is not None:
        if not args.trade_data_dir.is_dir():
            parser.error(f"trade data directory does not exist: {args.trade_data_dir}")
    comparison_end = max(end for _, end in WINDOWS.values())
    with tempfile.TemporaryDirectory(prefix="legacy-bounded-data-") as temporary:
        canonical_data_dir = Path(temporary)
        staged = _stage_bounded_data(
            args.data_dir,
            canonical_data_dir,
            end=comparison_end,
        )
        print(
            f"legacy adapter: staged {staged} canonical CSVs through {comparison_end}",
            flush=True,
        )
        if args.trade_data_dir is not None:
            return _execute_matrix(
                args,
                roots,
                canonical_data_dir,
                args.trade_data_dir,
            )
        with tempfile.TemporaryDirectory(prefix="legacy-trade-data-") as trade_temp:
            trade_data_dir = Path(trade_temp)
            linked = _stage_trade_data(canonical_data_dir, trade_data_dir)
            print(
                f"legacy adapter: staged {linked} legacy filename links",
                flush=True,
            )
            return _execute_matrix(
                args,
                roots,
                canonical_data_dir,
                trade_data_dir,
            )


if __name__ == "__main__":
    raise SystemExit(main())
