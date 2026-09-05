from __future__ import annotations

import copy

import pandas as pd
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_epoch import _epoch, _grant
from test_strategic_grant_observation import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_epoch import activate_strategic_epoch
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.discovery import observe_strategic_candidates
from uquant.types import AccountState


def test_active_owner_observes_all_candidates_without_capital_authority() -> None:
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
        observe_strategic_candidates(
            allocator,
            date=session,
            qualification_panel=panel,
            qualification_leaders=leaders,
            user_panel=panel,
            leaders=leaders,
            account=account,
            risk=_risk(frozen=False),
        )

    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sz300502'] == DEFAULT_CONFIG.strategic_cohort_confirm_days
    assert account.strategic_successor_qualification.candidate_symbol == ""
    assert account.strategic_cohort_targets == before_targets
    assert account.protected_weights == before_protected
    assert account.strategic_grant == before_grant
    assert account.pending_orders == []


def test_candidate_streak_survives_one_unavailable_other_reference() -> None:
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
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    tradable = {"sz300308", "sz300502"}

    for index, session in enumerate(dates[-2:]):
        available = set(symbols)
        if index == 1:
            available.remove("sh688008")
        roles = build_strategic_universe_roles(
            as_of=str(session.date()),
            tradable_symbols=tradable,
            qualification_reference_symbols=symbols,
            risk_reference_symbols=("sh000300", "sh000682"),
            industries={symbol: "optical" for symbol in symbols},
            available_symbols=(*available, "sh000300", "sh000682"),
        )
        observe_strategic_candidates(
            allocator,
            date=session,
            qualification_panel=panel,
            qualification_leaders=leaders,
            user_panel={symbol: panel[symbol] for symbol in tradable},
            leaders=leaders,
            account=account,
            risk=_risk(frozen=False),
            strategic_universe=roles,
        )

    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sz300502'] == 2
    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sh688008'] == 0
    assert account.strategic_successor_qualification.candidate_symbol == ""
    assert account.strategic_grant is grant
    assert account.pending_orders == []
