from __future__ import annotations

import pandas as pd
from test_lifecycle_and_risk import _leader, _strategic_frame

import uquant.portfolio.strategic.discovery as strategic_discovery
from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_grant import StrategicGrantStatus
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, Risk, RiskAssessment


def _risk(*, frozen: bool) -> RiskAssessment:
    return RiskAssessment(
        Risk.CAUTION if frozen else Risk.NORMAL,
        0.60 if frozen else 1.0,
        1 if frozen else 0,
        {
            "configured_user_universe_size": 3,
            "risk_anchor_group_count": 3,
            "breadth20": 1.0,
            "broad_ret20": 0.05,
            "tech_ret20": 0.05,
            "broad_ret120": 0.0,
            "tech_ret120": 0.0,
            "freeze_new_risk": frozen,
        },
        ("capital deployment blocked",) if frozen else (),
        "NONE",
        freeze_new_risk=frozen,
        reduction_level=1 if frozen else 0,
    )


def test_freeze_observes_qualification_without_deploying_then_authorizes() -> None:
    dates = pd.bdate_range("2023-01-02", periods=247)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.05, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    prices = {symbol: float(panel[symbol].loc[dates[-1], "close"]) for symbol in symbols}
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    for session in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days :]:
        frozen_targets = allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=True),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )
        assert not any(target.weight > 0.0 for target in frozen_targets)

    observed = account.strategic_qualification
    assert observed.candidate_symbol == "sz300308"
    assert observed.qualification_ready is True
    assert observed.deployment_blocked is True
    assert observed.deployment_block_reason == "freeze_new_risk"
    assert observed.qualification_streak == DEFAULT_CONFIG.strategic_cohort_confirm_days
    assert account.strategic_grant is None
    assert account.strategic_cohort_targets == {}

    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=_risk(frozen=False),
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices=prices,
    )

    assert any(target.weight > 0.0 for target in targets)
    assert account.strategic_qualification.qualification_streak >= (
        DEFAULT_CONFIG.strategic_cohort_confirm_days
    )
    assert account.strategic_qualification.deployment_blocked is False
    assert account.strategic_grant is not None
    assert account.strategic_grant.status == StrategicGrantStatus.PENDING_EXECUTION.value
    assert account.strategic_grant.candidate_symbol == "sz300308"


def test_risk_off_records_candidate_without_creating_a_target_owner() -> None:
    dates = pd.bdate_range("2023-01-02", periods=247)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {symbol: _leader(symbol, 0.90, industry="optical") for symbol in symbols}
    prices = {symbol: float(panel[symbol].loc[dates[-1], "close"]) for symbol in symbols}
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    risk = _risk(frozen=True)
    risk = RiskAssessment(
        Risk.RISK_OFF,
        0.0,
        3,
        risk.evidence,
        ("risk off",),
        "BREAK",
        freeze_new_risk=True,
        reduction_level=3,
    )

    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.WEAK,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices=prices,
    )

    assert not any(target.weight > 0.0 for target in targets)
    assert account.strategic_qualification.candidate_symbol
    assert account.strategic_qualification.deployment_blocked is True
    assert account.strategic_qualification.deployment_block_reason == "risk_off"
    assert account.strategic_grant is None


def test_candidate_removal_expires_the_grant_without_promoting_a_runner() -> None:
    dates = pd.bdate_range("2023-01-02", periods=248)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.05, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    prices = {symbol: float(panel[symbol].loc[dates[-1], "close"]) for symbol in symbols}
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for session in dates[-3:-1]:
        allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )
    assert account.strategic_grant is not None
    original_grant_id = account.strategic_grant.grant_id

    reduced_panel = {symbol: panel[symbol] for symbol in symbols if symbol != "sz300308"}
    reduced_leaders = {symbol: leaders[symbol] for symbol in reduced_panel}
    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=_risk(frozen=False),
        user_panel=reduced_panel,
        leaders=reduced_leaders,
        account=account,
        prices={symbol: prices[symbol] for symbol in reduced_panel},
    )

    assert targets == ()
    assert account.strategic_grant.grant_id == original_grant_id
    assert account.strategic_grant.status == StrategicGrantStatus.EXPIRED.value
    assert account.strategic_grant.expiry_reason == "candidate_removed_from_allowed_universe"
    assert account.strategic_qualification.candidate_invalidation_reason == (
        "candidate_removed_from_allowed_universe"
    )
    assert account.strategic_cohort_targets == {}


def test_absolute_qualification_loss_expires_partial_grant(monkeypatch) -> None:
    dates = pd.bdate_range("2023-01-02", periods=248)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.05, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    prices = {symbol: float(panel[symbol].loc[dates[-1], "close"]) for symbol in symbols}
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for session in dates[-3:-1]:
        allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )
    assert account.strategic_grant is not None
    account.strategic_grant.status = StrategicGrantStatus.PARTIALLY_FILLED.value
    monkeypatch.setattr(
        strategic_discovery,
        "_qualification_evidence",
        lambda *_args, **_kwargs: (False, False),
    )
    monkeypatch.setattr(
        strategic_discovery,
        "_grant_candidate_meets_route",
        lambda *_args, **_kwargs: False,
        raising=False,
    )

    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=_risk(frozen=False),
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices=prices,
    )

    assert account.strategic_grant.status == StrategicGrantStatus.EXPIRED.value
    assert account.strategic_grant.expiry_reason == "candidate_or_route_no_longer_qualified"
    assert targets == ()


def test_sentinel_freeze_observes_qualification_without_zhongji_universe() -> None:
    dates = pd.bdate_range("2023-01-02", periods=247)
    symbols = ("sz300502", "sz300394", "sh688008")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.05, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    prices = {symbol: float(panel[symbol].loc[dates[-1], "close"]) for symbol in symbols}
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    base = _risk(frozen=True)
    sentinel_risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {
            **base.evidence,
            "base_freeze_new_risk": False,
            "sentinel_freeze_new_risk": True,
        },
        (),
        "NONE",
        freeze_new_risk=True,
        reduction_level=0,
    )

    for session in dates[-5:]:
        targets = allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=sentinel_risk,
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )
        assert not any(target.weight > 0.0 for target in targets)

    observed = account.strategic_qualification
    assert observed.candidate_symbol == "sz300502"
    assert observed.qualification_ready is True
    assert observed.qualification_streak >= DEFAULT_CONFIG.strategic_cohort_confirm_days
    assert observed.deployment_blocked is True
    assert observed.deployment_block_reason == "freeze_new_risk"
    assert account.strategic_grant is None


def test_missing_route_observation_preserves_still_qualified_partial_grant(
    monkeypatch,
) -> None:
    dates = pd.bdate_range("2023-01-02", periods=248)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.05, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    prices = {symbol: float(panel[symbol].loc[dates[-1], "close"]) for symbol in symbols}
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for session in dates[-3:-1]:
        allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )
    assert account.strategic_grant is not None
    account.strategic_grant.status = StrategicGrantStatus.PARTIALLY_FILLED.value
    monkeypatch.setattr(
        strategic_discovery,
        "_qualification_evidence",
        lambda *_args, **_kwargs: (False, False),
    )
    monkeypatch.setattr(
        strategic_discovery,
        "_grant_candidate_meets_route",
        lambda *_args, **_kwargs: True,
        raising=False,
    )

    valid = allocator._revalidate_strategic_grant(
        date=dates[-1],
        user_panel=panel,
        leaders=leaders,
        account=account,
        risk=_risk(frozen=False),
        admission_open=True,
        weights_now={},
    )

    assert valid is True
    assert account.strategic_grant.status == StrategicGrantStatus.PARTIALLY_FILLED.value
    assert account.strategic_grant.expiry_reason == ""
