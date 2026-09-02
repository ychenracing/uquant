"""Bounded post-generalization sensitivity and stress evidence.

This research-only module deliberately owns no execution model.  Its order
stresses call the production next-open planner, and its parameter cases call
the production replay engine one factor at a time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from dataclasses import asdict, dataclass, fields
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from uquant.config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from uquant.contracts.strict_json import (
    canonical_json_bytes,
    canonical_json_sha256,
    strict_json_loads,
)
from uquant.data import DataStore
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.execution import ExecutionPlanner
from uquant.types import (
    AccountState,
    AttributionMechanism,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation.manifest import verify_data_manifest
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe


@dataclass(frozen=True, slots=True)
class ParameterVariantSpec:
    """One predeclared one-factor perturbation."""

    case_id: str
    field: str
    value: float


@dataclass(frozen=True, slots=True)
class ExecutionStressSpec:
    """One bounded execution-tail case."""

    case_id: str
    kind: str
    value: float | None = None


PARAMETER_VARIANT_SPECS = (
    ParameterVariantSpec("P1_LOWER", "strategic_reversal_max_ret240", -0.18),
    ParameterVariantSpec("P1_UPPER", "strategic_reversal_max_ret240", -0.12),
    ParameterVariantSpec("P2_LOWER", "strategic_reversal_min_ret5", 0.04),
    ParameterVariantSpec("P2_UPPER", "strategic_reversal_min_ret5", 0.06),
    ParameterVariantSpec("P3_LOWER", "strategic_reversal_min_median_ret20", -0.06),
    ParameterVariantSpec("P3_UPPER", "strategic_reversal_min_median_ret20", -0.04),
    ParameterVariantSpec("P4_LOWER", "strategic_reversal_max_tech_ret120", -0.02),
    ParameterVariantSpec("P4_UPPER", "strategic_reversal_max_tech_ret120", 0.0),
    ParameterVariantSpec("P5_LOWER", "strategic_dominant_min_leader_gap", 0.04),
    ParameterVariantSpec("P5_UPPER", "strategic_dominant_min_leader_gap", 0.06),
    ParameterVariantSpec("P6_LOWER", "strategic_transition_min_component", 0.65),
    ParameterVariantSpec("P6_UPPER", "strategic_transition_min_component", 0.75),
    ParameterVariantSpec("P7_LOWER", "strategic_dominant_profit_lock_mfe", 1.98),
    ParameterVariantSpec("P7_UPPER", "strategic_dominant_profit_lock_mfe", 2.42),
    ParameterVariantSpec("P8_LOWER", "strategic_dominant_retained_gross", 0.65),
    ParameterVariantSpec("P8_UPPER", "strategic_dominant_retained_gross", 0.75),
)

EXECUTION_STRESS_SPECS = (
    ExecutionStressSpec("S25", "ADVERSE_SLIPPAGE_BPS", 25.0),
    ExecutionStressSpec("S50", "ADVERSE_SLIPPAGE_BPS", 50.0),
    ExecutionStressSpec("S100", "ADVERSE_SLIPPAGE_BPS", 100.0),
    ExecutionStressSpec("S200", "ADVERSE_SLIPPAGE_BPS", 200.0),
    ExecutionStressSpec("P75", "PARTIAL_FILL_RATIO", 0.75),
    ExecutionStressSpec("P50", "PARTIAL_FILL_RATIO", 0.50),
    ExecutionStressSpec("P25", "PARTIAL_FILL_RATIO", 0.25),
    ExecutionStressSpec("B-UP", "LIMIT_BLOCKED_BUY"),
    ExecutionStressSpec("B-DOWN", "LIMIT_BLOCKED_SELL"),
    ExecutionStressSpec("B-SUSP", "SUSPENDED"),
    ExecutionStressSpec("B-CAP0", "CAPACITY_UNAVAILABLE"),
)

_SYMBOL = "sh603986"
_SIGNAL_DATE = "2026-01-05"
_NEXT_OPEN = "2026-01-06"
_DELAYED_OPEN = "2026-01-07"


def _frame(rows: list[dict[str, float | str]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def _row(date: str, price: float, volume: float = 100_000_000.0) -> dict[str, float | str]:
    return {
        "date": date,
        "open": price,
        "high": price * 1.01,
        "low": price * 0.99,
        "close": price,
        "volume": volume,
        "amount": price * volume,
    }


def _pending(*, side: str, target_weight: float) -> PendingOrder:
    lifecycle = "CORE"
    origin = OriginSubsystem.LEADER if side == "BUY" else OriginSubsystem.RISK
    mechanism = AttributionMechanism.LEADER_SELECTION if side == "BUY" else AttributionMechanism.RISK_OFF
    industry = default_ai_universe().industry_of(_SYMBOL, _SIGNAL_DATE)
    identity = {
        "origin_subsystem": origin.value,
        "mechanism": mechanism.value,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": industry,
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }
    event_id = derive_attribution_event_id(
        signal_date=_SIGNAL_DATE,
        symbol=_SYMBOL,
        target_weight=target_weight,
        lifecycle=lifecycle,
        reduction_policy=ReductionPolicy.FIFO.value,
        reason_code="execution_stress",
        exit_kind="stress",
        **identity,
    )
    return PendingOrder(
        signal_date=_SIGNAL_DATE,
        symbol=_SYMBOL,
        side=side,
        target_weight=target_weight,
        reason="research-only execution tail stress",
        lifecycle=lifecycle,
        reason_code="execution_stress",
        exit_kind="stress",
        event_id=event_id,
        **identity,
    )


def _buy_account() -> AccountState:
    # At the default 10 bps slippage, a 60% target requests exactly 10,000
    # shares at a 100.0 open.  The partial-fill ratios therefore retain their
    # literal economic meaning instead of merely naming a capacity bucket.
    account = AccountState.empty(1_668_333.34)
    account.pending_orders = [_pending(side="BUY", target_weight=0.60)]
    return account


def _sell_account() -> AccountState:
    tranche = Tranche(
        "stress-lot",
        "CORE",
        10_000,
        100.0,
        "2026-01-02",
        _SIGNAL_DATE,
        100.0,
    )
    account = AccountState(
        initial_cash=1_000_000.0,
        cash=0.0,
        positions={
            _SYMBOL: Position(
                symbol=_SYMBOL,
                shares=10_000,
                avg_cost=100.0,
                entry_date="2026-01-02",
                highest_close=100.0,
                tranches=[tranche],
            )
        },
        operating_peak=1_000_000.0,
        capital_peak=1_000_000.0,
    )
    account.pending_orders = [_pending(side="SELL", target_weight=0.0)]
    return account


def _execution_invariants(account: AccountState) -> dict[str, bool]:
    order_ids = [order.order_id for order in account.order_ledger]
    fill_ids = [_physical_fill_identity(asdict(fill)) for fill in account.fills]
    return {
        "no_negative_cash": account.cash >= -1e-8,
        "no_leverage": account.cash >= -1e-8,
        "no_short": all(position.shares >= 0 for position in account.positions.values()),
        "no_duplicate_order": len(order_ids) == len(set(order_ids)),
        "no_duplicate_fill": len(fill_ids) == len(set(fill_ids)),
        "no_same_signal_fill": all(fill.fill_date > fill.signal_date for fill in account.fills),
        "no_fabricated_broker_fact": all(not fill.fill_id.startswith("BROKER-") for fill in account.fills),
    }


def _run_one_execution_stress(spec: ExecutionStressSpec) -> dict[str, Any]:
    config = DEFAULT_CONFIG
    account = _buy_account()
    rows = [_row(_SIGNAL_DATE, 100.0), _row(_NEXT_OPEN, 100.0)]
    blocked_sessions = 0
    pending_preserved = False

    if spec.kind == "ADVERSE_SLIPPAGE_BPS":
        assert spec.value is not None
        config = config.override(slippage=DEFAULT_CONFIG.slippage + spec.value / 10_000.0)
    elif spec.kind == "PARTIAL_FILL_RATIO":
        assert spec.value is not None
        requested_capacity = int(10_000 * spec.value)
        rows[-1] = _row(_NEXT_OPEN, 100.0, requested_capacity / config.max_volume_participation)
    elif spec.kind == "LIMIT_BLOCKED_BUY":
        rows[-1] = {
            "date": _NEXT_OPEN,
            "open": 110.0,
            "high": 110.0,
            "low": 110.0,
            "close": 110.0,
            "volume": 100_000_000.0,
            "amount": 11_000_000_000.0,
        }
        rows.append(_row(_DELAYED_OPEN, 109.0))
    elif spec.kind == "LIMIT_BLOCKED_SELL":
        account = _sell_account()
        rows[-1] = {
            "date": _NEXT_OPEN,
            "open": 90.0,
            "high": 90.0,
            "low": 90.0,
            "close": 90.0,
            "volume": 100_000_000.0,
            "amount": 9_000_000_000.0,
        }
        rows.append(_row(_DELAYED_OPEN, 91.0))
    elif spec.kind == "SUSPENDED":
        rows = [_row(_SIGNAL_DATE, 100.0), _row(_DELAYED_OPEN, 100.0)]
    elif spec.kind == "CAPACITY_UNAVAILABLE":
        rows[-1] = _row(_NEXT_OPEN, 100.0, 19_999.0)
        rows.append(_row(_DELAYED_OPEN, 100.0))
    else:  # pragma: no cover - specs above are exhaustive
        raise ValueError(f"unsupported execution stress: {spec.kind}")

    planner = ExecutionPlanner(config)
    panel = {_SYMBOL: _frame(rows)}
    same_signal_fills = planner.execute_open(date=pd.Timestamp(_SIGNAL_DATE), account=account, panel=panel)
    fills = planner.execute_open(date=pd.Timestamp(_NEXT_OPEN), account=account, panel=panel)
    if spec.kind in {"LIMIT_BLOCKED_BUY", "LIMIT_BLOCKED_SELL", "SUSPENDED", "CAPACITY_UNAVAILABLE"}:
        blocked_sessions = 1
        pending_preserved = not fills and len(account.pending_orders) == 1
        fills = planner.execute_open(date=pd.Timestamp(_DELAYED_OPEN), account=account, panel=panel)

    fill = fills[0] if fills else None
    ledger_order = account.order_ledger[-1]
    requested_shares = ledger_order.requested_shares
    filled_shares = fill.shares if fill is not None else 0
    completion_ratio = filled_shares / requested_shares if requested_shares else 0.0
    next_open_row = next((row for row in rows if row["date"] == _NEXT_OPEN), None)
    baseline_price = (
        float(next_open_row["open"])
        * (
            1.0 + DEFAULT_CONFIG.slippage
            if ledger_order.side == "BUY"
            else 1.0 - DEFAULT_CONFIG.slippage
        )
        if next_open_row is not None
        else None
    )
    incremental_cash_cost = (
        (float(fill.price) - baseline_price) * filled_shares
        if fill is not None and baseline_price is not None and ledger_order.side == "BUY"
        else (baseline_price - float(fill.price)) * filled_shares
        if fill is not None and baseline_price is not None
        else None
    )
    return {
        "case_id": spec.case_id,
        "kind": spec.kind,
        "stress_value": spec.value,
        "baseline_slippage_bps": DEFAULT_CONFIG.slippage * 10_000.0,
        "effective_slippage_bps": config.slippage * 10_000.0,
        "same_signal_fill_count": len(same_signal_fills),
        "requested_shares": requested_shares,
        "filled_shares": filled_shares,
        "order_completion_ratio": completion_ratio,
        "fill_price": fill.price if fill is not None else None,
        "order_level_incremental_cash_cost_vs_default_next_open": incremental_cash_cost,
        "blocked_sessions": blocked_sessions,
        "fill_delay_sessions": blocked_sessions,
        "pending_preserved_after_block": pending_preserved,
        "final_pending_orders": len(account.pending_orders),
        "model_order_count": len(account.order_ledger),
        "model_fill_count": len(account.fills),
        "invariants": _execution_invariants(account),
    }


def run_execution_stresses() -> dict[str, Any]:
    """Run the fixed order-level matrix through production next-open execution."""

    cases = [_run_one_execution_stress(spec) for spec in EXECUTION_STRESS_SPECS]
    measured_costs = [
        case for case in cases if case["order_level_incremental_cash_cost_vs_default_next_open"] is not None
    ]
    worst = max(
        measured_costs,
        key=lambda case: float(case["order_level_incremental_cash_cost_vs_default_next_open"]),
    )
    return {
        "scope": "DETERMINISTIC_ORDER_LEVEL_NATIVE_EXECUTION_STRESS",
        "authoritative_acceptance": False,
        "actual_broker_facts": False,
        "full_strategy_pnl": False,
        "cases": cases,
        "worst_order_level_case": {
            "case_id": worst["case_id"],
            "incremental_cash_cost_vs_default_next_open": worst[
                "order_level_incremental_cash_cost_vs_default_next_open"
            ],
        },
        "portfolio_level_outputs": {
            "stressed_wealth": None,
            "stressed_max_drawdown": None,
            "portfolio_turnover": None,
            "portfolio_opportunity_cost": None,
            "worst_key_trade": None,
            "degradation_vs_baseline": None,
            "status": "EVIDENCE GAP — ORDER_LEVEL SCOPE DOES NOT ESTABLISH PORTFOLIO PNL",
        },
    }


_REPLAY_SYMBOLS = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")
_REPLAY_START = "2023-01-03"
_REPLAY_END = "2026-08-05"
_COMPATIBILITY_FIELDS = (
    "hierarchical_industry_shrinkage_enabled",
    "group_balanced_reference_enabled",
    "same_day_leader_pipeline_enabled",
    "evidence_family_voting_enabled",
)
_ALLOWED_UTILITY_CLASSIFICATIONS = (
    "ACTIVE_USEFUL",
    "ACTIVE_REDUNDANT",
    "INACTIVE_REACHABLE",
    "UNREACHABLE",
    "COMPAT_ONLY",
    "DEAD",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_identity() -> dict[str, str]:
    payload = {
        "python": platform.python_version(),
        "python_full": sys.version,
        "numpy": version("numpy"),
        "pandas": version("pandas"),
        "uv_lock_sha256": _sha256_file(Path("uv.lock")),
    }
    return {**payload, "canonical_sha256": canonical_json_sha256(payload)}


def _base_identity(data_dir: Path) -> dict[str, Any]:
    return {
        "production_source_sha256": code_fingerprint(),
        "runner_source_sha256": _sha256_file(Path(__file__)),
        "frozen_data": verify_data_manifest(data_dir),
        "runtime": _runtime_identity(),
        "symbols": list(_REPLAY_SYMBOLS),
        "window": {"start": _REPLAY_START, "end": _REPLAY_END},
    }


def _case_config(case_id: str) -> tuple[SystemConfig, dict[str, float | str | None]]:
    if case_id == "BASELINE":
        return DEFAULT_CONFIG, {"field": None, "value": None}
    spec = next((item for item in PARAMETER_VARIANT_SPECS if item.case_id == case_id), None)
    if spec is None:
        raise ValueError(f"unknown parameter case: {case_id}")
    return DEFAULT_CONFIG.override(**{spec.field: spec.value}), {
        "field": spec.field,
        "value": spec.value,
    }


def _case_identity(case_id: str, data_dir: Path) -> tuple[SystemConfig, dict[str, Any]]:
    config, parameter = _case_config(case_id)
    payload = {
        **_base_identity(data_dir),
        "case_id": case_id,
        "parameter": parameter,
        "effective_config_sha256": config_fingerprint(config),
    }
    return config, {**payload, "canonical_sha256": canonical_json_sha256(payload)}


def _longest_true_streak(dates: list[str], flags: list[bool]) -> dict[str, Any]:
    best_start = best_end = ""
    best_length = current_start = current_length = 0
    for index, flag in enumerate(flags):
        if flag:
            if current_length == 0:
                current_start = index
            current_length += 1
            if current_length > best_length:
                best_length = current_length
                best_start = dates[current_start]
                best_end = dates[index]
        else:
            current_length = 0
    return {"sessions": best_length, "start": best_start or None, "end": best_end or None}


def _economic_target_signature(row: dict[str, Any]) -> str:
    targets = [
        {
            key: target.get(key)
            for key in (
                "symbol",
                "weight",
                "lifecycle",
                "reduction_policy",
                "reason_code",
                "exit_kind",
                "origin_subsystem",
                "mechanism",
                "replaces_symbol",
                "industry_at_entry",
            )
        }
        for target in row["targets"]
    ]
    return canonical_json_sha256(targets)


def _strategic_owner(row: dict[str, Any]) -> str:
    candidates = [
        target
        for target in row["targets"]
        if target.get("grant_id") and target.get("origin_subsystem") == "STRATEGIC"
    ]
    if not candidates:
        return ""
    return str(max(candidates, key=lambda item: float(item["weight"]))["symbol"])


def _epoch_projection(account: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            key: epoch.get(key)
            for key in (
                "owner_symbol",
                "qualification_route",
                "qualification_quorum",
                "opened_session",
                "first_fill_session",
                "active_session",
                "closed_session",
                "close_reason",
                "realized_status",
                "target_weight",
                "full_weight",
            )
        }
        for epoch in account.get("strategic_epochs", [])
    ]


def _physical_fill_identity(fill: dict[str, Any]) -> tuple[object, ...]:
    fill_id = str(fill["fill_id"])
    if fill_id:
        return ("BROKER", fill_id)
    return (
        "SIMULATED",
        str(fill["order_id"]),
        str(fill["signal_date"]),
        str(fill["fill_date"]),
        str(fill["symbol"]),
        str(fill["side"]),
        int(fill["shares"]),
        float(fill["price"]).hex(),
        float(fill["gross_value"]).hex(),
        str(fill["event_id"]),
        str(fill["grant_id"]),
        str(fill["epoch_id"]),
    )


def _activity_projection(result: dict[str, Any]) -> dict[str, Any]:
    trace = result["decision_trace"]
    account = result["final_account"]
    target_origins: dict[str, set[str]] = {}
    target_mechanisms: dict[str, set[str]] = {}
    dominant_sessions: set[str] = set()
    for row in trace:
        for target in row["targets"]:
            date = str(row["date"])
            target_origins.setdefault(str(target["origin_subsystem"]), set()).add(date)
            target_mechanisms.setdefault(str(target["mechanism"]), set()).add(date)
            if (
                target["origin_subsystem"] == "STRATEGIC"
                and float(target["weight"]) > DEFAULT_CONFIG.max_symbol_weight
            ):
                dominant_sessions.add(date)
    order_origins: dict[str, int] = {}
    order_mechanisms: dict[str, int] = {}
    for order in account["order_ledger"]:
        origin = str(order["origin_subsystem"])
        mechanism = str(order["mechanism"])
        order_origins[origin] = order_origins.get(origin, 0) + 1
        order_mechanisms[mechanism] = order_mechanisms.get(mechanism, 0) + 1
    fill_origins: dict[str, int] = {}
    fill_mechanisms: dict[str, int] = {}
    for fill in account["fills"]:
        origin = str(fill["origin_subsystem"])
        mechanism = str(fill["mechanism"])
        fill_origins[origin] = fill_origins.get(origin, 0) + 1
        fill_mechanisms[mechanism] = fill_mechanisms.get(mechanism, 0) + 1
    risk_events = account.get("risk_events", [])
    return {
        "target_origin_sessions": {key: len(value) for key, value in sorted(target_origins.items())},
        "target_mechanism_sessions": {key: len(value) for key, value in sorted(target_mechanisms.items())},
        "order_origins": order_origins,
        "order_mechanisms": order_mechanisms,
        "fill_origins": fill_origins,
        "fill_mechanisms": fill_mechanisms,
        "dominant_owner_exception_sessions": len(dominant_sessions),
        "sentinel_freeze_sessions": sum(
            bool(event.get("freeze_new_risk")) for event in result.get("sentinel_events", [])
        ),
        "risk_event_tokens": [
            json.dumps(event, sort_keys=True, separators=(",", ":")).lower() for event in risk_events
        ],
        "final_state": {
            key: account.get(key)
            for key in (
                "sector_guard_active",
                "capital_budget_level",
                "chronic_level",
                "strategic_epoch",
                "strategic_epochs_completed",
            )
        },
    }


def _project_replay(result: dict[str, Any]) -> dict[str, Any]:
    trace = result["decision_trace"]
    ledger = result["attribution"]["daily_ledger"]
    account = result["final_account"]
    dates = [str(row["date"]) for row in trace]
    concentration = result["attribution"]["concentration"]["positive"]
    cash_flags = [float(row["cash_weight"]) >= 1.0 - 1e-12 for row in ledger]
    order_ids = [str(order["order_id"]) for order in account["order_ledger"]]
    fill_ids = [_physical_fill_identity(fill) for fill in account["fills"]]
    invariants = {
        "no_negative_cash": all(float(row["cash"]) >= -1e-8 for row in ledger),
        "no_leverage": all(float(row["gross_exposure"]) <= DEFAULT_CONFIG.max_gross + 1e-8 for row in ledger),
        "no_short": all(
            int(shares) >= 0
            for row in result["daily_replay_evidence"]
            for shares in row["position_shares"].values()
        ),
        "no_duplicate_order": len(order_ids) == len(set(order_ids)),
        "no_duplicate_fill": len(fill_ids) == len(set(fill_ids)),
        "no_future_data": all(str(fill["fill_date"]) > str(fill["signal_date"]) for fill in account["fills"]),
        "accounting_reconciled": bool(result["attribution"]["accounting"]["reconciled"]),
    }
    return {
        "metrics": {
            "final_wealth": float(result["final_wealth"]),
            "final_equity": float(result["final_equity"]),
            "max_drawdown": float(result["max_drawdown"]),
            "account_orders": int(result["account_orders"]),
            "fills": len(account["fills"]),
            "gross_turnover": float(result["gross_turnover"]),
            "annual_turnover": float(result["annual_turnover"]),
            "top1_concentration": float(concentration.get("top1") or 0.0),
            "top3_concentration": float(concentration.get("top3") or 0.0),
            "pnl_hhi": float(concentration.get("hhi") or 0.0),
            "concentration_status": str(concentration["status"]),
            "pending_orders": int(result["pending_orders"]),
        },
        "dates": dates,
        "target_signatures": [_economic_target_signature(row) for row in trace],
        "owners": [_strategic_owner(row) for row in trace],
        "risk_states": [str(row["risk"]["state"]) for row in trace],
        "opportunity_states": [str(row["opportunity"]) for row in trace],
        "target_counts": [len(row["targets"]) for row in trace],
        "cash_flags": cash_flags,
        "longest_cash_streak": _longest_true_streak(dates, cash_flags),
        "strategic_epochs": _epoch_projection(account),
        "activity": _activity_projection(result),
        "invariants": invariants,
        "acceptance_result": "PASS" if all(invariants.values()) else "FAIL",
    }


def _cache_path(cache_dir: Path, case_id: str) -> Path:
    return cache_dir / f"{case_id.lower().replace('_', '-')}.json"


def _sealed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "canonical_sha256": canonical_json_sha256(payload)}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(payload) + b"\n")
    temporary.replace(path)


def _write_pretty_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_sealed(path: Path) -> dict[str, Any]:
    raw = strict_json_loads(path.read_bytes())
    if not isinstance(raw, dict):
        raise ValueError(f"cache is not an object: {path}")
    payload = dict(raw)
    claimed = payload.pop("canonical_sha256", None)
    if claimed != canonical_json_sha256(payload):
        raise ValueError(f"cache seal differs: {path}")
    return raw


def run_parameter_case(
    case_id: str,
    *,
    data_dir: str | Path = "data/frozen",
    cache_dir: str | Path,
) -> dict[str, Any]:
    """Run or reuse one identity-bound successful production replay."""

    data_path = Path(data_dir)
    cache_path = _cache_path(Path(cache_dir), case_id)
    config, identity = _case_identity(case_id, data_path)
    if cache_path.exists():
        cached = _read_sealed(cache_path)
        if cached.get("identity") == identity and cached.get("status") == "SUCCESS":
            return cached
    parameter = _case_config(case_id)[1]
    try:
        result = ProductionEngine(data_path, config).backtest(
            symbols=_REPLAY_SYMBOLS,
            start=_REPLAY_START,
            end=_REPLAY_END,
        )
        payload = {
            "schema_version": 1,
            "case_id": case_id,
            "parameter": parameter,
            "identity": identity,
            "status": "SUCCESS",
            "projection": _project_replay(result),
        }
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "case_id": case_id,
            "parameter": parameter,
            "identity": identity,
            "status": "REPLAY_ERROR",
            "error": f"{type(exc).__name__}: {exc}",
        }
    sealed = _sealed(payload)
    _write_json(cache_path, sealed)
    return sealed


def _changed_count(left: list[Any], right: list[Any]) -> int:
    if len(left) != len(right):
        return max(len(left), len(right))
    return sum(a != b for a, b in zip(left, right, strict=True))


def _epoch_difference_count(left: list[Any], right: list[Any]) -> int:
    shared = sum(a != b for a, b in zip(left, right, strict=False))
    return shared + abs(len(left) - len(right))


def _case_summary(case: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    common = {
        "case_id": case["case_id"],
        "parameter": case["parameter"],
        "identity": case["identity"],
        "status": case["status"],
        "cache_canonical_sha256": case["canonical_sha256"],
    }
    if case["status"] != "SUCCESS" or baseline["status"] != "SUCCESS":
        return {**common, "error": case.get("error"), "acceptance_result": "INCOMPLETE"}
    current = case["projection"]
    reference = baseline["projection"]
    metrics = current["metrics"]
    base_metrics = reference["metrics"]
    return {
        **common,
        "metrics": metrics,
        "differences_vs_baseline": {
            "final_wealth": metrics["final_wealth"] - base_metrics["final_wealth"],
            "max_drawdown": metrics["max_drawdown"] - base_metrics["max_drawdown"],
            "account_orders": metrics["account_orders"] - base_metrics["account_orders"],
            "gross_turnover": metrics["gross_turnover"] - base_metrics["gross_turnover"],
            "top1_concentration": metrics["top1_concentration"] - base_metrics["top1_concentration"],
            "top3_concentration": metrics["top3_concentration"] - base_metrics["top3_concentration"],
            "pnl_hhi": metrics["pnl_hhi"] - base_metrics["pnl_hhi"],
            "target_changed_sessions": _changed_count(
                current["target_signatures"], reference["target_signatures"]
            ),
            "owner_changed_sessions": _changed_count(current["owners"], reference["owners"]),
            "risk_state_changed_sessions": _changed_count(current["risk_states"], reference["risk_states"]),
            "strategic_epoch_differences": _epoch_difference_count(
                current["strategic_epochs"], reference["strategic_epochs"]
            ),
            "cash_streak_sessions": current["longest_cash_streak"]["sessions"]
            - reference["longest_cash_streak"]["sessions"],
        },
        "longest_cash_streak": current["longest_cash_streak"],
        "invariants": current["invariants"],
        "acceptance_result": current["acceptance_result"],
    }


def _pair_classification(
    lower: dict[str, Any], upper: dict[str, Any], *, total_sessions: int
) -> tuple[str, str]:
    if lower.get("status") != "SUCCESS" or upper.get("status") != "SUCCESS":
        return "SENSITIVE", "incomplete replay evidence; stability is not established"
    deltas = [lower["differences_vs_baseline"], upper["differences_vs_baseline"]]
    exactly_inactive = all(
        all(abs(float(delta[key])) <= 1e-12 for key in ("final_wealth", "max_drawdown", "gross_turnover"))
        and all(
            int(delta[key]) == 0
            for key in (
                "account_orders",
                "target_changed_sessions",
                "owner_changed_sessions",
                "risk_state_changed_sessions",
                "strategic_epoch_differences",
            )
        )
        for delta in deltas
    )
    if exactly_inactive:
        return "INACTIVE", "both bounded perturbations are economically identical to baseline"
    severe = any(
        abs(float(delta["final_wealth"])) >= 0.25
        or abs(float(delta["max_drawdown"])) >= 0.10
        or abs(int(delta["account_orders"])) >= 4
        or int(delta["target_changed_sessions"]) >= max(10, int(total_sessions * 0.20))
        for delta in deltas
    )
    if severe:
        return "KNIFE_EDGE", "at least one bounded perturbation crosses a preregistered discontinuity bound"
    stable = all(
        abs(float(delta["final_wealth"])) <= 0.05
        and abs(float(delta["max_drawdown"])) <= 0.03
        and abs(int(delta["account_orders"])) <= 2
        and int(delta["target_changed_sessions"]) <= max(5, int(total_sessions * 0.05))
        and int(delta["risk_state_changed_sessions"]) <= 2
        for delta in deltas
    )
    if stable:
        return "STABLE", "both bounded perturbations remain inside preregistered economic bounds"
    return "SENSITIVE", "bounded perturbation changes behavior without crossing the knife-edge bound"


def _parameter_evidence(cases: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = cases[0]
    summaries = [_case_summary(case, baseline) for case in cases]
    total_sessions = len(baseline["projection"]["dates"]) if baseline["status"] == "SUCCESS" else 0
    by_id = {case["case_id"]: case for case in summaries}
    pairs = []
    for index in range(0, len(PARAMETER_VARIANT_SPECS), 2):
        lower_spec = PARAMETER_VARIANT_SPECS[index]
        upper_spec = PARAMETER_VARIANT_SPECS[index + 1]
        classification, rationale = _pair_classification(
            by_id[lower_spec.case_id], by_id[upper_spec.case_id], total_sessions=total_sessions
        )
        default_value = getattr(DEFAULT_CONFIG, lower_spec.field)
        successful = [
            by_id[case_id]
            for case_id in (lower_spec.case_id, upper_spec.case_id)
            if by_id[case_id]["status"] == "SUCCESS"
        ]
        baseline_wealth = (
            float(by_id["BASELINE"]["metrics"]["final_wealth"])
            if by_id["BASELINE"]["status"] == "SUCCESS"
            else None
        )
        default_is_best = bool(
            baseline_wealth is not None
            and successful
            and all(baseline_wealth >= float(case["metrics"]["final_wealth"]) for case in successful)
        )
        pairs.append(
            {
                "field": lower_spec.field,
                "default_value": default_value,
                "lower_case_id": lower_spec.case_id,
                "upper_case_id": upper_spec.case_id,
                "classification": classification,
                "rationale": rationale,
                "default_is_best": default_is_best,
                "default_is_best_positive_evidence": False,
            }
        )
    return {
        "budget": {
            "baseline_replays": 1,
            "one_factor_variants": 16,
            "historical_production_engine_replays": 17,
            "grid_search": False,
            "selection": False,
        },
        "window": {
            "start": _REPLAY_START,
            "end": _REPLAY_END,
            "future_holdout_boundary": "2026-08-06",
        },
        "symbols": list(_REPLAY_SYMBOLS),
        "classification_bounds": {
            "stable_final_wealth_absolute": 0.05,
            "stable_mdd_absolute": 0.03,
            "stable_account_orders_absolute": 2,
            "stable_target_changed_fraction": 0.05,
            "knife_edge_final_wealth_absolute": 0.25,
            "knife_edge_mdd_absolute": 0.10,
            "knife_edge_account_orders_absolute": 4,
            "knife_edge_target_changed_fraction": 0.20,
        },
        "cases": summaries,
        "pairs": pairs,
        "holdout_used_for_selection": False,
        "parameters_optimized": False,
    }


def _equal_weight_returns(store: DataStore, symbols: tuple[str, ...]) -> pd.DataFrame:
    closes = pd.concat(
        {symbol: store.load(symbol)["close"] for symbol in symbols},
        axis=1,
        join="outer",
        sort=True,
    ).loc[_REPLAY_START:_REPLAY_END]
    returns = closes.pct_change(fill_method=None).dropna(how="all")
    if len(returns) < 20:
        raise RuntimeError("regime evidence has fewer than 20 complete sessions")
    return returns


def _worst_compound_window(returns: pd.DataFrame, sessions: int) -> dict[str, Any]:
    equal_weight = returns.mean(axis=1)
    compounded = (1.0 + equal_weight).rolling(sessions).apply(np.prod, raw=True) - 1.0
    end = pd.Timestamp(compounded.idxmin())
    location = returns.index.get_loc(end)
    start = pd.Timestamp(returns.index[location - sessions + 1])
    return {
        "start": str(start.date()),
        "end": str(end.date()),
        "sessions": sessions,
        "equal_weight_compound_return": float(compounded.loc[end]),
        "maximum_members": int(returns.shape[1]),
        "minimum_available_members": int(
            returns.iloc[location - sessions + 1 : location + 1].notna().sum(axis=1).min()
        ),
        "method": "AVAILABLE_MEMBER_DAILY_EQUAL_WEIGHT_RETURN_THEN_COMPOUND_NO_IMPUTATION",
    }


def _highest_correlation_selloff(returns: pd.DataFrame) -> dict[str, Any]:
    equal_weight = returns.mean(axis=1)
    five_day = (1.0 + equal_weight).rolling(5).apply(np.prod, raw=True) - 1.0
    best: tuple[float, pd.Timestamp, float] | None = None
    for location in range(19, len(returns)):
        end = pd.Timestamp(returns.index[location])
        selloff = float(five_day.loc[end])
        if not np.isfinite(selloff) or selloff >= 0:
            continue
        correlation = returns.iloc[location - 19 : location + 1].corr().to_numpy()
        upper = correlation[np.triu_indices_from(correlation, k=1)]
        mean_correlation = float(np.nanmean(upper))
        if best is None or mean_correlation > best[0]:
            best = (mean_correlation, end, selloff)
    if best is None:  # pragma: no cover - frozen data has many selloffs
        raise RuntimeError("no synchronized negative AI window found")
    correlation, end, selloff = best
    location = returns.index.get_loc(end)
    return {
        "start": str(pd.Timestamp(returns.index[location - 19]).date()),
        "end": str(end.date()),
        "correlation_sessions": 20,
        "mean_pairwise_correlation": correlation,
        "ending_five_session_equal_weight_return": selloff,
        "members": int(returns.shape[1]),
    }


def _regime_evidence(data_dir: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    store = DataStore(data_dir)
    universe = default_ai_universe()
    ai_returns = _equal_weight_returns(store, universe.symbols)
    optical_symbols = ("sz300308", "sz300502", "sz300394")
    optical_returns = _equal_weight_returns(store, optical_symbols)
    checkpoint_a_raw = strict_json_loads(
        Path("benchmarks/post_generalization_trust_closure_checkpoint_a.json").read_bytes()
    )
    if not isinstance(checkpoint_a_raw, dict):
        raise ValueError("checkpoint A evidence must be an object")
    no_optical = checkpoint_a_raw["current_identity_counterfactual"]["bounded_current_diagnostics"][
        "cases"
    ]["no_optical"]
    prolonged_weak: dict[str, Any]
    if baseline["status"] == "SUCCESS":
        projection = baseline["projection"]
        streak = projection["longest_cash_streak"]
        dates = projection["dates"]
        if streak["start"] is not None:
            start_index = dates.index(streak["start"])
            end_index = dates.index(streak["end"])
            prolonged_weak = {
                **streak,
                "opportunity_states": sorted(
                    set(projection["opportunity_states"][start_index : end_index + 1])
                ),
                "risk_states": sorted(set(projection["risk_states"][start_index : end_index + 1])),
                "all_targets_zero": all(
                    count == 0 for count in projection["target_counts"][start_index : end_index + 1]
                ),
                "source": "BASELINE_PRODUCTION_REPLAY",
            }
        else:
            prolonged_weak = {"status": "NO_FULL_CASH_STREAK_OBSERVED"}
    else:
        prolonged_weak = {"status": "EVIDENCE_GAP_BASELINE_REPLAY_ERROR"}
    return {
        "scope": "OBSERVED_FROZEN_HISTORY_ONLY",
        "random_synthetic_paths": False,
        "observed_frozen_windows": {
            "ai_worst_5d": _worst_compound_window(ai_returns, 5),
            "ai_worst_20d": _worst_compound_window(ai_returns, 20),
            "ai_highest_correlation_selloff": _highest_correlation_selloff(ai_returns),
            "optical_worst_5d": _worst_compound_window(optical_returns, 5),
            "optical_worst_20d": _worst_compound_window(optical_returns, 20),
        },
        "prolonged_weak_ai": prolonged_weak,
        "checkpoint_a_no_optical_latency": {
            "observed_optical_failure": False,
            "scenario": "PERMANENT_EX_ANTE_OPTICAL_REMOVAL",
            "first_positive_target_session": no_optical["metrics"]["first_positive_target_session"],
            "first_positive_strategic_target_session": no_optical["metrics"][
                "first_positive_strategic_target_session"
            ],
            "final_wealth": no_optical["metrics"]["final_wealth"],
            "interpretation": (
                "capability diagnostic only; permanent removal cannot measure an observed "
                "failure, discovery latency, or transition latency"
            ),
        },
        "leadership_rotation": {"status": "EVIDENCE GAP"},
        "whipsaw": {"status": "EVIDENCE GAP"},
        "evidence_gaps": [
            "archived_state_trajectories",
            "formal_optical_failure_latencies",
            "real_rotation_events",
            "whipsaw",
        ],
    }


def _mechanism_count(activity: dict[str, Any], mechanisms: tuple[str, ...]) -> tuple[int, int, int]:
    targets = sum(int(activity["target_mechanism_sessions"].get(mechanism, 0)) for mechanism in mechanisms)
    orders = sum(int(activity["order_mechanisms"].get(mechanism, 0)) for mechanism in mechanisms)
    fills = sum(int(activity["fill_mechanisms"].get(mechanism, 0)) for mechanism in mechanisms)
    return targets, orders, fills


def _origin_count(activity: dict[str, Any], origin: str) -> tuple[int, int, int]:
    return (
        int(activity["target_origin_sessions"].get(origin, 0)),
        int(activity["order_origins"].get(origin, 0)),
        int(activity["fill_origins"].get(origin, 0)),
    )


def _state_row(
    name: str,
    counts: tuple[int, int, int],
    *,
    observed_state_sessions: int | None = None,
    current_reachable: bool = True,
    compatibility_only: bool = False,
    evidence_note: str,
) -> dict[str, Any]:
    target_sessions, orders, fills = counts
    observed = (observed_state_sessions or 0) + target_sessions + orders + fills
    if compatibility_only:
        classification = "COMPAT_ONLY"
    elif observed:
        classification = "ACTIVE_USEFUL"
    elif current_reachable:
        classification = "INACTIVE_REACHABLE"
    else:
        classification = "UNREACHABLE"
    return {
        "name": name,
        "classification": classification,
        "current_reachable": current_reachable,
        "historical_trigger_sessions": (
            observed_state_sessions if observed_state_sessions is not None else target_sessions
        ),
        "changed_target_sessions": target_sessions if target_sessions else None,
        "model_orders": orders,
        "model_fills": fills,
        "changes_order_or_fill": bool(orders or fills),
        "independent_risk_reduction": "NOT_ISOLATED",
        "independent_return_change": "NOT_ISOLATED",
        "overlap_with_other_state": "NOT_ISOLATED",
        "deletion_changes_current_behavior": (
            False
            if compatibility_only
            else True
            if target_sessions or orders or fills
            else None
        ),
        "compatibility_only": compatibility_only,
        "production_callers": current_reachable,
        "test_only": False,
        "legacy_schema_or_api_only": False,
        "removal_disposition": (
            "CHECKPOINT_C_DELETE_AFTER_EQUIVALENCE"
            if compatibility_only
            else "REPORT_ONLY_NO_ACTIVE_ECONOMIC_DELETION"
        ),
        "evidence_note": evidence_note,
    }


def _utility_evidence(baseline: dict[str, Any], parameter: dict[str, Any]) -> dict[str, Any]:
    if baseline["status"] != "SUCCESS":
        raise RuntimeError("state utility requires a successful baseline replay")
    activity = baseline["projection"]["activity"]
    tokens = activity["risk_event_tokens"]

    def event_count(token: str) -> int:
        return sum(token in event for event in tokens)

    states = [
        _state_row(
            "ordinary_leader",
            _origin_count(activity, "LEADER"),
            evidence_note="structured LEADER Target/Order/Fill counts from baseline",
        ),
        _state_row(
            "strategic",
            _origin_count(activity, "STRATEGIC"),
            evidence_note="structured STRATEGIC Target/Order/Fill counts from baseline",
        ),
        _state_row(
            "recovery",
            _origin_count(activity, "RECOVERY"),
            evidence_note="structured RECOVERY Target/Order/Fill counts from baseline",
        ),
        _state_row(
            "tactical",
            _mechanism_count(activity, ("TACTICAL_REBOUND",)),
            evidence_note="TACTICAL_REBOUND mechanism counts from baseline",
        ),
        _state_row(
            "sector_guard",
            _mechanism_count(activity, ("SECTOR_GUARD",)),
            evidence_note="SECTOR_GUARD mechanism counts from baseline",
        ),
        _state_row(
            "chronic_overlay",
            (0, 0, 0),
            observed_state_sessions=event_count("chronic"),
            evidence_note="chronic risk-event token count; no deletion counterfactual was run",
        ),
        _state_row(
            "capital_budget",
            _mechanism_count(activity, ("CAPITAL_BUDGET",)),
            evidence_note="CAPITAL_BUDGET mechanism counts from baseline",
        ),
        _state_row(
            "concentrated_break",
            (0, 0, 0),
            observed_state_sessions=event_count("concentrated"),
            evidence_note="concentrated risk-event token count; no deletion counterfactual was run",
        ),
        _state_row(
            "rearm",
            _mechanism_count(
                activity,
                ("RECOVERY_REARM", "STRATEGIC_RESTORATION", "POST_SHOCK_RESTORATION"),
            ),
            evidence_note="restoration/rearm mechanism counts from baseline",
        ),
        _state_row(
            "freeze",
            (0, 0, 0),
            observed_state_sessions=int(activity["sentinel_freeze_sessions"]),
            evidence_note="causal sentinel freeze session count from baseline",
        ),
        _state_row(
            "strategic_damage_guard",
            _mechanism_count(activity, ("STRATEGIC_DAMAGE_GUARD",)),
            evidence_note="STRATEGIC_DAMAGE_GUARD mechanism counts from baseline",
        ),
        _state_row(
            "dominant_owner_exception",
            (int(activity["dominant_owner_exception_sessions"]), 0, 0),
            evidence_note="strategic target weight exceeded ordinary symbol cap in observed sessions",
        ),
        _state_row(
            "active_compatibility_paths",
            (0, 0, 0),
            compatibility_only=True,
            evidence_note="all four compatibility switches are false on the current default path",
        ),
    ]
    pair_by_field = {row["field"]: row for row in parameter["pairs"]}
    config_fields: list[dict[str, Any]] = [
        {
            "name": name,
            "classification": "COMPAT_ONLY",
            "current_default_value": getattr(DEFAULT_CONFIG, name),
            "current_default_active": bool(getattr(DEFAULT_CONFIG, name)),
            "historical_production_path_active": False,
            "deletion_checkpoint": "C",
        }
        for name in _COMPATIBILITY_FIELDS
    ]
    for field_name, pair in pair_by_field.items():
        config_fields.append(
            {
                "name": field_name,
                "classification": (
                    "INACTIVE_REACHABLE" if pair["classification"] == "INACTIVE" else "ACTIVE_USEFUL"
                ),
                "current_default_value": getattr(DEFAULT_CONFIG, field_name),
                "sensitivity_classification": pair["classification"],
                "deletion_checkpoint": None,
            }
        )
    return {
        "allowed_classifications": list(_ALLOWED_UTILITY_CLASSIFICATIONS),
        "audit_window": {"start": _REPLAY_START, "end": _REPLAY_END},
        "active_state_deletion_performed": False,
        "causal_deletion_counterfactual_performed": False,
        "states_and_guards": states,
        "config_fields": config_fields,
        "current_config_field_count": len(fields(SystemConfig)),
        "compatibility_fields_scheduled_for_checkpoint_c": list(_COMPATIBILITY_FIELDS),
        "limits": (
            "activity proves reachability and behavior changes only; independent risk, return, "
            "redundancy, and safe deletion require a separate counterfactual"
        ),
    }


def assemble_checkpoint_b(
    *,
    data_dir: str | Path = "data/frozen",
    cache_dir: str | Path,
) -> dict[str, Any]:
    """Assemble sealed Checkpoint B evidence from all fixed replay caches."""

    data_path = Path(data_dir)
    cache_path = Path(cache_dir)
    case_ids = ("BASELINE", *(spec.case_id for spec in PARAMETER_VARIANT_SPECS))
    cases = [_read_sealed(_cache_path(cache_path, case_id)) for case_id in case_ids]
    for case_id, case in zip(case_ids, cases, strict=True):
        _, expected_identity = _case_identity(case_id, data_path)
        if case.get("identity") != expected_identity:
            raise ValueError(f"parameter cache identity differs: {case_id}")
    parameter = _parameter_evidence(cases)
    payload = {
        "schema_version": 1,
        "evidence_id": "post-generalization-trust-closure-checkpoint-b",
        "authoritative_acceptance": False,
        "future_holdout_used": False,
        "scope": "HISTORICAL_DIAGNOSTIC_ONLY",
        "production_defaults_changed": False,
        "parameter_sensitivity": parameter,
        "state_guard_config_utility": _utility_evidence(cases[0], parameter),
        "execution_stress": run_execution_stresses(),
        "regime_evidence": _regime_evidence(data_path, cases[0]),
        "observation_policy": {
            "future_holdout_used_for_tuning": False,
            "parameter_search": False,
            "production_authority_granted": False,
            "actual_broker_facts_present": False,
        },
    }
    return _sealed(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/frozen")
    parser.add_argument("--cache-dir", required=True)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--case", choices=("BASELINE", *(spec.case_id for spec in PARAMETER_VARIANT_SPECS)))
    actions.add_argument("--run-all", action="store_true")
    actions.add_argument("--assemble", action="store_true")
    parser.add_argument("--output", default="benchmarks/post_generalization_trust_closure_checkpoint_b.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.case:
        result = run_parameter_case(
            args.case,
            data_dir=args.data_dir,
            cache_dir=args.cache_dir,
        )
        print(json.dumps({key: result.get(key) for key in ("case_id", "status", "error")}))
        return 0 if result["status"] == "SUCCESS" else 1
    if args.run_all:
        for case_id in ("BASELINE", *(spec.case_id for spec in PARAMETER_VARIANT_SPECS)):
            result = run_parameter_case(
                case_id,
                data_dir=args.data_dir,
                cache_dir=args.cache_dir,
            )
            print(json.dumps({key: result.get(key) for key in ("case_id", "status", "error")}))
            if result["status"] != "SUCCESS":
                return 1
        return 0
    evidence = assemble_checkpoint_b(data_dir=args.data_dir, cache_dir=args.cache_dir)
    _write_pretty_json(Path(args.output), evidence)
    print(evidence["canonical_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
