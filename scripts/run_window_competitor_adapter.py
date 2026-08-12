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
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

INITIAL_CASH = 2_000_000.0
TARGET_START = "2025-01-02"
TARGET_END = "2026-07-31"
WINDOWS: dict[str, tuple[str, str]] = {"target": (TARGET_START, TARGET_END)}
SYSTEMS = ("aquant", "qwenquant", "trade")
LOCKED_SOURCES = {
    "aquant": {
        "repository": "ychenracing/aquant",
        "commit": "3c38fbbf679a0fb1b4ee8f3d47b6931d3eb8fdbd",
        "python_sha256": "0fdc39c40239e51b5c91024507bef1bed222cd83575e4d9f870b8ada2f73a50a",
    },
    "qwenquant": {
        "repository": "ychenracing/qwenquant",
        "commit": "0b3681e10b75425ad8600e75835677a6a125ed13",
        "python_sha256": "66fc531989e294990d40dae5f0c0ff867fe4e144ab2bae81863b42e7113c46c0",
    },
    "trade": {
        "repository": "ychenracing/trade",
        "commit": "cee1620f40af3af8f839e15db188a9e388a78dd0",
        "python_sha256": "03e33e1396ca31d61e724bcd9cf58971ae656134740eb8929313167aa8ed8597",
    },
}


def _load_pools() -> dict[str, tuple[str, ...]]:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "benchmarks" / "promotion_baseline.json").read_text(encoding="utf-8")
    )
    raw = payload.get("pools")
    if not isinstance(raw, dict) or set(raw) != {"a", "b", "c", "d", "e"}:
        raise RuntimeError("promotion baseline must define exactly pools A-E")
    pools: dict[str, tuple[str, ...]] = {}
    for name in ("a", "b", "c", "d", "e"):
        symbols = raw[name]
        if not isinstance(symbols, list) or not symbols or len(symbols) != len(set(symbols)):
            raise RuntimeError(f"promotion pool is malformed: {name}")
        pools[name] = tuple(str(symbol) for symbol in symbols)
    return pools


POOLS = _load_pools()


def _default_source_roots() -> dict[str, Path]:
    repository_root = Path(__file__).resolve().parents[1]
    competitor_root = repository_root.parents[1] / "competitors"
    return {system: competitor_root / system for system in SYSTEMS}
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


def _validate_source_roots(roots: dict[str, Path], systems: Sequence[str]) -> None:
    for system in systems:
        root = roots[system]
        if not root.is_dir():
            raise RuntimeError(f"frozen {system} checkout does not exist: {root}")
        marker = root / ".frozen_commit"
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != LOCKED_SOURCES[system]["commit"]:
            raise RuntimeError(f"frozen {system} commit marker mismatch")
        observed = _source_hash(root)
        expected = LOCKED_SOURCES[system]["python_sha256"]
        if observed != expected:
            raise RuntimeError(
                f"frozen {system} Python source hash mismatch: expected={expected}, observed={observed}"
            )


def _bounded_data_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(root.glob("*.csv"), key=lambda path: path.name)
    if not paths:
        raise RuntimeError(f"no bounded market-data CSVs found in {root}")
    for path in paths:
        digest.update(path.name.encode("utf-8"))
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


def _submission_key(signal_date: str, symbol: str, side: str) -> tuple[str, str, str]:
    """Return the common broker-account netting key.

    The three frozen engines expose different internal fill ledgers.  A real
    account, however, submits at most one same-side instruction for a symbol
    from one close.  This key merges virtual sleeves/tranches while preserving
    a genuinely new instruction produced at a later close.
    """
    return (str(signal_date), str(symbol), str(side).upper())


def _order_ledger(
    submissions: Sequence[dict[str, Any]],
    fills: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a deterministic internal-intent ledger from audited submissions."""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for submission in submissions:
        key = _submission_key(
            str(submission["signal_date"]),
            str(submission["symbol"]),
            str(submission["side"]),
        )
        row = merged.setdefault(
            key,
            {
                "signal_date": key[0],
                "symbol": key[1],
                "side": key[2],
                "first_attempt_date": submission.get("attempt_date"),
                "attempt_dates": [],
                "internal_intents": 0,
                "status": "UNFILLED_OR_EXPIRED",
            },
        )
        attempt = submission.get("attempt_date")
        if attempt is not None and attempt not in row["attempt_dates"]:
            row["attempt_dates"].append(str(attempt))
        row["internal_intents"] += int(submission.get("internal_intents", 1))

    filled_keys = {
        _submission_key(
            str(fill.get("signal_date", "")),
            str(fill.get("symbol", "")),
            str(fill.get("side", "")),
        )
        for fill in fills
        if fill.get("signal_date") not in (None, "", "None")
    }
    filled_attempts = {
        (
            str(fill.get("fill_date", "")),
            str(fill.get("symbol", "")),
            str(fill.get("side", "")).upper(),
        )
        for fill in fills
        if fill.get("fill_date") not in (None, "", "None")
    }
    for key, row in merged.items():
        row["attempt_dates"].sort()
        if row["first_attempt_date"] is None and row["attempt_dates"]:
            row["first_attempt_date"] = row["attempt_dates"][0]
        if key in filled_keys or any(
            (attempt, key[1], key[2]) in filled_attempts
            for attempt in row["attempt_dates"]
        ):
            row["status"] = "FILLED"
    return [merged[key] for key in sorted(merged)]


def _broker_order_ledger(
    system: str,
    fills: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Net executed sleeve fills into comparable broker/account orders.

    The frozen Trade implementation defines an account order as one executed
    date/symbol/direction tuple. QwenQuant and AQuant expose one fill per such
    tuple, while Trade can expose several virtual-sleeve fills. Applying the
    same key to all three systems preserves real broker turnover and keeps
    unfilled strategy intents out of the user-cost metric.
    """
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for fill in fills:
        key = (
            str(fill["fill_date"]),
            str(fill["symbol"]),
            str(fill["side"]).upper(),
        )
        grouped.setdefault(key, []).append(dict(fill))

    order_ids = {
        key: f"{system.upper()}-{index:06d}"
        for index, key in enumerate(sorted(grouped), start=1)
    }
    ledger: list[dict[str, Any]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        signal_dates = sorted(
            {
                str(row["signal_date"])
                for row in rows
                if row.get("signal_date") not in (None, "", "None")
            }
        )
        ledger.append(
            {
                "order_id": order_ids[key],
                "signal_date": signal_dates[0] if signal_dates else None,
                "fill_date": key[0],
                "symbol": key[1],
                "side": key[2],
                "status": "FILLED",
                "filled_shares": sum(int(row["shares"]) for row in rows),
                "internal_fills": len(rows),
                "reasons": sorted({str(row.get("reason", "")) for row in rows}),
            }
        )

    linked_fills = []
    for fill in fills:
        row = dict(fill)
        key = (
            str(row["fill_date"]),
            str(row["symbol"]),
            str(row["side"]).upper(),
        )
        row["order_id"] = order_ids[key]
        linked_fills.append(row)
    return ledger, linked_fills


def _intent_diagnostics(
    submissions: Sequence[dict[str, Any]],
    fills: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize internal signals separately from broker account orders."""
    ledger = _order_ledger(submissions, fills)
    canonical = json.dumps(
        ledger,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "internal_submission_events": len(submissions),
        "unique_signal_intents": len(ledger),
        "filled_signal_intents": sum(row["status"] == "FILLED" for row in ledger),
        "unfilled_signal_intents": sum(
            row["status"] != "FILLED" for row in ledger
        ),
        "intent_ledger_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _link_missing_signal_dates(
    fills: Sequence[dict[str, Any]],
    submissions: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover a fill's close date from its unique next-open attempt."""
    candidates: dict[tuple[str, str, str], set[str]] = {}
    for submission in submissions:
        attempt = submission.get("attempt_date")
        if attempt in (None, "", "None"):
            continue
        key = (
            str(attempt),
            str(submission["symbol"]),
            str(submission["side"]).upper(),
        )
        candidates.setdefault(key, set()).add(str(submission["signal_date"]))

    linked: list[dict[str, Any]] = []
    for fill in fills:
        row = dict(fill)
        if row.get("signal_date") in (None, "", "None"):
            key = (
                str(row["fill_date"]),
                str(row["symbol"]),
                str(row["side"]).upper(),
            )
            signal_dates = candidates.get(key, set())
            if len(signal_dates) != 1:
                raise RuntimeError(
                    "fill does not map to exactly one close submission: "
                    f"key={key}, signal_dates={sorted(signal_dates)}"
                )
            row["signal_date"] = next(iter(signal_dates))
        if pd.Timestamp(str(row["signal_date"])) >= pd.Timestamp(str(row["fill_date"])):
            raise RuntimeError(f"fill violates next-open causality: {row}")
        linked.append(row)
    return linked


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


def _observable_sessions(
    panel: dict[str, pd.DataFrame],
    dates: pd.DatetimeIndex,
) -> pd.DatetimeIndex:
    """Limit a fixed basket calendar to sessions with at least one observation."""
    observable = pd.DatetimeIndex([])
    for frame in panel.values():
        observable = observable.union(pd.DatetimeIndex(frame.index))
    return pd.DatetimeIndex(dates).intersection(observable)


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
    order_ledger: list[dict[str, Any]] | None = None,
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
        "turnover": sum(
            abs(float(fill.get("price", 0.0)) * int(fill.get("shares", 0)))
            for fill in fills
        )
        / INITIAL_CASH,
        "order_ledger": order_ledger or [],
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
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.account_submissions: list[dict[str, Any]] = []

        def _execute_pending(
            self,
            panel: Any,
            features: Any,
            orders: Any,
            i: int,
            positions: Any,
            cash: float,
            fills: Any,
            regime: Any,
        ) -> Any:
            signal_date = panel.dates[max(0, i - 1)]
            attempt_date = panel.dates[i]
            for order in orders:
                self.account_submissions.append(
                    {
                        "signal_date": signal_date,
                        "attempt_date": attempt_date,
                        "symbol": order.symbol,
                        "side": order.side.value,
                    }
                )
            return super()._execute_pending(
                panel,
                features,
                orders,
                i,
                positions,
                cash,
                fills,
                regime,
            )

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
    engine = NextOpenOnly(
        preset_for_universe(len(symbols)),
        data_dir=task.data_dir,
    )
    result, terminal = engine.run(
        list(symbols),
        start,
        end,
        init_cash=INITIAL_CASH,
        return_terminal=True,
    )
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
    fills = _link_missing_signal_dates(fills, engine.account_submissions)
    curve = [
        {
            "date": item.date,
            "equity": float(item.equity),
            "gross": float(item.gross_exposure),
        }
        for item in result.equity_curve
    ]
    replacements = [fill for fill in fills if fill["reason"] == "rotation"]
    for order in terminal.pending_orders:
        engine.account_submissions.append(
            {
                "signal_date": terminal.date,
                "attempt_date": None,
                "symbol": order.symbol,
                "side": order.side.value,
            }
        )
    intent_diagnostics = _intent_diagnostics(engine.account_submissions, fills)
    order_ledger, fills = _broker_order_ledger(task.system, fills)
    return _standard(
        task=task,
        effective_symbols=symbols,
        final_wealth=float(result.final_equity / INITIAL_CASH),
        max_drawdown=float(result.max_drawdown),
        account_orders=len(order_ledger),
        equity_curve=curve,
        fills=fills,
        risk_events=[],
        replacements=replacements,
        order_ledger=order_ledger,
        extra={
            "execution_model": "close_signal_next_open; intraday exits disabled",
            "account_order_count_method": "unique executed fill_date/symbol/side",
            "fill_count": int(result.n_trades),
            **intent_diagnostics,
        },
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
    submissions: list[dict[str, Any]] = []
    original_metrics = replay._performance_metrics
    original_route = replay.daily._automatic_route
    original_orders = replay._orders_from_report
    original_sector_observations = replay.daily.core.build_sector_observations
    sector_observation_cache: dict[
        tuple[tuple[str, ...], int, int], dict[pd.Timestamp, Any]
    ] = {}

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

    def capture_orders(report: dict[str, object], params: dict[str, Any]) -> Any:
        orders = original_orders(report, params)
        submissions.extend(

                {
                    "signal_date": str(order.signal_date.date()),
                    "attempt_date": None,
                    "symbol": order.symbol,
                    "side": (
                        "SELL"
                        if order.action in ("SELL_ALL", "REDUCE")
                        else "BUY"
                    ),
                }
                for order in orders

        )
        return orders

    def point_in_time_sector_observations(
        reference_panel: dict[str, pd.DataFrame],
        dates: pd.DatetimeIndex,
        *,
        shock_ma: int,
        recovery_ma: int,
    ) -> Any:
        # The broad index can trade on a date where every fixed-basket member
        # is suspended. Skipping that unobservable basket session is equivalent
        # to receiving no sector observation and never reads a future row.
        if dates.empty:
            return {}
        key = (tuple(sorted(reference_panel)), shock_ma, recovery_ma)
        if key not in sector_observation_cache:
            full_dates = panel["sh000300"].index
            full_dates = full_dates[full_dates <= pd.Timestamp(end)]
            sector_observation_cache[key] = original_sector_observations(
                reference_panel,
                _observable_sessions(reference_panel, full_dates),
                shock_ma=shock_ma,
                recovery_ma=recovery_ma,
            )
        cutoff = pd.Timestamp(dates.max())
        return {
            date: observation
            for date, observation in sector_observation_cache[key].items()
            if date <= cutoff
        }

    replay._performance_metrics = capture_metrics
    replay.daily._automatic_route = point_in_time_route
    replay._orders_from_report = capture_orders
    replay.daily.core.build_sector_observations = point_in_time_sector_observations
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
        replay._orders_from_report = original_orders
        replay.daily.core.build_sector_observations = original_sector_observations
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
    intent_diagnostics = _intent_diagnostics(submissions, fills)
    order_ledger, fills = _broker_order_ledger(task.system, fills)
    return _standard(
        task=task,
        effective_symbols=symbols,
        final_wealth=float(metrics["final_assets"]) / INITIAL_CASH,
        max_drawdown=float(metrics["max_drawdown"]),
        account_orders=len(order_ledger),
        equity_curve=captured["equity_curve"],
        fills=fills,
        risk_events=[],
        replacements=_jsonable(result["leader_replacements"]),
        order_ledger=order_ledger,
        extra={
            "execution_model": "manual_close_next_open",
            "account_order_count_method": "unique executed fill_date/symbol/side",
            "fill_count": len(fills),
            **intent_diagnostics,
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
    submissions: list[dict[str, Any]] = []
    sleeve_class = route.qf._CausalBacktestEngine
    original_execute = sleeve_class._execute_pending_signals

    def capture_pending(
        self: Any,
        pending: Any,
        data_map: Any,
        date: pd.Timestamp,
        date_to_pos: Any,
        directions: frozenset[str] | None = None,
    ) -> Any:
        allowed = directions or frozenset({"buy", "sell"})
        for signal, _ in pending:
            if signal.direction not in allowed:
                continue
            submissions.append(
                {
                    "signal_date": signal.signal_date,
                    "attempt_date": str(pd.Timestamp(date).date()),
                    "symbol": signal.symbol,
                    "side": signal.direction,
                }
            )
        return original_execute(
            self,
            pending,
            data_map,
            date,
            date_to_pos,
            directions,
        )

    sleeve_class._execute_pending_signals = capture_pending
    try:
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
    finally:
        sleeve_class._execute_pending_signals = original_execute
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
    submissions.extend(

            {
                "signal_date": signal.signal_date,
                "attempt_date": None,
                "symbol": signal.symbol,
                "side": signal.direction,
            }
            for signal in result.get("pending_signals", [])

    )
    intent_diagnostics = _intent_diagnostics(submissions, fills)
    order_ledger, fills = _broker_order_ledger(task.system, fills)
    curve = _equity_rows(result["equity_curve"]["assets"])
    return _standard(
        task=task,
        effective_symbols=prefixed,
        final_wealth=float(result["final_assets"]) / INITIAL_CASH,
        max_drawdown=float(result["max_drawdown"]),
        account_orders=len(order_ledger),
        equity_curve=curve,
        fills=fills,
        risk_events=_jsonable(result["risk_events"]),
        replacements=replacements,
        order_ledger=order_ledger,
        extra={
            "execution_model": "ProductionReplayEngine next-open",
            "account_order_count_method": "unique executed fill_date/symbol/side",
            "fill_merged_order_count": int(result["total_trades"]),
            **intent_diagnostics,
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
        for pool in args.pools
        for window in args.windows
    ]
    print(
        f"legacy adapter: executing {len(tasks)} cells with {args.workers} workers",
        flush=True,
    )
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        rows = []
        pending = {executor.submit(_run, task): task for task in tasks}
        for index, future in enumerate(as_completed(pending), start=1):
            task = pending[future]
            rows.append(future.result())
            print(
                f"legacy adapter: {index}/{len(tasks)} "
                f"latest={task.system}/{task.pool}/{task.window}",
                flush=True,
            )
    system_order = {value: index for index, value in enumerate(args.systems)}
    pool_order = {value: index for index, value in enumerate(args.pools)}
    window_order = {value: index for index, value in enumerate(args.windows)}
    rows.sort(
        key=lambda row: (
            system_order[str(row["system"])],
            pool_order[str(row["pool"])],
            window_order[str(row["window"])],
        )
    )
    _validate_complete_rows(rows)
    payload = {
        "schema_version": 2,
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "contract": {
            "initial_cash": INITIAL_CASH,
            "signal": "close_t",
            "execution": "next_tradable_open",
            "intraday_exit": False,
            "prelisting": "invisible until first observable row",
        },
        "repositories": {system: LOCKED_SOURCES[system] for system in args.systems},
        "source_hashes": {system: _source_hash(roots[system]) for system in args.systems},
        "data_provenance": {
            "through": TARGET_END,
            "sha256": _bounded_data_fingerprint(canonical_data_dir),
        },
        "systems": list(args.systems),
        "pools": list(args.pools),
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


def _validate_complete_rows(rows: Sequence[dict[str, Any]]) -> None:
    expected = {(system, pool) for system in SYSTEMS for pool in POOLS}
    observed: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("system")), str(row.get("pool")))
        if key in observed:
            raise RuntimeError(f"duplicate competitor cell: {key[0]}/{key[1]}")
        observed.add(key)
        if row.get("start") != TARGET_START or row.get("end") != TARGET_END:
            raise RuntimeError(f"target interval mismatch: {key[0]}/{key[1]}")
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise RuntimeError(f"missing competitor cells: {missing}")
    if unexpected:
        raise RuntimeError(f"unexpected competitor cells: {unexpected}")


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    default_roots = _default_source_roots()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--systems",
        nargs="+",
        choices=SYSTEMS,
        default=SYSTEMS,
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        choices=tuple(WINDOWS),
        default=tuple(WINDOWS),
    )
    parser.add_argument(
        "--pools",
        nargs="+",
        choices=tuple(POOLS),
        default=tuple(POOLS),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--qwen-root",
        type=Path,
        default=default_roots["qwenquant"],
    )
    parser.add_argument(
        "--aquant-root",
        type=Path,
        default=default_roots["aquant"],
    )
    parser.add_argument(
        "--trade-root",
        type=Path,
        default=default_roots["trade"],
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
        default=root / "benchmarks/window_competitor_results.json",
    )
    args = parser.parse_args(argv)
    roots = {
        "qwenquant": args.qwen_root,
        "aquant": args.aquant_root,
        "trade": args.trade_root,
    }
    try:
        _validate_source_roots(roots, args.systems)
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.trade_data_dir is not None and not args.trade_data_dir.is_dir():
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
