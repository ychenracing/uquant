"""Strict decision/account control-plane validation for matrix artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..account import account_from_dict
from ..types import (
    ACCOUNT_SCHEMA_VERSION,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    ReductionPolicy,
    Risk,
    Side,
    derive_attribution_event_id,
    validate_attribution_compatibility,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^evt_[0-9a-f]{64}$")
_TRACE_FIELDS = {
    "schema",
    "date",
    "opportunity",
    "risk",
    "target_gross",
    "targets",
    "orders",
    "effective_config_sha256",
}
_RISK_FIELDS = {
    "state",
    "target_gross_cap",
    "system_gross_cap",
}
_TARGET_FIELDS = {
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
}
_ORDER_FIELDS = {
    "order_id",
    "signal_date",
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
}
_IDENTITY_FIELDS = {
    "event_id",
    "origin_subsystem",
    "mechanism",
    "origin_lifecycle",
    "replaces_symbol",
    "industry_at_entry",
    "industry_manifest_sha256",
}


def _exact_mapping(value: Any, fields: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} fields differ from the exact control schema")
    return value


def _finite(value: Any, *, label: str, minimum: float | None = None) -> float:
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
    binary_summation_bound = math.ulp(
        max(1.0, abs(total), abs(component_sum))
    ) * (len(components) + 1)
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
    if replaces_symbol is not None and (
        not isinstance(replaces_symbol, str) or not replaces_symbol
    ):
        raise ValueError(f"{label} replacement identity is malformed")
    for name in ("industry_at_entry", "industry_manifest_sha256"):
        if not isinstance(value[name], str) or not value[name]:
            raise ValueError(f"{label} event identity is incomplete")
    if not _SHA256.fullmatch(value["industry_manifest_sha256"]):
        raise ValueError(f"{label} industry manifest identity is malformed")


def validate_engine_control_plane(
    result: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    expected_sessions: Sequence[str],
    expected_config_sha256: str,
    expected_code_sha256: str,
    attribution: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Validate current control evidence and return exact reconstructed legacy digests."""

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
        if any(
            token in message
            for token in ("attribution", "originating BUY", "lot", "event_id")
        ):
            raise ValueError(f"account event identity validation failed: {message}") from exc
        raise ValueError(f"account control validation failed: {message}") from exc

    if result.get("effective_config_sha256") != expected_config_sha256:
        raise ValueError("engine effective config differs from control-plane binding")
    if result.get("start") != economic_start or result.get("end") != economic_end:
        raise ValueError("engine control-plane interval differs from the exact cell")
    sessions = tuple(expected_sessions)
    if not sessions or sessions[0] != economic_start or sessions[-1] != economic_end:
        raise ValueError("verified control-plane sessions differ from the exact interval")

    trace_value = result.get("decision_trace")
    current_digests = result.get("decision_digests")
    advertised_legacy = result.get("legacy_decision_digests")
    if not isinstance(trace_value, list) or not isinstance(current_digests, list):
        raise ValueError("decision trace/digest evidence is missing")
    if not isinstance(advertised_legacy, list):
        raise ValueError("legacy decision digest evidence is missing")
    if not (len(trace_value) == len(current_digests) == len(advertised_legacy) == len(sessions)):
        raise ValueError("decision trace/digest coverage differs from verified sessions")

    selected_attribution = result.get("attribution") if attribution is None else attribution
    if not isinstance(selected_attribution, Mapping):
        raise ValueError("decision control plane is missing its daily ledger")
    ledger_value = selected_attribution.get("daily_ledger")
    if not isinstance(ledger_value, list) or len(ledger_value) != len(sessions):
        raise ValueError("decision control plane daily ledger coverage differs")

    ledger_orders = {item.order_id: item for item in account.order_ledger}
    ledger_orders_by_event: dict[str, list[Any]] = {}
    for ledger_order in account.order_ledger:
        ledger_orders_by_event.setdefault(ledger_order.event_id, []).append(ledger_order)
    traced_order_ids: set[str] = set()
    reconstructed_legacy: list[str] = []
    for index, (session, raw_trace, raw_ledger) in enumerate(
        zip(sessions, trace_value, ledger_value, strict=True)
    ):
        trace = _exact_mapping(
            raw_trace,
            _TRACE_FIELDS,
            label=f"decision trace {index}",
        )
        if trace["schema"] != "uquant.decision-control-plane.v2" or trace["date"] != session:
            raise ValueError("decision trace schema/date differs from verified sessions")
        if trace["effective_config_sha256"] != expected_config_sha256:
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
        risk_cap = _finite(risk["target_gross_cap"], label="decision trace risk cap", minimum=0.0)
        system_cap = _finite(
            risk["system_gross_cap"],
            label="decision trace system cap",
            minimum=0.0,
        )
        targets_value = trace["targets"]
        orders_value = trace["orders"]
        if not isinstance(targets_value, list) or not isinstance(orders_value, list):
            raise ValueError("decision trace targets/orders are malformed")
        target_weights: dict[str, float] = {}
        for raw_target in targets_value:
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
            if not isinstance(target["reason_code"], str) or not isinstance(
                target["exit_kind"], str
            ):
                raise ValueError("decision target reason identity is malformed")
            _validate_identity_fields(target, label="decision target")
            event_signal_date = target["event_signal_date"]
            event_weight_hex = target["event_target_weight_hex"]
            if not isinstance(event_signal_date, str) or not isinstance(
                event_weight_hex, str
            ):
                raise ValueError("decision target event derivation inputs are malformed")
            try:
                event_target_weight = float.fromhex(event_weight_hex)
            except ValueError as exc:
                raise ValueError(
                    "decision target event derivation inputs are malformed"
                ) from exc
            _finite(
                event_target_weight,
                label="decision target event weight",
                minimum=0.0,
            )
            durable_candidates = ledger_orders_by_event.get(str(target["event_id"]), [])
            if durable_candidates:
                matched_durable = any(
                    durable.symbol == symbol
                    and durable.lifecycle == target["lifecycle"]
                    and durable.reduction_policy == target["reduction_policy"]
                    and durable.signal_date == event_signal_date
                    and float(durable.target_weight).hex() == event_weight_hex
                    and all(
                        getattr(durable, name) == target[name]
                        for name in _IDENTITY_FIELDS
                    )
                    for durable in durable_candidates
                )
                if not matched_durable:
                    raise ValueError(
                        "decision target event identity differs from its durable origin"
                    )
            else:
                if event_signal_date != session:
                    raise ValueError(
                        "decision target event signal date has no durable origin"
                    )
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
                    industry_manifest_sha256=str(
                        target["industry_manifest_sha256"]
                    ),
                    reduction_policy=str(target["reduction_policy"]),
                    reason_code=str(target["reason_code"]),
                    exit_kind=str(target["exit_kind"]),
                )
                if target["event_id"] != expected_event:
                    raise ValueError(
                        "decision target event identity differs from canonical derivation"
                    )
            target_weights[symbol] = weight
        session_order_ids: set[str] = set()
        for raw_order in orders_value:
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
            _finite(order["target_weight"], label="decision order target weight", minimum=0.0)
            _validate_identity_fields(order, label="decision order")
            durable = ledger_orders.get(order_id)
            if durable is None:
                raise ValueError("decision order is absent from the durable account ledger")
            comparable = {
                "order_id": durable.order_id,
                "signal_date": durable.signal_date,
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
            if order_id not in traced_order_ids:
                if durable.signal_date != session:
                    raise ValueError(
                        "durable account order lacks its exact decision-date origin"
                    )
                traced_order_ids.add(order_id)

        target_gross = _finite(
            trace["target_gross"],
            label="decision trace target gross",
            minimum=0.0,
        )
        if not _rounded_sum_matches(target_gross, tuple(target_weights.values())):
            raise ValueError("decision trace target gross does not recompute")
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

        current = current_digests[index]
        if not isinstance(current, str) or current != _sha256_json(trace):
            raise ValueError(f"decision digest does not recompute at {session}")
        legacy_digest = _sha256_json(legacy_decision_payload(trace))
        advertised = advertised_legacy[index]
        if not isinstance(advertised, str) or advertised != legacy_digest:
            raise ValueError(f"legacy decision digest does not recompute at {session}")
        reconstructed_legacy.append(legacy_digest)

    missing_origins = sorted(set(ledger_orders) - traced_order_ids)
    if missing_origins:
        raise ValueError(
            "durable account order ledger contains IDs without decision origins: "
            + ", ".join(missing_origins)
        )

    return tuple(reconstructed_legacy)
