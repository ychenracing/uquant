from __future__ import annotations

from copy import deepcopy

import pytest

from research.strategic_evidence.intervention import (
    StrategicOwnerIntervention,
    _replace_counterfactual_epoch,
    _rewrite_grant,
)
from uquant.models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    derive_strategic_epoch_id,
)
from uquant.types import (
    AccountState,
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)


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
