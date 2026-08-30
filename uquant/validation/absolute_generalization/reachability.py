"""Fail-closed projections of production strategic reachability facts."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from types import MappingProxyType
from typing import cast

from uquant.account.codec import account_from_dict
from uquant.config import SystemConfig
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.models.decision import LeaderScore, RiskAssessment, Target
from uquant.models.strategic_epoch import (
    StrategicEpoch,
    validate_strategic_epoch,
)
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicQualificationObservation,
    validate_strategic_grant,
    validate_strategic_qualification,
)
from uquant.models.strategic_universe import StrategicUniverseRoles
from uquant.models.trading import (
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    Fill,
    order_intent_metadata,
)
from uquant.portfolio.strategic.authority import (
    assess_strategic_capital_authority,
    normalize_orphan_strategic_capital_residue,
)
from uquant.portfolio.strategic.quorum import route_consistent_owner_quality
from uquant.portfolio.strategic.rearm import flat_book_capital_repair_requirement
from uquant.portfolio.strategic.rearm_predicates import flat_book_repair_predicates
from uquant.types import AccountState

from ._physical_identity import validate_physical_execution_identities
from ._reachability_graph import (
    StateKey as _StateKey,
)
from ._reachability_graph import (
    maximum_consecutive_component_sessions,
    strong_components,
)
from ._reachability_recovery import (
    finish_recovery_analysis,
    validate_recovery_chain,
)
from .replay import AbsoluteGeneralizationReplayPayload


@dataclass(frozen=True, slots=True)
class HealthProjection:
    """Finite literal result of one production health predicate projection."""

    healthy: bool
    rejection_reasons: tuple[str, ...]
    predicate_results: tuple[tuple[str, bool], ...]
    persisted_damage_level: int
    repair_target_level: int
    required_healthy_sessions: int


@dataclass(frozen=True, slots=True)
class TerminalSccAnalysis:
    """Deterministic SCC result over consecutive observed production facts."""

    passed: bool
    bounded: bool
    maximum_terminal_zero_strategic_target_scc_sessions: int
    terminal_scc_violation_count: int
    no_positive_strategic_target_exit_count: int
    state_count: int
    edge_count: int
    state_transition_digest: str


@dataclass(frozen=True, slots=True)
class FailedGrantRecoveryAnalysis:
    """Exact failed-grant successor and bounded retry evidence."""

    observed: bool
    passed: bool
    first_candidate: str
    second_candidate: str
    first_grant_id: str
    second_grant_id: str
    first_epoch_id: str
    second_epoch_id: str
    healthy_retry_sessions: int
    previous_grant_reconciled: bool
    previous_epoch_reconciled: bool
    authorization_rotated: bool
    outlet_reconciled: bool
    duplicate_submission_count: int


_STATE_FIELDS = frozenset(
    {
        "active_epoch_state",
        "account_payload",
        "capital_budget_level",
        "cfg",
        "flat_all_cash",
        "grant_state",
        "leaders",
        "opportunity_state",
        "outlet_evidence",
        "pending_execution",
        "protected_authority",
        "qualification_quorum",
        "qualification_ready",
        "qualification_route",
        "recovery_authority",
        "reference_available",
        "repair_status",
        "restore_authority",
        "risk",
        "risk_state",
        "snapshots",
        "universe",
        "unknown_execution",
    }
)
_ROW_FIELDS = frozenset({"edge_kind", "phase", "session", "state"})
_PHASES = frozenset({"POST_OPEN", "POST_DECISION"})
_REPAIR_STATUSES = frozenset(
    {"BLOCKED", "ACCUMULATING", "READY", "CONSUMED", "RESET"}
)
_RISK_STATES = frozenset({"NORMAL", "CAUTION", "RISK_OFF", "CRISIS"})
_OPPORTUNITY_STATES = frozenset(
    {"STRONG_TREND", "TREND", "RECOVERY", "CHOPPY", "WEAK"}
)
_GRANT_STATES = frozenset(
    {
        "NONE",
        "QUALIFIED",
        "PENDING_EXECUTION",
        "PARTIALLY_FILLED",
        "ACTIVE",
        "COMPLETED",
        "EXPIRED",
        "CANCELLED",
    }
)
_EPOCH_STATES = frozenset(
    {"NONE", "QUALIFIED", "PROBE", "CORE", "ACTIVE", "CLOSED", "EXPIRED"}
)
_MAX_REACHABILITY_ROWS = 20_000
_MAX_REACHABILITY_STATES = 4_096
_ORDER_STATUS_EVENTS = MappingProxyType({
    "SUBMITTED": frozenset(
        {
            "AWAITING_HANDOFF_SELL",
            "CANCEL_REQUESTED",
            "CAPACITY_OR_CASH_BLOCKED",
            "INSUFFICIENT_HISTORY",
            "LIMIT_BLOCKED",
            "MISSING_OR_SUSPENDED",
            "POSITION_CAP_BLOCKED",
            "SUBMITTED",
            "WAITING_NEXT_OPEN",
        }
    ),
    "OPEN": frozenset(
        {
            "AWAITING_HANDOFF_SELL",
            "CANCEL_REQUESTED",
            "CAPACITY_OR_CASH_BLOCKED",
            "INSUFFICIENT_HISTORY",
            "LIMIT_BLOCKED",
            "MISSING_OR_SUSPENDED",
            "POSITION_CAP_BLOCKED",
            "WAITING_NEXT_OPEN",
        }
    ),
    "PARTIALLY_FILLED": frozenset(
        {"BROKER_FILL", "CANCEL_REQUESTED", "FILL", "PARTIAL_REMAINDER_RELEASED"}
    ),
    "FILLED": frozenset({"BROKER_FILL", "FILL", "FILLED"}),
    "CANCELLED": frozenset(
        {
            "BROKER_CANCELLED",
            "CANCELLED",
            "LATE_FILL_SUPPRESSED_RETRY",
            "PARTIAL_REMAINDER_RELEASED",
            "ZERO_REQUEST",
        }
    ),
    "REPLACED": frozenset({"REPLACED"}),
})


def _repair_predicate_projection(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    cfg: SystemConfig,
) -> tuple[tuple[tuple[str, bool], ...], tuple[str, ...]]:
    copied = deepcopy(account)
    initial_authority = assess_strategic_capital_authority(copied)
    normalized_orphans = normalize_orphan_strategic_capital_residue(copied)
    authority = assess_strategic_capital_authority(copied)
    predicates, reasons = flat_book_repair_predicates(
        account=copied,
        risk=risk,
        universe=universe,
        cfg=cfg,
        authority=authority,
        initial_authority=initial_authority,
        normalized_orphans=normalized_orphans,
    )
    return (
        tuple((predicate.code, predicate.passed) for predicate in predicates),
        tuple(reasons),
    )


def project_flat_book_repair_health(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    cfg: SystemConfig,
) -> HealthProjection:
    """Project the production account-repair predicates without advancing state."""

    predicates, reasons = _repair_predicate_projection(
        account=account,
        risk=risk,
        universe=universe,
        cfg=cfg,
    )
    level = account.capital_budget_level
    if type(level) is not int or level not in {1, 2, 3, 4}:
        return HealthProjection(
            healthy=False,
            rejection_reasons=(*reasons, "NO_DAMAGED_CAPITAL_BUDGET"),
            predicate_results=predicates,
            persisted_damage_level=0,
            repair_target_level=0,
            required_healthy_sessions=0,
        )
    target_level, required = flat_book_capital_repair_requirement(level)
    return HealthProjection(
        healthy=not reasons,
        rejection_reasons=reasons,
        predicate_results=predicates,
        persisted_damage_level=level,
        repair_target_level=target_level,
        required_healthy_sessions=required,
    )


def project_qualification_opportunity_health(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    observation: StrategicQualificationObservation,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    cfg: SystemConfig,
) -> HealthProjection:
    """Project a safe qualification opportunity independently of repair blocking."""

    predicates, _reasons = _repair_predicate_projection(
        account=account,
        risk=risk,
        universe=universe,
        cfg=cfg,
    )
    shared = tuple(
        (code, passed)
        for code, passed in predicates
        if code != "DEPLOYMENT_BLOCK_REPAIRABLE"
    )
    failures = [code for code, passed in shared if not passed]
    try:
        validate_strategic_qualification(observation)
    except (TypeError, ValueError):
        failures.append("QUALIFICATION_MALFORMED")
    route_quality = route_consistent_owner_quality(
        symbol=observation.candidate_symbol,
        qualification_route=observation.qualification_route,
        quorum_route=observation.qualification_quorum,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
        cfg=cfg,
    )
    qualification_results = (
        ("QUALIFICATION_READY", observation.qualification_ready),
        ("ROUTE_CONSISTENT_OWNER_QUALITY", route_quality),
        ("CANDIDATE_TRADABLE", observation.candidate_symbol in universe.tradable_symbols),
        (
            "QUALIFICATION_REFERENCES_AVAILABLE",
            not set(universe.qualification_reference_symbols)
            .difference(universe.available_symbols),
        ),
    )
    failures.extend(code for code, passed in qualification_results if not passed)
    return HealthProjection(
        healthy=not failures,
        rejection_reasons=tuple(dict.fromkeys(failures)),
        predicate_results=(*shared, *qualification_results),
        persisted_damage_level=max(account.capital_budget_level, 0),
        repair_target_level=max(account.capital_budget_level - 1, 0),
        required_healthy_sessions=0,
    )


def is_positive_strategic_outlet(
    *,
    target: Target,
    grant: StrategicGrantIntent | None,
    epoch: StrategicEpoch | None,
    orders: Sequence[AccountOrder],
    fills: Sequence[Fill],
) -> bool:
    """Return whether a positive allocator target has a realized strategic chain."""

    if not _valid_outlet_identity(target=target, grant=grant, epoch=epoch):
        return False
    if type(grant) is not StrategicGrantIntent or type(epoch) is not StrategicEpoch:
        raise ValueError("strategic outlet grant or epoch runtime type differs")
    validate_strategic_grant(grant)
    validate_strategic_epoch(epoch)
    _validate_physical_ids(orders=orders, fills=fills)
    order_by_id = _matching_strategic_orders(target, grant, orders)
    return _has_matching_strategic_fill(target, order_by_id, fills)


def _valid_outlet_identity(
    *,
    target: Target,
    grant: StrategicGrantIntent | None,
    epoch: StrategicEpoch | None,
) -> bool:
    return bool(
        type(target) is Target
        and math.isfinite(target.weight)
        and target.weight > 0.0
        and target.origin_subsystem == "STRATEGIC"
        and grant is not None
        and epoch is not None
        and target.symbol == grant.candidate_symbol == epoch.owner_symbol
        and target.grant_id == grant.grant_id == epoch.grant_id
        and target.epoch_id == grant.epoch_id == epoch.epoch_id
    )


def _validate_physical_ids(
    *, orders: Sequence[AccountOrder], fills: Sequence[Fill]
) -> None:
    validate_physical_execution_identities(orders=orders, fills=fills)


def _matching_strategic_orders(
    target: Target,
    grant: StrategicGrantIntent,
    orders: Sequence[AccountOrder],
) -> dict[str, AccountOrder]:
    return {
        order.order_id: order
        for order in orders
        if order.symbol == target.symbol
        and order.grant_id == target.grant_id
        and order.epoch_id == target.epoch_id
        and order.origin_subsystem == "STRATEGIC"
        and order.side == "BUY"
        and order.target_weight == target.weight
        and order.event_id == target.event_id
        and order.order_id in grant.submitted_order_ids
    }


def _has_matching_strategic_fill(
    target: Target,
    order_by_id: Mapping[str, AccountOrder],
    fills: Sequence[Fill],
) -> bool:
    return any(
        fill.shares > 0
        and fill.order_id in order_by_id
        and fill.symbol == target.symbol
        and fill.grant_id == target.grant_id
        and fill.epoch_id == target.epoch_id
        and fill.origin_subsystem == "STRATEGIC"
        and fill.side == "BUY"
        and fill.event_id == order_by_id[fill.order_id].event_id
        and fill.signal_date == order_by_id[fill.order_id].signal_date
        and fill.fill_date > fill.signal_date
        for fill in fills
    )


def analyze_failed_grant_recovery(
    *,
    first_grant: StrategicGrantIntent,
    first_epoch: StrategicEpoch,
    transitions: object,
    maximum_healthy_sessions: int = 20,
) -> FailedGrantRecoveryAnalysis:
    """Validate a distinct production successor after an unfilled terminal grant."""

    if type(first_grant) is not StrategicGrantIntent or type(first_epoch) is not StrategicEpoch:
        raise ValueError("absolute reachability failed-grant runtime type differs")
    if type(transitions) not in {list, tuple} or not transitions:
        raise ValueError("absolute reachability transitions are malformed")
    rows = cast(Sequence[object], transitions)
    final = _strict_mapping(rows[-1], label="transition")
    final_state = _strict_mapping(final.get("state"), label="state")
    evidence = _strict_mapping(final_state.get("outlet_evidence"), label="outlet evidence")
    target = evidence.get("target")
    second_grant = evidence.get("grant")
    second_epoch = evidence.get("epoch")
    orders = evidence.get("orders")
    fills = evidence.get("fills")
    if (
        type(target) is not Target
        or type(second_grant) is not StrategicGrantIntent
        or type(second_epoch) is not StrategicEpoch
        or type(orders) is not tuple
        or type(fills) is not tuple
    ):
        raise ValueError("absolute reachability failed-grant successor runtime type differs")
    if type(maximum_healthy_sessions) is not int or maximum_healthy_sessions < 0:
        raise ValueError("absolute reachability failed-grant retry bound is malformed")
    chain = validate_recovery_chain(
        first_grant=first_grant,
        first_epoch=first_epoch,
        target=target,
        second_grant=second_grant,
        second_epoch=second_epoch,
        orders=orders,
        fills=fills,
    )
    if not is_positive_strategic_outlet(
        target=target,
        grant=second_grant,
        epoch=second_epoch,
        orders=orders,
        fills=fills,
    ):
        raise ValueError("absolute reachability failed-grant successor outlet is absent")
    nodes, sessions, healthy, outlets, accounts = _validated_observations(transitions)
    del nodes
    values = finish_recovery_analysis(
        first_grant=first_grant,
        first_epoch=first_epoch,
        chain=chain,
        sessions=sessions,
        healthy=healthy,
        accounts=accounts,
        final_session=final["session"],
        final_outlet=outlets[-1],
        maximum_healthy_sessions=maximum_healthy_sessions,
    )
    return FailedGrantRecoveryAnalysis(
        observed=True,
        passed=values.passed,
        first_candidate=values.first_candidate,
        second_candidate=values.second_candidate,
        first_grant_id=values.first_grant_id,
        second_grant_id=values.second_grant_id,
        first_epoch_id=values.first_epoch_id,
        second_epoch_id=values.second_epoch_id,
        healthy_retry_sessions=values.healthy_count,
        previous_grant_reconciled=True,
        previous_epoch_reconciled=True,
        authorization_rotated=True,
        outlet_reconciled=True,
        duplicate_submission_count=values.duplicate_submissions,
    )


def _strict_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"absolute reachability {label} is malformed")
    return cast(Mapping[str, object], value)


def _strict_text(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"absolute reachability {label} is malformed")
    return value


def _strict_bool(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"absolute reachability {label} is malformed")
    return value


def _outlet_from_state(state: Mapping[str, object], account: AccountState) -> bool:
    evidence = state["outlet_evidence"]
    if evidence is None:
        return False
    raw = _strict_mapping(evidence, label="outlet evidence")
    expected = {"epoch", "fills", "grant", "orders", "target"}
    if set(raw) != expected:
        raise ValueError("absolute reachability outlet evidence fields differ")
    orders = raw["orders"]
    fills = raw["fills"]
    if (
        type(raw["target"]) is not Target
        or type(raw["grant"]) is not StrategicGrantIntent
        or type(raw["epoch"]) is not StrategicEpoch
        or type(orders) is not tuple
        or type(fills) is not tuple
        or any(type(item) is not AccountOrder for item in orders)
        or any(type(item) is not Fill for item in fills)
    ):
        raise ValueError("absolute reachability outlet runtime type differs")
    outlet = is_positive_strategic_outlet(
        target=raw["target"],
        grant=cast(StrategicGrantIntent | None, raw["grant"]),
        epoch=cast(StrategicEpoch | None, raw["epoch"]),
        orders=cast(tuple[AccountOrder, ...], orders),
        fills=cast(tuple[Fill, ...], fills),
    )
    if outlet and (
        raw["grant"] != account.strategic_grant
        or raw["epoch"] not in account.strategic_epochs
        or any(order not in account.order_ledger for order in orders)
        or any(fill not in account.fills for fill in fills)
    ):
        raise ValueError("absolute reachability outlet has orphan authority")
    return outlet


def _validate_account_runtime(account: AccountState) -> None:
    ledger_ids = {order.order_id for order in account.order_ledger}
    grant = account.strategic_grant
    if grant is not None:
        submitted = set(grant.submitted_order_ids)
        acknowledged = set(grant.acknowledged_order_ids)
        if not submitted.issubset(ledger_ids):
            raise ValueError("absolute reachability grant submitted order is absent from ledger")
        if not acknowledged.issubset(submitted):
            raise ValueError("absolute reachability grant acknowledged order was not submitted")
    for order in account.order_ledger:
        allowed = _ORDER_STATUS_EVENTS.get(order.status)
        if allowed is None or order.last_event not in allowed:
            raise ValueError("absolute reachability order status/event pair is impossible")


def _validate_account_payload(value: object) -> AccountState:
    if type(value) is not AbsoluteGeneralizationReplayPayload:
        raise ValueError("absolute reachability account payload type differs")
    payload = value
    if hashlib.sha256(payload.canonical_json).hexdigest() != payload.sha256:
        raise ValueError("absolute reachability account payload digest differs")
    raw = strict_json_loads(payload.canonical_json)
    if canonical_json_bytes(raw) != payload.canonical_json:
        raise ValueError("absolute reachability account payload is not canonical")
    account_raw = _strict_mapping(raw, label="account payload")
    required_containers = {
        "fills": list,
        "flat_book_capital_repair": dict,
        "order_ledger": list,
        "pending_orders": list,
        "positions": dict,
        "strategic_epochs": list,
    }
    if any(type(account_raw.get(field)) is not expected for field, expected in required_containers.items()):
        raise ValueError("absolute reachability account payload is invalid")
    try:
        account = account_from_dict(account_raw, require_hashes=False)
    except (RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("absolute reachability account payload is invalid") from exc
    _validate_account_runtime(account)
    return account


def _validated_market_evidence(
    state: Mapping[str, object],
) -> tuple[
    SystemConfig,
    RiskAssessment,
    StrategicUniverseRoles,
    dict[str, dict[str, float]],
    dict[str, LeaderScore],
]:
    cfg = state["cfg"]
    risk = state["risk"]
    universe = state["universe"]
    snapshots = state["snapshots"]
    leaders = state["leaders"]
    if (
        not isinstance(cfg, SystemConfig)
        or type(risk) is not RiskAssessment
        or type(universe) is not StrategicUniverseRoles
        or type(snapshots) is not dict
        or type(leaders) is not dict
        or len(snapshots) > 256
        or len(leaders) > 256
    ):
        raise ValueError("absolute reachability production evidence runtime type differs")
    _validate_snapshot_evidence(snapshots)
    _validate_leader_evidence(leaders)
    return cfg, risk, universe, snapshots, leaders


def _validate_snapshot_evidence(snapshots: Mapping[object, object]) -> None:
    for symbol, values in snapshots.items():
        if type(symbol) is not str or type(values) is not dict:
            raise ValueError("absolute reachability snapshot evidence is malformed")
        for name, value in values.items():
            if (
                type(name) is not str
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError("absolute reachability snapshot evidence is malformed")


def _validate_leader_evidence(leaders: Mapping[object, object]) -> None:
    for symbol, leader in leaders.items():
        if type(symbol) is not str or type(leader) is not LeaderScore:
            raise ValueError("absolute reachability leader evidence is malformed")
        if symbol != leader.symbol or any(
            not math.isfinite(float(value)) for value in leader.components.values()
        ):
            raise ValueError("absolute reachability leader evidence is malformed")


def _derived_state(
    *,
    account: AccountState,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
) -> dict[str, object]:
    authority = assess_strategic_capital_authority(account)
    active_epoch_state = "NONE"
    if account.active_strategic_epoch_id:
        active = next(
            (
                epoch
                for epoch in account.strategic_epochs
                if epoch.epoch_id == account.active_strategic_epoch_id
            ),
            None,
        )
        if active is None:
            raise ValueError("absolute reachability active epoch is unresolved")
        active_epoch_state = active.realized_status
    observation = account.strategic_qualification
    return {
        "active_epoch_state": active_epoch_state,
        "capital_budget_level": account.capital_budget_level,
        "flat_all_cash": authority.all_cash,
        "grant_state": "NONE" if account.strategic_grant is None else account.strategic_grant.status,
        "opportunity_state": account.opportunity,
        "pending_execution": bool(
            authority.pending_execution_symbols
            or authority.unsettled_order_ids
            or authority.late_fill_order_ids
        ),
        "protected_authority": bool(
            account.protected_weights or account.protected_weight_epoch_ids
        ),
        "qualification_quorum": observation.qualification_quorum,
        "qualification_ready": observation.qualification_ready,
        "qualification_route": observation.qualification_route,
        "recovery_authority": bool(
            account.recovery_conviction_symbol or account.recovery_owner_epoch_id
        ),
        "reference_available": not set(
            (*universe.qualification_reference_symbols, *universe.risk_reference_symbols)
        ).difference(universe.available_symbols),
        "repair_status": account.flat_book_capital_repair.status,
        "restore_authority": bool(
            account.strategic_restore_weights or account.strategic_restore_epoch_ids
        ),
        "risk_state": risk.state.value,
        "unknown_execution": False,
    }


def project_observed_reachability_state(
    *,
    account_payload: AbsoluteGeneralizationReplayPayload,
    cfg: SystemConfig,
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    outlet_evidence: object = None,
) -> dict[str, object]:
    """Project all state claims from exact runtime objects for Task 6 analysis."""

    account = _validate_account_payload(account_payload)
    market = {
        "cfg": cfg,
        "risk": risk,
        "universe": universe,
        "snapshots": snapshots,
        "leaders": leaders,
    }
    _validated_market_evidence(market)
    return {
        "account_payload": account_payload,
        "cfg": cfg,
        "risk": risk,
        "universe": universe,
        "snapshots": snapshots,
        "leaders": leaders,
        **_derived_state(account=account, risk=risk, universe=universe),
        "outlet_evidence": outlet_evidence,
    }


def _reconcile_state_claims(
    state: Mapping[str, object],
    derived: Mapping[str, object],
) -> None:
    for field, expected in derived.items():
        if state[field] != expected:
            raise ValueError(
                f"absolute reachability account/state projection differs at {field}"
            )


def _validated_state(raw: object) -> tuple[_StateKey, bool, bool, AccountState]:
    state = _strict_mapping(raw, label="state")
    if set(state) != _STATE_FIELDS:
        raise ValueError("absolute reachability state fields differ")
    account = _validate_account_payload(state["account_payload"])
    cfg, risk, universe, snapshots, leaders = _validated_market_evidence(state)
    booleans = (
        "flat_all_cash",
        "qualification_ready",
        "pending_execution",
        "unknown_execution",
        "reference_available",
        "protected_authority",
        "recovery_authority",
        "restore_authority",
    )
    for field in booleans:
        _strict_bool(state[field], label=field)
    level = state["capital_budget_level"]
    if type(level) is not int or not 0 <= level <= 4:
        raise ValueError("absolute reachability capital budget level is malformed")
    enums = (
        ("repair_status", _REPAIR_STATUSES),
        ("risk_state", _RISK_STATES),
        ("opportunity_state", _OPPORTUNITY_STATES),
        ("grant_state", _GRANT_STATES),
        ("active_epoch_state", _EPOCH_STATES),
    )
    for field, allowed in enums:
        if _strict_text(state[field], label=field) not in allowed:
            raise ValueError(f"absolute reachability {field} is UNKNOWN")
    route = _strict_text(state["qualification_route"], label="qualification route")
    quorum = _strict_text(state["qualification_quorum"], label="qualification quorum")
    derived = _derived_state(account=account, risk=risk, universe=universe)
    _reconcile_state_claims(state, derived)
    projection = project_qualification_opportunity_health(
        account=account,
        risk=risk,
        universe=universe,
        observation=account.strategic_qualification,
        snapshots=snapshots,
        leaders=leaders,
        cfg=cfg,
    )
    outlet = _outlet_from_state(state, account)
    rejections = projection.rejection_reasons
    key = (
        *(
            state[field]
            for field in sorted(
                _STATE_FIELDS - {"account_payload", "outlet_evidence"}
            )
            if field
            not in {"cfg", "leaders", "risk", "snapshots", "universe"}
        ),
        route,
        quorum,
        outlet,
        rejections,
    )
    return key, projection.healthy, outlet, account


def _validated_observations(
    transitions: object,
) -> tuple[
    list[_StateKey],
    list[str],
    list[bool],
    list[bool],
    list[AccountState],
]:
    if type(transitions) not in {list, tuple}:
        raise ValueError("absolute reachability transitions are malformed")
    rows = cast(Sequence[object], transitions)
    if not rows or len(rows) > _MAX_REACHABILITY_ROWS:
        raise ValueError("absolute reachability transition count is unbounded")
    nodes: list[_StateKey] = []
    sessions: list[str] = []
    healthy: list[bool] = []
    outlets: list[bool] = []
    accounts: list[AccountState] = []
    known_orders: dict[str, tuple[object, ...]] = {}
    previous_phase = ""
    previous_session = ""
    for item in rows:
        row = _strict_mapping(item, label="transition")
        if set(row) != _ROW_FIELDS or row["edge_kind"] != "OBSERVED":
            raise ValueError("absolute reachability requires an observed edge")
        phase = _strict_text(row["phase"], label="phase")
        if phase not in _PHASES:
            raise ValueError("absolute reachability phase is UNKNOWN")
        session = _strict_text(row["session"], label="session")
        try:
            date.fromisoformat(session)
        except ValueError as exc:
            raise ValueError("absolute reachability session is malformed") from exc
        _validate_observed_order(
            previous_phase=previous_phase,
            previous_session=previous_session,
            phase=phase,
            session=session,
        )
        node, is_healthy, outlet, account = _validated_state(row["state"])
        current_orders = {
            order.order_id: order_intent_metadata(order)
            for order in account.order_ledger
        }
        removed_orders = set(known_orders).difference(current_orders)
        if removed_orders:
            raise ValueError("absolute reachability unsupported order mutation: removal")
        for order_id in set(known_orders).intersection(current_orders):
            if known_orders[order_id] != current_orders[order_id]:
                changed = [
                    field
                    for field, before, after in zip(
                        ORDER_INTENT_IMMUTABLE_FIELDS,
                        known_orders[order_id],
                        current_orders[order_id],
                        strict=True,
                    )
                    if before != after
                ]
                raise ValueError(
                    "absolute reachability unsupported order mutation: "
                    + ", ".join(changed)
                )
        nodes.append(node)
        sessions.append(session)
        healthy.append(is_healthy)
        outlets.append(outlet)
        accounts.append(account)
        known_orders.update(current_orders)
        previous_phase, previous_session = phase, session
    if len(set(nodes)) > _MAX_REACHABILITY_STATES:
        raise ValueError("absolute reachability state groups are unbounded")
    return nodes, sessions, healthy, outlets, accounts


def _validate_observed_order(
    *,
    previous_phase: str,
    previous_session: str,
    phase: str,
    session: str,
) -> None:
    if not previous_phase:
        return
    valid = (
        previous_phase == "POST_DECISION"
        and phase == "POST_OPEN"
        and session > previous_session
    ) or (
        previous_phase == "POST_OPEN"
        and phase == "POST_DECISION"
        and session == previous_session
    )
    if not valid:
        raise ValueError("absolute reachability observed phase order differs")


def analyze_terminal_scc(
    transitions: object,
    *,
    maximum_healthy_sessions: int = 60,
) -> TerminalSccAnalysis:
    """Analyze only consecutive observed edges; never invent reachability."""

    if type(maximum_healthy_sessions) is not int or maximum_healthy_sessions < 0:
        raise ValueError("absolute reachability SCC bound is malformed")
    nodes, sessions, healthy, outlets, _accounts = _validated_observations(transitions)
    node_set = set(nodes)
    edges = set(pairwise(nodes))
    components = strong_components(node_set, edges)
    terminal_zero_durations: list[int] = []
    for component in components:
        members = set(component)
        if any(source in members and destination not in members for source, destination in edges):
            continue
        indexes = [index for index, node in enumerate(nodes) if node in members]
        if any(outlets[index] for index in indexes):
            continue
        duration = maximum_consecutive_component_sessions(
            members=members,
            nodes=nodes,
            sessions=sessions,
            healthy=healthy,
        )
        if duration:
            terminal_zero_durations.append(duration)
    maximum = max(terminal_zero_durations, default=0)
    violations = sum(
        duration > maximum_healthy_sessions for duration in terminal_zero_durations
    )
    digest_payload = {
        "edges": sorted((repr(source), repr(destination)) for source, destination in edges),
        "nodes": sorted(repr(node) for node in node_set),
        "sessions": sessions,
    }
    return TerminalSccAnalysis(
        passed=violations == 0,
        bounded=True,
        maximum_terminal_zero_strategic_target_scc_sessions=maximum,
        terminal_scc_violation_count=violations,
        no_positive_strategic_target_exit_count=len(terminal_zero_durations),
        state_count=len(node_set),
        edge_count=len(edges),
        state_transition_digest=hashlib.sha256(
            canonical_json_bytes(digest_payload)
        ).hexdigest(),
    )


__all__ = (
    "FailedGrantRecoveryAnalysis",
    "HealthProjection",
    "TerminalSccAnalysis",
    "analyze_failed_grant_recovery",
    "analyze_terminal_scc",
    "is_positive_strategic_outlet",
    "project_flat_book_repair_health",
    "project_observed_reachability_state",
    "project_qualification_opportunity_health",
)
