from __future__ import annotations

import copy

import pandas as pd
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_epoch import _epoch, _grant
from test_strategic_grant_observation import _risk

from uquant.models.strategic_epoch import activate_strategic_epoch
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState
from uquant.config import DEFAULT_CONFIG


def test_active_owner_observes_successor_without_capital_authority() -> None:
    dates = pd.bdate_range("2023-01-02", periods=250)
    symbols = ("sz300308", "sz300502", "sz300394", "sh688008")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(
            symbol,
            0.99 if symbol == "sz300502" else 0.92,
            industry="optical",
        )
        for symbol in symbols
    }
    grant = _grant(created="2023-11-30")
    epoch = _epoch(grant)
    grant.epoch_id = epoch.epoch_id
    activate_strategic_epoch(
        epoch,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session="2023-12-01",
        filled_shares=100,
    )
    account = AccountState.empty(2_000_000.0)
    account.account_identity = grant.account_identity
    account.strategic_grant = grant
    account.strategic_epochs = [epoch]
    account.active_strategic_epoch_id = epoch.epoch_id
    account.candidate_tenure["strategic_cohort_active"] = 1
    account.strategic_cohort_symbols = [grant.candidate_symbol]
    account.strategic_cohort_targets = {grant.candidate_symbol: 0.50}
    account.protected_weights = {grant.candidate_symbol: 0.10}
    account.protected_weight_epoch_ids = {grant.candidate_symbol: epoch.epoch_id}
    before_targets = copy.deepcopy(account.strategic_cohort_targets)
    before_protected = copy.deepcopy(account.protected_weights)
    before_grant = copy.deepcopy(account.strategic_grant)
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for session in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        allocator._observe_strategic_successor(
            date=session,
            qualification_panel=panel,
            qualification_leaders=leaders,
            tradable_symbols=set(symbols),
            account=account,
            risk=_risk(frozen=False),
        )

    observation = account.strategic_successor_qualification
    assert observation.candidate_symbol == "sz300502"
    assert observation.qualification_ready is True
    assert observation.deployment_blocked is True
    assert observation.deployment_block_reason == "active_epoch_read_only"
    assert observation.qualification_last_observed_session == str(dates[-1].date())
    assert account.strategic_cohort_targets == before_targets
    assert account.protected_weights == before_protected
    assert account.strategic_grant == before_grant
    assert account.pending_orders == []
