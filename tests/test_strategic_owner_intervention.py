from __future__ import annotations

from copy import deepcopy

import pytest

from research.strategic_evidence.intervention import StrategicOwnerIntervention
from uquant.types import AccountState


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
