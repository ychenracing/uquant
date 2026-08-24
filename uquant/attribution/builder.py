"""Economic attribution orchestration over validated domain calculations."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ..types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    OriginSubsystem,
    Side,
)
from .concentration import (
    RECONCILIATION_TOLERANCE,
    contribution_concentration,
)
from .concentration import (
    finite_attribution_number as _finite,
)
from .concentration import (
    group_lot_pnl as _group_lot_pnl,
)
from .concentration import (
    holding_summary as _holding_summary,
)
from .diagnostics import attribution_diagnostics
from .replay_evidence import positive_attribution_integer as _positive_integer
from .validation import economic_sessions as _economic_sessions


def _realized_lot_rows(account: AccountState, session_set: set[str]) -> list[dict[str, Any]]:
    lots: list[dict[str, Any]] = []
    for fill in account.fills:
        if fill.fill_date not in session_set:
            raise ValueError("economic fill lies outside the exact economic interval")
        if fill.side != Side.SELL.value:
            continue
        sold_lot_shares = [
            _positive_integer(item.get("shares"), label="sold-lot shares") for item in fill.sold_tranches
        ]
        if sum(sold_lot_shares) != fill.shares:
            raise ValueError("sell fill must reconcile through per-lot sold_tranches")
        for component in ("commission", "stamp_duty", "transfer_fee", "slippage_cost"):
            allocated_component = sum(
                _finite(item.get(component), label=f"sold-lot {component}", minimum=0.0)
                for item in fill.sold_tranches
            )
            if not math.isclose(
                allocated_component,
                float(getattr(fill, component)),
                rel_tol=1e-12,
                abs_tol=1e-8,
            ):
                raise ValueError(f"sold-lot {component} does not reconcile to fill")
        for allocation, shares in zip(fill.sold_tranches, sold_lot_shares, strict=True):
            ratio = shares / fill.shares
            proceeds = fill.gross_value * ratio
            cash_fees = sum(
                _finite(
                    allocation.get(name, getattr(fill, name) * ratio),
                    label=f"sold-lot {name}",
                    minimum=0.0,
                )
                for name in ("commission", "stamp_duty", "transfer_fee")
            )
            cost_basis = _finite(
                allocation.get(
                    "cost_basis",
                    shares * float(allocation.get("unit_cost", allocation.get("avg_cost", 0.0))),
                ),
                label="sold-lot cost basis",
                minimum=0.0,
            )
            pnl = proceeds - cash_fees - cost_basis
            lots.append(
                {
                    "economic_status": "REALIZED",
                    "symbol": fill.symbol,
                    "tranche_id": str(allocation["tranche_id"]),
                    "shares": shares,
                    "entry_date": str(allocation["entry_date"]),
                    "exit_date": fill.fill_date,
                    "final_date": None,
                    "realized_pnl": pnl,
                    "open_pnl": 0.0,
                    "total_pnl": pnl,
                    "origin_event_id": str(allocation["event_id"]),
                    "origin_subsystem": str(allocation["origin_subsystem"]),
                    "origin_mechanism": str(allocation["mechanism"]),
                    "origin_lifecycle": str(allocation["origin_lifecycle"]),
                    "current_lifecycle": str(allocation["lifecycle"]),
                    "exit_subsystem": fill.origin_subsystem,
                    "exit_mechanism": fill.mechanism,
                    "replaces_symbol": allocation.get("replaces_symbol"),
                    "industry_at_entry": str(allocation["industry_at_entry"]),
                    "entry_gross_value": 0.0,
                    "exit_gross_value": proceeds,
                    "exit_costs": {
                        "commission": float(allocation["commission"]),
                        "stamp_duty": float(allocation["stamp_duty"]),
                        "transfer_fee": float(allocation["transfer_fee"]),
                        "slippage": float(allocation["slippage_cost"]),
                    },
                }
            )
    return lots


def _open_lot_rows(
    account: AccountState,
    final_prices: Mapping[str, float],
    *,
    economic_end: str,
    session_set: set[str],
) -> list[dict[str, Any]]:
    lots: list[dict[str, Any]] = []
    for symbol, position in sorted(account.positions.items()):
        if position.shares <= 0:
            continue
        mark = _finite(final_prices.get(symbol), label=f"final mark for {symbol}", minimum=0.0)
        if mark <= 0.0:
            raise ValueError(f"final mark for {symbol} must be positive")
        if sum(tranche.shares for tranche in position.tranches) != position.shares:
            raise ValueError("open position shares do not reconcile to tranches")
        for tranche in sorted(position.tranches, key=lambda item: item.tranche_id):
            if tranche.entry_date not in session_set:
                raise ValueError("open lot lies outside the exact economic interval")
            pnl = tranche.shares * (mark - tranche.avg_cost)
            lots.append(
                {
                    "economic_status": "OPEN",
                    "symbol": symbol,
                    "tranche_id": tranche.tranche_id,
                    "shares": tranche.shares,
                    "entry_date": tranche.entry_date,
                    "exit_date": None,
                    "final_date": economic_end,
                    "realized_pnl": 0.0,
                    "open_pnl": pnl,
                    "total_pnl": pnl,
                    "origin_event_id": tranche.event_id,
                    "origin_subsystem": tranche.origin_subsystem,
                    "origin_mechanism": tranche.mechanism,
                    "origin_lifecycle": tranche.origin_lifecycle,
                    "current_lifecycle": tranche.lifecycle,
                    "exit_subsystem": None,
                    "exit_mechanism": None,
                    "replaces_symbol": tranche.replaces_symbol,
                    "industry_at_entry": tranche.industry_at_entry,
                    "entry_gross_value": 0.0,
                    "exit_gross_value": 0.0,
                    "exit_costs": {
                        "commission": 0.0,
                        "stamp_duty": 0.0,
                        "transfer_fee": 0.0,
                        "slippage": 0.0,
                    },
                }
            )
    return lots


def _allocate_entry_fill_economics(account: AccountState, lots: list[dict[str, Any]]) -> None:
    buy_fills: dict[tuple[str, str, str], Any] = {}
    for fill in account.fills:
        if fill.side != Side.BUY.value:
            continue
        key = (fill.symbol, fill.event_id, fill.fill_date)
        if not fill.event_id or key in buy_fills:
            raise ValueError("originating BUY fill identity is missing or ambiguous")
        if fill.stamp_duty != 0.0:
            raise ValueError("BUY fill cannot carry stamp duty")
        buy_fills[key] = fill
    rows_by_buy: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for lot in lots:
        key = (str(lot["symbol"]), str(lot["origin_event_id"]), str(lot["entry_date"]))
        rows_by_buy.setdefault(key, []).append(lot)
    if set(rows_by_buy) != set(buy_fills):
        raise ValueError("economic lots do not exactly cover originating BUY fills")
    for key, fill in sorted(buy_fills.items()):
        _allocate_one_entry_fill(fill, rows_by_buy[key])


def _allocate_one_entry_fill(fill: Any, raw_rows: list[dict[str, Any]]) -> None:
    rows = sorted(raw_rows, key=lambda item: (str(item["tranche_id"]), str(item["economic_status"])))
    if sum(int(row["shares"]) for row in rows) != fill.shares:
        raise ValueError("economic lot shares do not reconcile to originating BUY fill")
    entry_components = {
        "entry_gross_value": "gross_value",
        "entry_commission": "commission",
        "entry_stamp_duty": "stamp_duty",
        "entry_transfer_fee": "transfer_fee",
        "entry_slippage": "slippage_cost",
    }
    allocated = {name: 0.0 for name in entry_components}
    for index, row in enumerate(rows):
        final_row = index == len(rows) - 1
        ratio = int(row["shares"]) / fill.shares
        entry_costs: dict[str, float] = {}
        for output_name, fill_name in entry_components.items():
            total = float(getattr(fill, fill_name))
            value = total - allocated[output_name] if final_row else total * ratio
            allocated[output_name] += value
            if output_name == "entry_gross_value":
                row[output_name] = value
            else:
                entry_costs[output_name] = value
        exit_costs = row.pop("exit_costs")
        cash_fees = sum(
            entry_costs[name] for name in ("entry_commission", "entry_stamp_duty", "entry_transfer_fee")
        ) + sum(float(exit_costs[name]) for name in ("commission", "stamp_duty", "transfer_fee"))
        slippage_cost = entry_costs["entry_slippage"] + float(exit_costs["slippage"])
        row["costs"] = {
            **entry_costs,
            "exit_commission": float(exit_costs["commission"]),
            "exit_stamp_duty": float(exit_costs["stamp_duty"]),
            "exit_transfer_fee": float(exit_costs["transfer_fee"]),
            "exit_slippage": float(exit_costs["slippage"]),
            "cash_fees": cash_fees,
            "slippage": slippage_cost,
            "all_in": cash_fees + slippage_cost,
        }
        row["gross_transaction_value"] = float(row["entry_gross_value"]) + float(row["exit_gross_value"])


def _assign_holding_sessions(lots: list[dict[str, Any]], sessions: Sequence[str]) -> None:
    session_index = {date: index for index, date in enumerate(sessions)}
    for lot in lots:
        exit_or_final = str(lot["exit_date"] or lot["final_date"])
        entry_date = str(lot["entry_date"])
        if entry_date not in session_index or exit_or_final not in session_index:
            raise ValueError("lot holding period lies outside the exact economic interval")
        holding_sessions = session_index[exit_or_final] - session_index[entry_date]
        if holding_sessions < 0:
            raise ValueError("lot holding period is negative")
        lot["holding_sessions"] = holding_sessions


def _daily_diagnostics(
    *,
    daily_ledger: Sequence[Mapping[str, Any]],
    canonical_sessions: tuple[str, ...],
    total_pnl: float,
    benchmark_close: Mapping[str, float] | None,
    paired_counterfactual_equity: Mapping[str, float] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ledger_rows = [dict(row) for row in daily_ledger]
    if ledger_rows:
        if tuple(str(row.get("date", "")) for row in ledger_rows) != canonical_sessions:
            raise ValueError("daily attribution ledger must exactly cover economic sessions")
        ledger_total = sum(_finite(row.get("daily_pnl"), label="daily ledger PnL") for row in ledger_rows)
        if not math.isclose(
            ledger_total,
            total_pnl,
            rel_tol=1e-12,
            abs_tol=RECONCILIATION_TOLERANCE,
        ):
            raise ValueError("daily attribution ledger PnL does not reconcile")
        if benchmark_close is None:
            raise ValueError("daily attribution ledger requires a benchmark for cash drag")
        diagnostics = attribution_diagnostics(
            daily_ledger=ledger_rows,
            benchmark_close=benchmark_close,
            paired_counterfactual_equity=paired_counterfactual_equity,
        )
    else:
        if benchmark_close is not None or paired_counterfactual_equity is not None:
            raise ValueError("attribution diagnostics require a daily ledger")
        diagnostics = {
            "cash_drag": {
                "status": "NOT_EVALUATED_REQUIRES_DAILY_LEDGER",
                "value": None,
                "is_accounting_pnl": False,
            },
            "risk_avoidance": {
                "status": "NOT_EVALUATED_REQUIRES_PAIRED_COUNTERFACTUAL",
                "value": None,
                "is_accounting_pnl": False,
            },
        }
    return ledger_rows, diagnostics


def _attribution_groups(lots: list[dict[str, Any]]) -> dict[str, Any]:
    by_symbol = _group_lot_pnl(lots, "symbol")
    by_industry = _group_lot_pnl(lots, "industry_at_entry")
    lifecycle_registry = tuple(item.value for item in Lifecycle)
    by_origin_lifecycle = _group_lot_pnl(lots, "origin_lifecycle", registry=lifecycle_registry)
    by_current_lifecycle = _group_lot_pnl(lots, "current_lifecycle", registry=lifecycle_registry)
    by_origin_subsystem = _group_lot_pnl(
        lots,
        "origin_subsystem",
        registry=tuple(item.value for item in OriginSubsystem),
    )
    by_mechanism = _group_lot_pnl(
        lots,
        "origin_mechanism",
        registry=tuple(item.value for item in AttributionMechanism),
    )
    realized_lots = [lot for lot in lots if lot["economic_status"] == "REALIZED"]
    by_exit_subsystem = _group_lot_pnl(
        realized_lots,
        "exit_subsystem",
        registry=tuple(item.value for item in OriginSubsystem),
    )
    by_exit_mechanism = _group_lot_pnl(
        realized_lots,
        "exit_mechanism",
        registry=tuple(item.value for item in AttributionMechanism),
    )
    replacement_lots = [lot for lot in lots if lot["replaces_symbol"] is not None]
    replacement_groups = _group_lot_pnl(replacement_lots, "replaces_symbol")
    replacements = {
        "linked_lot_count": len(replacement_lots),
        "realized_pnl": sum(float(lot["realized_pnl"]) for lot in replacement_lots),
        "open_pnl": sum(float(lot["open_pnl"]) for lot in replacement_lots),
        "total_pnl": sum(float(lot["total_pnl"]) for lot in replacement_lots),
        "by_replaced_symbol": {
            symbol: {name: bucket[name] for name in ("realized_pnl", "open_pnl", "total_pnl")}
            for symbol, bucket in replacement_groups.items()
        },
    }
    return {
        "by_symbol": by_symbol,
        "by_industry": by_industry,
        "by_origin_lifecycle": by_origin_lifecycle,
        "by_current_lifecycle": by_current_lifecycle,
        "by_origin_subsystem": by_origin_subsystem,
        "by_mechanism": by_mechanism,
        "by_exit_subsystem": by_exit_subsystem,
        "by_exit_mechanism": by_exit_mechanism,
        "replacements": replacements,
        "symbol_concentration": contribution_concentration(
            {symbol: bucket["total_pnl"] for symbol, bucket in by_symbol.items()}
        ),
        "industry_concentration": contribution_concentration(
            {industry: bucket["total_pnl"] for industry, bucket in by_industry.items()}
        ),
        "realized_lots": realized_lots,
        "open_lots": [lot for lot in lots if lot["economic_status"] == "OPEN"],
    }


def _render_attribution(
    *,
    economic_start: str,
    economic_end: str,
    initial_cash: float,
    lots: list[dict[str, Any]],
    metrics: dict[str, float],
    groups: dict[str, Any],
    ledger_rows: list[dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    symbol_concentration = groups["symbol_concentration"]
    gross_transaction_value = metrics["gross_transaction_value"]
    all_in = metrics["all_in"]
    return {
        "schema": "uquant.economic-attribution.v1",
        "status": "VALID",
        "interval": {"economic_start": economic_start, "economic_end": economic_end},
        "accounting": {
            "realized_pnl": metrics["realized_pnl"],
            "open_pnl": metrics["open_pnl"],
            "total_pnl": metrics["total_pnl"],
            "expected_pnl": metrics["expected_pnl"],
            "reconciliation_error": metrics["reconciliation_error"],
            "tolerance": RECONCILIATION_TOLERANCE,
            "reconciled": True,
        },
        "costs": {
            "commission": metrics["commission"],
            "stamp_duty": metrics["stamp_duty"],
            "transfer_fee": metrics["transfer_fee"],
            "cash_fees": metrics["cash_fees"],
            "slippage": metrics["slippage"],
            "all_in": all_in,
            "pre_all_in_cost_pnl": metrics["total_pnl"] + all_in,
            "all_in_cost_drag_initial_cash": all_in / initial_cash,
            "slippage_accounting": "embedded_in_execution_price_not_double_subtracted",
        },
        "by_symbol": groups["by_symbol"],
        "by_industry": groups["by_industry"],
        "by_origin_lifecycle": groups["by_origin_lifecycle"],
        "by_current_lifecycle": groups["by_current_lifecycle"],
        "by_origin_subsystem": groups["by_origin_subsystem"],
        "by_mechanism": groups["by_mechanism"],
        "by_exit_subsystem": groups["by_exit_subsystem"],
        "by_exit_mechanism": groups["by_exit_mechanism"],
        "replacements": groups["replacements"],
        "turnover": {
            "definition": "sum(fill.gross_value) / initial_cash",
            "gross_transaction_value": gross_transaction_value,
            "gross_turnover": gross_transaction_value / initial_cash,
        },
        "holding_period_sessions": {
            "definition": "zero-based distance between entry and exit/final session, share-weighted",
            "all": _holding_summary(lots),
            "realized": _holding_summary(groups["realized_lots"]),
            "open": _holding_summary(groups["open_lots"]),
        },
        "concentration": symbol_concentration,
        "symbol_concentration": symbol_concentration,
        "industry_concentration": groups["industry_concentration"],
        "daily_ledger": ledger_rows,
        "diagnostics": diagnostics,
        "lots": lots,
    }


def build_economic_attribution(
    *,
    account: AccountState,
    final_prices: Mapping[str, float],
    sessions: Sequence[str],
    economic_start: str,
    economic_end: str,
    final_equity: float,
    daily_ledger: Sequence[Mapping[str, Any]] = (),
    benchmark_close: Mapping[str, float] | None = None,
    paired_counterfactual_equity: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build per-lot realized/open PnL and require exact portfolio reconciliation."""

    canonical_sessions = _economic_sessions(
        sessions,
        economic_start=economic_start,
        economic_end=economic_end,
    )
    session_set = set(canonical_sessions)
    initial_cash = _finite(account.initial_cash, label="initial cash", minimum=0.0)
    if initial_cash <= 0.0:
        raise ValueError("economic attribution initial cash must be positive")
    expected_pnl = _finite(final_equity, label="final equity", minimum=0.0) - initial_cash
    lots = _realized_lot_rows(account, session_set)
    lots.extend(
        _open_lot_rows(
            account,
            final_prices,
            economic_end=economic_end,
            session_set=session_set,
        )
    )
    _allocate_entry_fill_economics(account, lots)
    _assign_holding_sessions(lots, canonical_sessions)

    realized_pnl = sum(float(item["realized_pnl"]) for item in lots)
    open_pnl = sum(float(item["open_pnl"]) for item in lots)
    total_pnl = realized_pnl + open_pnl
    reconciliation_error = total_pnl - expected_pnl
    if abs(reconciliation_error) > RECONCILIATION_TOLERANCE:
        raise ValueError(
            "economic attribution does not reconcile to final equity minus initial cash: "
            f"observed={total_pnl:.12f}, expected={expected_pnl:.12f}"
        )
    commission = sum(fill.commission for fill in account.fills)
    stamp_duty = sum(fill.stamp_duty for fill in account.fills)
    transfer_fee = sum(fill.transfer_fee for fill in account.fills)
    slippage = sum(fill.slippage_cost for fill in account.fills)
    cash_fees = commission + stamp_duty + transfer_fee
    all_in = cash_fees + slippage
    ledger_rows, diagnostics = _daily_diagnostics(
        daily_ledger=daily_ledger,
        canonical_sessions=canonical_sessions,
        total_pnl=total_pnl,
        benchmark_close=benchmark_close,
        paired_counterfactual_equity=paired_counterfactual_equity,
    )
    metrics = {
        "realized_pnl": realized_pnl,
        "open_pnl": open_pnl,
        "total_pnl": total_pnl,
        "expected_pnl": expected_pnl,
        "reconciliation_error": reconciliation_error,
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "cash_fees": cash_fees,
        "slippage": slippage,
        "all_in": all_in,
        "gross_transaction_value": sum(fill.gross_value for fill in account.fills),
    }
    return _render_attribution(
        economic_start=economic_start,
        economic_end=economic_end,
        initial_cash=initial_cash,
        lots=lots,
        metrics=metrics,
        groups=_attribution_groups(lots),
        ledger_rows=ledger_rows,
        diagnostics=diagnostics,
    )
