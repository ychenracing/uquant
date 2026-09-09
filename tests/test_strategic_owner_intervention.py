from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from research.strategic_evidence.intervention import (
    StrategicOwnerIntervention,
    _replace_counterfactual_epoch,
    _rewrite_grant,
)
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.execution import plan_orders, reconcile_account_orders
from uquant.models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    derive_strategic_epoch_id,
)
from uquant.types import (
    AccountState,
    Decision,
    Opportunity,
    Position,
    Risk,
    StrategicGrantIntent,
    StrategicGrantStatus,
    StrategicQualificationObservation,
    Target,
    derive_strategic_grant_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


@pytest.fixture(scope="module")
def unfilled_group_activation():
    """An explicit FULL_COHORT contract independent of live discovery rankings."""
    symbols = ("sz300308", "sz300394", "sz300502")
    owner, session = "sz300502", "2023-01-04"
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = "account-test"
    intervention = StrategicOwnerIntervention(owner="sh688037", target_gross=1.0)
    intervention.apply(account)
    signature, evidence, source = "qualification:full-optical-fixture", "a" * 64, "production-source"
    grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity, candidate_symbol=owner,
        qualification_signature=signature, qualification_route="established",
        qualification_evidence_sha256=evidence, created_session=session,
        previous_grant_id="", production_source_identity=source,
    )
    epoch_id = derive_strategic_epoch_id(
        account_identity=account.account_identity, owner_symbol=owner,
        qualification_signature=signature, qualification_route="established", grant_id=grant_id,
        opened_session=session, previous_epoch_id="", source_identity=source,
        config_identity="config:test", evidence_sha256=evidence,
    )
    account.strategic_grant = StrategicGrantIntent(
        grant_id=grant_id, candidate_symbol=owner, qualification_signature=signature,
        qualification_route="established", qualification_evidence_sha256=evidence,
        created_session=session, last_eligible_session=session, target_weight=1 / 3,
        status=StrategicGrantStatus.QUALIFIED.value, account_identity=account.account_identity,
        production_source_identity=source, epoch_id=epoch_id, qualification_quorum="FULL_COHORT",
    )
    account.strategic_epochs = [StrategicEpoch(
        epoch_id=epoch_id, owner_symbol=owner, qualification_signature=signature,
        qualification_route="established", qualification_quorum="FULL_COHORT", grant_id=grant_id,
        opened_session=session, source_identity=source, config_identity="config:test", evidence_sha256=evidence,
        realized_status=StrategicEpochStatus.PROBE.value, target_weight=1 / 3, full_weight=0.5,
        account_identity=account.account_identity,
    )]
    account.strategic_qualification = StrategicQualificationObservation(
        candidate_symbol=owner, qualification_signature=signature, qualification_route="established",
        qualification_evidence_sha256=evidence, qualification_ready=True, qualification_streak=5,
        qualification_last_observed_session=session, qualification_quorum="FULL_COHORT", candidate_symbols=list(symbols),
    )
    account.strategic_cohort_symbols = list(symbols)
    account.strategic_cohort_targets = dict.fromkeys(symbols, 1 / 3)
    account.strategic_candidate_signature = signature
    targets = attach_target_attribution("optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=session, targets=tuple(
        Target(symbol, 1 / 3, "CORE", 0.8, 0.95, "synthetic qualified full cohort",
               origin_subsystem="STRATEGIC", mechanism="STRATEGIC_COHORT", origin_lifecycle="CORE",
               reason_code="strategic_cohort", grant_id=grant_id if symbol == owner else "", epoch_id=epoch_id)
        for symbol in symbols
    ))
    planned = plan_orders(signal_date=session, targets=targets, account=account,
                          prices=dict.fromkeys(symbols, 10.0), cfg=DEFAULT_CONFIG)
    orders = reconcile_account_orders(account=account, previous=[], current=planned, submitted_date=session)
    decision = Decision(session, Opportunity.TREND, Risk.NORMAL, 1.0, 3, targets, orders,
                        {"effective_config_sha256": config_fingerprint(DEFAULT_CONFIG)}, "synthetic-group-fixture")
    assert len(decision.targets) == 3 and len(decision.pending_orders) == 3
    assert not account.positions and not account.fills and not account.pending_orders
    return account, decision, intervention


def test_first_unfilled_group_is_a_budget_preserving_explicit_counterfactual(unfilled_group_activation):
    account, decision, intervention = deepcopy(unfilled_group_activation)
    original_account = deepcopy(account.to_dict())
    original_orders = tuple(decision.pending_orders)

    forced = intervention.preserve_activation(account, decision)

    assert len(forced.targets) == len(forced.pending_orders) == len(account.order_ledger) == 1
    assert forced.targets[0].symbol == "sh688037"
    assert forced.targets[0].weight == pytest.approx(sum(target.weight for target in decision.targets))
    assert forced.target_gross == forced.targets[0].weight
    assert forced.target_k == 1
    assert forced.decision_digest != decision.decision_digest
    assert forced.pending_orders[0].order_id not in {order.order_id for order in original_orders}
    assert forced.pending_orders[0].event_id not in {order.event_id for order in original_orders}
    assert account.strategic_grant.qualification_signature.startswith("research_forced_owner:")
    assert not account.positions and not account.fills
    assert account.cash == original_account["cash"]
    audit = intervention.provenance["activation_counterfactual"]
    assert audit["kind"] == "COUNTERFACTUAL_UNFILLED_COHORT"
    assert audit["production_qualification_evidence"] is False
    assert audit["source_order_ledger"] == original_account["order_ledger"]
    assert audit["counterfactual_order_id"] == forced.pending_orders[0].order_id
    assert audit["counterfactual_source_order_ids"] == [order.order_id for order in original_orders]
    assert len(audit["source_targets"]) == len(audit["source_orders"]) == 3


@pytest.mark.parametrize("invalid", (
    "position", "old_pending", "old_order", "acknowledged", "filled", "missing_grant",
    "wrong_epoch", "mixed_target", "over_budget",
))
def test_group_counterfactual_rejects_executed_mixed_or_unbound_state_atomically(unfilled_group_activation, invalid):
    account, decision, intervention = deepcopy(unfilled_group_activation)
    if invalid == "position":
        account.positions["sz300308"] = Position("sz300308", 100, 10.0, "2023-01-03", 10.0)
    elif invalid == "old_pending":
        account.pending_orders = [deepcopy(decision.pending_orders[0])]
    elif invalid == "old_order":
        account.order_ledger[0].submitted_date = "2023-01-03"
    elif invalid == "acknowledged":
        account.order_ledger[0].status = "ACKNOWLEDGED"
    elif invalid == "filled":
        account.order_ledger[0].filled_shares = 100
    elif invalid == "missing_grant":
        account.strategic_grant = None
    elif invalid == "wrong_epoch":
        decision = replace(decision, targets=(replace(decision.targets[0], epoch_id=""), *decision.targets[1:]))
    elif invalid == "mixed_target":
        decision = replace(decision, targets=(
            replace(decision.targets[0], origin_subsystem="LEADER", mechanism="LEADER_SELECTION"),
            *decision.targets[1:],
        ))
    elif invalid == "over_budget":
        account.strategic_cohort_targets = {symbol: weight / 2 for symbol, weight in account.strategic_cohort_targets.items()}
        decision = replace(decision, targets=tuple(replace(target, weight=target.weight / 2) for target in decision.targets))
    before = deepcopy(account.to_dict())
    prior_provenance = deepcopy(intervention.provenance)

    with pytest.raises(ValueError, match="forced group activation"):
        intervention.preserve_activation(account, decision)

    assert account.to_dict() == before
    assert intervention.provenance == prior_provenance


def test_same_single_owner_intervention_leaves_account_and_decision_unchanged():
    account = AccountState.empty(1_000_000.0)
    account.strategic_cohort_symbols = ["sz300308"]
    account.strategic_cohort_targets = {"sz300308": 0.2}
    target = Target(
        "sz300308", 0.2, "CORE", 0.9, 0.9, "single owner activation",
        origin_subsystem="STRATEGIC", mechanism="STRATEGIC_COHORT", origin_lifecycle="CORE",
    )
    decision = Decision("2023-01-04", Opportunity.TREND, Risk.NORMAL, 0.2, 1, (target,), (), {}, "unit-fixture")
    before = deepcopy(account.to_dict())
    intervention = StrategicOwnerIntervention(owner="sz300308", target_gross=0.2)

    evidence = intervention.apply(account)
    preserved = intervention.preserve_activation(account, decision)

    assert preserved is decision
    assert account.to_dict() == before
    assert evidence["before_account_sha256"] == evidence["after_account_sha256"]
    assert "activation_counterfactual" not in evidence


def test_intervention_rejects_a_mixed_owner_without_partial_account_mutation() -> None:
    """Catches an owner rewrite that silently retains a second strategic owner."""

    account = AccountState.empty(1_000_000.0)
    account.strategic_cohort_symbols = ["sz300502", "sz300394"]
    account.strategic_cohort_targets = {"sz300502": 0.1, "sz300394": 0.1}
    before = deepcopy(account.to_dict())

    with pytest.raises(ValueError, match="mixed strategic owner"):
        StrategicOwnerIntervention(owner="sz300308", target_gross=0.2).apply(account)

    assert account.to_dict() == before


def test_intervention_rewrites_the_single_strategic_owner_atomically() -> None:
    """Catches a rewrite that changes the owner but not its target identity."""

    account = AccountState.empty(1_000_000.0)
    account.strategic_cohort_symbols = ["sz300502"]
    account.strategic_cohort_targets = {"sz300502": 0.1}
    account.strategic_exit_bands = {"sz300502": [0.1]}
    account.strategic_active_bands = {"sz300502": [True]}
    account.strategic_restore_weights = {"sz300502": 0.1}

    evidence = StrategicOwnerIntervention(owner="sz300308", target_gross=0.2).apply(account)

    assert account.strategic_cohort_symbols == ["sz300308"]
    assert account.strategic_cohort_targets == {"sz300308": 0.2}
    assert account.strategic_exit_bands == {"sz300308": [0.1]}
    assert evidence["before_account_sha256"] != evidence["after_account_sha256"]
    assert evidence["applied"] is True


def test_intervention_rejects_existing_forced_owner_key_collision() -> None:
    """Catches a dictionary rewrite that discards existing strategic owner state."""

    account = AccountState.empty(1_000_000.0)
    account.strategic_cohort_symbols = ["sz300502"]
    account.strategic_cohort_targets = {"sz300502": 0.1, "sz300308": 0.2}
    with pytest.raises(ValueError, match="collision"):
        StrategicOwnerIntervention(owner="sz300308", target_gross=0.2).apply(account)


def test_research_counterfactual_replaces_a_realized_epoch_as_one_identity_chain() -> None:
    """Catches historical order/fill rewrites leaving the durable epoch on the old owner."""

    account = AccountState.empty(1_000_000.0)
    account.account_identity = "account-test"
    source_identity = "production-source"
    evidence = "a" * 64
    old_owner = "sz300308"
    old_grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity,
        candidate_symbol=old_owner,
        qualification_signature="qualification:sz300308",
        qualification_route="established",
        qualification_evidence_sha256=evidence,
        created_session="2023-01-04",
        previous_grant_id="",
        production_source_identity=source_identity,
    )
    account.strategic_grant = StrategicGrantIntent(
        grant_id=old_grant_id,
        candidate_symbol=old_owner,
        qualification_signature="qualification:sz300308",
        qualification_route="established",
        qualification_evidence_sha256=evidence,
        created_session="2023-01-04",
        last_eligible_session="2023-01-04",
        filled_shares=100,
        target_weight=0.95,
        status=StrategicGrantStatus.COMPLETED.value,
        account_identity=account.account_identity,
        production_source_identity=source_identity,
        qualification_quorum="FULL_COHORT",
    )
    old_epoch_id = derive_strategic_epoch_id(
        account_identity=account.account_identity,
        owner_symbol=old_owner,
        qualification_signature="qualification:sz300308",
        qualification_route="established",
        grant_id=old_grant_id,
        opened_session="2023-01-04",
        previous_epoch_id="",
        source_identity=source_identity,
        config_identity="config:test",
        evidence_sha256=evidence,
    )
    account.strategic_epochs = [
        StrategicEpoch(
            epoch_id=old_epoch_id,
            owner_symbol=old_owner,
            qualification_signature="qualification:sz300308",
            qualification_route="established",
            qualification_quorum="FULL_COHORT",
            grant_id=old_grant_id,
            opened_session="2023-01-04",
            first_fill_session="2023-01-05",
            active_session="2023-01-05",
            source_identity=source_identity,
            config_identity="config:test",
            evidence_sha256=evidence,
            realized_status=StrategicEpochStatus.ACTIVE.value,
            target_weight=0.95,
            full_weight=0.95,
            account_identity=account.account_identity,
        )
    ]
    account.active_strategic_epoch_id = old_epoch_id
    account.protected_weight_epoch_ids = {old_owner: old_epoch_id}
    account.strategic_restore_epoch_ids = {old_owner: old_epoch_id}
    account.recovery_owner_epoch_id = old_epoch_id
    account.candidate_tenure["strategic_cohort_active"] = 1

    new_grant_id = _rewrite_grant(
        account,
        old=old_owner,
        new="sz300502",
        target_weight=0.95,
        session="2024-03-04",
    )
    new_epoch_id = _replace_counterfactual_epoch(
        account,
        old=old_owner,
        new="sz300502",
        grant_id=new_grant_id,
        session="2024-03-04",
        target_weight=0.95,
    )

    assert len(account.strategic_epochs) == 1
    replacement = account.strategic_epochs[0]
    assert replacement.owner_symbol == "sz300502"
    assert replacement.epoch_id == new_epoch_id != old_epoch_id
    assert replacement.grant_id == new_grant_id
    assert replacement.first_fill_session == "2023-01-05"
    assert replacement.active_session == "2023-01-05"
    assert replacement.realized_status == StrategicEpochStatus.ACTIVE.value
    assert account.active_strategic_epoch_id == new_epoch_id
    assert account.protected_weight_epoch_ids == {old_owner: new_epoch_id}
    assert account.strategic_restore_epoch_ids == {old_owner: new_epoch_id}
    assert account.recovery_owner_epoch_id == new_epoch_id
