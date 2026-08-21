#!/usr/bin/env python3
"""Run fixed portfolio-level Risk Differential shadow policies."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from research.risk_counterfactual import (
    POLICY_SET,
    clamp_pyramid_targets,
    effective_shadow_cap,
    layered_protection_line,
    marked_weights,
    rebuild_shadow_orders,
    trend_health_adjustment,
    wilder_atr,
)
from research.risk_differential_models import canonical_sha256
from uquant.atomic_io import atomic_write_text
from uquant.config import DEFAULT_CONFIG
from uquant.data import normalize_symbol
from uquant.engine import (
    INDEX_SYMBOLS,
    ProductionEngine,
    _attach_target_attribution,
    performance_metrics,
)
from uquant.leader import REFERENCE_UNIVERSE
from uquant.types import (
    AccountState,
    AttributionMechanism,
    OriginSubsystem,
    ReductionPolicy,
    Target,
)
from uquant.validation.ai_era import require_ai_era_interval

POLICIES = (
    "baseline_uquant",
    "trade_entry_freeze_shadow",
    "trade_pyramid_freeze_shadow",
    "trade_gross_cap_shadow",
    "trade_layered_protection_shadow",
    "trade_cluster_trim_hybrid_shadow",
)
EXECUTED_POLICIES = (
    "trade_gross_cap_shadow",
    "trade_layered_protection_shadow",
)
EVALUATION_CELLS = {
    "trade_gross_cap_shadow": frozenset({"official_pool/h1_2023/a", "official_pool/h1_2024/a"}),
    "trade_layered_protection_shadow": frozenset(
        {
            "official_pool/h1_2023/a",
            "official_pool/h1_2024/a",
            "official_pool/bull_crash_2025_2026/a",
        }
    ),
}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )


def _prices(engine: ProductionEngine, account: AccountState, date: pd.Timestamp) -> dict[str, float]:
    symbols = set(account.positions) | set(engine._raw)
    return {
        symbol: engine._price(symbol, date)
        for symbol in symbols
        if symbol in engine._raw and not engine._raw[symbol].loc[:date].empty
    }


def _layered_targets(
    *,
    engine: ProductionEngine,
    date: pd.Timestamp,
    account: AccountState,
    targets: tuple[Target, ...],
    trade: dict[str, Any],
    equity: float,
) -> tuple[tuple[Target, ...], int]:
    by_symbol = {target.symbol: target for target in targets}
    account_peak = max(account.operating_peak, account.capital_peak, equity)
    drawdown = 0.0 if account_peak <= 0 else max(0.0, 1.0 - equity / account_peak)
    triggered = 0
    for symbol, position in sorted(account.positions.items()):
        if position.shares <= 0 or symbol not in engine._raw:
            continue
        frame = engine._raw[symbol].loc[:date]
        closes = pd.to_numeric(frame["close"], errors="coerce")
        atr = None
        if len(frame) >= 20:
            atr = wilder_atr(
                tuple(float(item) for item in frame["high"]),
                tuple(float(item) for item in frame["low"]),
                tuple(float(item) for item in closes),
                period=20,
            )
        line, kind = layered_protection_line(
            entry=float(position.avg_cost),
            peak_close=float(position.highest_close),
            atr=atr,
            risk_level=int(trade.get("severity_rank") or 0),
            account_drawdown=drawdown,
            trend_adjustment=trend_health_adjustment(engine._raw[symbol], date),
        )
        if float(closes.iloc[-1]) > line + 1e-12:
            continue
        triggered += 1
        existing = by_symbol.get(symbol)
        if existing is None:
            existing = Target(
                symbol=symbol,
                weight=0.0,
                lifecycle=position.lifecycle,
                alpha_score=0.0,
                confidence=0.0,
                reason=f"trade layered protection shadow: {kind}",
            )
        by_symbol[symbol] = replace(
            existing,
            weight=0.0,
            reason=f"trade layered protection shadow: {kind}",
            reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
            reason_code="risk_off",
            exit_kind="risk",
            event_id="",
            origin_subsystem=OriginSubsystem.RISK.value,
            mechanism=AttributionMechanism.RISK_OFF.value,
            origin_lifecycle=position.lifecycle,
        )
    return tuple(by_symbol[key] for key in sorted(by_symbol)), triggered


def run_cell_policy(cell: dict[str, Any], policy_id: str, data_dir: Path) -> dict[str, Any]:
    start, end = require_ai_era_interval(cell["start"], cell["end"])
    symbols = tuple(sorted(normalize_symbol(item) for item in cell["symbols"]))
    engine = ProductionEngine(data_dir, DEFAULT_CONFIG)
    engine._load(set(symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS))
    sessions = engine._raw["sh000300"].index.intersection(engine._raw["sh000682"].index)
    sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {symbol: engine._raw[symbol] for symbol in symbols}
    trade_by_date = {item["date"]: item["trade"] for item in cell["days"]}
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    trigger_count = 0
    blocked_buy_intents = 0
    blocked_pyramid_intents = 0
    for date in sessions:
        engine.execution.execute_open(date=date, account=account, panel=panel)
        equity = engine.equity(account, date)
        equity_rows.append((date, equity))
        previous = deepcopy(account)
        planned = deepcopy(account)
        decision = engine.decide(symbols=symbols, as_of=str(date.date()), account=planned)
        targets = decision.targets
        trade = trade_by_date[str(date.date())]
        prices = _prices(engine, planned, date)
        weights_now, _ = marked_weights(planned, prices)
        changed = False
        removed_buy_reason = None
        if policy_id == "trade_entry_freeze_shadow" and trade["block_new_entries"] is True:
            targets = engine.allocator._frozen_existing_targets(
                strategy_targets=targets,
                leaders={},
                account=planned,
                weights_now=weights_now,
            )
            removed_buy_reason = "trade_entry_freeze_shadow"
            trigger_count += 1
            blocked_buy_intents += sum(
                order.side == "BUY" and order.symbol not in previous.positions
                for order in decision.pending_orders
            )
            changed = True
        elif policy_id == "trade_pyramid_freeze_shadow" and trade["block_pyramiding"] is True:
            targets = clamp_pyramid_targets(targets, weights_now)
            trigger_count += 1
            blocked_pyramid_intents += sum(
                order.side == "BUY" and order.symbol in previous.positions
                for order in decision.pending_orders
            )
            changed = True
        elif policy_id == "trade_gross_cap_shadow":
            trade_cap = trade["recommended_gross_cap"]
            base_cap = float(decision.risk_summary["target_gross_cap"])
            if trade_cap is not None and float(trade_cap) < base_cap:
                cap = effective_shadow_cap(base_cap, float(trade_cap))
                targets = engine.allocator._sparse_risk_reduce(
                    targets=targets,
                    weights_now=weights_now,
                    account=planned,
                    gross_cap=cap,
                    risk_reason="trade challenger gross-cap shadow",
                    risk_reason_code="risk_gross_cap",
                    risk_exit_kind="risk",
                    prices=prices,
                )
                trigger_count += 1
                changed = True
        elif policy_id == "trade_layered_protection_shadow":
            targets, daily_triggers = _layered_targets(
                engine=engine,
                date=date,
                account=planned,
                targets=targets,
                trade=trade,
                equity=equity,
            )
            trigger_count += daily_triggers
            changed = daily_triggers > 0
        # Daily weakest-cluster state is not serialized by trade. The hybrid
        # diagnostic therefore fails closed to an exact no-trigger baseline.
        if changed:
            targets = _attach_target_attribution(
                signal_date=str(date.date()),
                targets=targets,
                retained_orders=previous.pending_orders,
                cfg=DEFAULT_CONFIG,
            )
            planned.pending_orders = list(
                rebuild_shadow_orders(
                    account=planned,
                    previous_account=previous,
                    signal_date=str(date.date()),
                    targets=targets,
                    prices=prices,
                    cfg=DEFAULT_CONFIG,
                    removed_buy_reason=removed_buy_reason,
                )
            )
        else:
            planned.pending_orders = list(decision.pending_orders)
        account = planned
    metrics = performance_metrics(
        equity_rows=equity_rows,
        fills=account.fills,
        orders=account.order_ledger,
        initial_cash=account.initial_cash,
        risk_events=account.risk_events,
        benchmark_total_return=float(
            engine._raw["sh000682"].loc[sessions[-1], "close"]
            / engine._raw["sh000682"].loc[sessions[0], "close"]
            - 1.0
        ),
    )
    series = pd.Series(dict(equity_rows)).sort_index()
    acute = cell["acute"]
    acute_series = series.loc[pd.Timestamp(acute["start"]) : pd.Timestamp(acute["end"])]
    risk_sells = sum(fill.side == "SELL" and fill.exit_kind == "risk" for fill in account.fills)
    return {
        "cell_id": cell["cell_id"],
        "window": cell["window"],
        "universe": cell["universe"],
        "policy_id": policy_id,
        "trigger_count": trigger_count,
        "final_wealth": float(series.iloc[-1] / account.initial_cash),
        "total_return": float(series.iloc[-1] / account.initial_cash - 1.0),
        "max_drawdown": float(metrics["max_drawdown"]),
        "acute_return": float(acute_series.iloc[-1] / acute_series.iloc[0] - 1.0),
        "account_orders": int(metrics["account_orders"]),
        "gross_turnover": float(metrics["gross_turnover"]),
        "annual_turnover": float(metrics["annual_turnover"]),
        "risk_sell_orders": int(risk_sells),
        "blocked_buy_intents": int(blocked_buy_intents),
        "blocked_pyramid_intents": int(blocked_pyramid_intents),
    }


def _worker(args: tuple[dict[str, Any], str, str]) -> dict[str, Any]:
    cell, policy, data_dir = args
    return run_cell_policy(cell, policy, Path(data_dir))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    matrix = json.loads(
        (root / "artifacts/sentinel/risk_differential/risk_differential_matrix.json").read_text()
    )
    daily = json.loads(
        gzip.decompress(
            (root / "artifacts/sentinel/risk_differential/risk_differential_daily.json.gz").read_bytes()
        )
    )
    days_by_cell = {item["cell_id"]: item["days"] for item in daily["cells"]}
    exclusive = json.loads((root / "artifacts/sentinel/risk_differential/exclusive_events.json").read_text())
    actionable_admission = [
        item
        for item in exclusive["events"]
        if item["event_id"].startswith("official_pool/")
        and item["axis"] in {"block_new_entries", "block_pyramiding"}
        and (item["actionable_buy_intents"] or item["actionable_pyramid_intents"])
    ]
    if actionable_admission:
        raise RuntimeError(
            "admission policies require full replay because actionable exclusive intents exist"
        )
    contract = json.loads((root / "benchmarks/current_heads_comparison_contract.json").read_text())
    established = json.loads((root / "benchmarks/current_heads_competitor_matrix.json").read_text())
    windows = contract["windows"]
    cells = []
    for item in matrix["cells"]:
        if item.get("axis") != "official_pool" or item.get("status") != "SUCCESS":
            continue
        window = windows[item["window"]]
        cells.append(
            {
                **item,
                "days": days_by_cell[item["cell_id"]],
                "acute": {"start": window["acute_start"], "end": window["acute_end"]},
            }
        )
    established_baselines = {
        f"{item['axis']}/{item['window']}/{item['name']}": item["metrics"]
        for item in established["cells"]
        if item["axis"] == "official_pool" and item["system"] == "uquant" and item["status"] == "SUCCESS"
    }
    results = []
    for cell in cells:
        metrics = established_baselines[cell["cell_id"]]
        results.append(
            {
                "cell_id": cell["cell_id"],
                "window": cell["window"],
                "universe": cell["universe"],
                "policy_id": "baseline_uquant",
                "trigger_count": 0,
                "final_wealth": metrics["final_wealth"],
                "total_return": metrics["total_return"],
                "max_drawdown": metrics["max_drawdown"],
                "acute_return": metrics["acute_return"],
                "account_orders": metrics["account_orders"],
                "gross_turnover": metrics["gross_turnover"],
                "annual_turnover": metrics["annual_turnover"],
                "risk_sell_orders": 0,
                "blocked_buy_intents": 0,
                "blocked_pyramid_intents": 0,
                "equivalence_reason": "sealed production-equivalent current-head baseline",
            }
        )
    jobs = [
        (cell, policy, str(root / "data/frozen"))
        for cell in cells
        for policy in EXECUTED_POLICIES
        if cell["cell_id"] in EVALUATION_CELLS[policy]
    ]
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_worker, job): (job[0]["cell_id"], job[1]) for job in jobs}
        for index, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            cell_id, policy = futures[future]
            print(f"[{index}/{len(jobs)}] {cell_id}:{policy}", flush=True)
    baselines = {item["cell_id"]: item for item in results if item["policy_id"] == "baseline_uquant"}
    for policy in (
        "trade_entry_freeze_shadow",
        "trade_pyramid_freeze_shadow",
        "trade_cluster_trim_hybrid_shadow",
    ):
        results.extend(
            {
                **baseline,
                "policy_id": policy,
                "trigger_count": 0,
                "risk_sell_orders": 0,
                "blocked_buy_intents": 0,
                "blocked_pyramid_intents": 0,
                "equivalence_reason": (
                    "no actionable exclusive BUY/pyramid intent"
                    if policy != "trade_cluster_trim_hybrid_shadow"
                    else "daily weakest-cluster challenger state is unobservable"
                ),
            }
            for baseline in baselines.values()
        )
    payload = {
        "schema_version": 1,
        "provenance": {
            "risk_differential_matrix_sha256": matrix["payload_sha256"],
            "daily_trace_gzip_sha256": hashlib.sha256(
                (root / "artifacts/sentinel/risk_differential/risk_differential_daily.json.gz").read_bytes()
            )
            .hexdigest(),
            "frozen_exclusive_events_sha256": exclusive["payload_sha256"],
            "uquant_starting_commit": matrix["provenance"]["uquant_starting_commit"],
            "trade_commit": matrix["provenance"]["trade_commit"],
        },
        "policy_set": [
            {
                "policy_id": policy.policy_id,
                "transfer_kind": policy.transfer_kind,
                "trigger_axis": policy.trigger_axis,
                "description": policy.description,
            }
            for policy in POLICY_SET
        ],
        "cells": sorted(results, key=lambda item: (item["cell_id"], item["policy_id"])),
        "production_behavior_changed": False,
        "fixed_policy_stop_rule": {
            "trade_gross_cap_shadow": (
                "two preregistered representative cells plus archived Phase 5 gate failure"
            ),
            "trade_layered_protection_shadow": ("three preregistered representative risk regimes"),
        },
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    _write(root / "artifacts/sentinel/risk_differential/counterfactual_raw.json", payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
