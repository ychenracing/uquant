from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import (
    _leader,
    _normal_risk,
    _trend_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    derive_strategic_epoch_id,
)
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    Lifecycle,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
    Target,
)


def test_bounded_probe_trail_preserves_full_epoch_exit_timing() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "bounded_owner"
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 1.0
    frame.loc[date, "ma20"] = 1.1
    frame.loc[date, "ret20"] = -0.10
    frame.loc[date, "atr"] = 0.05
    account_identity = "account:bounded-owner"
    evidence_sha256 = "a" * 64
    authorization_id = "rearm_" + "b" * 64
    grant_id = derive_strategic_grant_id(
        account_identity=account_identity,
        candidate_symbol=symbol,
        qualification_signature="qualification:bounded-owner",
        qualification_route="established",
        qualification_evidence_sha256=evidence_sha256,
        created_session="2025-07-01",
        previous_grant_id="",
        production_source_identity="code:production",
        authorization_id=authorization_id,
    )
    epoch_id = derive_strategic_epoch_id(
        account_identity=account_identity,
        owner_symbol=symbol,
        qualification_signature="qualification:bounded-owner",
        qualification_route="established",
        grant_id=grant_id,
        opened_session="2025-07-01",
        previous_epoch_id="",
        source_identity="code:production",
        config_identity="config:frozen",
        evidence_sha256=evidence_sha256,
    )
    grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=symbol,
        qualification_signature="qualification:bounded-owner",
        qualification_route="established",
        qualification_evidence_sha256=evidence_sha256,
        created_session="2025-07-01",
        last_eligible_session="2025-07-01",
        filled_shares=20,
        target_weight=0.20,
        status=StrategicGrantStatus.COMPLETED.value,
        account_identity=account_identity,
        production_source_identity="code:production",
        epoch_id=epoch_id,
        qualification_quorum="FULL_COHORT",
        authorization_id=authorization_id,
    )
    epoch = StrategicEpoch(
        epoch_id=epoch_id,
        owner_symbol=symbol,
        qualification_signature=grant.qualification_signature,
        qualification_route=grant.qualification_route,
        qualification_quorum=grant.qualification_quorum,
        grant_id=grant_id,
        opened_session=grant.created_session,
        first_fill_session="2025-07-02",
        active_session="2025-07-02",
        source_identity=grant.production_source_identity,
        config_identity="config:frozen",
        evidence_sha256=evidence_sha256,
        realized_status=StrategicEpochStatus.ACTIVE.value,
        target_weight=0.20,
        full_weight=0.50,
        account_identity=account_identity,
    )
    account = AccountState(
        initial_cash=100.0,
        cash=80.0,
        positions={
            symbol: Position(
                symbol,
                shares=20,
                avg_cost=0.50,
                highest_close=2.0,
                grant_id=grant_id,
                epoch_id=epoch_id,
            )
        },
        strategic_grant=grant,
        strategic_epochs=[epoch],
        active_strategic_epoch_id=epoch_id,
        account_identity=account_identity,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.20},
        strategic_candidate_signature="qualification:bounded-owner",
        strategic_epoch=1,
        capital_budget_level=2,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(
        DEFAULT_CONFIG.override(min_trade_value=0.0)
    )._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.20},
    )

    observed = {target.symbol: target.weight for target in targets or ()}
    assert observed == pytest.approx({symbol: 0.196}), observed
    bands = account.strategic_exit_bands[symbol]
    assert sum(bands) == pytest.approx(0.196)
    assert bands == pytest.approx([0.0392] * 5)


@pytest.mark.parametrize(
    "guard_owner_key",
    ("strategic_damage_guard_active_epoch", "strategic_damage_trim_epoch"),
)
def test_strategic_damage_guard_preserves_trail_owner_until_restore_completes(
    guard_owner_key: str,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "guarded_secular_member"
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 1.0
    frame.loc[date, "ma20"] = 1.1
    frame.loc[date, "ret20"] = -0.10
    frame.loc[date, "atr"] = 0.05
    account = AccountState(
        initial_cash=100.0,
        cash=70.0,
        positions={symbol: Position(symbol, shares=30, avg_cost=0.50, highest_close=2.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        strategic_restore_weights={symbol: 0.30},
        strategic_candidate_signature="strategic_qualification:SECULAR:guarded",
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            guard_owner_key: 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))
    guarded = RiskAssessment(
        Risk.CAUTION,
        DEFAULT_CONFIG.strategic_damage_guard_gross,
        2,
        {"freeze_new_risk": True, "transition_damage": 0.60},
        ("strategic transition damage",),
        "NONE",
        freeze_new_risk=True,
    )

    allocator._strategic_cohort_targets(
        date=date,
        risk=guarded,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert account.strategic_exit_bands == {}
    assert account.strategic_restore_weights == {symbol: 0.30}
    assert account.candidate_tenure[guard_owner_key] == 1

    still_damaged = RiskAssessment(
        Risk.NORMAL,
        1.0,
        4,
        {"transition_damage": DEFAULT_CONFIG.strategic_damage_guard_transition},
        (),
        "NONE",
    )
    allocator._strategic_cohort_targets(
        date=date,
        risk=still_damaged,
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert account.strategic_restore_weights == {symbol: 0.30}
    assert account.candidate_tenure[guard_owner_key] == 1

    allocator._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert account.strategic_exit_bands == {}
    assert account.strategic_restore_weights == {}
    assert account.candidate_tenure[guard_owner_key] == 0
    assert account.candidate_tenure["strategic_damage_guard_complete_epoch"] == 1

@pytest.mark.parametrize(
    ("capital_budget_owned", "expected_weight"),
    ((False, 0.10), (True, 0.29)),
)
def test_repaired_strategic_damage_guard_uses_a_decisive_next_profit_trail(
    capital_budget_owned: bool,
    expected_weight: float,
) -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "repaired_guard_member"
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 1.0
    frame.loc[date, "ma20"] = 1.1
    frame.loc[date, "ret20"] = -0.10
    frame.loc[date, "atr"] = 0.10
    account = AccountState(
        initial_cash=100.0,
        cash=70.0,
        positions={symbol: Position(symbol, shares=30, avg_cost=0.50, highest_close=2.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.30},
        strategic_candidate_signature="strategic_qualification:SECULAR:repaired",
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_damage_guard_active_epoch": 0,
            "strategic_damage_guard_complete_epoch": 1,
            **({"strategic_guard_level2_epoch": 1} if capital_budget_owned else {}),
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(
        DEFAULT_CONFIG.override(min_trade_value=0.0)
    )._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 1.0},
        weights_now={symbol: 0.30},
    )

    assert {target.symbol: target.weight for target in targets or ()} == pytest.approx(
        {symbol: expected_weight}
    )

def test_post_guard_trail_exits_acute_damage_faster_than_gradual_damage() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("gradual", "acute")
    frames = {symbol: _trend_frame(dates) for symbol in symbols}
    for frame in frames.values():
        frame.loc[date, "close"] = 1.0
        frame.loc[date, "ma20"] = 1.1
        frame.loc[date, "ret20"] = -0.16
        frame.loc[date, "ret60"] = -0.02
        frame.loc[date, "ret120"] = 0.70
        frame.loc[date, "atr"] = 0.10
    frames["gradual"].loc[date, "ret5"] = -0.05
    frames["acute"].loc[date, "ret5"] = -0.10
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(symbol, shares=30, avg_cost=0.50, highest_close=2.0)
            for symbol in symbols
        },
        strategic_cohort_symbols=list(symbols),
        strategic_cohort_targets={symbol: 0.30 for symbol in symbols},
        strategic_candidate_signature="strategic_qualification:SECULAR:ranked",
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_damage_guard_active_epoch": 0,
            "strategic_damage_guard_complete_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(
        DEFAULT_CONFIG.override(min_trade_value=0.0)
    )._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel=frames,
        leaders={
            "gradual": _leader("gradual", 0.80),
            "acute": _leader("acute", 0.90),
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
        weights_now={symbol: 0.30 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets or ()} == pytest.approx(
        {"gradual": 0.13, "acute": 0.10}
    )

def test_dominant_strategic_owner_locks_profit_once_without_staged_churn() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbol = "causal_dominant"
    frame = _trend_frame(dates)
    frame.loc[date, "close"] = 33.0
    frame.loc[date, "ma20"] = 30.0
    frame.loc[date, "ret20"] = 0.30
    frame.loc[date, "atr"] = 1.0
    account = AccountState(
        initial_cash=3_300.0,
        cash=0.0,
        positions={symbol: Position(symbol, shares=100, avg_cost=10.0, highest_close=33.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 1.0},
        strategic_candidate_signature=(
            "strategic_qualification:EMERGING_SECULAR:causal_dominant,runner"
            ":evidence=reversal_industry"
        ),
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=3_300.0,
        capital_peak=3_300.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG.override(min_trade_value=0.0))

    locked = allocator._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 33.0},
        weights_now={symbol: 1.0},
    )

    assert {target.symbol: target.weight for target in locked or ()} == pytest.approx(
        {symbol: DEFAULT_CONFIG.strategic_dominant_retained_gross}
    )
    assert account.candidate_tenure["strategic_dominant_profit_lock_epoch"] == 1
    assert account.strategic_exit_bands == {}

    frame.loc[date, "close"] = 20.0
    frame.loc[date, "ma20"] = 25.0
    frame.loc[date, "ret20"] = -0.20
    held = allocator._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={symbol: frame},
        leaders={symbol: _leader(symbol, 0.90)},
        account=account,
        prices={symbol: 20.0},
        weights_now={symbol: DEFAULT_CONFIG.strategic_dominant_retained_gross},
    )

    assert {target.symbol: target.weight for target in held or ()} == pytest.approx(
        {symbol: DEFAULT_CONFIG.strategic_dominant_retained_gross}
    )
    assert account.strategic_exit_bands == {}

def test_dominant_owner_respects_symbol_cap_and_hard_crisis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = "causal_dominant"
    account = AccountState(
        initial_cash=100.0,
        cash=0.0,
        positions={symbol: Position(symbol, shares=100, avg_cost=1.0, highest_close=1.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 1.0},
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    monkeypatch.setattr(
        allocator,
        "_allocate_strategy",
        lambda **_: (
            Target(symbol, 1.0, "CORE", 0.90, 1.0, "dominant strategic owner"),
        ),
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        0.82,
        2,
        {"transition_damage": 0.40},
        ("level-1 evidence freeze",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    retained = allocator.allocate(
        date=pd.Timestamp("2025-01-02"),
        opportunity=Opportunity.TREND,
        risk=caution,
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 1.0},
    )
    assert retained[0].weight == pytest.approx(
        DEFAULT_CONFIG.strategic_dominant_max_weight
    )

    crisis = RiskAssessment(
        Risk.CRISIS,
        0.25,
        5,
        {},
        ("hard crisis",),
        "SEVERE",
        freeze_new_risk=True,
        reduction_level=3,
    )
    reduced = allocator.allocate(
        date=pd.Timestamp("2025-01-03"),
        opportunity=Opportunity.WEAK,
        risk=crisis,
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 1.0},
    )
    assert reduced[0].weight == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("case", "risk_state", "reduction_level", "evidence", "target_weight", "second_weight"),
    (
        ("multiple live positions", Risk.CAUTION, 1, {}, 1.0, 0.10),
        ("crisis", Risk.CRISIS, 1, {}, 1.0, 0.0),
        ("higher reduction level", Risk.CAUTION, 2, {}, 1.0, 0.0),
        ("sector guard", Risk.CAUTION, 1, {"sector_guard_active": True}, 1.0, 0.0),
        (
            "strategic damage guard",
            Risk.CAUTION,
            1,
            {"strategic_damage_guard": True},
            1.0,
            0.0,
        ),
        (
            "acute evacuation",
            Risk.CAUTION,
            1,
            {"acute_sector_evacuation": True},
            1.0,
            0.0,
        ),
        ("strategy reduction", Risk.CAUTION, 1, {}, 0.90, 0.0),
    ),
)
def test_dominant_level1_retention_requires_every_bounded_predicate(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    risk_state: Risk,
    reduction_level: int,
    evidence: dict[str, bool],
    target_weight: float,
    second_weight: float,
) -> None:
    del case
    symbol = "bounded_dominant"
    positions = {
        symbol: Position(symbol, shares=90 if second_weight else 100, avg_cost=1.0, highest_close=1.0)
    }
    prices = {symbol: 1.0}
    if second_weight:
        positions["second_live_position"] = Position(
            "second_live_position",
            shares=10,
            avg_cost=1.0,
            highest_close=1.0,
        )
        prices["second_live_position"] = 1.0
    account = AccountState(
        initial_cash=100.0,
        cash=0.0,
        positions=positions,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 1.0},
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    monkeypatch.setattr(
        allocator,
        "_allocate_strategy",
        lambda **_: (
            Target(symbol, target_weight, "CORE", 0.90, 1.0, "dominant strategic owner"),
        ),
    )
    risk = RiskAssessment(
        risk_state,
        0.82,
        2,
        evidence,
        ("bounded retention negative case",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=reduction_level,
    )

    targets = allocator.allocate(
        date=pd.Timestamp("2025-01-02"),
        opportunity=Opportunity.TREND,
        risk=risk,
        user_panel={},
        leaders={},
        account=account,
        prices=prices,
    )

    assert sum(target.weight for target in targets) == pytest.approx(0.82)


def test_dominant_level1_retention_never_buys_up_to_the_exception_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = "bounded_dominant"
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={symbol: Position(symbol, shares=90, avg_cost=1.0, highest_close=1.0)},
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 1.0},
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    monkeypatch.setattr(
        allocator,
        "_allocate_strategy",
        lambda **_: (
            Target(symbol, 1.0, "CORE", 0.90, 1.0, "dominant strategic owner"),
        ),
    )
    caution = RiskAssessment(
        Risk.CAUTION,
        0.82,
        2,
        {},
        ("bounded retention",),
        "NONE",
        freeze_new_risk=True,
        reduction_level=1,
    )

    targets = allocator.allocate(
        date=pd.Timestamp("2025-01-02"),
        opportunity=Opportunity.TREND,
        risk=caution,
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 1.0},
    )

    assert sum(target.weight for target in targets) == pytest.approx(0.90)

def test_completed_strategic_epoch_clears_zero_exit_band_state():
    date = pd.Timestamp("2025-12-31")
    account = AccountState(
        initial_cash=100.0,
        cash=100.0,
        strategic_cohort_symbols=["completed_member"],
        strategic_exit_bands={"completed_member": [0.0] * 5},
        strategic_active_bands={"completed_member": [True] * 5},
        protected_weights={"completed_member": 0.30, "unrelated_recovery": 0.20},
        strategic_epoch=1,
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    result = PortfolioAllocator(DEFAULT_CONFIG)._strategic_cohort_targets(
        date=date,
        risk=_normal_risk(),
        user_panel={},
        leaders={},
        account=account,
        prices={},
        weights_now={},
    )

    assert result is None
    assert account.strategic_exit_bands == {}
    assert account.strategic_active_bands == {}
    assert account.protected_weights == {"unrelated_recovery": 0.20}
    assert account.candidate_tenure["strategic_cohort_started"] == 0
    assert account.strategic_epochs_completed == 1

def test_strategic_trail_exempts_a_winner_with_intact_structure():
    dates = pd.bdate_range("2025-01-02", periods=150)
    frame = _trend_frame(dates)
    date = dates[-1]
    frame.loc[date, "close"] = 1.50
    frame.loc[date, "ma20"] = 1.00
    frame.loc[date, "ret20"] = 0.30
    frame.loc[date, "atr"] = 0.05
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            "winner": Position(
                "winner",
                shares=40,
                avg_cost=0.50,
                entry_date=str(dates[-60].date()),
                highest_close=2.00,
            )
        },
        strategic_cohort_symbols=["winner"],
        strategic_cohort_targets={"winner": 0.60},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_cohort_started": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=_normal_risk(),
        user_panel={"winner": frame},
        leaders={"winner": _leader("winner", 0.95)},
        account=account,
        prices={"winner": 1.50},
    )

    assert account.strategic_exit_bands == {}
    assert next(target for target in targets if target.symbol == "winner").weight > 0

def test_completed_strategic_label_does_not_bypass_current_market_evidence():
    dates = pd.bdate_range("2025-01-02", periods=150)
    leaders = {"one": _leader("one", 0.95)}
    panel = {"one": _trend_frame(dates)}
    risk = RiskAssessment(
        Risk.NORMAL, 1.0, 0,
        {"broad_ret120": -0.10, "tech_ret120": -0.10}, (), "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for completed in (0, 1):
        account = AccountState.empty(1_000_000.0)
        account.candidate_tenure["strategic_cohort_completed"] = completed
        # An old coordination label cannot replace the current entry clock.
        account.candidate_tenure["leader_cycle_armed"] = 1
        targets = allocator.allocate(
            date=dates[-1], opportunity=Opportunity.STRONG_TREND, risk=risk,
            leaders=leaders, user_panel=panel, account=account, prices={"one": 1.0},
        )
        assert not any(target.weight > 0 for target in targets)
        assert account.positions == {}

def test_normal_level1_freeze_preserves_a_live_leader_owner() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "live_core"
    account = AccountState(
        initial_cash=100.0, cash=40.0,
        positions={symbol: Position(symbol, shares=60, avg_cost=1.0,
                                    lifecycle=Lifecycle.ADD2.value)},
        active_leaders=['live_core'],
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0, capital_peak=100.0,
    )
    freeze = RiskAssessment(
        Risk.NORMAL, 1.0, 1, {"freeze_new_risk": True},
        ("temporary level-1 capital freeze",), "NONE",
        freeze_new_risk=True, reduction_level=1,
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1], opportunity=Opportunity.TREND, risk=freeze,
        leaders={symbol: _leader(symbol, 0.95)},
        user_panel={symbol: _trend_frame(dates)},
        account=account, prices={symbol: 1.0},
    )
    assert {target.symbol: target.weight for target in targets} == {symbol: pytest.approx(0.60)}
    assert account.positions[symbol].shares == 60
    assert account.pending_orders == []

def test_normal_level1_freeze_preserves_armed_core_when_label_is_transiently_absent() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "live_core"
    account = AccountState(
        initial_cash=100.0, cash=40.0,
        positions={symbol: Position(symbol, shares=60, avg_cost=1.0,
                                    lifecycle=Lifecycle.ADD2.value)},
        active_leaders=[],
        candidate_tenure={"leader_cycle_armed": 1},
        operating_peak=100.0, capital_peak=100.0,
    )
    freeze = RiskAssessment(
        Risk.NORMAL, 1.0, 1, {"freeze_new_risk": True},
        ("temporary level-1 capital freeze",), "NONE",
        freeze_new_risk=True, reduction_level=1,
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1], opportunity=Opportunity.TREND, risk=freeze,
        leaders={symbol: _leader(symbol, 0.95)},
        user_panel={symbol: _trend_frame(dates)},
        account=account, prices={symbol: 1.0},
    )
    assert {target.symbol: target.weight for target in targets} == {symbol: pytest.approx(0.60)}
    assert account.positions[symbol].shares == 60
    assert account.pending_orders == []

def test_confirmed_live_core_waits_in_place_while_leader_owner_rearms() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("confirmed_core_a", "confirmed_core_b")
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=0.80,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in symbols
        },
        active_leaders=list(symbols),
        dynamic_k=2,
        last_k_change_date=str(date.date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.02, "tech_ret120": -0.02},
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=risk,
        user_panel={symbol: frame for symbol in symbols},
        leaders={
            symbols[0]: _leader(symbols[0], 0.90, industry="optical"),
            symbols[1]: _leader(symbols[1], 0.88, industry="equipment"),
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: 0.30 for symbol in symbols}
    )
    assert account.candidate_tenure.get("leader_cycle_armed", 0) == 0

def test_unconfirmed_entry_maturity_does_not_force_healthy_core_exits() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = (
        "healthy_core_a",
        "healthy_core_b",
        "temporarily_unconfirmed_core",
    )
    frame = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=10.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=0.80,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in symbols
        },
        active_leaders=list(symbols),
        dynamic_k=3,
        last_k_change_date=str(date.date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    risk = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.02, "tech_ret120": -0.02},
        (),
        "NONE",
    )

    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date,
        opportunity=Opportunity.STRONG_TREND,
        risk=risk,
        user_panel={symbol: frame for symbol in symbols},
        leaders={
            symbols[0]: _leader(symbols[0], 0.90, industry="optical"),
            symbols[1]: _leader(symbols[1], 0.88, industry="equipment"),
            symbols[2]: _leader(
                symbols[2],
                0.75,
                mature=False,
                industry="materials",
            ),
        },
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        {symbol: 0.30 for symbol in symbols}
    )
    # Entry maturity is not an exit signal for a physically held healthy name.
    assert account.replacement_tenure[f"lifecycle_exit:{symbols[2]}"] == 0
    assert account.cash == 10.0
    assert {symbol: position.shares for symbol, position in account.positions.items()} == {
        symbol: 30 for symbol in symbols
    }

def test_only_confirmed_structural_damage_exits_through_market_recovery() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    date = dates[-1]
    symbols = ("healthy_core", "temporarily_unconfirmed_core")
    healthy = _trend_frame(dates)
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={
            symbol: Position(
                symbol,
                shares=30,
                avg_cost=0.80,
                entry_date=str(dates[-20].date()),
                highest_close=1.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in symbols
        },
        active_leaders=list(symbols),
        dynamic_k=2,
        last_k_change_date=str(date.date()),
        operating_peak=100.0,
        capital_peak=100.0,
    )
    leaders = {
        symbols[0]: _leader(symbols[0], 0.90, industry="optical"),
        symbols[1]: _leader(
            symbols[1],
            0.75,
            mature=False,
            industry="equipment",
        ),
    }
    slow_market = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": -0.02, "tech_ret120": -0.02},
        (),
        "NONE",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    retained = allocator.allocate(
        date=dates[-4],
        opportunity=Opportunity.STRONG_TREND,
        risk=slow_market,
        user_panel={symbol: healthy for symbol in symbols},
        leaders=leaders,
        account=account,
        prices={symbol: 1.0 for symbol in symbols},
    )

    assert {target.symbol: target.weight for target in retained} == pytest.approx(
        {symbol: 0.30 for symbol in symbols}
    )
    assert all(
        account.replacement_tenure[f"lifecycle_exit:{symbol}"] == 0
        for symbol in symbols
    )

    aligned_market = RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"broad_ret120": 0.02, "tech_ret120": 0.02},
        (),
        "NONE",
    )
    broken = _trend_frame(
        dates, close=np.linspace(1.0, 0.70, len(dates)), ma20=1.0, ma60=1.05,
        ret20=-0.16, ret60=-0.16,
    )
    for observed in dates[-DEFAULT_CONFIG.replacement_confirm_days :]:
        retained = allocator.allocate(
            date=observed,
            opportunity=Opportunity.STRONG_TREND,
            risk=aligned_market,
            user_panel={symbols[0]: healthy, symbols[1]: broken},
            leaders=leaders,
            account=account,
            prices={symbol: 1.0 for symbol in symbols},
        )

    assert {target.symbol: target.weight for target in retained} == pytest.approx(
        {symbols[0]: 0.30, symbols[1]: 0.0}
    )

def test_synchronized_impulse_does_not_bypass_current_independent_confirmation() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "impulse"
    for weak_leg in (-0.001, -0.02):
        account = AccountState.empty(1_000_000.0)
        risk = RiskAssessment(
            Risk.NORMAL, 1.0, 0,
            {"broad_ret120": 0.034, "tech_ret120": weak_leg,
             "ai_fast_return": 0.161, "declining_ratio": 0.0,
             "below_ma20_ratio": 0.0, "tech_speed": 0.114, "broad_speed": 0.157},
            (), "NONE",
        )
        targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
            date=dates[-1], opportunity=Opportunity.TREND, risk=risk,
            user_panel={symbol: _trend_frame(dates)},
            leaders={symbol: _leader(symbol, 0.83)}, account=account,
            prices={symbol: 1.0},
        )
        assert not any(target.weight > 0 for target in targets)
        assert account.positions == {}

def test_completed_recovery_cycle_does_not_bypass_current_confirmation() -> None:
    dates = pd.bdate_range("2025-01-02", periods=150)
    symbol = "current_leader"
    account = AccountState.empty(1_000_000.0)
    account.candidate_tenure.update({
        "recovery_cycle_rearm_pending": 1, "tactical_cooldown": 0,
        "leader_cycle_armed": 1,
    })
    risk = RiskAssessment(
        Risk.NORMAL, 1.0, 0,
        {"broad_ret120": 0.10, "tech_ret120": 0.12, "trend_health": 0.84},
        (), "NONE",
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-1], opportunity=Opportunity.STRONG_TREND, risk=risk,
        leaders={symbol: _leader(symbol, 0.95)},
        user_panel={symbol: _trend_frame(dates)}, account=account, prices={symbol: 1.0},
    )
    assert not any(target.weight > 0 for target in targets)
    assert account.positions == {}
    assert account.candidate_tenure["recovery_cycle_rearm_pending"] == 1
