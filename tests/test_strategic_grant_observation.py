from __future__ import annotations

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame

import uquant.portfolio.strategic.grant_lifecycle as strategic_grant_lifecycle
from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_epoch import (
    StrategicEpochStatus,
    record_account_strategic_epoch_fill,
)
from uquant.models.strategic_grant import StrategicGrantStatus
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, PendingOrder, Position, Risk, RiskAssessment


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
    original_epoch_id = account.strategic_grant.epoch_id
    account.pending_orders = [
        PendingOrder(
            signal_date=str(dates[-2].date()),
            symbol="sz300308",
            side="BUY",
            target_weight=0.20,
            reason="strategic owner",
            lifecycle="CORE",
            grant_id=original_grant_id,
            epoch_id=original_epoch_id,
        ),
        PendingOrder(
            signal_date=str(dates[-2].date()),
            symbol="sz300502",
            side="BUY",
            target_weight=0.20,
            reason="strategic peer",
            lifecycle="CORE",
            epoch_id=original_epoch_id,
        ),
    ]

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
    assert account.pending_orders == []
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
        strategic_grant_lifecycle,
        "strategic_qualification_evidence",
        lambda *_args, **_kwargs: (False, False),
    )
    monkeypatch.setattr(
        strategic_grant_lifecycle,
        "strategic_candidate_meets_route",
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


def test_absolute_qualification_loss_emits_a_formal_exit_for_a_filled_probe(
    monkeypatch,
) -> None:
    dates = pd.bdate_range("2023-01-02", periods=248)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.90 - index * 0.05, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    session = dates[-2]
    prices = {symbol: float(panel[symbol].loc[session, "close"]) for symbol in symbols}
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for observed_session in dates[-3:-1]:
        allocator.allocate(
            date=observed_session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )

    grant = account.strategic_grant
    assert grant is not None
    epoch = account.strategic_epochs[-1]
    epoch.qualification_quorum = "STRONG_PAIR"
    record_account_strategic_epoch_fill(
        account,
        epoch_id=epoch.epoch_id,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session=str(session.date()),
        filled_shares=10_000,
    )
    account.positions[grant.candidate_symbol] = Position(
        symbol=grant.candidate_symbol,
        shares=10_000,
        avg_cost=prices[grant.candidate_symbol],
        entry_date=str(session.date()),
        highest_close=prices[grant.candidate_symbol],
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    peer_symbol = next(symbol for symbol in symbols if symbol != grant.candidate_symbol)
    account.positions[peer_symbol] = Position(
        symbol=peer_symbol,
        shares=10_000,
        avg_cost=prices[peer_symbol],
        entry_date=str(session.date()),
        highest_close=prices[peer_symbol],
        epoch_id=epoch.epoch_id,
    )
    monkeypatch.setattr(
        strategic_grant_lifecycle,
        "strategic_qualification_evidence",
        lambda *_args, **_kwargs: (False, False),
    )
    monkeypatch.setattr(
        strategic_grant_lifecycle,
        "strategic_candidate_meets_route",
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

    owner_target = next(target for target in targets if target.symbol == grant.candidate_symbol)
    peer_target = next(target for target in targets if target.symbol == peer_symbol)
    assert owner_target.weight == 0.0
    assert owner_target.grant_id == grant.grant_id
    assert owner_target.epoch_id == epoch.epoch_id
    assert peer_target.weight == 0.0
    assert peer_target.grant_id == ""
    assert peer_target.epoch_id == epoch.epoch_id
    assert account.strategic_cohort_targets == {
        grant.candidate_symbol: 0.0,
        peer_symbol: 0.0,
    }
    assert grant.status == StrategicGrantStatus.EXPIRED.value
    assert epoch.realized_status == StrategicEpochStatus.CORE.value


@pytest.mark.parametrize("successor_qualified", (True, False))
def test_flat_expired_probe_releases_its_deployment_state(
    monkeypatch, successor_qualified: bool,
) -> None:
    dates = pd.bdate_range("2023-01-02", periods=249)
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
    for observed_session in dates[-4:-2]:
        allocator.allocate(
            date=observed_session,
            opportunity=Opportunity.TREND,
            risk=_risk(frozen=False),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices=prices,
        )

    grant = account.strategic_grant
    assert grant is not None
    epoch = account.strategic_epochs[-1]
    epoch.qualification_quorum = "STRONG_PAIR"
    record_account_strategic_epoch_fill(
        account,
        epoch_id=epoch.epoch_id,
        grant_id=grant.grant_id,
        symbol=grant.candidate_symbol,
        fill_session=str(dates[-2].date()),
        filled_shares=10_000,
    )
    account.positions[grant.candidate_symbol] = Position(
        symbol=grant.candidate_symbol,
        shares=10_000,
        avg_cost=prices[grant.candidate_symbol],
        entry_date=str(dates[-2].date()),
        highest_close=prices[grant.candidate_symbol],
        grant_id=grant.grant_id,
        epoch_id=epoch.epoch_id,
    )
    monkeypatch.setattr(
        strategic_grant_lifecycle,
        "strategic_candidate_meets_route",
        lambda *_args, **_kwargs: False,
    )
    allocator.allocate(
        date=dates[-2],
        opportunity=Opportunity.TREND,
        risk=_risk(frozen=False),
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices=prices,
    )
    assert account.candidate_tenure['strategic_repair_observed_session'] == dates[-2].toordinal()
    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sz300502'] == 3
    account.positions.clear()
    monkeypatch.undo()
    if not successor_qualified:
        leaders = {symbol: _leader(symbol, 0.0, mature=False) for symbol in symbols}
        for symbol, frame in panel.items():
            # Current price damage also invalidates the persistent/reversal routes.
            frame.loc[dates[-1], ["close", "ret20", "ret60"]] = [1.0, -0.20, -0.20]
            prices[symbol] = 1.0

    targets = allocator.allocate(
        date=dates[-1],
        opportunity=Opportunity.TREND,
        risk=_risk(frozen=False),
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices=prices,
    )

    assert epoch.realized_status == StrategicEpochStatus.EXPIRED.value
    assert epoch.close_reason == "candidate_or_route_no_longer_qualified"
    assert grant.status == StrategicGrantStatus.EXPIRED.value
    assert grant.expiry_reason == "candidate_or_route_no_longer_qualified"
    assert account.active_strategic_epoch_id == ""
    assert account.positions == {}
    assert account.candidate_tenure["strategic_repair_observed_session"] == dates[-1].toordinal()
    if not successor_qualified:
        assert account.strategic_grant is grant
        assert account.strategic_epochs == [epoch]
        assert account.candidate_tenure["strategic_cohort_active"] == 0
        assert account.strategic_cohort_symbols == []
        assert account.strategic_cohort_targets == {}
        assert targets == ()
        return

    successor = account.strategic_grant
    assert successor is not None and successor is not grant
    assert successor.candidate_symbol == "sz300502" != grant.candidate_symbol
    assert successor.previous_grant_id == grant.grant_id
    assert successor.grant_id != grant.grant_id
    assert successor.status == StrategicGrantStatus.PENDING_EXECUTION.value
    assert successor.filled_shares == 0
    assert successor.submitted_order_ids == []
    assert len(account.strategic_epochs) == 2
    successor_epoch = account.strategic_epochs[-1]
    assert successor_epoch.previous_epoch_id == epoch.epoch_id
    assert successor_epoch.owner_symbol == successor.candidate_symbol
    assert successor_epoch.grant_id == successor.grant_id
    assert successor_epoch.epoch_id == successor.epoch_id != epoch.epoch_id
    assert successor_epoch.realized_status == StrategicEpochStatus.PROBE.value
    assert successor_epoch.first_fill_session == successor_epoch.active_session == ""
    assert account.candidate_tenure["strategic_cohort_active"] == 1
    assert {target.symbol for target in targets} == set(account.strategic_cohort_symbols)
    assert all(target.epoch_id == successor.epoch_id for target in targets)
    assert account.replacement_tenure["strategic_eligibility:persistent_industry:sz300308"] == 1
    assert account.replacement_tenure["strategic_eligibility:persistent_industry:sz300502"] == 4


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
        strategic_grant_lifecycle,
        "strategic_qualification_evidence",
        lambda *_args, **_kwargs: (False, False),
    )
    monkeypatch.setattr(
        strategic_grant_lifecycle,
        "strategic_candidate_meets_route",
        lambda *_args, **_kwargs: True,
        raising=False,
    )

    valid = strategic_grant_lifecycle.revalidate_strategic_grant(
        allocator,
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
