"""Causal production-backtest observations for offline divergence research."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import date
from numbers import Real
from typing import Any

import pandas as pd

from uquant.engine import ProductionEngine
from uquant.types import AccountState, Decision, Fill
from uquant.validation.ai_era import AI_ERA_START

_CAUSAL_STAGES = (
    "reference_context",
    "leaders",
    "risk",
    "opportunity",
    "targets",
    "orders",
    "fills",
)
_EXECUTABLE_STAGES = ("orders", "fills")

_REFERENCE_CONTEXT_FIELDS = (
    "reference_visible_symbols",
    "reference_expected_symbols",
    "reference_visible_groups",
    "reference_coverage",
    "name_weighted_breadth20",
    "group_balanced_breadth20",
    "breadth20",
    "name_weighted_breadth60",
    "group_balanced_breadth60",
    "breadth60",
    "name_weighted_declining_ratio",
    "group_balanced_declining_ratio",
    "declining_ratio",
    "sector_stress_ratio",
    "reference_dispersion20",
    "median_correlation",
    "reference_global_strength",
    "reference_industry_strength",
    "reference_details",
)
_LEADER_FIELDS = ("symbol", "score", "industry", "mature", "emerging")
_TARGET_FIELDS = (
    "symbol",
    "weight",
    "lifecycle",
    "reduction_policy",
    "reason_code",
    "exit_kind",
)
_ORDER_FIELDS = (
    "order_id",
    "symbol",
    "side",
    "target_weight",
    "reduction_policy",
    "reason_code",
    "exit_kind",
)
_FILL_FIELDS = (
    "signal_date",
    "fill_date",
    "symbol",
    "side",
    "shares",
    "price",
    "gross_value",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "slippage_cost",
    "lifecycle",
    "order_id",
    "fill_id",
    "reduction_policy",
    "reason_code",
    "exit_kind",
    "sold_tranches",
)
_SOLD_TRANCHE_FIELDS = (
    "tranche_id",
    "shares",
    "cost",
    "unit_cost",
    "avg_cost",
    "cost_basis",
    "lifecycle",
    "mfe",
    "mae",
    "entry_date",
    "commission",
    "stamp_duty",
    "transfer_fee",
    "slippage_cost",
    "fees",
    "transaction_costs",
)
_RISK_IGNORED_FIELDS = frozenset(
    (
        *_REFERENCE_CONTEXT_FIELDS,
        "leader_ranking",
        "evidence_families",
        "effective_config_sha256",
    )
)
_METADATA_NOISE_FIELDS = frozenset(
    {
        "reason",
        "alpha_score",
        "confidence",
        "entry_score",
        "entry_confidence",
        "entry_regime",
        "entry_industry_strength",
    }
)


def _canonical_value(value: Any) -> Any:
    """Freeze JSON-like values while normalizing numeric representation."""

    if isinstance(value, Mapping):
        return {
            str(name): _canonical_value(item)
            for name, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(name) not in _METADATA_NOISE_FIELDS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_canonical_value(item) for item in value)
    if isinstance(value, (bool, int)):
        return value
    if isinstance(value, Real):
        numeric = float(value)
        if math.isnan(numeric):
            return "NaN"
        if math.isinf(numeric):
            return "Infinity" if numeric > 0 else "-Infinity"
        return round(numeric, 12)
    return value


def _project_mapping(
    value: Mapping[str, Any],
    fields: Sequence[str],
) -> dict[str, Any]:
    """Retain an explicitly reviewed canonical field set."""

    projected: dict[str, Any] = {}
    for field in fields:
        if field not in value:
            continue
        if field == "sold_tranches":
            projected[field] = _project_records(value[field], _SOLD_TRANCHE_FIELDS)
        else:
            projected[field] = _canonical_value(value[field])
    return projected


def _project_records(value: Any, fields: Sequence[str]) -> Any:
    """Project a deterministic ordered record sequence onto canonical fields."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return _canonical_value(value)
    return tuple(
        _project_mapping(item, fields) if isinstance(item, Mapping) else _canonical_value(item)
        for item in value
    )


def _reference_context(row: Mapping[str, Any]) -> Any:
    source = row.get("reference_context", row.get("reference_evidence", {}))
    if isinstance(source, Mapping):
        return _project_mapping(source, _REFERENCE_CONTEXT_FIELDS)
    return _canonical_value(source)


def _leaders(row: Mapping[str, Any]) -> Any:
    return _project_records(row.get("leaders", row.get("ranked_leaders", ())), _LEADER_FIELDS)


def _risk(row: Mapping[str, Any]) -> Any:
    raw_risk = row.get("risk", "")
    risk = (
        {
            str(name): _canonical_value(value)
            for name, value in raw_risk.items()
            if str(name) not in _METADATA_NOISE_FIELDS
        }
        if isinstance(raw_risk, Mapping)
        else {"state": _canonical_value(raw_risk)}
    )
    evidence = row.get("risk_evidence", {})
    if isinstance(evidence, Mapping):
        risk.update(
            {
                str(name): _canonical_value(value)
                for name, value in sorted(evidence.items(), key=lambda pair: str(pair[0]))
                if str(name) not in _RISK_IGNORED_FIELDS
                and str(name) not in _METADATA_NOISE_FIELDS
            }
        )
    for field in (
        "family_votes",
        "sector_guard_active",
        "capital_damage",
        "capital_budget_level",
    ):
        if field in row and field not in risk:
            risk[field] = _canonical_value(row[field])
    return risk


def _targets(row: Mapping[str, Any]) -> dict[str, Any]:
    targets: dict[str, Any] = {
        "items": _project_records(row.get("targets", ()), _TARGET_FIELDS),
    }
    if "target_gross" in row:
        targets["target_gross"] = _canonical_value(row["target_gross"])
    if "strategic_targets" in row:
        targets["strategic_targets"] = _canonical_value(row["strategic_targets"])
    return targets


def _orders(row: Mapping[str, Any]) -> Any:
    return _project_records(row.get("orders", row.get("pending_orders", ())), _ORDER_FIELDS)


def _fills(row: Mapping[str, Any]) -> Any:
    return _project_records(row.get("fills", row.get("new_fills", ())), _FILL_FIELDS)


def _canonical_stages(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reference_context": _reference_context(row),
        "leaders": _leaders(row),
        "risk": _risk(row),
        "opportunity": _canonical_value(row.get("opportunity", "")),
        "targets": _targets(row),
        "orders": _orders(row),
        "fills": _fills(row),
    }


def _validated_dates(trace: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    if not trace:
        raise ValueError("decision traces require non-empty sorted dates")
    raw_dates = tuple(str(item.get("date", "")) for item in trace)
    try:
        parsed = tuple(date.fromisoformat(item) for item in raw_dates)
    except ValueError as exc:
        raise ValueError("decision traces require ISO-8601 dates") from exc
    if parsed != tuple(sorted(set(parsed))):
        raise ValueError("decision traces require non-empty sorted dates")
    if parsed[0] < date.fromisoformat(AI_ERA_START):
        raise ValueError(f"decision traces cannot start before {AI_ERA_START}")
    return raw_dates


def _validate_trace_interval(start: str, end: str) -> None:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("trace window requires ISO-8601 dates") from exc
    if start_date < date.fromisoformat(AI_ERA_START):
        raise ValueError(f"trace window cannot start before {AI_ERA_START}")
    if start_date > end_date:
        raise ValueError("trace window starts after it ends")


def _trace_row(
    *,
    engine: ProductionEngine,
    decision: Decision,
    account: AccountState,
    new_fills: Sequence[Fill],
) -> dict[str, Any]:
    """Capture state already produced by the sole production decision path."""
    risk_summary = decision.risk_summary
    raw_family_votes = risk_summary.get("family_votes", {})
    family_votes = (
        {str(name): bool(active) for name, active in sorted(raw_family_votes.items())}
        if isinstance(raw_family_votes, Mapping)
        else {}
    )
    raw_leaders = risk_summary.get("leader_ranking", ())
    ranked_leaders = tuple(
        dict(item) for item in raw_leaders if isinstance(item, Mapping)
    ) if isinstance(raw_leaders, Sequence) else ()
    equity = engine.equity(account, pd.Timestamp(decision.date))
    actual_gross = (equity - account.cash) / equity if equity > 1e-12 else 0.0
    risk_evidence = {
        str(name): value
        for name, value in sorted(risk_summary.items())
        if name != "leader_ranking"
    }
    reference_context = _reference_context({"reference_evidence": risk_evidence})
    leaders = _project_records(ranked_leaders, _LEADER_FIELDS)
    raw_targets = tuple(asdict(target) for target in decision.targets)
    raw_fills = tuple(asdict(fill) for fill in new_fills)
    raw_orders = tuple(asdict(order) for order in decision.pending_orders)

    return {
        "date": decision.date,
        "reference_context": reference_context,
        "leaders": leaders,
        "opportunity": decision.opportunity.value,
        "risk": decision.risk.value,
        "family_votes": family_votes,
        "sector_guard_active": bool(risk_summary.get("sector_guard_active", False)),
        "capital_damage": bool(family_votes.get("capital_damage", False)),
        "capital_drawdown": float(risk_summary.get("capital_drawdown", 0.0)),
        "capital_budget_level": int(risk_summary.get("capital_budget_level", 0)),
        "reference_evidence": {
            name: value for name, value in risk_evidence.items() if name.startswith("reference_")
        },
        "risk_evidence": risk_evidence,
        "ranked_leaders": ranked_leaders,
        "strategic_targets": dict(sorted(account.strategic_cohort_targets.items())),
        "targets": raw_targets,
        "target_gross": decision.target_gross,
        "actual_gross": actual_gross,
        "equity": equity,
        "new_fills": raw_fills,
        "fills": _project_records(raw_fills, _FILL_FIELDS),
        "pending_orders": raw_orders,
        "orders": _project_records(raw_orders, _ORDER_FIELDS),
    }


def trace_backtest(
    engine: ProductionEngine,
    *,
    symbols: Iterable[str],
    start: str,
    end: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Run ``engine.backtest`` unchanged while observing its real daily decisions."""
    _validate_trace_interval(start, end)
    traces: list[dict[str, Any]] = []
    fill_cursor = 0
    original_decide = engine.decide

    def observed_decide(
        *,
        symbols: Iterable[str],
        as_of: str,
        account: AccountState,
    ) -> Decision:
        """Delegate to production and append the resulting economic trace row."""

        nonlocal fill_cursor
        new_fills = tuple(account.fills[fill_cursor:])
        fill_cursor = len(account.fills)
        decision = original_decide(symbols=symbols, as_of=as_of, account=account)
        traces.append(
            _trace_row(
                engine=engine,
                decision=decision,
                account=account,
                new_fills=new_fills,
            )
        )
        return decision

    try:
        object.__setattr__(engine, "decide", observed_decide)
        result = engine.backtest(symbols=symbols, start=start, end=end)
    finally:
        object.__setattr__(engine, "decide", original_decide)
    return result, tuple(traces)


def first_economic_divergence(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the earliest daily change in strict causal-stage order."""
    left_dates = _validated_dates(left)
    right_dates = _validated_dates(right)
    if left_dates != right_dates:
        raise ValueError("decision traces require aligned dates")
    for left_item, right_item in zip(left, right, strict=True):
        left_stages = _canonical_stages(left_item)
        right_stages = _canonical_stages(right_item)
        changed_fields = tuple(
            stage
            for stage in _CAUSAL_STAGES
            if left_stages[stage] != right_stages[stage]
        )
        if changed_fields:
            return {
                "date": left_item["date"],
                "changed_fields": changed_fields,
                "first_stage": changed_fields[0],
                "left": dict(left_item),
                "right": dict(right_item),
            }
    return None


def first_executable_divergence(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the first row whose submitted orders or completed fills diverge."""

    left_dates = _validated_dates(left)
    right_dates = _validated_dates(right)
    if left_dates != right_dates:
        raise ValueError("decision traces require aligned dates")
    for left_item, right_item in zip(left, right, strict=True):
        left_stages = _canonical_stages(left_item)
        right_stages = _canonical_stages(right_item)
        changed_fields = tuple(
            stage
            for stage in _CAUSAL_STAGES
            if left_stages[stage] != right_stages[stage]
        )
        executable = tuple(stage for stage in _EXECUTABLE_STAGES if stage in changed_fields)
        if executable:
            return {
                "date": left_item["date"],
                "changed_fields": changed_fields,
                "first_stage": executable[0],
                "left": dict(left_item),
                "right": dict(right_item),
            }
    return None
