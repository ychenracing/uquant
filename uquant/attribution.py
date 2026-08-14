"""Canonical, reconciled economic attribution for production replays."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_type
from typing import Any

import pandas as pd

from .types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    OriginSubsystem,
    Side,
    validate_attribution_compatibility,
)

RECONCILIATION_TOLERANCE = 1e-6

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
_LEDGER_FIELDS = {
    "date",
    "cash",
    "equity",
    "gross_exposure",
    "net_exposure",
    "cash_weight",
    "position_weights",
    "daily_pnl",
    "target_weights",
    "target_gross",
    "caps",
    "binding_owner",
    "risk_state",
    "opportunity",
}
_DAILY_REPLAY_FIELDS = {
    "date",
    "cash",
    "position_shares",
    "close_marks",
}


def _finite(value: Any, *, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{label} must be a finite number")
    return number


def _positive_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


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


def _empty_pnl_bucket() -> dict[str, float]:
    return {
        "realized_pnl": 0.0,
        "open_pnl": 0.0,
        "total_pnl": 0.0,
        "cash_fees": 0.0,
        "slippage": 0.0,
        "all_in_costs": 0.0,
        "gross_transaction_value": 0.0,
    }


def contribution_concentration(values: Mapping[str, float]) -> dict[str, Any]:
    """Describe positive, signed-net, and absolute PnL contribution denominators."""

    normalized: dict[str, float] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise ValueError("contribution keys must be non-empty text")
        normalized[key] = _finite(value, label=f"contribution for {key}")
    positive_values = sorted((value for value in normalized.values() if value > 0.0), reverse=True)
    positive_denominator = sum(positive_values)
    signed_denominator = sum(normalized.values())
    absolute_denominator = sum(abs(value) for value in normalized.values())
    if positive_denominator > 0.0:
        positive_weights = [value / positive_denominator for value in positive_values]
        positive: dict[str, Any] = {
            "status": "DEFINED",
            "top1": positive_weights[0],
            "top3": sum(positive_weights[:3]),
            "hhi": sum(weight * weight for weight in positive_weights),
        }
    else:
        positive = {
            "status": "UNDEFINED_NO_POSITIVE_PNL",
            "top1": None,
            "top3": None,
            "hhi": None,
        }
    signed = (
        {
            "status": "DEFINED",
            "contributions": {
                key: value / signed_denominator for key, value in sorted(normalized.items())
            },
        }
        if signed_denominator > 0.0
        else {
            "status": "UNDEFINED_NONPOSITIVE_NET_PNL",
            "contributions": None,
        }
    )
    absolute = (
        {
            "status": "DEFINED",
            "contributions": {
                key: abs(value) / absolute_denominator for key, value in sorted(normalized.items())
            },
            "hhi": sum(
                (abs(value) / absolute_denominator) ** 2 for value in normalized.values()
            ),
        }
        if absolute_denominator > 0.0
        else {
            "status": "UNDEFINED_ZERO_ABSOLUTE_PNL",
            "contributions": None,
            "hhi": None,
        }
    )
    return {
        "denominators": {
            "positive": positive_denominator,
            "signed_net": signed_denominator,
            "absolute": absolute_denominator,
        },
        "positive": positive,
        "signed": signed,
        "absolute": absolute,
        "winner_count": sum(value > 0.0 for value in normalized.values()),
        "loser_count": sum(value < 0.0 for value in normalized.values()),
        "zero_count": sum(value == 0.0 for value in normalized.values()),
    }


def _group_lot_pnl(
    lots: Sequence[Mapping[str, Any]],
    field: str,
    *,
    registry: Sequence[str] = (),
) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = {}
    for lot in lots:
        name = str(lot[field])
        bucket = grouped.setdefault(name, _empty_pnl_bucket())
        for pnl_field in ("realized_pnl", "open_pnl", "total_pnl"):
            bucket[pnl_field] += float(lot[pnl_field])
        costs = lot["costs"]
        bucket["cash_fees"] += float(costs["cash_fees"])
        bucket["slippage"] += float(costs["slippage"])
        bucket["all_in_costs"] += float(costs["all_in"])
        bucket["gross_transaction_value"] += float(lot["gross_transaction_value"])
    for name in registry:
        grouped.setdefault(name, _empty_pnl_bucket())
    return dict(sorted(grouped.items()))


def _holding_summary(lots: Sequence[Mapping[str, Any]]) -> dict[str, float | int | None]:
    total_shares = sum(int(lot["shares"]) for lot in lots)
    if total_shares == 0:
        return {"lot_count": 0, "shares": 0, "weighted_average": None}
    return {
        "lot_count": len(lots),
        "shares": total_shares,
        "weighted_average": sum(
            int(lot["shares"]) * int(lot["holding_sessions"]) for lot in lots
        )
        / total_shares,
    }


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


def _require_exact_fields(value: Any, expected: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label} fields differ from the exact attribution schema")
    return value


def _close(observed: float, expected: float, *, label: str) -> None:
    if not math.isclose(
        observed,
        expected,
        rel_tol=1e-12,
        abs_tol=RECONCILIATION_TOLERANCE,
    ):
        raise ValueError(f"{label} does not reconcile")


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
            field: _finite(bucket[field], label=f"{label}/{name}/{field}")
            for field in _GROUP_FIELDS
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
        if replaces_symbol is not None and (
            not isinstance(replaces_symbol, str) or not replaces_symbol
        ):
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

    cost_payload = _require_exact_fields(
        payload["costs"], _COST_FIELDS, label="economic attribution costs"
    )
    cost_numbers = {
        field: _finite(
            cost_payload[field], label=f"economic attribution cost {field}", minimum=0.0
        )
        for field in _COST_FIELDS
        - {"slippage_accounting", "pre_all_in_cost_pnl"}
    }
    pre_cost = _finite(cost_payload["pre_all_in_cost_pnl"], label="pre-all-in-cost PnL")
    if (
        cost_payload["slippage_accounting"]
        != "embedded_in_execution_price_not_double_subtracted"
    ):
        raise ValueError("economic attribution slippage accounting label differs")
    lot_commission = sum(
        float(lot["costs"]["entry_commission"]) + float(lot["costs"]["exit_commission"])
        for lot in lots
    )
    lot_stamp = sum(
        float(lot["costs"]["entry_stamp_duty"]) + float(lot["costs"]["exit_stamp_duty"])
        for lot in lots
    )
    lot_transfer = sum(
        float(lot["costs"]["entry_transfer_fee"]) + float(lot["costs"]["exit_transfer_fee"])
        for lot in lots
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
        cost_numbers["commission"]
        + cost_numbers["stamp_duty"]
        + cost_numbers["transfer_fee"],
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
        contributions={
            name: bucket["total_pnl"] for name, bucket in groups["by_industry"].items()
        },
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
        name: {
            field: bucket[field]
            for field in ("realized_pnl", "open_pnl", "total_pnl")
        }
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
    if holding["definition"] != (
        "zero-based distance between entry and exit/final session, share-weighted"
    ):
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
        if parsed_date < date_type.fromisoformat(economic_start) or parsed_date > date_type.fromisoformat(economic_end):
            raise ValueError("daily attribution ledger date lies outside the economic interval")
        dates.append(date)
        cash = _finite(row["cash"], label="daily ledger cash", minimum=0.0)
        equity = _finite(row["equity"], label="daily ledger equity", minimum=0.0)
        if equity <= 0.0:
            raise ValueError("daily attribution ledger equity must be positive")
        gross_exposure = _finite(
            row["gross_exposure"], label="daily ledger gross exposure", minimum=0.0
        )
        net_exposure = _finite(row["net_exposure"], label="daily ledger net exposure")
        cash_weight = _finite(row["cash_weight"], label="daily ledger cash weight")
        daily_pnl = _finite(row["daily_pnl"], label="daily ledger PnL")
        target_gross = _finite(
            row["target_gross"], label="daily ledger target gross", minimum=0.0
        )
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
        caps = _require_exact_fields(
            row["caps"], {"risk_gross", "system_gross"}, label="daily ledger caps"
        )
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
        if not date_type.fromisoformat(economic_start) <= fill_day <= date_type.fromisoformat(
            economic_end
        ):
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
        symbol: float(bucket["total_pnl"])
        for symbol, bucket in canonical["by_symbol"].items()
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


def build_daily_ledger_row(
    *,
    date: str,
    account: AccountState,
    close_prices: Mapping[str, float],
    previous_equity: float,
    target_weights: Mapping[str, float],
    target_gross: float,
    risk_gross_cap: float,
    system_gross_cap: float,
    risk_state: str,
    opportunity: str,
) -> dict[str, Any]:
    """Capture one same-close account/decision row without future information."""

    try:
        date_type.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValueError("daily attribution ledger date must be ISO") from exc
    cash = _finite(account.cash, label="daily cash", minimum=0.0)
    prior = _finite(previous_equity, label="previous equity", minimum=0.0)
    position_values: dict[str, float] = {}
    for symbol, position in sorted(account.positions.items()):
        if position.shares <= 0:
            continue
        price = _finite(close_prices.get(symbol), label=f"daily close for {symbol}", minimum=0.0)
        if price <= 0.0:
            raise ValueError(f"daily close for {symbol} must be positive")
        position_values[symbol] = position.shares * price
    equity = cash + sum(position_values.values())
    if equity <= 0.0:
        raise ValueError("daily equity must be positive")
    risk_cap = _finite(risk_gross_cap, label="risk gross cap", minimum=0.0)
    system_cap = _finite(system_gross_cap, label="system gross cap", minimum=0.0)
    target = _finite(target_gross, label="target gross", minimum=0.0)
    effective_cap = min(risk_cap, system_cap)
    if target > effective_cap + 1e-12:
        binding_owner = "STRATEGY_RETENTION_OVERRIDE"
    elif math.isclose(target, effective_cap, rel_tol=0.0, abs_tol=1e-12):
        if math.isclose(risk_cap, system_cap, rel_tol=0.0, abs_tol=1e-12):
            binding_owner = "RISK_AND_SYSTEM"
        elif risk_cap < system_cap:
            binding_owner = "RISK"
        else:
            binding_owner = "SYSTEM"
    else:
        binding_owner = "STRATEGY"
    normalized_targets = {
        symbol: _finite(weight, label=f"target weight for {symbol}", minimum=0.0)
        for symbol, weight in sorted(target_weights.items())
    }
    weights = {symbol: value / equity for symbol, value in position_values.items()}
    gross = sum(abs(value) for value in position_values.values()) / equity
    net = sum(position_values.values()) / equity
    return {
        "date": date,
        "cash": cash,
        "equity": equity,
        "gross_exposure": gross,
        "net_exposure": net,
        "cash_weight": cash / equity,
        "position_weights": weights,
        "daily_pnl": equity - prior,
        "target_weights": normalized_targets,
        "target_gross": target,
        "caps": {"risk_gross": risk_cap, "system_gross": system_cap},
        "binding_owner": binding_owner,
        "risk_state": risk_state,
        "opportunity": opportunity,
    }


def build_daily_replay_evidence_row(
    *,
    date: str,
    account: AccountState,
    close_prices: Mapping[str, float],
) -> dict[str, Any]:
    """Capture only raw same-close facts used to independently rebuild a ledger row."""

    try:
        date_type.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValueError("daily replay evidence date must be ISO") from exc
    cash = _finite(account.cash, label="daily replay evidence cash", minimum=0.0)
    position_shares = {
        symbol: _positive_integer(position.shares, label=f"daily replay shares/{symbol}")
        for symbol, position in sorted(account.positions.items())
        if position.shares > 0
    }
    if set(close_prices) != set(position_shares):
        raise ValueError("daily replay close marks differ from open positions")
    close_marks = {
        symbol: _finite(
            close_prices[symbol],
            label=f"daily replay close/{symbol}",
            minimum=0.0,
        )
        for symbol in sorted(close_prices)
    }
    if any(mark <= 0.0 for mark in close_marks.values()):
        raise ValueError("daily replay close marks must be positive")
    return {
        "date": date,
        "cash": cash,
        "position_shares": position_shares,
        "close_marks": close_marks,
    }


def _validate_daily_replay_evidence(
    *,
    result: Mapping[str, Any],
    attribution: Mapping[str, Any],
    account: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
    positions: Mapping[str, Any],
    economic_start: str,
    economic_end: str,
    trusted_sessions: Sequence[str] | None,
    trusted_close: Callable[[str, str], float] | None,
) -> None:
    """Rebuild every derived daily value from fills plus verified closing marks."""

    evidence_value = result.get("daily_replay_evidence")
    equity_curve_value = result.get("equity_curve")
    if not isinstance(evidence_value, list) or not evidence_value:
        raise ValueError("engine result daily replay evidence is required")
    if not isinstance(equity_curve_value, list) or not equity_curve_value:
        raise ValueError("engine result equity curve is required for daily replay evidence")
    ledger_value = attribution.get("daily_ledger")
    if not isinstance(ledger_value, list) or not ledger_value:
        raise ValueError("economic attribution daily ledger is required for replay")

    evidence_by_date: dict[str, Mapping[str, Any]] = {}
    evidence_dates: list[str] = []
    for index, raw_row in enumerate(evidence_value):
        row = _require_exact_fields(
            raw_row,
            _DAILY_REPLAY_FIELDS,
            label=f"daily replay evidence row {index}",
        )
        row_date = row["date"]
        if not isinstance(row_date, str):
            raise ValueError("daily replay evidence date is invalid")
        try:
            parsed = date_type.fromisoformat(row_date)
        except ValueError as exc:
            raise ValueError("daily replay evidence date is invalid") from exc
        if not date_type.fromisoformat(economic_start) <= parsed <= date_type.fromisoformat(
            economic_end
        ):
            raise ValueError("daily replay evidence lies outside the economic interval")
        if row_date in evidence_by_date:
            raise ValueError("daily replay evidence dates must be unique")
        evidence_dates.append(row_date)
        evidence_by_date[row_date] = row
    if tuple(evidence_dates) != tuple(sorted(evidence_dates)):
        raise ValueError("daily replay evidence dates must be ordered")
    if evidence_dates[0] != economic_start or evidence_dates[-1] != economic_end:
        raise ValueError("daily replay evidence does not span the exact economic interval")
    if trusted_sessions is not None and tuple(evidence_dates) != tuple(trusted_sessions):
        raise ValueError("daily replay evidence differs from verified market sessions")
    if (trusted_sessions is None) != (trusted_close is None):
        raise ValueError("daily replay evidence trusted market source is incomplete")

    curve_by_date: dict[str, float] = {}
    curve_dates: list[str] = []
    for index, raw_point in enumerate(equity_curve_value):
        point = _require_exact_fields(
            raw_point,
            {"date", "equity"},
            label=f"engine equity curve row {index}",
        )
        point_date = point["date"]
        if not isinstance(point_date, str) or point_date in curve_by_date:
            raise ValueError("engine equity curve dates are malformed")
        curve_dates.append(point_date)
        curve_by_date[point_date] = _finite(
            point["equity"],
            label="engine equity curve value",
            minimum=0.0,
        )
    ledger_dates = [str(row.get("date", "")) for row in ledger_value]
    if curve_dates != evidence_dates or ledger_dates != evidence_dates:
        raise ValueError("daily replay evidence, equity curve, and attribution ledger dates differ")

    fills_by_date: dict[str, list[Mapping[str, Any]]] = {}
    for raw_fill in fills:
        fill_date = str(raw_fill.get("fill_date", ""))
        fills_by_date.setdefault(fill_date, []).append(raw_fill)
    initial_cash = _finite(
        account.get("initial_cash"),
        label="daily replay initial cash",
        minimum=0.0,
    )
    replay_cash = initial_cash
    replay_positions: dict[str, int] = {}
    previous_equity = initial_cash
    for row_date, raw_ledger in zip(evidence_dates, ledger_value, strict=True):
        for fill in fills_by_date.get(row_date, []):
            side = fill.get("side")
            symbol = str(fill.get("symbol", ""))
            shares = _positive_integer(fill.get("shares"), label="daily replay fill shares")
            gross = _finite(
                fill.get("gross_value"),
                label="daily replay fill gross value",
                minimum=0.0,
            )
            cash_fees = sum(
                _finite(
                    fill.get(name),
                    label=f"daily replay fill {name}",
                    minimum=0.0,
                )
                for name in ("commission", "stamp_duty", "transfer_fee")
            )
            if side == Side.BUY.value:
                replay_cash -= gross + cash_fees
                replay_positions[symbol] = replay_positions.get(symbol, 0) + shares
            elif side == Side.SELL.value:
                available = replay_positions.get(symbol, 0)
                if shares > available:
                    raise ValueError("daily replay SELL exceeds reconstructed position shares")
                replay_cash += gross - cash_fees
                remaining = available - shares
                if remaining:
                    replay_positions[symbol] = remaining
                else:
                    replay_positions.pop(symbol, None)
            else:  # pragma: no cover - raw fill validation rejects this first
                raise ValueError("daily replay fill side is invalid")
        evidence = evidence_by_date[row_date]
        evidence_cash = _finite(
            evidence["cash"],
            label="daily replay evidence cash",
            minimum=0.0,
        )
        _close(evidence_cash, replay_cash, label="daily replay evidence cash versus fills")
        raw_shares = evidence["position_shares"]
        if not isinstance(raw_shares, Mapping):
            raise ValueError("daily replay evidence position shares are malformed")
        evidence_shares = {
            str(symbol): _positive_integer(
                shares,
                label=f"daily replay evidence shares/{symbol}",
            )
            for symbol, shares in raw_shares.items()
        }
        if evidence_shares != dict(sorted(replay_positions.items())):
            raise ValueError("daily replay evidence position shares differ from fills")
        raw_marks = evidence["close_marks"]
        if not isinstance(raw_marks, Mapping) or set(raw_marks) != set(evidence_shares):
            raise ValueError("daily replay evidence close marks differ from positions")
        marks = {
            str(symbol): _finite(
                mark,
                label=f"daily replay evidence close/{symbol}",
                minimum=0.0,
            )
            for symbol, mark in raw_marks.items()
        }
        if any(mark <= 0.0 for mark in marks.values()):
            raise ValueError("daily replay evidence close marks must be positive")
        if trusted_close is not None:
            for symbol, mark in marks.items():
                _close(
                    mark,
                    trusted_close(symbol, row_date),
                    label=f"daily replay evidence close versus frozen data/{symbol}/{row_date}",
                )
        position_values = {
            symbol: shares * marks[symbol] for symbol, shares in evidence_shares.items()
        }
        equity = evidence_cash + sum(position_values.values())
        _close(
            curve_by_date[row_date],
            equity,
            label="daily replay evidence versus engine equity curve",
        )
        ledger = _require_exact_fields(
            raw_ledger,
            _LEDGER_FIELDS,
            label=f"daily replay ledger/{row_date}",
        )
        _close(float(ledger["cash"]), evidence_cash, label="daily replay ledger cash")
        _close(float(ledger["equity"]), equity, label="daily replay ledger equity")
        _close(
            float(ledger["cash_weight"]),
            evidence_cash / equity,
            label="daily replay ledger cash weight",
        )
        expected_weights = {
            symbol: value / equity for symbol, value in position_values.items()
        }
        observed_weights = ledger["position_weights"]
        if not isinstance(observed_weights, Mapping) or set(observed_weights) != set(
            expected_weights
        ):
            raise ValueError("daily replay ledger position weights differ from positions")
        for symbol, expected_weight in expected_weights.items():
            _close(
                float(observed_weights[symbol]),
                expected_weight,
                label=f"daily replay ledger position weight/{symbol}",
            )
        gross = sum(abs(value) for value in position_values.values()) / equity
        net = sum(position_values.values()) / equity
        _close(float(ledger["gross_exposure"]), gross, label="daily replay gross exposure")
        _close(float(ledger["net_exposure"]), net, label="daily replay net exposure")
        _close(
            float(ledger["daily_pnl"]),
            equity - previous_equity,
            label="daily replay ledger PnL",
        )
        previous_equity = equity

    final_cash = _finite(account.get("cash"), label="engine final account cash", minimum=0.0)
    _close(replay_cash, final_cash, label="daily replay cash versus final account")
    final_position_shares: dict[str, int] = {}
    for symbol, raw_position in positions.items():
        if not isinstance(raw_position, Mapping):
            raise ValueError("engine final position is malformed")
        raw_shares_value = raw_position.get("shares")
        if (
            isinstance(raw_shares_value, bool)
            or not isinstance(raw_shares_value, int)
            or raw_shares_value < 0
        ):
            raise ValueError("engine final position shares are malformed")
        position_shares = int(raw_shares_value)
        if position_shares:
            final_position_shares[str(symbol)] = position_shares
    if replay_positions != final_position_shares:
        raise ValueError("daily replay positions differ from final account")


def attribution_diagnostics(
    *,
    daily_ledger: Sequence[Mapping[str, Any]],
    benchmark_close: Mapping[str, float],
    paired_counterfactual_equity: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Calculate non-accounting cash opportunity cost and paired risk avoidance."""

    dates = tuple(str(row.get("date", "")) for row in daily_ledger)
    if not dates or tuple(sorted(set(dates))) != dates:
        raise ValueError("diagnostic daily ledger must be unique and ordered")
    if set(benchmark_close) != set(dates):
        raise ValueError("cash-drag benchmark must exactly cover daily ledger dates")
    benchmark = {
        date: _finite(benchmark_close[date], label=f"benchmark close {date}", minimum=0.0)
        for date in dates
    }
    if any(value <= 0.0 for value in benchmark.values()):
        raise ValueError("cash-drag benchmark closes must be positive")
    cash_drag = 0.0
    for prior_row, date, prior_date in zip(daily_ledger, dates[1:], dates, strict=False):
        cash = _finite(prior_row.get("cash"), label=f"ledger cash {prior_date}", minimum=0.0)
        cash_drag -= cash * (benchmark[date] / benchmark[prior_date] - 1.0)
    if paired_counterfactual_equity is None:
        risk_avoidance: dict[str, Any] = {
            "status": "NOT_EVALUATED_REQUIRES_PAIRED_COUNTERFACTUAL",
            "value": None,
            "is_accounting_pnl": False,
        }
    else:
        if set(paired_counterfactual_equity) != set(dates):
            raise ValueError("paired counterfactual must exactly cover daily ledger dates")
        counterfactual = {
            date: _finite(
                paired_counterfactual_equity[date],
                label=f"paired counterfactual equity {date}",
                minimum=0.0,
            )
            for date in dates
        }
        actual_final = _finite(daily_ledger[-1].get("equity"), label="actual final equity")
        risk_avoidance = {
            "status": "PAIRED_COUNTERFACTUAL",
            "value": actual_final - counterfactual[dates[-1]],
            "definition": "actual final equity minus paired counterfactual final equity",
            "is_accounting_pnl": False,
        }
    return {
        "cash_drag": {
            "status": "DIAGNOSTIC",
            "value": cash_drag,
            "definition": "negative prior-close cash times next-session benchmark return",
            "is_accounting_pnl": False,
        },
        "risk_avoidance": risk_avoidance,
    }


@dataclass(frozen=True, slots=True)
class ExitRecord:
    """Structured exit identity used only for bounded post-exit diagnostics."""

    symbol: str
    exit_date: str
    exit_price: float
    origin_subsystem: str
    mechanism: str
    benchmark_symbol: str = ""

    def __post_init__(self) -> None:
        if not self.symbol or not self.origin_subsystem or not self.mechanism:
            raise ValueError("exit diagnostics require structured attribution identity")
        try:
            date_type.fromisoformat(self.exit_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("exit diagnostic date must be ISO") from exc
        if not math.isfinite(self.exit_price) or self.exit_price <= 0.0:
            raise ValueError("exit diagnostic price must be positive and finite")


def _bounded_price_series(
    series: pd.Series,
    *,
    symbol: str,
    economic_end: pd.Timestamp,
) -> pd.Series:
    clean = series.astype(float).dropna().copy()
    clean.index = pd.to_datetime(clean.index)
    clean = clean.sort_index().loc[:economic_end]
    if clean.empty or not clean.index.is_unique or (clean <= 0.0).any():
        raise ValueError(f"invalid bounded attribution prices: {symbol}")
    return clean


def post_exit_diagnostics(
    *,
    exits: Sequence[ExitRecord],
    prices: Mapping[str, pd.Series],
    economic_end: str,
    horizons: Sequence[int] = (5, 10, 20, 40),
) -> list[dict[str, Any]]:
    """Measure post-exit paths after slicing every input at ``economic_end``."""

    try:
        end = pd.Timestamp(date_type.fromisoformat(economic_end))
    except (TypeError, ValueError) as exc:
        raise ValueError("post-exit economic_end must be an ISO date") from exc
    requested = tuple(sorted(set(horizons)))
    if not requested or requested[0] <= 0:
        raise ValueError("post-exit horizons must be positive")
    bounded = {
        symbol: _bounded_price_series(series, symbol=symbol, economic_end=end)
        for symbol, series in prices.items()
    }
    output: list[dict[str, Any]] = []
    for record in sorted(exits, key=lambda item: (item.exit_date, item.symbol)):
        if pd.Timestamp(record.exit_date) > end:
            raise ValueError("exit diagnostic lies after economic_end")
        series = bounded.get(record.symbol)
        if series is None:
            raise ValueError(f"missing bounded attribution prices: {record.symbol}")
        exit_date = pd.Timestamp(record.exit_date)
        if exit_date not in series.index:
            raise ValueError(f"exit date is not an observed session: {record.symbol}")
        location = int(series.index.get_indexer(pd.DatetimeIndex([exit_date]))[0])
        values: dict[str, Any] = {}
        for horizon in requested:
            future = location + horizon
            if future >= len(series):
                values[str(horizon)] = None
                continue
            absolute = float(series.iloc[future] / record.exit_price - 1.0)
            values[str(horizon)] = {
                "absolute_return": absolute,
                "avoided_loss": max(0.0, -absolute),
                "regret": max(0.0, absolute),
            }
        output.append(
            {
                "symbol": record.symbol,
                "exit_date": record.exit_date,
                "economic_end": economic_end,
                "origin_subsystem": record.origin_subsystem,
                "mechanism": record.mechanism,
                "horizons": values,
            }
        )
    return output


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
    lots: list[dict[str, Any]] = []
    by_symbol: dict[str, dict[str, float]] = {}

    for fill in account.fills:
        if fill.fill_date not in session_set:
            raise ValueError("economic fill lies outside the exact economic interval")
        if fill.side != Side.SELL.value:
            continue
        sold_lot_shares = [
            _positive_integer(item.get("shares"), label="sold-lot shares")
            for item in fill.sold_tranches
        ]
        allocated_shares = sum(sold_lot_shares)
        if allocated_shares != fill.shares:
            raise ValueError("sell fill must reconcile through per-lot sold_tranches")
        for component in ("commission", "stamp_duty", "transfer_fee", "slippage_cost"):
            allocated_component = sum(
                _finite(
                    item.get(component),
                    label=f"sold-lot {component}",
                    minimum=0.0,
                )
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
            row = {
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
            lots.append(row)
            bucket = by_symbol.setdefault(fill.symbol, _empty_pnl_bucket())
            bucket["realized_pnl"] += pnl
            bucket["total_pnl"] += pnl

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
            row = {
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
            lots.append(row)
            bucket = by_symbol.setdefault(symbol, _empty_pnl_bucket())
            bucket["open_pnl"] += pnl
            bucket["total_pnl"] += pnl

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
    entry_components = {
        "entry_gross_value": "gross_value",
        "entry_commission": "commission",
        "entry_stamp_duty": "stamp_duty",
        "entry_transfer_fee": "transfer_fee",
        "entry_slippage": "slippage_cost",
    }
    for key, fill in sorted(buy_fills.items()):
        rows = sorted(
            rows_by_buy[key],
            key=lambda item: (str(item["tranche_id"]), str(item["economic_status"])),
        )
        if sum(int(row["shares"]) for row in rows) != fill.shares:
            raise ValueError("economic lot shares do not reconcile to originating BUY fill")
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
                entry_costs[name]
                for name in ("entry_commission", "entry_stamp_duty", "entry_transfer_fee")
            ) + sum(
                float(exit_costs[name])
                for name in ("commission", "stamp_duty", "transfer_fee")
            )
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
            row["gross_transaction_value"] = float(row["entry_gross_value"]) + float(
                row["exit_gross_value"]
            )

    session_index = {date: index for index, date in enumerate(canonical_sessions)}
    for lot in lots:
        exit_or_final = str(lot["exit_date"] or lot["final_date"])
        entry_date = str(lot["entry_date"])
        if entry_date not in session_index or exit_or_final not in session_index:
            raise ValueError("lot holding period lies outside the exact economic interval")
        holding_sessions = session_index[exit_or_final] - session_index[entry_date]
        if holding_sessions < 0:
            raise ValueError("lot holding period is negative")
        lot["holding_sessions"] = holding_sessions

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
    by_symbol = _group_lot_pnl(lots, "symbol")
    by_industry = _group_lot_pnl(lots, "industry_at_entry")
    lifecycle_registry = tuple(item.value for item in Lifecycle)
    by_origin_lifecycle = _group_lot_pnl(
        lots,
        "origin_lifecycle",
        registry=lifecycle_registry,
    )
    by_current_lifecycle = _group_lot_pnl(
        lots,
        "current_lifecycle",
        registry=lifecycle_registry,
    )
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
            symbol: {
                name: bucket[name]
                for name in ("realized_pnl", "open_pnl", "total_pnl")
            }
            for symbol, bucket in replacement_groups.items()
        },
    }
    symbol_concentration = contribution_concentration(
        {symbol: bucket["total_pnl"] for symbol, bucket in by_symbol.items()}
    )
    industry_concentration = contribution_concentration(
        {industry: bucket["total_pnl"] for industry, bucket in by_industry.items()}
    )
    gross_transaction_value = sum(fill.gross_value for fill in account.fills)
    open_lots = [lot for lot in lots if lot["economic_status"] == "OPEN"]
    return {
        "schema": "uquant.economic-attribution.v1",
        "status": "VALID",
        "interval": {"economic_start": economic_start, "economic_end": economic_end},
        "accounting": {
            "realized_pnl": realized_pnl,
            "open_pnl": open_pnl,
            "total_pnl": total_pnl,
            "expected_pnl": expected_pnl,
            "reconciliation_error": reconciliation_error,
            "tolerance": RECONCILIATION_TOLERANCE,
            "reconciled": True,
        },
        "costs": {
            "commission": commission,
            "stamp_duty": stamp_duty,
            "transfer_fee": transfer_fee,
            "cash_fees": cash_fees,
            "slippage": slippage,
            "all_in": all_in,
            "pre_all_in_cost_pnl": total_pnl + all_in,
            "all_in_cost_drag_initial_cash": all_in / initial_cash,
            "slippage_accounting": "embedded_in_execution_price_not_double_subtracted",
        },
        "by_symbol": by_symbol,
        "by_industry": by_industry,
        "by_origin_lifecycle": by_origin_lifecycle,
        "by_current_lifecycle": by_current_lifecycle,
        "by_origin_subsystem": by_origin_subsystem,
        "by_mechanism": by_mechanism,
        "by_exit_subsystem": by_exit_subsystem,
        "by_exit_mechanism": by_exit_mechanism,
        "replacements": replacements,
        "turnover": {
            "definition": "sum(fill.gross_value) / initial_cash",
            "gross_transaction_value": gross_transaction_value,
            "gross_turnover": gross_transaction_value / initial_cash,
        },
        "holding_period_sessions": {
            "definition": (
                "zero-based distance between entry and exit/final session, share-weighted"
            ),
            "all": _holding_summary(lots),
            "realized": _holding_summary(realized_lots),
            "open": _holding_summary(open_lots),
        },
        "concentration": symbol_concentration,
        "symbol_concentration": symbol_concentration,
        "industry_concentration": industry_concentration,
        "daily_ledger": ledger_rows,
        "diagnostics": diagnostics,
        "lots": lots,
    }
