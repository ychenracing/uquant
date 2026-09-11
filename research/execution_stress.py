"""Deterministic next-open execution stresses using the production planner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from uquant.config import DEFAULT_CONFIG
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
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe


@dataclass(frozen=True, slots=True)
class ExecutionStressSpec:
    """One bounded execution-tail case."""

    case_id: str
    kind: str
    value: float | None = None

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
    event_id = derive_attribution_event_id(
        signal_date=_SIGNAL_DATE,
        symbol=_SYMBOL,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_subsystem=origin.value,
        mechanism=mechanism.value,
        origin_lifecycle=lifecycle,
        replaces_symbol=None,
        industry_at_entry=industry,
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy=ReductionPolicy.FIFO.value,
        reason_code="execution_stress",
        exit_kind="stress",
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
        origin_subsystem=origin.value,
        mechanism=mechanism.value,
        origin_lifecycle=lifecycle,
        replaces_symbol=None,
        industry_at_entry=industry,
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
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
        if spec.value is None:
            raise ValueError("ADVERSE_SLIPPAGE_BPS requires a value")
        config = config.override(slippage=DEFAULT_CONFIG.slippage + spec.value / 10_000.0)
    elif spec.kind == "PARTIAL_FILL_RATIO":
        if spec.value is None:
            raise ValueError("PARTIAL_FILL_RATIO requires a value")
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
