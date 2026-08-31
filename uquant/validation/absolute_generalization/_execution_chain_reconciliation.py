"""Private exact physical-chain validation for complete cell evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from uquant.types import ORDER_INTENT_IMMUTABLE_FIELDS

from ._metric_primitives import (
    metric_iso_session,
    metric_mapping,
    metric_number,
    metric_positive_number,
    metric_rows,
    metric_stable_ids,
    metric_text,
)
from ._physical_identity import (
    physical_fill_identity_map,
    physical_fill_identity_sha256,
)
from .metrics import (
    EpochFact,
)

_IDENTITY_FIELDS = ("event_id", "epoch_id", "grant_id", "symbol", "side")
_GRANT_QUALIFICATION_FIELDS = (
    "qualification_signature",
    "qualification_route",
    "qualification_quorum",
)


@dataclass(frozen=True, slots=True)
class _ChainIndexes:
    final_orders: Mapping[str, Mapping[str, object]]
    trace_orders: Mapping[str, tuple[str, Mapping[str, object]]]
    fills: tuple[Mapping[str, object], ...]


def _trace_order_index(
    trace: Sequence[Mapping[str, object]],
    *,
    final_orders: Mapping[str, Mapping[str, object]],
) -> dict[str, tuple[str, Mapping[str, object]]]:
    trace_orders: dict[str, tuple[str, Mapping[str, object]]] = {}
    last_order_sessions: dict[str, str] = {}
    for row in trace:
        session = metric_iso_session(row.get("session"), label="trace session")
        session_order_ids: set[str] = set()
        for order in metric_rows(row.get("orders", ()), label="trace orders"):
            if order.get("origin_subsystem") != "STRATEGIC":
                continue
            order_id = metric_text(order.get("order_id"), label="trace order identity")
            if order_id in session_order_ids:
                raise ValueError("absolute generalization duplicate trace order identity")
            session_order_ids.add(order_id)
            prior = trace_orders.get(order_id)
            if prior is not None:
                if session <= last_order_sessions[order_id]:
                    raise ValueError("absolute generalization duplicate trace order identity")
                _validate_order_immutable_intent(prior[1], order)
                last_order_sessions[order_id] = session
                continue
            trace_orders[order_id] = (session, order)
            last_order_sessions[order_id] = session
    if set(trace_orders) - set(final_orders):
        raise ValueError("absolute generalization orphan trace order identity")
    if any(
        final_orders[order_id].get("origin_subsystem") != "STRATEGIC"
        for order_id in trace_orders
    ):
        raise ValueError("absolute generalization strategic order origin differs")
    return trace_orders


def _validate_order_immutable_intent(
    final_order: Mapping[str, object], trace_order: Mapping[str, object]
) -> None:
    for field in ORDER_INTENT_IMMUTABLE_FIELDS:
        final_value = final_order.get(field)
        trace_value = trace_order.get(field)
        if (
            field not in final_order
            or field not in trace_order
            or type(final_value) is not type(trace_value)
            or final_value != trace_value
        ):
            raise ValueError(f"absolute generalization strategic order {field} differs")


def _validate_strategic_orders(
    *,
    final_orders: Mapping[str, Mapping[str, object]],
    trace_orders: Mapping[str, tuple[str, Mapping[str, object]]],
    trace: Sequence[Mapping[str, object]],
) -> None:
    for order_id, final_order in final_orders.items():
        if final_order.get("origin_subsystem") != "STRATEGIC":
            continue
        traced = trace_orders.get(order_id)
        if traced is None:
            raise ValueError("absolute generalization strategic order identity differs")
        session, trace_order = traced
        _validate_order_immutable_intent(final_order, trace_order)
        side = metric_text(trace_order.get("side"), label="trace order side")
        order_target_weight = metric_number(
            trace_order.get("target_weight"),
            label="trace order target weight",
            minimum=0.0,
        )
        if (
            metric_iso_session(trace_order.get("signal_date"), label="order signal session")
            != session
            or side not in {"BUY", "SELL"}
            or order_target_weight > 1.0
            or (side == "BUY" and order_target_weight <= 0.0)
        ):
            raise ValueError("absolute generalization strategic order session differs")
        targets = [
            target
            for row in trace
            if row.get("session") == session
            for target in metric_rows(row.get("targets", ()), label="trace targets")
            if target.get("origin_subsystem") == "STRATEGIC"
            and all(
                target.get(field, "") == trace_order.get(field, "")
                for field in _IDENTITY_FIELDS[:-1]
            )
            and metric_number(
                target.get("weight"),
                label="trace target weight",
                minimum=0.0,
            )
            == order_target_weight
        ]
        if not targets:
            raise ValueError("absolute generalization order target identity differs")
        if len(targets) != 1:
            raise ValueError("absolute generalization duplicate trace target identity")


def _validate_fill_order_links(indexes: _ChainIndexes) -> None:
    for fill in indexes.fills:
        order_id = metric_text(fill.get("order_id"), label="fill order")
        order = indexes.final_orders.get(order_id)
        if order is None:
            raise ValueError("absolute generalization fill order identity differs")
        for field in _IDENTITY_FIELDS:
            fill_value = metric_text(fill.get(field, ""), label=f"fill {field}", empty=True)
            order_value = metric_text(order.get(field, ""), label=f"order {field}", empty=True)
            if fill_value != order_value:
                raise ValueError(f"absolute generalization fill {field} identity differs")
        if fill.get("origin_subsystem") != order.get("origin_subsystem"):
            raise ValueError("absolute generalization fill origin identity differs")


def _grant_qualification_observation(
    *,
    trace: Sequence[Mapping[str, object]],
    fact: EpochFact,
) -> Mapping[str, object]:
    matches: list[Mapping[str, object]] = []
    for row in trace:
        if row.get("session") != fact.qualification_session:
            continue
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw = risk.get("strategic_qualification")
        if not isinstance(raw, Mapping):
            continue
        qualification = metric_mapping(raw, label="strategic qualification")
        if (
            qualification.get("qualification_ready") is True
            and qualification.get("candidate_symbol") == fact.owner_symbol
        ):
            matches.append(qualification)
    if len(matches) != 1:
        raise ValueError("absolute generalization grant qualification provenance differs")
    return matches[0]


def _validate_grant_qualification_provenance(
    *,
    trace: Sequence[Mapping[str, object]],
    fact: EpochFact,
    grant: Mapping[str, object],
) -> None:
    qualification = _grant_qualification_observation(trace=trace, fact=fact)
    expected = {
        "qualification_signature": fact.qualification_signature,
        "qualification_route": fact.qualification_route,
        "qualification_quorum": fact.qualification_quorum,
    }
    for field in _GRANT_QUALIFICATION_FIELDS:
        if grant.get(field, "") != expected[field] or qualification.get(
            field, ""
        ) != expected[field]:
            raise ValueError(f"absolute generalization grant {field.replace('_', ' ')} differs")
    grant_evidence = metric_text(
        grant.get("qualification_evidence_sha256"),
        label="grant qualification evidence",
    )
    qualification_evidence = metric_text(
        qualification.get("qualification_evidence_sha256"),
        label="qualification evidence",
    )
    if (
        grant_evidence != qualification_evidence
        or len(grant_evidence) != 64
        or any(character not in "0123456789abcdef" for character in grant_evidence)
    ):
        raise ValueError("absolute generalization grant qualification evidence differs")


def _grant_creation(
    *,
    trace: Sequence[Mapping[str, object]],
    fact: EpochFact,
) -> tuple[Mapping[str, object], tuple[Mapping[str, object], ...]]:
    matches: list[tuple[str, Mapping[str, object], Mapping[str, object]]] = []
    for row in trace:
        risk = metric_mapping(row.get("risk", {}), label="trace risk")
        raw_grant = risk.get("strategic_grant")
        if not isinstance(raw_grant, Mapping):
            continue
        grant = metric_mapping(raw_grant, label="strategic grant")
        if grant.get("grant_id") == fact.grant_id:
            matches.append(
                (metric_iso_session(row.get("session"), label="grant trace session"), grant, risk)
            )
    created = [item for item in matches if item[0] == item[1].get("created_session")]
    if len(created) != 1:
        raise ValueError("absolute generalization grant session differs")
    _, grant, _ = created[0]
    if grant.get("candidate_symbol") != fact.owner_symbol:
        raise ValueError("absolute generalization grant candidate differs")
    if grant.get("created_session") != fact.grant_session:
        raise ValueError("absolute generalization grant session differs")
    identities = {
        (
            item.get("candidate_symbol", ""),
            item.get("created_session", ""),
            item.get("authorization_id", ""),
            item.get("previous_grant_id", ""),
            item.get("qualification_signature", ""),
            item.get("qualification_route", ""),
            item.get("qualification_quorum", ""),
            item.get("qualification_evidence_sha256", ""),
        )
        for _, item, _ in matches
    }
    if len(identities) != 1:
        raise ValueError("absolute generalization grant identity changed across trace")
    _validate_grant_qualification_provenance(trace=trace, fact=fact, grant=grant)
    return grant, tuple(risk for _, _, risk in matches)


def _validate_authorization(
    *,
    fact: EpochFact,
    grant: Mapping[str, object],
    risks: Sequence[Mapping[str, object]],
) -> None:
    authorization_id = metric_text(
        grant.get("authorization_id", ""), label="grant authorization", empty=True
    )
    if authorization_id != fact.authorization_id or grant.get("previous_grant_id", "") != fact.previous_grant_id:
        raise ValueError("absolute generalization authorization identity differs")
    if not authorization_id:
        if fact.authorization_session:
            raise ValueError("absolute generalization authorization session differs")
        return
    for risk in risks:
        rearm = metric_mapping(risk.get("strategic_cash_rearm"), label="strategic cash rearm")
        authorized = metric_iso_session(
            rearm.get("authorized_session"), label="authorization session"
        )
        if rearm.get("authorization_id") != authorization_id:
            raise ValueError("absolute generalization authorization identity differs")
        if authorized != fact.authorization_session or authorized > fact.grant_session:
            raise ValueError("absolute generalization authorization session differs")
        if rearm.get("candidate_symbol", fact.owner_symbol) != fact.owner_symbol:
            raise ValueError("absolute generalization authorization candidate differs")
        if rearm.get("consumed_grant_id", fact.grant_id) != fact.grant_id:
            raise ValueError("absolute generalization authorization grant differs")


def _validate_epoch_edge(fact: EpochFact, indexes: _ChainIndexes) -> None:
    matching = [
        fill
        for fill in indexes.fills
        if fill.get("epoch_id") == fact.epoch_id
        and fill.get("grant_id") == fact.grant_id
        and fill.get("symbol") == fact.owner_symbol
        and fill.get("side") == "BUY"
        and metric_positive_number(fill.get("shares")) > 0.0
    ]
    first_fill = min(
        matching,
        key=lambda fill: (
            metric_iso_session(fill.get("fill_date"), label="fill session"),
            physical_fill_identity_sha256(fill),
        ),
    )
    traced = indexes.trace_orders.get(metric_text(first_fill.get("order_id"), label="fill order"))
    if traced is None:
        raise ValueError("absolute generalization epoch order identity differs")
    session, order = traced
    if (
        order.get("epoch_id") != fact.epoch_id
        or order.get("grant_id") != fact.grant_id
        or order.get("symbol") != fact.owner_symbol
        or session != fact.target_session
        or session != fact.order_session
        or metric_iso_session(first_fill.get("fill_date"), label="fill session")
        != fact.fill_session
        or fact.fill_session != fact.active_session
    ):
        raise ValueError("absolute generalization epoch target/order/fill causality differs")


def validate_exact_execution_chain(
    *,
    final_account: Mapping[str, object],
    trace: Sequence[Mapping[str, object]],
    epochs: Sequence[EpochFact],
) -> None:
    """Reject non-keyed or non-causal rows in one complete cell replay."""

    final_orders = metric_stable_ids(
        metric_rows(final_account.get("order_ledger", ()), label="order ledger"),
        field="order_id",
        label="order",
    )
    fills = metric_rows(final_account.get("fills", ()), label="fill ledger")
    physical_fill_identity_map(fills)
    trace_orders = _trace_order_index(trace, final_orders=final_orders)
    _validate_strategic_orders(
        final_orders=final_orders, trace_orders=trace_orders, trace=trace
    )
    indexes = _ChainIndexes(final_orders=final_orders, trace_orders=trace_orders, fills=fills)
    _validate_fill_order_links(indexes)
    for fact in epochs:
        grant, risks = _grant_creation(trace=trace, fact=fact)
        _validate_authorization(fact=fact, grant=grant, risks=risks)
        _validate_epoch_edge(fact, indexes)


__all__ = ("validate_exact_execution_chain",)
