#!/usr/bin/env python3
"""Replay frozen Trade on the exact Unified stress scenario matrix.

This is a validation-only adapter.  It stages the canonical causal data under
Trade's legacy filenames, runs the frozen ProductionReplayEngine, and emits a
signed per-scenario benchmark.  Production code never imports the old project.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_legacy_common_adapter as common  # noqa: E402
from unified_ai_quant.validation.provenance import (  # noqa: E402
    bounded_data_fingerprint,
)
from unified_ai_quant.validation.stress import (  # noqa: E402
    ORDERED_UNIVERSE,
    STRESS_END,
    STRESS_START,
    build_scenarios,
    scenario_fingerprint,
)

INITIAL_CASH = 2_000_000.0
TRADE_STRESS_NAME_HINTS = {
    # The fixed 32-name matrix does not contain this reference-universe name,
    # so the fixed adapter never needed it.  The label supplies Trade's
    # documented PCB/default route for this passive-component issuer.
    "000636": "PCB passive components",
}


@dataclass(frozen=True, slots=True)
class TradeStressTask:
    scenario_id: str
    scenario_type: str
    symbols: tuple[str, ...]


_ROUTE: Any = None
_TRADE_DATA_DIR = ""
_POLICY: Any = None


def _init_worker(
    trade_root: str,
    canonical_data_dir: str,
    trade_data_dir: str,
) -> None:
    global _ROUTE, _TRADE_DATA_DIR, _POLICY
    if trade_root not in sys.path:
        sys.path.insert(0, trade_root)
    _ROUTE = importlib.import_module("regime_adaptive")
    _TRADE_DATA_DIR = trade_data_dir
    base_policy = _ROUTE.qf.PortfolioPolicy()
    references = tuple(
        ("sh" if symbol.startswith(("6", "9")) else "sz") + symbol
        for symbol in base_policy.regime_symbols
    )
    visible = common._visible_symbols(
        Path(canonical_data_dir),
        references,
        STRESS_START,
    )
    _POLICY = replace(
        base_policy,
        regime_symbols=tuple(symbol[2:] for symbol in visible),
    )
    unresolved = []
    for symbol in ORDERED_UNIVERSE:
        code = symbol[2:]
        name = TRADE_STRESS_NAME_HINTS.get(
            code, common.TRADE_NAME_HINTS.get(code, code)
        )
        if _ROUTE.qf._CoreBacktestEngine._uses_unmapped_auto_route(code, name):
            unresolved.append(symbol)
    if unresolved:
        raise RuntimeError(
            "Trade common stress has unresolved explicit symbol routes: "
            + ", ".join(unresolved)
        )
    # Trade's engine reloads and recomputes identical immutable inputs for each
    # universe.  Cache only those pure inputs inside a worker; every replay
    # still constructs a fresh engine, account, strategies, and risk state.
    data_cache: dict[tuple[str, str, str, str], Any] = {}
    indicator_cache: dict[tuple[str, str, str, str], Any] = {}
    original_load = _ROUTE.qf.DataFetcher.load_stock_data
    original_compute = _ROUTE.qf.Indicators.compute_all

    def cached_load(
        symbol: str,
        start_date: str,
        end_date: str,
        data_dir: str | None = None,
    ) -> Any:
        key = (str(symbol), str(start_date), str(end_date), str(data_dir))
        if key not in data_cache:
            frame = original_load(symbol, start_date, end_date, data_dir=data_dir)
            frame.attrs["trade_common_symbol"] = str(symbol)
            data_cache[key] = frame
        return data_cache[key].copy(deep=False)

    def cached_compute(frame: Any, config: dict[str, Any]) -> Any:
        symbol = str(frame.attrs.get("trade_common_symbol", ""))
        if not symbol:
            raise RuntimeError(
                "Trade common-scenario indicator cache received a frame "
                "without an immutable symbol identity"
            )
        config_hash = hashlib.sha256(
            json.dumps(config, sort_keys=True, default=str).encode()
        ).hexdigest()
        key = (
            symbol,
            str(frame.index[0]),
            str(frame.index[-1]),
            config_hash,
        )
        if key not in indicator_cache:
            indicator_cache[key] = original_compute(frame, config)
        return indicator_cache[key]

    _ROUTE.qf.DataFetcher.load_stock_data = staticmethod(cached_load)
    _ROUTE.qf.Indicators.compute_all = staticmethod(cached_compute)


def _run_trade_scenario(task: TradeStressTask) -> dict[str, Any]:
    if _ROUTE is None or _POLICY is None or not _TRADE_DATA_DIR:
        raise RuntimeError("Trade stress worker was not initialized")
    codes = tuple(symbol[2:] for symbol in task.symbols)
    names = {
        code: TRADE_STRESS_NAME_HINTS.get(
            code, common.TRADE_NAME_HINTS.get(code, code)
        )
        for code in codes
    }
    with contextlib.redirect_stdout(io.StringIO()):
        result = _ROUTE.ProductionReplayEngine(
            INITIAL_CASH,
            policy=_POLICY,
        ).run(
            names,
            STRESS_START,
            STRESS_END,
            data_dir=_TRADE_DATA_DIR,
            regime_data_dir=_TRADE_DATA_DIR,
            leader_data_dir=_TRADE_DATA_DIR,
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
    orders, _ = common._broker_order_ledger("trade", fills)
    if len(orders) != int(result["total_trades"]):
        raise RuntimeError(
            f"Trade broker-order reconciliation failed for {task.scenario_id}: "
            f"merged={len(orders)}, native={result['total_trades']}"
        )
    return {
        **asdict(task),
        "symbol_count": len(task.symbols),
        "final_wealth": float(result["final_assets"]) / INITIAL_CASH,
        "total_return": float(result["final_assets"]) / INITIAL_CASH - 1.0,
        "max_drawdown": abs(float(result["max_drawdown"])),
        "account_orders": len(orders),
        "sleeve_fill_count": int(result["sleeve_fill_count"]),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    workspace = ROOT.parent
    parser = argparse.ArgumentParser(
        description="Run frozen Trade on the exact Unified stress scenarios"
    )
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/frozen")
    parser.add_argument(
        "--trade-root",
        type=Path,
        default=workspace / "frozen_benchmarks/trade",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "benchmarks/trade_common_stress.json",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(4, max(1, os.cpu_count() or 1)),
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="diagnostic prefix only; omitted for the formal complete artifact",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.data_dir.is_dir():
        raise SystemExit(f"canonical data directory does not exist: {args.data_dir}")
    if not args.trade_root.is_dir():
        raise SystemExit(f"frozen Trade checkout does not exist: {args.trade_root}")
    if args.workers < 1:
        raise SystemExit("--workers must be positive")
    scenarios = build_scenarios(args.data_dir)
    formal_complete = args.limit is None
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be positive")
        scenarios = scenarios[: args.limit]
    tasks = [TradeStressTask(**asdict(item)) for item in scenarios]

    with tempfile.TemporaryDirectory(prefix="trade-common-bounded-") as bounded:
        canonical = Path(bounded)
        common._stage_bounded_data(args.data_dir, canonical, end=STRESS_END)
        with tempfile.TemporaryDirectory(prefix="trade-common-data-") as staged:
            trade_data = Path(staged)
            common._stage_trade_data(canonical, trade_data)
            print(
                f"Trade common stress: executing {len(tasks)} scenarios "
                f"with {args.workers} workers",
                flush=True,
            )
            results: list[dict[str, Any]] = []
            with ProcessPoolExecutor(
                max_workers=args.workers,
                initializer=_init_worker,
                initargs=(
                    str(args.trade_root),
                    str(canonical),
                    str(trade_data),
                ),
            ) as executor:
                for index, row in enumerate(
                    executor.map(_run_trade_scenario, tasks, chunksize=1),
                    start=1,
                ):
                    results.append(row)
                    if index % 50 == 0 or index == len(tasks):
                        print(
                            f"Trade common stress: {index}/{len(tasks)}",
                            flush=True,
                        )

    results.sort(key=lambda row: str(row["scenario_id"]))
    payload = {
        "schema_version": 1,
        "formal_complete": formal_complete,
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "common_adapter_sha256": hashlib.sha256(
            (ROOT / "scripts" / "run_legacy_common_adapter.py").read_bytes()
        ).hexdigest(),
        "trade_source_sha256": common._source_hash(args.trade_root),
        "contract": {
            "initial_cash": INITIAL_CASH,
            "start": STRESS_START,
            "end": STRESS_END,
            "signal": "close_t",
            "execution": "next_tradable_open",
            "account_orders": "unique executed fill_date/symbol/side",
        },
        "data_provenance": {
            "through": STRESS_END,
            "sha256": bounded_data_fingerprint(args.data_dir, end=STRESS_END),
        },
        "scenario_sha256": scenario_fingerprint(scenarios),
        "scenario_count": len(scenarios),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Trade common stress artifact: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
