"""Strict decision/account control-plane validation for matrix artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field
from typing import Any

from ..account import account_from_dict
from ..config import SystemConfig, canonical_control_float, config_fingerprint
from ..types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OrderStatus,
    OriginSubsystem,
    ReductionPolicy,
    Risk,
    Side,
    StrategicEpoch,
    derive_attribution_event_id,
    validate_attribution_compatibility,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^evt_[0-9a-f]{64}$")
_GRANT_ID = re.compile(r"^grant_[0-9a-f]{64}$")
_EPOCH_ID = re.compile(r"^epoch_[0-9a-f]{64}$")
_TRACE_FIELDS = frozenset(
    {
        "schema",
        "date",
        "opportunity",
        "risk",
        "target_gross",
        "targets",
        "orders",
        "effective_config_sha256",
    }
)
_RISK_FIELDS = frozenset(
    {
        "state",
        "target_gross_cap",
        "system_gross_cap",
    }
)
_TARGET_FIELDS = frozenset(
    {
        "symbol",
        "weight",
        "lifecycle",
        "reduction_policy",
        "reason_code",
        "exit_kind",
        "event_id",
        "event_signal_date",
        "event_target_weight_hex",
        "origin_subsystem",
        "mechanism",
        "origin_lifecycle",
        "replaces_symbol",
        "industry_at_entry",
        "industry_manifest_sha256",
        "grant_id",
        "epoch_id",
    }
)
_ORDER_FIELDS = frozenset(
    {
        "order_id",
        "signal_date",
        "snapshot_kind",
        "symbol",
        "side",
        "target_weight",
        "reduction_policy",
        "reason_code",
        "exit_kind",
        "event_id",
        "origin_subsystem",
        "mechanism",
        "origin_lifecycle",
        "replaces_symbol",
        "industry_at_entry",
        "industry_manifest_sha256",
        "grant_id",
        "epoch_id",
    }
)
_IDENTITY_FIELDS = frozenset(
    {
        "event_id",
        "origin_subsystem",
        "mechanism",
        "origin_lifecycle",
        "replaces_symbol",
        "industry_at_entry",
        "industry_manifest_sha256",
        "grant_id",
        "epoch_id",
    }
)


def _exact_mapping(value: Any, fields: Set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} fields differ from the exact control schema")
    return value


def _finite_control_value(value: Any, *, label: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be finite")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{label} must be finite")
    return number


def _sha256_json(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("decision control payload is not finite canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _rounded_sum_matches(total: float, components: Sequence[float]) -> bool:
    """Match independently rounded 12-decimal values at their exact error bound."""

    component_sum = sum(components)
    decimal_rounding_bound = (len(components) + 1) * 0.5e-12
    binary_summation_bound = math.ulp(max(1.0, abs(total), abs(component_sum))) * (len(components) + 1)
    return abs(total - component_sum) <= decimal_rounding_bound + binary_summation_bound


def legacy_decision_payload(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Project one validated v2 trace row to the exact frozen schema-v3 payload."""

    risk = trace["risk"]
    targets = trace["targets"]
    orders = trace["orders"]
    if not isinstance(risk, Mapping) or not isinstance(targets, list) or not isinstance(orders, list):
        raise ValueError("decision trace cannot form a legacy payload")
    return {
        "date": trace["date"],
        "opportunity": trace["opportunity"],
        "risk": risk["state"],
        "targets": [
            {
                name: target[name]
                for name in (
                    "symbol",
                    "weight",
                    "lifecycle",
                    "reduction_policy",
                    "reason_code",
                    "exit_kind",
                )
            }
            for target in targets
        ],
        "orders": [
            {
                name: order[name]
                for name in (
                    "order_id",
                    "symbol",
                    "side",
                    "target_weight",
                    "reduction_policy",
                    "reason_code",
                    "exit_kind",
                )
            }
            for order in orders
        ],
    }


def _validate_identity_fields(value: Mapping[str, Any], *, label: str) -> None:
    event_id = value["event_id"]
    if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
        raise ValueError(f"{label} event identity is malformed")
    try:
        OriginSubsystem(value["origin_subsystem"])
        AttributionMechanism(value["mechanism"])
        Lifecycle(value["origin_lifecycle"])
        validate_attribution_compatibility(
            origin_subsystem=value["origin_subsystem"],
            mechanism=value["mechanism"],
            side=value.get("side"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} event identity is incompatible") from exc
    replaces_symbol = value["replaces_symbol"]
    if replaces_symbol is not None and (not isinstance(replaces_symbol, str) or not replaces_symbol):
        raise ValueError(f"{label} replacement identity is malformed")
    for name in ("industry_at_entry", "industry_manifest_sha256"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError(f"{label} event identity is incomplete")
    if not _SHA256.fullmatch(value["industry_manifest_sha256"]):
        raise ValueError(f"{label} industry manifest identity is malformed")
    grant_id = value["grant_id"]
    epoch_id = value["epoch_id"]
    if not isinstance(grant_id, str) or (grant_id and not _GRANT_ID.fullmatch(grant_id)):
        raise ValueError(f"{label} grant identity is malformed")
    if not isinstance(epoch_id, str) or (epoch_id and not _EPOCH_ID.fullmatch(epoch_id)):
        raise ValueError(f"{label} epoch identity is malformed")
    if epoch_id and not grant_id:
        raise ValueError(f"{label} epoch identity lacks a grant")


@dataclass(slots=True)
class _ControlContext:
    expected_config: SystemConfig
    expected_config_sha256: str
    account: AccountState
    sessions: tuple[str, ...]
    trace_rows: list[Any]
    current_digests: list[Any]
    advertised_legacy: list[Any]
    ledger_rows: list[Any]
    ledger_orders: dict[str, AccountOrder]
    ledger_orders_by_event: dict[str, list[AccountOrder]]
    strategic_epochs_by_id: dict[str, StrategicEpoch]
    traced_order_sessions: dict[str, list[str]] = field(default_factory=dict)


def _validated_control_account(
    result: Mapping[str, Any],
    *,
    expected_code_sha256: str,
) -> AccountState:
    account_value = result.get("final_account")
    if not isinstance(account_value, Mapping):
        raise ValueError("engine control plane is missing final account")
    if account_value.get("schema_version") != ACCOUNT_SCHEMA_VERSION:
        raise ValueError("account schema differs from the compiled production schema")
    if account_value.get("code_hash") != expected_code_sha256:
        raise ValueError("account code hash differs from compiled production source")
    try:
        account = account_from_dict(account_value)
    except RuntimeError as exc:
        message = str(exc)
        if any(token in message for token in ("attribution", "originating BUY", "lot", "event_id")):
            raise ValueError(f"account event identity validation failed: {message}") from exc
        raise ValueError(f"account control validation failed: {message}") from exc
    return account


def _validated_control_rows(
    result: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    expected_sessions: Sequence[str],
    expected_config_sha256: str,
    attribution: Mapping[str, Any] | None,
) -> tuple[tuple[str, ...], list[Any], list[Any], list[Any], list[Any]]:
    if result.get("effective_config_sha256") != expected_config_sha256:
        raise ValueError("engine effective config differs from control-plane binding")
    if result.get("start") != economic_start or result.get("end") != economic_end:
        raise ValueError("engine control-plane interval differs from the exact cell")
    sessions = tuple(expected_sessions)
    if not sessions or sessions[0] != economic_start or sessions[-1] != economic_end:
        raise ValueError("verified control-plane sessions differ from the exact interval")
    trace_rows = result.get("decision_trace")
    current_digests = result.get("decision_digests")
    advertised_legacy = result.get("legacy_decision_digests")
    if not isinstance(trace_rows, list) or not isinstance(current_digests, list):
        raise ValueError("decision trace/digest evidence is missing")
    if not isinstance(advertised_legacy, list):
        raise ValueError("legacy decision digest evidence is missing")
    if not (len(trace_rows) == len(current_digests) == len(advertised_legacy) == len(sessions)):
        raise ValueError("decision trace/digest coverage differs from verified sessions")
    selected_attribution = result.get("attribution") if attribution is None else attribution
    if not isinstance(selected_attribution, Mapping):
        raise ValueError("decision control plane is missing its daily ledger")
    ledger_rows = selected_attribution.get("daily_ledger")
    if not isinstance(ledger_rows, list) or len(ledger_rows) != len(sessions):
        raise ValueError("decision control plane daily ledger coverage differs")
    return sessions, trace_rows, current_digests, advertised_legacy, ledger_rows


def _control_context(
    result: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    expected_sessions: Sequence[str],
    expected_config: SystemConfig,
    expected_code_sha256: str,
    attribution: Mapping[str, Any] | None,
) -> _ControlContext:
    if not isinstance(expected_config, SystemConfig):
        raise ValueError("engine control plane requires a trusted effective config")
    expected_config_sha256 = config_fingerprint(expected_config)
    account = _validated_control_account(
        result,
        expected_code_sha256=expected_code_sha256,
    )
    sessions, trace_rows, current_digests, advertised_legacy, ledger_rows = _validated_control_rows(
        result,
        economic_start=economic_start,
        economic_end=economic_end,
        expected_sessions=expected_sessions,
        expected_config_sha256=expected_config_sha256,
        attribution=attribution,
    )
    ledger_orders = {item.order_id: item for item in account.order_ledger}
    ledger_orders_by_event: dict[str, list[AccountOrder]] = {}
    for ledger_order in account.order_ledger:
        ledger_orders_by_event.setdefault(ledger_order.event_id, []).append(ledger_order)
    return _ControlContext(
        expected_config=expected_config,
        expected_config_sha256=expected_config_sha256,
        account=account,
        sessions=sessions,
        trace_rows=trace_rows,
        current_digests=current_digests,
        advertised_legacy=advertised_legacy,
        ledger_rows=ledger_rows,
        ledger_orders=ledger_orders,
        ledger_orders_by_event=ledger_orders_by_event,
        strategic_epochs_by_id={epoch.epoch_id: epoch for epoch in account.strategic_epochs},
    )


def _validate_target_origin(
    ctx: _ControlContext,
    *,
    target: Mapping[str, Any],
    symbol: str,
    session: str,
    event_signal_date: str,
    event_weight_hex: str,
    event_target_weight: float,
) -> None:
    durable_candidates = ctx.ledger_orders_by_event.get(str(target["event_id"]), [])
    if durable_candidates:
        matched_durable = any(
            durable.symbol == symbol
            and durable.lifecycle == target["lifecycle"]
            and durable.reduction_policy == target["reduction_policy"]
            and durable.signal_date == event_signal_date
            and float(durable.target_weight).hex() == event_weight_hex
            and all(getattr(durable, name) == target[name] for name in _IDENTITY_FIELDS)
            for durable in durable_candidates
        )
        if not matched_durable:
            raise ValueError("decision target event identity differs from its durable origin")
        return
    epoch_id = str(target["epoch_id"])
    if epoch_id:
        epoch = ctx.strategic_epochs_by_id.get(epoch_id)
        if (
            epoch is None
            or epoch.grant_id != target["grant_id"]
            or epoch.owner_symbol != symbol
            or session < epoch.opened_session
            or bool(epoch.closed_session and session > epoch.closed_session)
        ):
            raise ValueError(
                "decision target strategic ownership identity differs from its durable epoch"
            )
    if event_signal_date != session:
        raise ValueError("decision target event signal date has no durable origin")
    expected_event = derive_attribution_event_id(
        signal_date=event_signal_date,
        symbol=symbol,
        target_weight=event_target_weight,
        lifecycle=str(target["lifecycle"]),
        origin_lifecycle=str(target["origin_lifecycle"]),
        origin_subsystem=str(target["origin_subsystem"]),
        mechanism=str(target["mechanism"]),
        replaces_symbol=target["replaces_symbol"],
        industry_at_entry=str(target["industry_at_entry"]),
        industry_manifest_sha256=str(target["industry_manifest_sha256"]),
        reduction_policy=str(target["reduction_policy"]),
        reason_code=str(target["reason_code"]),
        exit_kind=str(target["exit_kind"]),
    )
    if target["event_id"] != expected_event:
        raise ValueError("decision target event identity differs from canonical derivation")


def _validated_target(
    ctx: _ControlContext,
    *,
    raw_target: Any,
    target_weights: Mapping[str, float],
    session: str,
) -> tuple[str, float]:
    target = _exact_mapping(raw_target, _TARGET_FIELDS, label="decision target")
    symbol = target["symbol"]
    if not isinstance(symbol, str) or not symbol or symbol in target_weights:
        raise ValueError("decision target symbols are malformed or duplicated")
    weight = _finite(target["weight"], label="decision target weight", minimum=0.0)
    try:
        Lifecycle(target["lifecycle"])
        ReductionPolicy(target["reduction_policy"])
    except (TypeError, ValueError) as exc:
        raise ValueError("decision target lifecycle/policy is malformed") from exc
    if not isinstance(target["reason_code"], str) or not isinstance(target["exit_kind"], str):
        raise ValueError("decision target reason identity is malformed")
    _validate_identity_fields(target, label="decision target")
    event_signal_date = target["event_signal_date"]
    event_weight_hex = target["event_target_weight_hex"]
    if not isinstance(event_signal_date, str) or not isinstance(event_weight_hex, str):
        raise ValueError("decision target event derivation inputs are malformed")
    try:
        event_target_weight = float.fromhex(event_weight_hex)
    except ValueError as exc:
        raise ValueError("decision target event derivation inputs are malformed") from exc
    _finite(
        event_target_weight,
        label="decision target event weight",
        minimum=0.0,
    )
    _validate_target_origin(
        ctx,
        target=target,
        symbol=symbol,
        session=session,
        event_signal_date=event_signal_date,
        event_weight_hex=event_weight_hex,
        event_target_weight=event_target_weight,
    )
    return symbol, weight


def _validated_targets(
    ctx: _ControlContext,
    *,
    targets: list[Any],
    session: str,
) -> dict[str, float]:
    target_weights: dict[str, float] = {}
    for raw_target in targets:
        symbol, weight = _validated_target(
            ctx,
            raw_target=raw_target,
            target_weights=target_weights,
            session=session,
        )
        target_weights[symbol] = weight
    return target_weights


def _validate_control_order(
    ctx: _ControlContext,
    *,
    raw_order: Any,
    session_order_ids: set[str],
    session: str,
) -> None:
    order = _exact_mapping(raw_order, _ORDER_FIELDS, label="decision order")
    order_id = order["order_id"]
    if not isinstance(order_id, str) or not order_id or order_id in session_order_ids:
        raise ValueError("decision order IDs are malformed or duplicated")
    session_order_ids.add(order_id)
    try:
        Side(order["side"])
        ReductionPolicy(order["reduction_policy"])
    except (TypeError, ValueError) as exc:
        raise ValueError("decision order side/policy is malformed") from exc
    _finite(
        order["target_weight"],
        label="decision order target weight",
        minimum=0.0,
    )
    _validate_identity_fields(order, label="decision order")
    durable = ctx.ledger_orders.get(order_id)
    if durable is None:
        raise ValueError("decision order is absent from the durable account ledger")
    comparable = {
        "order_id": durable.order_id,
        "signal_date": durable.signal_date,
        "snapshot_kind": ("ORIGIN" if durable.signal_date == session else "CARRIED_FORWARD"),
        "symbol": durable.symbol,
        "side": durable.side,
        "target_weight": round(durable.target_weight, 12),
        "reduction_policy": durable.reduction_policy,
        "reason_code": durable.reason_code,
        "exit_kind": durable.exit_kind,
        **{name: getattr(durable, name) for name in _IDENTITY_FIELDS},
    }
    if dict(order) != comparable:
        raise ValueError("decision order differs from durable account event identity")
    ctx.traced_order_sessions.setdefault(order_id, []).append(session)


def _validated_trace(
    ctx: _ControlContext,
    *,
    index: int,
    session: str,
    raw_trace: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any], float, float, dict[str, float]]:
    trace = _exact_mapping(raw_trace, _TRACE_FIELDS, label=f"decision trace {index}")
    if trace["schema"] != "uquant.decision-control-plane.v2" or trace["date"] != session:
        raise ValueError("decision trace schema/date differs from verified sessions")
    if trace["effective_config_sha256"] != ctx.expected_config_sha256:
        raise ValueError("decision trace effective config differs")
    try:
        Opportunity(trace["opportunity"])
    except (TypeError, ValueError) as exc:
        raise ValueError("decision trace opportunity is malformed") from exc
    risk = _exact_mapping(trace["risk"], _RISK_FIELDS, label="decision trace risk")
    try:
        Risk(risk["state"])
    except (TypeError, ValueError) as exc:
        raise ValueError("decision trace risk state is malformed") from exc
    risk_cap = _finite(
        risk["target_gross_cap"],
        label="decision trace risk cap",
        minimum=0.0,
    )
    system_cap = _finite(
        risk["system_gross_cap"],
        label="decision trace system cap",
        minimum=0.0,
    )
    if system_cap != canonical_control_float(ctx.expected_config.max_gross):
        raise ValueError("decision trace system gross cap differs from trusted config")
    targets = trace["targets"]
    orders = trace["orders"]
    if not isinstance(targets, list) or not isinstance(orders, list):
        raise ValueError("decision trace targets/orders are malformed")
    target_weights = _validated_targets(ctx, targets=targets, session=session)
    session_order_ids: set[str] = set()
    for raw_order in orders:
        _validate_control_order(
            ctx,
            raw_order=raw_order,
            session_order_ids=session_order_ids,
            session=session,
        )
    return trace, risk, risk_cap, system_cap, target_weights


def _validate_control_daily_ledger(
    *,
    raw_ledger: Any,
    session: str,
    trace: Mapping[str, Any],
    risk: Mapping[str, Any],
    risk_cap: float,
    system_cap: float,
    target_weights: Mapping[str, float],
    target_gross: float,
) -> None:
    ledger = _exact_mapping(
        raw_ledger,
        {
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
        },
        label="decision daily ledger",
    )
    ledger_target_weights = ledger["target_weights"]
    if (
        ledger["date"] != session
        or not isinstance(ledger_target_weights, Mapping)
        or set(ledger_target_weights) != set(target_weights)
        or ledger["risk_state"] != risk["state"]
        or ledger["opportunity"] != trace["opportunity"]
    ):
        raise ValueError("decision trace differs from daily ledger state/targets")
    for symbol, expected_weight in target_weights.items():
        if not math.isclose(
            _finite(
                ledger_target_weights[symbol],
                label=f"decision daily ledger target/{symbol}",
                minimum=0.0,
            ),
            expected_weight,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("decision trace differs from daily ledger state/targets")
    caps = ledger["caps"]
    if not isinstance(caps, Mapping) or caps != {
        "risk_gross": risk_cap,
        "system_gross": system_cap,
    }:
        raise ValueError("decision trace differs from daily ledger caps")
    if not math.isclose(float(ledger["target_gross"]), target_gross, abs_tol=1e-12):
        raise ValueError("decision trace differs from daily ledger target gross")


def _validated_decision_digest(
    ctx: _ControlContext,
    *,
    index: int,
    session: str,
    trace: Mapping[str, Any],
) -> str:
    current = ctx.current_digests[index]
    if not isinstance(current, str) or current != _sha256_json(trace):
        raise ValueError(f"decision digest does not recompute at {session}")
    legacy_digest = _sha256_json(legacy_decision_payload(trace))
    advertised = ctx.advertised_legacy[index]
    if not isinstance(advertised, str) or advertised != legacy_digest:
        raise ValueError(f"legacy decision digest does not recompute at {session}")
    return legacy_digest


def _validated_control_session(
    ctx: _ControlContext,
    *,
    index: int,
    session: str,
    raw_trace: Any,
    raw_ledger: Any,
) -> str:
    trace, risk, risk_cap, system_cap, target_weights = _validated_trace(
        ctx,
        index=index,
        session=session,
        raw_trace=raw_trace,
    )
    target_gross = _finite(
        trace["target_gross"],
        label="decision trace target gross",
        minimum=0.0,
    )
    if not _rounded_sum_matches(target_gross, tuple(target_weights.values())):
        raise ValueError("decision trace target gross does not recompute")
    _validate_control_daily_ledger(
        raw_ledger=raw_ledger,
        session=session,
        trace=trace,
        risk=risk,
        risk_cap=risk_cap,
        system_cap=system_cap,
        target_weights=target_weights,
        target_gross=target_gross,
    )
    return _validated_decision_digest(
        ctx,
        index=index,
        session=session,
        trace=trace,
    )


def _validate_durable_order_lifecycles(ctx: _ControlContext) -> None:
    session_index = {session: index for index, session in enumerate(ctx.sessions)}
    terminal_statuses = {
        OrderStatus.FILLED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REPLACED.value,
    }
    active_ids = {
        order_id for order_id, durable in ctx.ledger_orders.items() if durable.status not in terminal_statuses
    }
    pending_ids = {order.order_id for order in ctx.account.pending_orders}
    if active_ids != pending_ids:
        raise ValueError("durable active order lifecycle differs from final pending-order state")
    prior_physical_order_by_event: dict[tuple[str, str, str, str, str], AccountOrder] = {}
    for order_id, durable in ctx.ledger_orders.items():
        chain_identity = (
            durable.event_id,
            durable.symbol,
            durable.side,
            durable.grant_id,
            durable.epoch_id,
        )
        prior_physical_order = prior_physical_order_by_event.get(chain_identity)
        partial_remainder_origin = bool(
            prior_physical_order is not None
            and durable.grant_id
            and prior_physical_order.status == OrderStatus.CANCELLED.value
            and prior_physical_order.cancel_reason == "strategic partial remainder replaced"
            and prior_physical_order.last_event == "PARTIAL_REMAINDER_RELEASED"
        )
        origin_session = (
            prior_physical_order.last_update_date
            if partial_remainder_origin and prior_physical_order is not None
            else durable.signal_date
        )
        origin_index = session_index.get(origin_session)
        if origin_index is None:
            raise ValueError(f"durable account order {order_id} lacks an in-window decision origin")
        end_index = len(ctx.sessions)
        if durable.status in terminal_statuses:
            terminal_index = session_index.get(durable.last_update_date)
            if terminal_index is None or terminal_index <= origin_index:
                raise ValueError(f"durable account order {order_id} has an invalid terminal lifecycle")
            end_index = terminal_index
        expected_occurrences = tuple(ctx.sessions[origin_index:end_index])
        observed_occurrences = tuple(ctx.traced_order_sessions.get(order_id, ()))
        if observed_occurrences != expected_occurrences:
            raise ValueError(
                f"durable account order {order_id} decision snapshot lifecycle differs: "
                f"expected {expected_occurrences}, observed {observed_occurrences}"
            )
        prior_physical_order_by_event[chain_identity] = durable


def validate_engine_control_plane(
    result: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    expected_sessions: Sequence[str],
    expected_config: SystemConfig,
    expected_code_sha256: str,
    attribution: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Validate current control evidence and return exact reconstructed legacy digests."""
    ctx = _control_context(
        result,
        economic_start=economic_start,
        economic_end=economic_end,
        expected_sessions=expected_sessions,
        expected_config=expected_config,
        expected_code_sha256=expected_code_sha256,
        attribution=attribution,
    )
    reconstructed_legacy = [
        _validated_control_session(
            ctx,
            index=index,
            session=session,
            raw_trace=raw_trace,
            raw_ledger=raw_ledger,
        )
        for index, (session, raw_trace, raw_ledger) in enumerate(
            zip(ctx.sessions, ctx.trace_rows, ctx.ledger_rows, strict=True)
        )
    ]
    _validate_durable_order_lifecycles(ctx)
    return tuple(reconstructed_legacy)


_finite = _finite_control_value
