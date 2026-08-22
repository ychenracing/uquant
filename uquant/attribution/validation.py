"""Economic attribution schema and engine-result reconciliation."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import date as date_type
from typing import Any

from ..types import (
    AttributionMechanism,
    Lifecycle,
    OriginSubsystem,
    Side,
    validate_attribution_compatibility,
)
from .concentration import (
    RECONCILIATION_TOLERANCE,
    _finite,
    _group_lot_pnl,
    _holding_summary,
    contribution_concentration,
)
from .replay_evidence import (
    _LEDGER_FIELDS,
    _close,
    _positive_integer,
    _require_exact_fields,
    _validate_daily_replay_evidence,
)

_ATTRIBUTION_FIELDS = {
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


_ACCOUNTING_FIELDS = {
    "realized_pnl",
    "open_pnl",
    "total_pnl",
    "expected_pnl",
    "reconciliation_error",
    "tolerance",
    "reconciled",
}


_COST_FIELDS = {
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


_GROUP_FIELDS = {
    "realized_pnl",
    "open_pnl",
    "total_pnl",
    "cash_fees",
    "slippage",
    "all_in_costs",
    "gross_transaction_value",
}


_LOT_FIELDS = {
    "economic_status",
    "symbol",
    "tranche_id",
    "shares",
    "entry_date",
    "exit_date",
    "final_date",
    "realized_pnl",
    "open_pnl",
    "total_pnl",
    "origin_event_id",
    "origin_subsystem",
    "origin_mechanism",
    "origin_lifecycle",
    "current_lifecycle",
    "exit_subsystem",
    "exit_mechanism",
    "replaces_symbol",
    "industry_at_entry",
    "entry_gross_value",
    "exit_gross_value",
    "costs",
    "gross_transaction_value",
    "holding_sessions",
}


_LOT_COST_FIELDS = {
    "entry_commission",
    "entry_stamp_duty",
    "entry_transfer_fee",
    "entry_slippage",
    "exit_commission",
    "exit_stamp_duty",
    "exit_transfer_fee",
    "exit_slippage",
    "cash_fees",
    "slippage",
    "all_in",
}


def _economic_sessions(
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


def validate_economic_attribution(
    value: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    require_daily_ledger: bool = True,
) -> dict[str, Any]:
    """Validate and canonicalize the closed production attribution schema.

    This validation is intentionally independent of strategy output text.  All
    economic ownership is checked against structured lot identity, and the
    inclusive interval is supplied by the caller rather than trusted from the
    artifact being validated.
    """

    payload = _canonical_attribution_copy(value)
    _require_exact_fields(payload, _ATTRIBUTION_FIELDS, label="economic attribution")
    if payload["schema"] != "uquant.economic-attribution.v1" or payload["status"] != "VALID":
        raise ValueError("economic attribution schema/status is invalid")
    interval = _require_exact_fields(
        payload["interval"],
        {"economic_start", "economic_end"},
        label="economic attribution interval",
    )
    _economic_sessions(
        (economic_start, economic_end) if economic_start != economic_end else (economic_start,),
        economic_start=economic_start,
        economic_end=economic_end,
    )
    if interval != {"economic_start": economic_start, "economic_end": economic_end}:
        raise ValueError("economic attribution interval differs from the exact cell interval")

    lots_value = payload["lots"]
    if not isinstance(lots_value, list):
        raise ValueError("economic attribution lots must be a list")
    lots: list[dict[str, Any]] = []
    seen_lots: set[tuple[str, str, str, str]] = set()
    for index, raw_lot in enumerate(lots_value):
        lot = dict(_require_exact_fields(raw_lot, _LOT_FIELDS, label=f"economic lot {index}"))
        status = lot["economic_status"]
        if status not in {"REALIZED", "OPEN"}:
            raise ValueError("economic lot status is invalid")
        for field in (
            "symbol",
            "tranche_id",
            "entry_date",
            "origin_event_id",
            "origin_subsystem",
            "origin_mechanism",
            "origin_lifecycle",
            "current_lifecycle",
            "industry_at_entry",
        ):
            if not isinstance(lot[field], str) or not lot[field]:
                raise ValueError(f"economic lot {field} is missing")
        try:
            entry = date_type.fromisoformat(lot["entry_date"])
            start = date_type.fromisoformat(economic_start)
            end = date_type.fromisoformat(economic_end)
        except ValueError as exc:
            raise ValueError("economic lot dates must be canonical ISO dates") from exc
        terminal_name = "exit_date" if status == "REALIZED" else "final_date"
        other_terminal = "final_date" if status == "REALIZED" else "exit_date"
        terminal = lot[terminal_name]
        if not isinstance(terminal, str) or lot[other_terminal] is not None:
            raise ValueError("economic lot terminal-date status is inconsistent")
        try:
            terminal_date = date_type.fromisoformat(terminal)
        except ValueError as exc:
            raise ValueError("economic lot terminal date must be canonical ISO") from exc
        if entry < start or entry > terminal_date or terminal_date > end:
            raise ValueError("economic lot date lies outside the exact economic interval")
        shares = lot["shares"]
        holding = lot["holding_sessions"]
        if (
            isinstance(shares, bool)
            or not isinstance(shares, int)
            or shares <= 0
            or isinstance(holding, bool)
            or not isinstance(holding, int)
            or holding < 0
        ):
            raise ValueError("economic lot shares/holding sessions are invalid")
        for field in (
            "realized_pnl",
            "open_pnl",
            "total_pnl",
            "entry_gross_value",
            "exit_gross_value",
            "gross_transaction_value",
        ):
            lot[field] = _finite(lot[field], label=f"economic lot {field}")
        if lot["entry_gross_value"] < 0.0 or lot["exit_gross_value"] < 0.0:
            raise ValueError("economic lot transaction value cannot be negative")
        if status == "REALIZED" and lot["open_pnl"] != 0.0:
            raise ValueError("realized economic lot contains open PnL")
        if status == "OPEN" and lot["realized_pnl"] != 0.0:
            raise ValueError("open economic lot contains realized PnL")
        _close(
            lot["realized_pnl"] + lot["open_pnl"],
            lot["total_pnl"],
            label="economic lot PnL",
        )
        _close(
            lot["entry_gross_value"] + lot["exit_gross_value"],
            lot["gross_transaction_value"],
            label="economic lot transaction value",
        )
        try:
            Lifecycle(lot["origin_lifecycle"])
            Lifecycle(lot["current_lifecycle"])
            validate_attribution_compatibility(
                origin_subsystem=lot["origin_subsystem"],
                mechanism=lot["origin_mechanism"],
            )
        except ValueError as exc:
            raise ValueError("economic lot structured origin identity is invalid") from exc
        exit_subsystem = lot["exit_subsystem"]
        exit_mechanism = lot["exit_mechanism"]
        if status == "REALIZED":
            if not isinstance(exit_subsystem, str) or not isinstance(exit_mechanism, str):
                raise ValueError("realized economic lot exit identity is missing")
            try:
                validate_attribution_compatibility(
                    origin_subsystem=exit_subsystem,
                    mechanism=exit_mechanism,
                    side=Side.SELL.value,
                )
            except ValueError as exc:
                raise ValueError("realized economic lot exit identity is invalid") from exc
        elif exit_subsystem is not None or exit_mechanism is not None:
            raise ValueError("open economic lot cannot contain exit identity")
        replaces_symbol = lot["replaces_symbol"]
        if replaces_symbol is not None and (not isinstance(replaces_symbol, str) or not replaces_symbol):
            raise ValueError("economic lot replacement identity is invalid")
        costs = dict(
            _require_exact_fields(
                lot["costs"],
                _LOT_COST_FIELDS,
                label=f"economic lot {index} costs",
            )
        )
        for field in _LOT_COST_FIELDS:
            costs[field] = _finite(costs[field], label=f"economic lot cost {field}", minimum=0.0)
        _close(
            costs["entry_commission"]
            + costs["entry_stamp_duty"]
            + costs["entry_transfer_fee"]
            + costs["exit_commission"]
            + costs["exit_stamp_duty"]
            + costs["exit_transfer_fee"],
            costs["cash_fees"],
            label="economic lot cash fees",
        )
        _close(
            costs["entry_slippage"] + costs["exit_slippage"],
            costs["slippage"],
            label="economic lot slippage",
        )
        _close(
            costs["cash_fees"] + costs["slippage"],
            costs["all_in"],
            label="economic lot all-in costs",
        )
        lot["costs"] = costs
        identity = (lot["symbol"], lot["tranche_id"], status, terminal)
        if identity in seen_lots:
            raise ValueError("economic attribution contains a duplicate lot record")
        seen_lots.add(identity)
        lots.append(lot)

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

    cost_payload = _require_exact_fields(payload["costs"], _COST_FIELDS, label="economic attribution costs")
    cost_numbers = {
        field: _finite(cost_payload[field], label=f"economic attribution cost {field}", minimum=0.0)
        for field in _COST_FIELDS - {"slippage_accounting", "pre_all_in_cost_pnl"}
    }
    pre_cost = _finite(cost_payload["pre_all_in_cost_pnl"], label="pre-all-in-cost PnL")
    if cost_payload["slippage_accounting"] != "embedded_in_execution_price_not_double_subtracted":
        raise ValueError("economic attribution slippage accounting label differs")
    lot_commission = sum(
        float(lot["costs"]["entry_commission"]) + float(lot["costs"]["exit_commission"]) for lot in lots
    )
    lot_stamp = sum(
        float(lot["costs"]["entry_stamp_duty"]) + float(lot["costs"]["exit_stamp_duty"]) for lot in lots
    )
    lot_transfer = sum(
        float(lot["costs"]["entry_transfer_fee"]) + float(lot["costs"]["exit_transfer_fee"]) for lot in lots
    )
    lot_slippage = sum(float(lot["costs"]["slippage"]) for lot in lots)
    for field, expected in (
        ("commission", lot_commission),
        ("stamp_duty", lot_stamp),
        ("transfer_fee", lot_transfer),
        ("slippage", lot_slippage),
    ):
        _close(cost_numbers[field], expected, label=f"attribution {field}")
    _close(
        cost_numbers["cash_fees"],
        cost_numbers["commission"] + cost_numbers["stamp_duty"] + cost_numbers["transfer_fee"],
        label="attribution cash fees",
    )
    _close(
        cost_numbers["all_in"],
        cost_numbers["cash_fees"] + cost_numbers["slippage"],
        label="attribution all-in costs",
    )
    _close(pre_cost, total + cost_numbers["all_in"], label="pre-all-in-cost PnL")

    group_specs = {
        "by_symbol": ("symbol", ()),
        "by_industry": ("industry_at_entry", ()),
        "by_origin_lifecycle": ("origin_lifecycle", tuple(item.value for item in Lifecycle)),
        "by_current_lifecycle": ("current_lifecycle", tuple(item.value for item in Lifecycle)),
        "by_origin_subsystem": (
            "origin_subsystem",
            tuple(item.value for item in OriginSubsystem),
        ),
        "by_mechanism": (
            "origin_mechanism",
            tuple(item.value for item in AttributionMechanism),
        ),
    }
    groups: dict[str, dict[str, dict[str, float]]] = {}
    for output_name, (lot_field, registry) in group_specs.items():
        observed = _validate_group_map(payload[output_name], label=output_name)
        expected_group = _group_lot_pnl(lots, lot_field, registry=registry)
        if observed != expected_group:
            raise ValueError(f"economic attribution {output_name} does not recompute from lots")
        groups[output_name] = observed
    realized_lots = [lot for lot in lots if lot["economic_status"] == "REALIZED"]
    for output_name, lot_field, registry in (
        (
            "by_exit_subsystem",
            "exit_subsystem",
            tuple(item.value for item in OriginSubsystem),
        ),
        (
            "by_exit_mechanism",
            "exit_mechanism",
            tuple(item.value for item in AttributionMechanism),
        ),
    ):
        observed_exit_group = _validate_group_map(payload[output_name], label=output_name)
        expected_exit_group = _group_lot_pnl(
            realized_lots,
            lot_field,
            registry=registry,
        )
        if observed_exit_group != expected_exit_group:
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

    holding = _require_exact_fields(
        payload["holding_period_sessions"],
        {"definition", "all", "realized", "open"},
        label="economic attribution holding periods",
    )
    if holding["definition"] != ("zero-based distance between entry and exit/final session, share-weighted"):
        raise ValueError("economic attribution holding-period definition differs")
    for name, selected in (
        ("all", lots),
        ("realized", [lot for lot in lots if lot["economic_status"] == "REALIZED"]),
        ("open", [lot for lot in lots if lot["economic_status"] == "OPEN"]),
    ):
        if holding[name] != _holding_summary(selected):
            raise ValueError(f"economic attribution {name} holding periods differ from lots")

    ledger = payload["daily_ledger"]
    if not isinstance(ledger, list):
        raise ValueError("economic attribution daily ledger must be a list")
    if require_daily_ledger and not ledger:
        raise ValueError("economic attribution daily ledger is required")
    dates: list[str] = []
    ledger_equities: list[float] = []
    ledger_pnls: list[float] = []
    for index, raw_row in enumerate(ledger):
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
        dates.append(date)
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
        position_weights = normalized_weights["position_weights"]
        target_weights = normalized_weights["target_weights"]
        _close(cash_weight, cash / equity, label="daily attribution cash weight")
        _close(
            gross_exposure,
            sum(abs(weight) for weight in position_weights.values()),
            label="daily attribution gross exposure",
        )
        _close(
            net_exposure,
            sum(position_weights.values()),
            label="daily attribution net exposure",
        )
        _close(
            cash_weight + sum(position_weights.values()),
            1.0,
            label="daily attribution portfolio weights",
        )
        _close(
            target_gross,
            sum(target_weights.values()),
            label="daily attribution target gross",
        )
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
        ledger_equities.append(equity)
        ledger_pnls.append(daily_pnl)
    if dates and (tuple(sorted(set(dates))) != tuple(dates)):
        raise ValueError("daily attribution ledger must be unique and ordered")
    if dates and (dates[0] != economic_start or dates[-1] != economic_end):
        raise ValueError("daily attribution ledger does not span the exact economic interval")
    if ledger:
        _close(
            sum(ledger_pnls),
            total,
            label="daily attribution ledger PnL",
        )
        implied_initial_equity = ledger_equities[-1] - total
        previous_equity = implied_initial_equity
        for equity, daily_pnl in zip(ledger_equities, ledger_pnls, strict=True):
            _close(equity - previous_equity, daily_pnl, label="daily attribution PnL path")
            previous_equity = equity

    diagnostics = _require_exact_fields(
        payload["diagnostics"],
        {"cash_drag", "risk_avoidance"},
        label="economic attribution diagnostics",
    )
    for name in ("cash_drag", "risk_avoidance"):
        diagnostic = diagnostics[name]
        if not isinstance(diagnostic, Mapping) or diagnostic.get("is_accounting_pnl") is not False:
            raise ValueError(f"economic attribution {name} must be non-accounting")
        status = diagnostic.get("status")
        if name == "cash_drag":
            if status == "DIAGNOSTIC":
                expected_diagnostic_fields = {
                    "status",
                    "value",
                    "definition",
                    "is_accounting_pnl",
                }
                if diagnostic.get("definition") != (
                    "negative prior-close cash times next-session benchmark return"
                ):
                    raise ValueError("economic attribution cash-drag definition differs")
            elif status == "NOT_EVALUATED_REQUIRES_DAILY_LEDGER":
                expected_diagnostic_fields = {"status", "value", "is_accounting_pnl"}
            else:
                raise ValueError("economic attribution cash-drag status is invalid")
        elif status == "PAIRED_COUNTERFACTUAL":
            expected_diagnostic_fields = {
                "status",
                "value",
                "definition",
                "is_accounting_pnl",
            }
            if diagnostic.get("definition") != (
                "actual final equity minus paired counterfactual final equity"
            ):
                raise ValueError("economic attribution risk-avoidance definition differs")
        elif status == "NOT_EVALUATED_REQUIRES_PAIRED_COUNTERFACTUAL":
            expected_diagnostic_fields = {"status", "value", "is_accounting_pnl"}
        else:
            raise ValueError("economic attribution risk-avoidance status is invalid")
        _require_exact_fields(
            diagnostic,
            expected_diagnostic_fields,
            label=f"economic attribution {name}",
        )
        value_field = diagnostic.get("value")
        if value_field is not None:
            _finite(value_field, label=f"economic attribution {name}")
        if status in {"DIAGNOSTIC", "PAIRED_COUNTERFACTUAL"} and value_field is None:
            raise ValueError(f"economic attribution {name} value is missing")
        if status.startswith("NOT_EVALUATED") and value_field is not None:
            raise ValueError(f"economic attribution {name} unevaluated value must be null")
    return payload


def validate_attribution_against_engine_result(
    result: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    attribution: Mapping[str, Any] | None = None,
    trusted_sessions: Sequence[str] | None = None,
    trusted_close: Callable[[str, str], float] | None = None,
    require_daily_replay_evidence: bool = False,
) -> dict[str, Any]:
    """Reconcile attribution to raw fills, sold tranches, positions, and equity."""

    attribution_value = result.get("attribution") if attribution is None else attribution
    if not isinstance(attribution_value, Mapping):
        raise ValueError("engine result is missing economic attribution")
    canonical = validate_economic_attribution(
        attribution_value,
        economic_start=economic_start,
        economic_end=economic_end,
    )
    account = result.get("final_account")
    if not isinstance(account, Mapping):
        raise ValueError("engine result is missing structured final account")
    fills = account.get("fills")
    positions = account.get("positions")
    if not isinstance(fills, list) or not isinstance(positions, Mapping):
        raise ValueError("engine result final account has malformed fills or positions")
    initial_cash = _finite(account.get("initial_cash"), label="engine initial cash", minimum=0.0)
    final_equity = _finite(result.get("final_equity"), label="engine final equity", minimum=0.0)
    if initial_cash <= 0.0:
        raise ValueError("engine initial cash must be positive")
    _close(
        float(canonical["accounting"]["expected_pnl"]),
        final_equity - initial_cash,
        label="attribution versus engine final equity",
    )
    _close(
        _finite(result.get("final_wealth"), label="engine final wealth"),
        final_equity / initial_cash,
        label="engine final wealth",
    )
    if result.get("start") != economic_start or result.get("end") != economic_end:
        raise ValueError("engine result interval differs from the exact economic interval")

    fill_costs = {
        "commission": 0.0,
        "stamp_duty": 0.0,
        "transfer_fee": 0.0,
        "slippage": 0.0,
        "gross_transaction_value": 0.0,
    }
    buy_shares: dict[tuple[str, str, str], int] = {}
    sold_lots: list[tuple[str, str, str, str, int]] = []
    normalized_fills: list[Mapping[str, Any]] = []
    for index, raw_fill in enumerate(fills):
        if not isinstance(raw_fill, Mapping):
            raise ValueError("engine result contains a malformed fill")
        normalized_fills.append(raw_fill)
        side = raw_fill.get("side")
        symbol = raw_fill.get("symbol")
        fill_date = raw_fill.get("fill_date")
        shares = raw_fill.get("shares")
        if (
            side not in {Side.BUY.value, Side.SELL.value}
            or not isinstance(symbol, str)
            or not symbol
            or not isinstance(fill_date, str)
            or isinstance(shares, bool)
            or not isinstance(shares, int)
            or shares <= 0
        ):
            raise ValueError(f"engine fill {index} identity is malformed")
        try:
            fill_day = date_type.fromisoformat(fill_date)
        except ValueError as exc:
            raise ValueError("engine fill date is malformed") from exc
        if not date_type.fromisoformat(economic_start) <= fill_day <= date_type.fromisoformat(economic_end):
            raise ValueError("engine fill lies outside the exact economic interval")
        for output_name, source_name in (
            ("commission", "commission"),
            ("stamp_duty", "stamp_duty"),
            ("transfer_fee", "transfer_fee"),
            ("slippage", "slippage_cost"),
            ("gross_transaction_value", "gross_value"),
        ):
            fill_costs[output_name] += _finite(
                raw_fill.get(source_name),
                label=f"engine fill {source_name}",
                minimum=0.0,
            )
        if side == Side.BUY.value:
            event_id = raw_fill.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("engine BUY fill is missing structured origin event")
            key = (symbol, event_id, fill_date)
            if key in buy_shares:
                raise ValueError("engine BUY fill origin identity is ambiguous")
            buy_shares[key] = shares
            continue
        allocations = raw_fill.get("sold_tranches")
        if not isinstance(allocations, list):
            raise ValueError("engine SELL fill does not reconcile through sold tranches")
        allocated_shares: list[int] = []
        for allocation in allocations:
            if not isinstance(allocation, Mapping):
                raise ValueError("engine sold tranche is malformed")
            allocated_shares.append(
                _positive_integer(
                    allocation.get("shares"),
                    label="engine sold tranche shares",
                )
            )
        if sum(allocated_shares) != shares:
            raise ValueError("engine SELL fill does not reconcile through sold tranches")
        for allocation, allocation_shares in zip(
            allocations,
            allocated_shares,
            strict=True,
        ):
            sold_lots.append(
                (
                    symbol,
                    str(allocation.get("tranche_id", "")),
                    str(allocation.get("event_id", "")),
                    fill_date,
                    allocation_shares,
                )
            )
    costs = canonical["costs"]
    for field in ("commission", "stamp_duty", "transfer_fee", "slippage"):
        _close(float(costs[field]), fill_costs[field], label=f"attribution versus fills {field}")
    turnover = canonical["turnover"]
    _close(
        float(turnover["gross_transaction_value"]),
        fill_costs["gross_transaction_value"],
        label="attribution versus fills transaction value",
    )
    _close(
        float(turnover["gross_turnover"]),
        _finite(result.get("gross_turnover"), label="engine gross turnover", minimum=0.0),
        label="attribution versus engine gross turnover",
    )

    lots = canonical["lots"]
    attributed_buy_shares: dict[tuple[str, str, str], int] = {}
    attributed_sold_lots: list[tuple[str, str, str, str, int]] = []
    attributed_open_lots: list[tuple[str, str, str, int]] = []
    for lot in lots:
        key = (str(lot["symbol"]), str(lot["origin_event_id"]), str(lot["entry_date"]))
        attributed_buy_shares[key] = attributed_buy_shares.get(key, 0) + int(lot["shares"])
        if lot["economic_status"] == "REALIZED":
            attributed_sold_lots.append(
                (
                    str(lot["symbol"]),
                    str(lot["tranche_id"]),
                    str(lot["origin_event_id"]),
                    str(lot["exit_date"]),
                    int(lot["shares"]),
                )
            )
        else:
            attributed_open_lots.append(
                (
                    str(lot["symbol"]),
                    str(lot["tranche_id"]),
                    str(lot["origin_event_id"]),
                    int(lot["shares"]),
                )
            )
    if attributed_buy_shares != buy_shares:
        raise ValueError("attribution lots do not exactly cover raw BUY fills")
    if sorted(attributed_sold_lots) != sorted(sold_lots):
        raise ValueError("attribution realized lots differ from raw sold tranches")

    account_open_lots: list[tuple[str, str, str, int]] = []
    for symbol, raw_position in positions.items():
        if not isinstance(symbol, str) or not isinstance(raw_position, Mapping):
            raise ValueError("engine open position is malformed")
        tranches = raw_position.get("tranches")
        if not isinstance(tranches, list):
            raise ValueError("engine open position tranches are malformed")
        for tranche in tranches:
            if not isinstance(tranche, Mapping):
                raise ValueError("engine open tranche is malformed")
            account_open_lots.append(
                (
                    symbol,
                    str(tranche.get("tranche_id", "")),
                    str(tranche.get("event_id", "")),
                    int(tranche.get("shares", 0)),
                )
            )
    if sorted(attributed_open_lots) != sorted(account_open_lots):
        raise ValueError("attribution open lots differ from raw account tranches")

    symbol_pnl = result.get("symbol_pnl")
    if not isinstance(symbol_pnl, Mapping):
        raise ValueError("engine result is missing symbol PnL")
    normalized_symbol_pnl = {
        str(symbol): _finite(value, label=f"engine symbol PnL {symbol}")
        for symbol, value in symbol_pnl.items()
    }
    attributed_symbol_pnl = {
        symbol: float(bucket["total_pnl"]) for symbol, bucket in canonical["by_symbol"].items()
    }
    if set(normalized_symbol_pnl) != set(attributed_symbol_pnl):
        raise ValueError("attribution symbol coverage differs from engine result")
    for symbol, pnl in attributed_symbol_pnl.items():
        _close(pnl, normalized_symbol_pnl[symbol], label=f"attribution symbol PnL {symbol}")
    if canonical["daily_ledger"]:
        _close(
            float(canonical["daily_ledger"][-1]["equity"]),
            final_equity,
            label="attribution ledger versus engine final equity",
        )
    if require_daily_replay_evidence or result.get("daily_replay_evidence") is not None:
        _validate_daily_replay_evidence(
            result=result,
            attribution=canonical,
            account=account,
            fills=normalized_fills,
            positions=positions,
            economic_start=economic_start,
            economic_end=economic_end,
            trusted_sessions=trusted_sessions,
            trusted_close=trusted_close,
        )
    return canonical
