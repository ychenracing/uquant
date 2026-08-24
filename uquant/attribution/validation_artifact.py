"""Closed economic attribution artifact validation stages."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date as date_type
from typing import Any

from ..types import AttributionMechanism, Lifecycle, OriginSubsystem
from .concentration import RECONCILIATION_TOLERANCE, contribution_concentration
from .concentration import finite_attribution_number as _finite
from .concentration import group_lot_pnl as _group_lot_pnl
from .concentration import holding_summary as _holding_summary
from .replay_evidence import LEDGER_FIELDS as _LEDGER_FIELDS
from .replay_evidence import close_attribution_values as _close
from .replay_evidence import require_exact_attribution_fields as _require_exact_fields
from .validation_lots import validated_economic_lots

_ATTRIBUTION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "interval",
        "accounting",
        "costs",
        "by_symbol",
        "by_industry",
        "by_origin_lifecycle",
        "by_current_lifecycle",
        "by_origin_subsystem",
        "by_mechanism",
        "by_exit_subsystem",
        "by_exit_mechanism",
        "replacements",
        "turnover",
        "holding_period_sessions",
        "concentration",
        "symbol_concentration",
        "industry_concentration",
        "daily_ledger",
        "diagnostics",
        "lots",
    }
)


_ACCOUNTING_FIELDS = frozenset(
    {
        "realized_pnl",
        "open_pnl",
        "total_pnl",
        "expected_pnl",
        "reconciliation_error",
        "tolerance",
        "reconciled",
    }
)


_COST_FIELDS = frozenset(
    {
        "commission",
        "stamp_duty",
        "transfer_fee",
        "cash_fees",
        "slippage",
        "all_in",
        "pre_all_in_cost_pnl",
        "all_in_cost_drag_initial_cash",
        "slippage_accounting",
    }
)


_GROUP_FIELDS = frozenset(
    {
        "realized_pnl",
        "open_pnl",
        "total_pnl",
        "cash_fees",
        "slippage",
        "all_in_costs",
        "gross_transaction_value",
    }
)


ACCOUNTING_FIELDS = _ACCOUNTING_FIELDS
ATTRIBUTION_FIELDS = _ATTRIBUTION_FIELDS
COST_FIELDS = _COST_FIELDS
GROUP_FIELDS = _GROUP_FIELDS


def economic_sessions(
    sessions: Sequence[str],
    *,
    economic_start: str,
    economic_end: str,
) -> tuple[str, ...]:
    try:
        start = date_type.fromisoformat(economic_start)
        end = date_type.fromisoformat(economic_end)
        parsed = tuple(date_type.fromisoformat(item) for item in sessions)
    except (TypeError, ValueError) as exc:
        raise ValueError("economic attribution requires canonical ISO sessions") from exc
    if start > end or not parsed or tuple(sorted(set(parsed))) != parsed:
        raise ValueError("economic attribution sessions must be unique and ordered")
    if parsed[0] < start or parsed[-1] > end:
        raise ValueError("economic attribution sessions exceed the economic interval")
    return tuple(item.isoformat() for item in parsed)


def _canonical_attribution_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        copied = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("economic attribution must be finite canonical JSON") from exc
    if not isinstance(copied, dict):
        raise ValueError("economic attribution must be an object")
    return copied


def _validate_group_map(
    value: Any,
    *,
    label: str,
) -> dict[str, dict[str, float]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an attribution group object")
    normalized: dict[str, dict[str, float]] = {}
    for name, raw_bucket in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{label} keys must be non-empty text")
        bucket = _require_exact_fields(raw_bucket, _GROUP_FIELDS, label=f"{label}/{name}")
        normalized[name] = {
            field: _finite(bucket[field], label=f"{label}/{name}/{field}") for field in _GROUP_FIELDS
        }
        _close(
            normalized[name]["realized_pnl"] + normalized[name]["open_pnl"],
            normalized[name]["total_pnl"],
            label=f"{label}/{name} PnL",
        )
        _close(
            normalized[name]["cash_fees"] + normalized[name]["slippage"],
            normalized[name]["all_in_costs"],
            label=f"{label}/{name} costs",
        )
        for field in ("cash_fees", "slippage", "all_in_costs", "gross_transaction_value"):
            if normalized[name][field] < 0.0:
                raise ValueError(f"{label}/{name}/{field} cannot be negative")
    return dict(sorted(normalized.items()))


def _validate_concentration(
    value: Any,
    *,
    contributions: Mapping[str, float],
    label: str,
) -> dict[str, Any]:
    candidate = _require_exact_fields(
        value,
        {"denominators", "positive", "signed", "absolute", "winner_count", "loser_count", "zero_count"},
        label=label,
    )
    canonical = _canonical_attribution_copy(dict(candidate))
    expected = contribution_concentration(contributions)
    if canonical != expected:
        raise ValueError(f"{label} does not recompute from economic PnL")
    return canonical


def _validated_accounting(
    payload: dict[str, Any], lots: list[dict[str, Any]]
) -> tuple[dict[str, float], float]:
    accounting = _require_exact_fields(
        payload["accounting"], _ACCOUNTING_FIELDS, label="economic attribution accounting"
    )
    numbers = {
        field: _finite(accounting[field], label=f"economic accounting {field}")
        for field in _ACCOUNTING_FIELDS - {"reconciled"}
    }
    if accounting["reconciled"] is not True:
        raise ValueError("economic attribution must advertise reconciled accounting")
    if numbers["tolerance"] != RECONCILIATION_TOLERANCE:
        raise ValueError("economic attribution reconciliation tolerance differs")
    realized = sum(float(lot["realized_pnl"]) for lot in lots)
    open_pnl = sum(float(lot["open_pnl"]) for lot in lots)
    total = realized + open_pnl
    _close(numbers["realized_pnl"], realized, label="realized attribution PnL")
    _close(numbers["open_pnl"], open_pnl, label="open attribution PnL")
    _close(numbers["total_pnl"], total, label="total attribution PnL")
    _close(numbers["expected_pnl"], total, label="expected attribution PnL")
    _close(
        numbers["reconciliation_error"],
        numbers["total_pnl"] - numbers["expected_pnl"],
        label="attribution reconciliation error",
    )
    if abs(numbers["reconciliation_error"]) > numbers["tolerance"]:
        raise ValueError("economic attribution exceeds its reconciliation tolerance")
    return numbers, total


def _validated_attribution_costs(
    payload: dict[str, Any],
    lots: list[dict[str, Any]],
    *,
    total: float,
) -> dict[str, float]:
    cost_payload = _require_exact_fields(payload["costs"], _COST_FIELDS, label="economic attribution costs")
    numbers = {
        field: _finite(cost_payload[field], label=f"economic attribution cost {field}", minimum=0.0)
        for field in _COST_FIELDS - {"slippage_accounting", "pre_all_in_cost_pnl"}
    }
    pre_cost = _finite(cost_payload["pre_all_in_cost_pnl"], label="pre-all-in-cost PnL")
    if cost_payload["slippage_accounting"] != "embedded_in_execution_price_not_double_subtracted":
        raise ValueError("economic attribution slippage accounting label differs")
    lot_costs = {
        "commission": sum(
            float(lot["costs"]["entry_commission"]) + float(lot["costs"]["exit_commission"]) for lot in lots
        ),
        "stamp_duty": sum(
            float(lot["costs"]["entry_stamp_duty"]) + float(lot["costs"]["exit_stamp_duty"]) for lot in lots
        ),
        "transfer_fee": sum(
            float(lot["costs"]["entry_transfer_fee"]) + float(lot["costs"]["exit_transfer_fee"])
            for lot in lots
        ),
        "slippage": sum(float(lot["costs"]["slippage"]) for lot in lots),
    }
    for field, expected in lot_costs.items():
        _close(numbers[field], expected, label=f"attribution {field}")
    _close(
        numbers["cash_fees"],
        numbers["commission"] + numbers["stamp_duty"] + numbers["transfer_fee"],
        label="attribution cash fees",
    )
    _close(numbers["all_in"], numbers["cash_fees"] + numbers["slippage"], label="attribution all-in costs")
    _close(pre_cost, total + numbers["all_in"], label="pre-all-in-cost PnL")
    return numbers


def _validated_attribution_groups(
    payload: dict[str, Any], lots: list[dict[str, Any]]
) -> dict[str, dict[str, dict[str, float]]]:
    group_specs = {
        "by_symbol": ("symbol", ()),
        "by_industry": ("industry_at_entry", ()),
        "by_origin_lifecycle": ("origin_lifecycle", tuple(item.value for item in Lifecycle)),
        "by_current_lifecycle": ("current_lifecycle", tuple(item.value for item in Lifecycle)),
        "by_origin_subsystem": ("origin_subsystem", tuple(item.value for item in OriginSubsystem)),
        "by_mechanism": ("origin_mechanism", tuple(item.value for item in AttributionMechanism)),
    }
    groups: dict[str, dict[str, dict[str, float]]] = {}
    for output_name, (lot_field, registry) in group_specs.items():
        observed = _validate_group_map(payload[output_name], label=output_name)
        if observed != _group_lot_pnl(lots, lot_field, registry=registry):
            raise ValueError(f"economic attribution {output_name} does not recompute from lots")
        groups[output_name] = observed
    realized_lots = [lot for lot in lots if lot["economic_status"] == "REALIZED"]
    for output_name, lot_field, registry in (
        ("by_exit_subsystem", "exit_subsystem", tuple(item.value for item in OriginSubsystem)),
        ("by_exit_mechanism", "exit_mechanism", tuple(item.value for item in AttributionMechanism)),
    ):
        observed = _validate_group_map(payload[output_name], label=output_name)
        if observed != _group_lot_pnl(realized_lots, lot_field, registry=registry):
            raise ValueError(f"economic attribution {output_name} does not recompute from lots")
    symbol_concentration = _validate_concentration(
        payload["symbol_concentration"],
        contributions={name: bucket["total_pnl"] for name, bucket in groups["by_symbol"].items()},
        label="symbol attribution concentration",
    )
    if payload["concentration"] != symbol_concentration:
        raise ValueError("economic attribution concentration aliases differ")
    _validate_concentration(
        payload["industry_concentration"],
        contributions={name: bucket["total_pnl"] for name, bucket in groups["by_industry"].items()},
        label="industry attribution concentration",
    )
    return groups


def _validate_replacements(payload: dict[str, Any], lots: list[dict[str, Any]]) -> None:
    replacements = _require_exact_fields(
        payload["replacements"],
        {"linked_lot_count", "realized_pnl", "open_pnl", "total_pnl", "by_replaced_symbol"},
        label="economic attribution replacements",
    )
    replacement_lots = [lot for lot in lots if lot["replaces_symbol"] is not None]
    count = replacements["linked_lot_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count != len(replacement_lots):
        raise ValueError("economic attribution replacement count differs from lots")
    for field in ("realized_pnl", "open_pnl", "total_pnl"):
        observed_value = _finite(replacements[field], label=f"replacement {field}")
        expected_value = sum(float(lot[field]) for lot in replacement_lots)
        _close(observed_value, expected_value, label=f"replacement {field}")
    raw_replaced = replacements["by_replaced_symbol"]
    if not isinstance(raw_replaced, Mapping):
        raise ValueError("replacement groups are malformed")
    expected_replaced = {
        name: {field: bucket[field] for field in ("realized_pnl", "open_pnl", "total_pnl")}
        for name, bucket in _group_lot_pnl(replacement_lots, "replaces_symbol").items()
    }
    if raw_replaced != expected_replaced:
        raise ValueError("economic attribution replacements do not recompute from lots")


def _validate_turnover(
    payload: dict[str, Any],
    lots: list[dict[str, Any]],
    *,
    cost_numbers: dict[str, float],
) -> None:
    turnover = _require_exact_fields(
        payload["turnover"],
        {"definition", "gross_transaction_value", "gross_turnover"},
        label="economic attribution turnover",
    )
    if turnover["definition"] != "sum(fill.gross_value) / initial_cash":
        raise ValueError("economic attribution turnover definition differs")
    transaction_value = _finite(
        turnover["gross_transaction_value"], label="gross transaction value", minimum=0.0
    )
    _close(
        transaction_value,
        sum(float(lot["gross_transaction_value"]) for lot in lots),
        label="gross transaction value",
    )
    gross_turnover = _finite(turnover["gross_turnover"], label="gross turnover", minimum=0.0)
    initial_cash_candidates: list[float] = []
    if gross_turnover > 0.0:
        initial_cash_candidates.append(transaction_value / gross_turnover)
    drag = cost_numbers["all_in_cost_drag_initial_cash"]
    if drag > 0.0:
        initial_cash_candidates.append(cost_numbers["all_in"] / drag)
    if initial_cash_candidates:
        initial_cash = initial_cash_candidates[0]
        if initial_cash <= 0.0:
            raise ValueError("economic attribution implied initial cash is invalid")
        for candidate in initial_cash_candidates[1:]:
            _close(candidate, initial_cash, label="economic attribution initial cash")
    elif transaction_value != 0.0 or cost_numbers["all_in"] != 0.0:
        raise ValueError("economic attribution cost/turnover denominators are inconsistent")


def _validate_holding_periods(payload: dict[str, Any], lots: list[dict[str, Any]]) -> None:
    holding = _require_exact_fields(
        payload["holding_period_sessions"],
        {"definition", "all", "realized", "open"},
        label="economic attribution holding periods",
    )
    if holding["definition"] != "zero-based distance between entry and exit/final session, share-weighted":
        raise ValueError("economic attribution holding-period definition differs")
    for name, selected in (
        ("all", lots),
        ("realized", [lot for lot in lots if lot["economic_status"] == "REALIZED"]),
        ("open", [lot for lot in lots if lot["economic_status"] == "OPEN"]),
    ):
        if holding[name] != _holding_summary(selected):
            raise ValueError(f"economic attribution {name} holding periods differ from lots")


def _validated_daily_ledger_row(
    raw_row: Any,
    *,
    index: int,
    economic_start: str,
    economic_end: str,
) -> tuple[str, float, float]:
    row = _require_exact_fields(raw_row, _LEDGER_FIELDS, label=f"daily ledger row {index}")
    date = row["date"]
    if not isinstance(date, str):
        raise ValueError("daily attribution ledger date is invalid")
    try:
        parsed_date = date_type.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("daily attribution ledger date is invalid") from exc
    if parsed_date < date_type.fromisoformat(economic_start) or parsed_date > date_type.fromisoformat(
        economic_end
    ):
        raise ValueError("daily attribution ledger date lies outside the economic interval")
    cash = _finite(row["cash"], label="daily ledger cash", minimum=0.0)
    equity = _finite(row["equity"], label="daily ledger equity", minimum=0.0)
    if equity <= 0.0:
        raise ValueError("daily attribution ledger equity must be positive")
    gross_exposure = _finite(row["gross_exposure"], label="daily ledger gross exposure", minimum=0.0)
    net_exposure = _finite(row["net_exposure"], label="daily ledger net exposure")
    cash_weight = _finite(row["cash_weight"], label="daily ledger cash weight")
    daily_pnl = _finite(row["daily_pnl"], label="daily ledger PnL")
    target_gross = _finite(row["target_gross"], label="daily ledger target gross", minimum=0.0)
    normalized_weights: dict[str, dict[str, float]] = {}
    for field in ("position_weights", "target_weights"):
        raw_weights = row[field]
        if not isinstance(raw_weights, Mapping):
            raise ValueError(f"daily ledger {field} is malformed")
        normalized_weights[field] = {}
        for symbol, weight in raw_weights.items():
            if not isinstance(symbol, str) or not symbol:
                raise ValueError(f"daily ledger {field} symbol is invalid")
            normalized_weight = _finite(weight, label=f"daily ledger {field}/{symbol}")
            if field == "target_weights" and normalized_weight < 0.0:
                raise ValueError("daily attribution target weight cannot be negative")
            normalized_weights[field][symbol] = normalized_weight
    _validate_ledger_weights_and_owner(
        row=row,
        cash=cash,
        equity=equity,
        cash_weight=cash_weight,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
        target_gross=target_gross,
        position_weights=normalized_weights["position_weights"],
        target_weights=normalized_weights["target_weights"],
    )
    return date, equity, daily_pnl


def _validate_ledger_weights_and_owner(
    *,
    row: Mapping[str, Any],
    cash: float,
    equity: float,
    cash_weight: float,
    gross_exposure: float,
    net_exposure: float,
    target_gross: float,
    position_weights: dict[str, float],
    target_weights: dict[str, float],
) -> None:
    _close(cash_weight, cash / equity, label="daily attribution cash weight")
    _close(
        gross_exposure,
        sum(abs(weight) for weight in position_weights.values()),
        label="daily attribution gross exposure",
    )
    _close(net_exposure, sum(position_weights.values()), label="daily attribution net exposure")
    _close(cash_weight + sum(position_weights.values()), 1.0, label="daily attribution portfolio weights")
    _close(target_gross, sum(target_weights.values()), label="daily attribution target gross")
    caps = _require_exact_fields(row["caps"], {"risk_gross", "system_gross"}, label="daily ledger caps")
    risk_cap = _finite(caps["risk_gross"], label="daily risk cap", minimum=0.0)
    system_cap = _finite(caps["system_gross"], label="daily system cap", minimum=0.0)
    effective_cap = min(risk_cap, system_cap)
    if target_gross > effective_cap + 1e-12:
        expected_owner = "STRATEGY_RETENTION_OVERRIDE"
    elif math.isclose(target_gross, effective_cap, rel_tol=0.0, abs_tol=1e-12):
        if math.isclose(risk_cap, system_cap, rel_tol=0.0, abs_tol=1e-12):
            expected_owner = "RISK_AND_SYSTEM"
        elif risk_cap < system_cap:
            expected_owner = "RISK"
        else:
            expected_owner = "SYSTEM"
    else:
        expected_owner = "STRATEGY"
    if row["binding_owner"] != expected_owner:
        raise ValueError("daily attribution binding owner does not recompute from caps")
    if not isinstance(row["risk_state"], str) or not isinstance(row["opportunity"], str):
        raise ValueError("daily attribution decision state is malformed")


def _validate_daily_ledger(
    payload: dict[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    require_daily_ledger: bool,
    total: float,
) -> None:
    ledger = payload["daily_ledger"]
    if not isinstance(ledger, list):
        raise ValueError("economic attribution daily ledger must be a list")
    if require_daily_ledger and not ledger:
        raise ValueError("economic attribution daily ledger is required")
    rows = [
        _validated_daily_ledger_row(
            raw_row,
            index=index,
            economic_start=economic_start,
            economic_end=economic_end,
        )
        for index, raw_row in enumerate(ledger)
    ]
    dates = [row[0] for row in rows]
    equities = [row[1] for row in rows]
    pnls = [row[2] for row in rows]
    if dates and tuple(sorted(set(dates))) != tuple(dates):
        raise ValueError("daily attribution ledger must be unique and ordered")
    if dates and (dates[0] != economic_start or dates[-1] != economic_end):
        raise ValueError("daily attribution ledger does not span the exact economic interval")
    if ledger:
        _close(sum(pnls), total, label="daily attribution ledger PnL")
        previous_equity = equities[-1] - total
        for equity, daily_pnl in zip(equities, pnls, strict=True):
            _close(equity - previous_equity, daily_pnl, label="daily attribution PnL path")
            previous_equity = equity


def _validate_diagnostic(name: str, diagnostic: Any) -> None:
    if not isinstance(diagnostic, Mapping) or diagnostic.get("is_accounting_pnl") is not False:
        raise ValueError(f"economic attribution {name} must be non-accounting")
    status = diagnostic.get("status")
    if name == "cash_drag":
        if status == "DIAGNOSTIC":
            expected_fields = {"status", "value", "definition", "is_accounting_pnl"}
            if diagnostic.get("definition") != (
                "negative prior-close cash times next-session benchmark return"
            ):
                raise ValueError("economic attribution cash-drag definition differs")
        elif status == "NOT_EVALUATED_REQUIRES_DAILY_LEDGER":
            expected_fields = {"status", "value", "is_accounting_pnl"}
        else:
            raise ValueError("economic attribution cash-drag status is invalid")
    elif status == "PAIRED_COUNTERFACTUAL":
        expected_fields = {"status", "value", "definition", "is_accounting_pnl"}
        if diagnostic.get("definition") != ("actual final equity minus paired counterfactual final equity"):
            raise ValueError("economic attribution risk-avoidance definition differs")
    elif status == "NOT_EVALUATED_REQUIRES_PAIRED_COUNTERFACTUAL":
        expected_fields = {"status", "value", "is_accounting_pnl"}
    else:
        raise ValueError("economic attribution risk-avoidance status is invalid")
    _require_exact_fields(diagnostic, expected_fields, label=f"economic attribution {name}")
    value_field = diagnostic.get("value")
    if value_field is not None:
        _finite(value_field, label=f"economic attribution {name}")
    if status in {"DIAGNOSTIC", "PAIRED_COUNTERFACTUAL"} and value_field is None:
        raise ValueError(f"economic attribution {name} value is missing")
    if status.startswith("NOT_EVALUATED") and value_field is not None:
        raise ValueError(f"economic attribution {name} unevaluated value must be null")


def _validate_diagnostics(payload: dict[str, Any]) -> None:
    diagnostics = _require_exact_fields(
        payload["diagnostics"],
        {"cash_drag", "risk_avoidance"},
        label="economic attribution diagnostics",
    )
    for name in ("cash_drag", "risk_avoidance"):
        _validate_diagnostic(name, diagnostics[name])


def validate_economic_attribution_artifact(
    value: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    require_daily_ledger: bool = True,
) -> dict[str, Any]:
    """Validate and canonicalize the closed production attribution schema."""

    payload = _canonical_attribution_copy(value)
    _require_exact_fields(payload, _ATTRIBUTION_FIELDS, label="economic attribution")
    if payload["schema"] != "uquant.economic-attribution.v1" or payload["status"] != "VALID":
        raise ValueError("economic attribution schema/status is invalid")
    interval = _require_exact_fields(
        payload["interval"],
        {"economic_start", "economic_end"},
        label="economic attribution interval",
    )
    economic_sessions(
        (economic_start, economic_end) if economic_start != economic_end else (economic_start,),
        economic_start=economic_start,
        economic_end=economic_end,
    )
    if interval != {"economic_start": economic_start, "economic_end": economic_end}:
        raise ValueError("economic attribution interval differs from the exact cell interval")
    lots = validated_economic_lots(
        payload["lots"],
        economic_start=economic_start,
        economic_end=economic_end,
    )
    _, total = _validated_accounting(payload, lots)
    cost_numbers = _validated_attribution_costs(payload, lots, total=total)
    _validated_attribution_groups(payload, lots)
    _validate_replacements(payload, lots)
    _validate_turnover(payload, lots, cost_numbers=cost_numbers)
    _validate_holding_periods(payload, lots)
    _validate_daily_ledger(
        payload,
        economic_start=economic_start,
        economic_end=economic_end,
        require_daily_ledger=require_daily_ledger,
        total=total,
    )
    _validate_diagnostics(payload)
    return payload
