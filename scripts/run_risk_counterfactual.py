#!/usr/bin/env python3
"""Run fixed portfolio-level Risk Differential shadow policies."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, replace
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
    "base_only_control",
    "sentinel_freeze_only_control",
    "trade_entry_freeze_shadow",
    "trade_pyramid_freeze_shadow",
    "trade_gross_cap_shadow",
    "trade_layered_protection_shadow",
    "trade_cluster_trim_hybrid_shadow",
)
EXECUTED_POLICIES = (
    "baseline_uquant",
    "base_only_control",
    "sentinel_freeze_only_control",
    "trade_entry_freeze_shadow",
    "trade_pyramid_freeze_shadow",
    "trade_gross_cap_shadow",
    "trade_layered_protection_shadow",
    "trade_cluster_trim_hybrid_shadow",
)
GENERALIZATION_POLICIES = (
    "baseline_uquant",
    "trade_gross_cap_shadow",
    "trade_layered_protection_shadow",
)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )


def _write_job_checkpoint(path: Path, *, identity: str, result: dict[str, Any]) -> None:
    payload: dict[str, Any] = {"identity": identity, "result": result}
    payload["payload_sha256"] = canonical_sha256(payload)
    _write(path, payload)


def _load_job_checkpoint(path: Path, *, identity: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("identity") != identity
        or payload.get("payload_sha256") != canonical_sha256(payload)
        or not isinstance(payload.get("result"), dict)
    ):
        return None
    return dict(payload["result"])


def _checkpoint_path(cache_dir: Path, cell_id: str, policy_id: str) -> Path:
    job_id = hashlib.sha256(f"{cell_id}:{policy_id}".encode()).hexdigest()
    return cache_dir / f"{job_id}.json"


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
    cfg = (
        DEFAULT_CONFIG.override(risk_sentinel_mode="SHADOW")
        if policy_id == "base_only_control"
        else DEFAULT_CONFIG
    )
    engine = ProductionEngine(data_dir, cfg)
    engine._load(set(symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS))
    sessions = engine._raw["sh000300"].index.intersection(engine._raw["sh000682"].index)
    sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
    account = AccountState.empty(cfg.initial_cash)
    panel = {symbol: engine._raw[symbol] for symbol in symbols}
    trade_by_date = {item["date"]: item["trade"] for item in cell["days"]}
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    decision_digests: list[str] = []
    target_plans: list[dict[str, Any]] = []
    pending_order_plans: list[dict[str, Any]] = []
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
        decision_digests.append(decision.decision_digest)
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
                cfg=cfg,
            )
            planned.pending_orders = list(
                rebuild_shadow_orders(
                    account=planned,
                    previous_account=previous,
                    signal_date=str(date.date()),
                    targets=targets,
                    prices=prices,
                    cfg=cfg,
                    removed_buy_reason=removed_buy_reason,
                )
            )
        else:
            planned.pending_orders = list(decision.pending_orders)
        target_plans.append(
            {"date": str(date.date()), "targets": [asdict(item) for item in targets]}
        )
        pending_order_plans.append(
            {
                "date": str(date.date()),
                "pending_orders": [asdict(item) for item in planned.pending_orders],
            }
        )
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
        "matrix_axis": cell["axis"],
        "window": cell["window"],
        "universe": cell["universe"],
        "policy_id": policy_id,
        "execution_mode": "FULL_PRODUCTION_ENGINE_REPLAY",
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
        "decision_digest_sha256": canonical_sha256({"digests": decision_digests}),
        "target_plan_sha256": canonical_sha256({"days": target_plans}),
        "pending_order_plan_sha256": canonical_sha256({"days": pending_order_plans}),
        "fill_ledger_sha256": canonical_sha256(
            {"fills": [asdict(item) for item in account.fills]}
        ),
        "order_ledger_sha256": canonical_sha256(
            {"orders": [asdict(item) for item in account.order_ledger]}
        ),
        "economic_account_sha256": canonical_sha256(account.to_dict()),
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
    contract = json.loads((root / "benchmarks/current_heads_comparison_contract.json").read_text())
    windows = contract["windows"]
    cells = []
    for item in matrix["cells"]:
        if item.get("axis") not in {"official_pool", "generalization"} or item.get(
            "status"
        ) != "SUCCESS":
            continue
        window = windows[item["window"]]
        cells.append(
            {
                **item,
                "days": days_by_cell[item["cell_id"]],
                "acute": {"start": window["acute_start"], "end": window["acute_end"]},
            }
        )
    results = []
    runner_identity = canonical_sha256(
        {
            "matrix_sha256": matrix["payload_sha256"],
            "daily_trace_sha256": hashlib.sha256(
                (root / "artifacts/sentinel/risk_differential/risk_differential_daily.json.gz").read_bytes()
            ).hexdigest(),
            "policy_set": [asdict(policy) for policy in POLICY_SET],
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    cache_dir = root / ".risk_differential_runtime/counterfactual" / runner_identity
    jobs = []
    expected_jobs = sum(
        len(EXECUTED_POLICIES if cell["axis"] == "official_pool" else GENERALIZATION_POLICIES)
        for cell in cells
    )
    for cell in cells:
        policies = EXECUTED_POLICIES if cell["axis"] == "official_pool" else GENERALIZATION_POLICIES
        for policy in policies:
            checkpoint = _checkpoint_path(cache_dir, cell["cell_id"], policy)
            cached = _load_job_checkpoint(checkpoint, identity=runner_identity)
            if cached is not None:
                results.append(cached)
            else:
                jobs.append((cell, policy, str(root / "data/frozen")))
    if results:
        print(f"resuming {len(results)}/{expected_jobs} completed jobs", flush=True)
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_worker, job): (job[0]["cell_id"], job[1]) for job in jobs}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            cell_id, policy = futures[future]
            _write_job_checkpoint(
                _checkpoint_path(cache_dir, cell_id, policy),
                identity=runner_identity,
                result=result,
            )
            print(f"[{len(results)}/{expected_jobs}] {cell_id}:{policy}", flush=True)
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
            "counterfactual_runner_identity": runner_identity,
            "runtime": {
                "python": platform.python_version(),
                "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
            },
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
            "official_scope": "all 30 official cells executed for every control and shadow policy",
            "generalization": (
                "all economically READY generalization cells executed for baseline and every "
                "shadow policy that passed the preregistered sample gate; insufficient-sample "
                "and non-transferable candidates stop before generalization"
            ),
        },
    }
    payload["payload_sha256"] = canonical_sha256(payload)
    _write(root / "artifacts/sentinel/risk_differential/counterfactual_raw.json", payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
