from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import asdict
from datetime import date, timedelta

import pytest
from test_account_schema_v3_integrity import _position_state
from test_strategic_cash_rearm import _qualification, _risk, _roles, _strict_inputs

from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.models.decision import Target
from uquant.models.strategic_epoch import StrategicEpoch, derive_strategic_epoch_id
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    derive_strategic_grant_id,
)
from uquant.models.strategic_rearm import (
    derive_flat_book_capital_repair_episode_id,
    derive_strategic_cash_rearm_authorization_id,
)
from uquant.models.trading import derive_attribution_event_id
from uquant.types import AccountOrder, AccountState, Fill, Opportunity, Risk
from uquant.validation.absolute_generalization import (
    analyze_terminal_scc,
    is_positive_strategic_outlet,
    project_flat_book_repair_health,
    project_qualification_opportunity_health,
)
from uquant.validation.absolute_generalization.replay import (
    AbsoluteGeneralizationReplayPayload,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _cash_account(*, budget_level: int) -> AccountState:
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:reachability"
    account.capital_budget_level = budget_level
    account.opportunity = Opportunity.TREND.value
    return account


def _rearm_evidence(candidate: str, session: str) -> dict[str, object]:
    account_identity = "account:reachability"
    risk_identity = "a" * 64
    config_identity = config_fingerprint(DEFAULT_CONFIG)
    repair_id = derive_flat_book_capital_repair_episode_id(
        account_identity=account_identity,
        capital_budget_level=1,
        first_observed_session="2026-01-01",
        risk_reference_universe_identity=risk_identity,
        config_identity=config_identity,
    )
    values: dict[str, object] = {
        "account_identity": account_identity,
        "repair_episode_id": repair_id,
        "candidate_symbol": candidate,
        "qualification_signature": "qualification:optical",
        "qualification_route": "established",
        "qualification_quorum": "FULL_COHORT",
        "qualification_evidence_sha256": "c" * 64,
        "capital_budget_level": 1,
        "tradable_universe_identity": "b" * 64,
        "qualification_reference_universe_identity": "c" * 64,
        "risk_reference_universe_identity": risk_identity,
        "point_in_time_industry_identity": "d" * 64,
        "authorized_session": session,
    }
    values["authorization_id"] = derive_strategic_cash_rearm_authorization_id(
        **values
    )
    values["config_identity"] = config_identity
    return values


@pytest.mark.parametrize(
    ("persisted_level", "target_level", "required_sessions"),
    ((1, 0, 20), (2, 1, 40), (3, 2, 60), (4, 3, 60)),
)
def test_repair_projection_preserves_exact_bounded_ladder(
    persisted_level: int,
    target_level: int,
    required_sessions: int,
) -> None:
    projection = project_flat_book_repair_health(
        account=_cash_account(budget_level=persisted_level),
        risk=_risk(),
        universe=_roles(),
        cfg=DEFAULT_CONFIG,
    )

    assert projection.healthy is True
    assert projection.persisted_damage_level == persisted_level
    assert projection.repair_target_level == target_level
    assert projection.required_healthy_sessions == required_sessions


def test_qualification_projection_does_not_require_a_repairable_block() -> None:
    account = _cash_account(budget_level=0)
    observation = _qualification()
    observation.deployment_blocked = False
    observation.deployment_block_reason = ""
    snapshots, leaders = _strict_inputs()
    risk = _risk(freeze_new_risk=False)

    repair = project_flat_book_repair_health(
        account=account,
        risk=risk,
        universe=_roles(),
        cfg=DEFAULT_CONFIG,
    )
    qualification = project_qualification_opportunity_health(
        account=account,
        risk=risk,
        universe=_roles(),
        observation=observation,
        snapshots=snapshots,
        leaders=leaders,
        cfg=DEFAULT_CONFIG,
    )

    assert repair.healthy is False
    assert qualification.healthy is True


def _probe_chain(
    *,
    candidate: str = "sz300308",
    qualification_signature: str = "qualification:optical",
    previous_grant_id: str = "",
    previous_epoch_id: str = "",
    authorization_id: str = "rearm_" + "d" * 64,
    created_session: str = "2026-01-05",
) -> tuple[Target, StrategicGrantIntent, StrategicEpoch]:
    account_identity = "account:reachability"
    source_identity = "production-source:test"
    config_identity = config_fingerprint(DEFAULT_CONFIG)
    evidence_sha256 = "c" * 64
    event_id = derive_attribution_event_id(
        signal_date=created_session,
        symbol=candidate,
        target_weight=0.10,
        lifecycle="CORE",
        origin_lifecycle="CORE",
        origin_subsystem="STRATEGIC",
        mechanism="STRATEGIC_COHORT",
        replaces_symbol=None,
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy="FIFO",
        reason_code="strategy_target",
        exit_kind="strategy",
    )
    grant_id = derive_strategic_grant_id(
        account_identity=account_identity,
        candidate_symbol=candidate,
        qualification_signature=qualification_signature,
        qualification_route="established",
        qualification_evidence_sha256=evidence_sha256,
        created_session=created_session,
        previous_grant_id=previous_grant_id,
        production_source_identity=source_identity,
        authorization_id=authorization_id,
    )
    epoch_id = derive_strategic_epoch_id(
        account_identity=account_identity,
        owner_symbol=candidate,
        qualification_signature=qualification_signature,
        qualification_route="established",
        grant_id=grant_id,
        opened_session=created_session,
        previous_epoch_id=previous_epoch_id,
        source_identity=source_identity,
        config_identity=config_identity,
        evidence_sha256=evidence_sha256,
    )
    target = Target(
        symbol=candidate,
        weight=0.10,
        lifecycle="CORE",
        alpha_score=0.95,
        confidence=0.95,
        reason="strategic probe",
        event_id=event_id,
        origin_subsystem="STRATEGIC",
        grant_id=grant_id,
        epoch_id=epoch_id,
    )
    grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=candidate,
        qualification_signature=qualification_signature,
        qualification_route="established",
        qualification_evidence_sha256=evidence_sha256,
        created_session=created_session,
        last_eligible_session=created_session,
        target_weight=0.10,
        status="PENDING_EXECUTION",
        epoch_id=epoch_id,
        qualification_quorum="FULL_COHORT",
        account_identity=account_identity,
        production_source_identity=source_identity,
        authorization_id=authorization_id,
        previous_grant_id=previous_grant_id,
    )
    epoch = StrategicEpoch(
        epoch_id=epoch_id,
        owner_symbol=candidate,
        qualification_signature=qualification_signature,
        qualification_route="established",
        qualification_quorum="FULL_COHORT",
        grant_id=grant_id,
        opened_session=created_session,
        realized_status="PROBE",
        target_weight=0.10,
        full_weight=0.20,
        source_identity=source_identity,
        config_identity=config_identity,
        evidence_sha256=evidence_sha256,
        account_identity=account_identity,
        previous_epoch_id=previous_epoch_id,
    )
    return target, grant, epoch


def test_ordinary_target_and_unfilled_probe_are_not_strategic_outlets() -> None:
    target, grant, epoch = _probe_chain()
    ordinary = Target(
        symbol=target.symbol,
        weight=target.weight,
        lifecycle=target.lifecycle,
        alpha_score=target.alpha_score,
        confidence=target.confidence,
        reason="ordinary leader target",
        origin_subsystem="LEADER",
    )

    assert not is_positive_strategic_outlet(
        target=ordinary,
        grant=None,
        epoch=None,
        orders=(),
        fills=(),
    )
    assert not is_positive_strategic_outlet(
        target=target,
        grant=grant,
        epoch=epoch,
        orders=(),
        fills=(),
    )


def _filled_chain(
    *,
    candidate: str = "sz300308",
    qualification_signature: str = "qualification:optical",
    previous_grant_id: str = "",
    previous_epoch_id: str = "",
    authorization_id: str = "rearm_" + "d" * 64,
    created_session: str = "2026-01-05",
    fill_session: str = "2026-01-06",
) -> tuple[
    Target,
    StrategicGrantIntent,
    StrategicEpoch,
    AccountOrder,
    Fill,
]:
    target, grant, epoch = _probe_chain(
        candidate=candidate,
        qualification_signature=qualification_signature,
        previous_grant_id=previous_grant_id,
        previous_epoch_id=previous_epoch_id,
        authorization_id=authorization_id,
        created_session=created_session,
    )
    grant.status = "ACTIVE"
    grant.filled_shares = 100
    grant.first_submission_session = created_session
    grant.last_submission_session = created_session
    grant.submitted_order_ids = ["O000000001"]
    grant.acknowledged_order_ids = ["O000000001"]
    epoch.realized_status = "ACTIVE"
    epoch.first_fill_session = fill_session
    epoch.active_session = fill_session
    order = AccountOrder(
        order_id="O000000001",
        signal_date=created_session,
        submitted_date=created_session,
        symbol=target.symbol,
        side="BUY",
        target_weight=target.weight,
        reason=target.reason,
        lifecycle=target.lifecycle,
        status="FILLED",
        requested_shares=100,
        filled_shares=100,
        remaining_shares=0,
        last_update_date=fill_session,
        last_event="FILL",
        event_id=target.event_id,
        origin_subsystem="STRATEGIC",
        mechanism="STRATEGIC_COHORT",
        origin_lifecycle="CORE",
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    fill = Fill(
        signal_date=created_session,
        fill_date=fill_session,
        symbol=target.symbol,
        side="BUY",
        shares=100,
        price=10.0,
        gross_value=1_000.0,
        commission=5.0,
        stamp_duty=0.0,
        transfer_fee=0.0,
        slippage_cost=0.0,
        reason=target.reason,
        lifecycle=target.lifecycle,
        order_id=order.order_id,
        fill_id="fill:strategic:1",
        event_id=order.event_id,
        origin_subsystem="STRATEGIC",
        mechanism="STRATEGIC_COHORT",
        origin_lifecycle="CORE",
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    return target, grant, epoch, order, fill


def test_fully_filled_exact_strategic_chain_is_an_outlet() -> None:
    target, grant, epoch, order, fill = _filled_chain()

    assert is_positive_strategic_outlet(
        target=target,
        grant=grant,
        epoch=epoch,
        orders=(order,),
        fills=(fill,),
    )


@pytest.mark.parametrize("duplicate", ("order", "fill"))
def test_strategic_outlet_rejects_ambiguous_physical_identities(
    duplicate: str,
) -> None:
    target, grant, epoch, order, fill = _filled_chain()
    orders = (order, order) if duplicate == "order" else (order,)
    fills = (fill, fill) if duplicate == "fill" else (fill,)

    with pytest.raises(ValueError, match="duplicate"):
        is_positive_strategic_outlet(
            target=target,
            grant=grant,
            epoch=epoch,
            orders=orders,
            fills=fills,
        )


def _account_payload(account: AccountState) -> AbsoluteGeneralizationReplayPayload:
    encoded = canonical_json_bytes(account.to_dict())
    return AbsoluteGeneralizationReplayPayload(
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _reachability_state(
    *,
    outlet: bool = False,
    outlet_chain: tuple[
        Target,
        StrategicGrantIntent,
        StrategicEpoch,
        AccountOrder,
        Fill,
    ]
    | None = None,
    account: AccountState | None = None,
) -> dict[str, object]:
    outlet_evidence: object = None
    runtime_account = account or _cash_account(budget_level=1)
    if outlet or outlet_chain is not None:
        target, grant, epoch, order, fill = deepcopy(
            outlet_chain or _filled_chain()
        )
        if outlet_chain is None and account is None:
            grant.status = "COMPLETED"
            runtime_account.strategic_grant = grant
            runtime_account.strategic_epochs = [epoch]
            runtime_account.active_strategic_epoch_id = epoch.epoch_id
            runtime_account.order_ledger = [order]
            runtime_account.next_order_sequence = 2
            runtime_account.fills = [fill]
        outlet_evidence = {
            "target": target,
            "grant": grant,
            "epoch": epoch,
            "orders": (order,),
            "fills": (fill,),
        }
    runtime_account.strategic_qualification = _qualification()
    snapshots, leaders = _strict_inputs()
    risk = _risk()
    universe = _roles()
    active_epoch_state = "NONE"
    if runtime_account.active_strategic_epoch_id:
        active_epoch_state = next(
            epoch.realized_status
            for epoch in runtime_account.strategic_epochs
            if epoch.epoch_id == runtime_account.active_strategic_epoch_id
        )
    return {
        "account_payload": _account_payload(runtime_account),
        "cfg": DEFAULT_CONFIG,
        "risk": risk,
        "universe": universe,
        "snapshots": snapshots,
        "leaders": leaders,
        "flat_all_cash": not any(
            position.shares > 0 for position in runtime_account.positions.values()
        ),
        "capital_budget_level": runtime_account.capital_budget_level,
        "repair_status": runtime_account.flat_book_capital_repair.status,
        "risk_state": risk.state.value,
        "opportunity_state": runtime_account.opportunity,
        "qualification_ready": runtime_account.strategic_qualification.qualification_ready,
        "qualification_route": runtime_account.strategic_qualification.qualification_route,
        "qualification_quorum": runtime_account.strategic_qualification.qualification_quorum,
        "grant_state": (
            "NONE"
            if runtime_account.strategic_grant is None
            else runtime_account.strategic_grant.status
        ),
        "active_epoch_state": active_epoch_state,
        "pending_execution": bool(
            runtime_account.pending_orders
            or any(
                order.status not in {"FILLED", "CANCELLED", "REPLACED"}
                for order in runtime_account.order_ledger
            )
        ),
        "unknown_execution": False,
        "reference_available": True,
        "protected_authority": False,
        "recovery_authority": False,
        "restore_authority": False,
        "outlet_evidence": outlet_evidence,
    }


def _observed_trace(
    session_count: int,
    *,
    start: date = date(2026, 1, 1),
    outlet_at_end: bool = False,
    outlet_chain: tuple[
        Target,
        StrategicGrantIntent,
        StrategicEpoch,
        AccountOrder,
        Fill,
    ]
    | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(session_count):
        session = (start + timedelta(days=index)).isoformat()
        if index:
            rows.append(
                {
                    "edge_kind": "OBSERVED",
                    "phase": "POST_OPEN",
                    "session": session,
                    "state": _reachability_state(),
                }
            )
        rows.append(
            {
                "edge_kind": "OBSERVED",
                "phase": "POST_DECISION",
                "session": session,
                "state": _reachability_state(
                    outlet=outlet_at_end and index == session_count - 1,
                    outlet_chain=(
                        outlet_chain if index == session_count - 1 else None
                    ),
                ),
            }
        )
    return rows


def test_terminal_scc_uses_exact_sixty_session_boundary() -> None:
    bounded = analyze_terminal_scc(_observed_trace(60))
    violation = analyze_terminal_scc(_observed_trace(61))

    assert bounded.passed is True
    assert bounded.maximum_terminal_zero_strategic_target_scc_sessions == 60
    assert bounded.terminal_scc_violation_count == 0
    assert violation.passed is False
    assert violation.maximum_terminal_zero_strategic_target_scc_sessions == 61
    assert violation.terminal_scc_violation_count == 1


def test_observed_positive_outlet_closes_the_terminal_zero_target_component() -> None:
    result = analyze_terminal_scc(_observed_trace(61, outlet_at_end=True))

    assert result.passed is True
    assert result.maximum_terminal_zero_strategic_target_scc_sessions == 0
    assert result.no_positive_strategic_target_exit_count == 0


def test_terminal_scc_rejects_unknown_or_hand_authored_edges() -> None:
    trace = _observed_trace(2)
    trace[1]["edge_kind"] = "UNKNOWN"

    with pytest.raises(ValueError, match="observed edge"):
        analyze_terminal_scc(trace)


def test_terminal_scc_rejects_self_asserted_outlet_boolean() -> None:
    trace = _observed_trace(2)
    state = trace[-1]["state"]
    assert isinstance(state, dict)
    state["positive_strategic_outlet"] = True

    with pytest.raises(ValueError, match="state fields"):
        analyze_terminal_scc(trace)


def _replace_account_payload(
    trace: list[dict[str, object]],
    *,
    index: int,
    mutate: object,
) -> None:
    state = trace[index]["state"]
    assert isinstance(state, dict)
    payload = state["account_payload"]
    assert isinstance(payload, AbsoluteGeneralizationReplayPayload)
    raw = strict_json_loads(payload.canonical_json)
    assert isinstance(raw, dict)
    assert callable(mutate)
    mutate(raw)
    encoded = canonical_json_bytes(raw)
    state["account_payload"] = AbsoluteGeneralizationReplayPayload(
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def test_terminal_scc_rejects_malformed_repair_runtime_container() -> None:
    trace = _observed_trace(2)

    def corrupt(raw: dict[str, object]) -> None:
        raw["flat_book_capital_repair"] = []

    _replace_account_payload(trace, index=0, mutate=corrupt)
    with pytest.raises(ValueError, match="account payload is invalid"):
        analyze_terminal_scc(trace)


def test_terminal_scc_rejects_grant_order_ids_absent_from_ledger() -> None:
    trace = _observed_trace(2)
    _target, grant, _epoch = _probe_chain()
    grant.epoch_id = ""
    grant.first_submission_session = "2026-01-05"
    grant.last_submission_session = "2026-01-05"
    grant.submitted_order_ids = ["O000000999"]

    def fabricate(raw: dict[str, object]) -> None:
        raw["strategic_grant"] = asdict(grant)

    _replace_account_payload(trace, index=0, mutate=fabricate)
    with pytest.raises(ValueError, match="account payload is invalid"):
        analyze_terminal_scc(trace)


def _historical_order(*, symbol: str = "sz300308", last_event: str = "CANCELLED") -> AccountOrder:
    event_id = derive_attribution_event_id(
        signal_date="2026-01-05",
        symbol=symbol,
        target_weight=0.10,
        lifecycle="CORE",
        origin_lifecycle="CORE",
        origin_subsystem="LEADER",
        mechanism="LEADER_SELECTION",
        replaces_symbol=None,
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy="FIFO",
        reason_code="strategy_target",
        exit_kind="strategy",
    )
    return AccountOrder(
        order_id="O000000001",
        signal_date="2026-01-05",
        submitted_date="2026-01-05",
        symbol=symbol,
        side="BUY",
        target_weight=0.10,
        reason="runtime evidence",
        lifecycle="CORE",
        status="CANCELLED",
        requested_shares=0,
        filled_shares=0,
        remaining_shares=0,
        last_update_date="2026-01-06",
        last_event=last_event,
        cancel_reason="blocked",
        event_id=event_id,
        origin_subsystem="LEADER",
        mechanism="LEADER_SELECTION",
        origin_lifecycle="CORE",
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
    )


def test_terminal_scc_rejects_impossible_order_status_event_pair() -> None:
    account = _cash_account(budget_level=1)
    account.order_ledger = [_historical_order(last_event="FILL")]
    account.next_order_sequence = 2
    trace = _observed_trace(1)
    state = trace[0]["state"]
    assert isinstance(state, dict)
    state["account_payload"] = _account_payload(account)

    with pytest.raises(ValueError, match="status/event"):
        analyze_terminal_scc(trace)


def test_terminal_scc_rejects_unsupported_order_identity_mutation() -> None:
    first = _cash_account(budget_level=1)
    first.strategic_qualification = _qualification()
    first.order_ledger = [_historical_order()]
    first.next_order_sequence = 2
    second = _cash_account(budget_level=1)
    second.strategic_qualification = _qualification()
    second.order_ledger = [_historical_order(symbol="sz300394")]
    second.next_order_sequence = 2
    trace = _observed_trace(2)
    first_state = trace[0]["state"]
    last_state = trace[-1]["state"]
    assert isinstance(first_state, dict) and isinstance(last_state, dict)
    first_state["account_payload"] = _account_payload(first)
    last_state["account_payload"] = _account_payload(second)

    with pytest.raises(ValueError, match="unsupported order mutation"):
        analyze_terminal_scc(trace)


def test_terminal_scc_recomputes_account_facts_instead_of_trusting_claims() -> None:
    trace = _observed_trace(2)
    state = trace[0]["state"]
    assert isinstance(state, dict)
    state["account_payload"] = _account_payload(_position_state())
    assert state["flat_all_cash"] is True

    with pytest.raises(ValueError, match="account/state projection"):
        analyze_terminal_scc(trace)


def test_terminal_scc_rejects_wrong_outlet_runtime_types_stably() -> None:
    trace = _observed_trace(2, outlet_at_end=True)
    state = trace[-1]["state"]
    assert isinstance(state, dict)
    evidence = state["outlet_evidence"]
    assert isinstance(evidence, dict)
    evidence["target"] = "forged-target"

    with pytest.raises(ValueError, match="outlet runtime type"):
        analyze_terminal_scc(trace)


def test_terminal_scc_does_not_join_healthy_sessions_across_an_unhealthy_gap() -> None:
    trace = _observed_trace(3)
    for row in trace:
        if row["session"] == "2026-01-02":
            state = row["state"]
            assert isinstance(state, dict)
            state["risk"] = _risk(state=Risk.CAUTION)
            state["risk_state"] = "CAUTION"

    result = analyze_terminal_scc(trace)

    assert result.maximum_terminal_zero_strategic_target_scc_sessions == 1
