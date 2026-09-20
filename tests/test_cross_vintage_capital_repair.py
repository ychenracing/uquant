"""Historic losses remain facts without permanently freezing repaired exposure."""
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.risk.capital import apply_capital_overlays, observe_capital_budget
from uquant.risk_sector import SectorGuardTransition
from uquant.types import AccountState


@pytest.mark.parametrize('operating_dd,expected', [(0.0, 0), (0.15, 1)])
def test_repair_uses_current_exposure_and_preserves_history(operating_dd, expected):
    account = AccountState.empty(2_000_000)
    account.cash = 1_755_651
    account.capital_peak = 2_024_610
    account.operating_peak = account.cash
    account.capital_budget_level = 1
    facts = (account.cash, account.initial_cash, account.capital_peak, account.operating_peak)
    for _ in range(DEFAULT_CONFIG.capital_budget_repair_days):
        apply_capital_overlays(account=account, cfg=DEFAULT_CONFIG, observed_budget_level=0,
            transition_damage=0.1, votes=0, held_damage_ratio=0,
            operating_dd=operating_dd, strategic_damage_guard=False)
    assert account.capital_budget_level == expected
    assert (account.cash, account.initial_cash, account.capital_peak, account.operating_peak) == facts
    # Current renewed damage still escalates immediately after repair.
    result = apply_capital_overlays(account=account, cfg=DEFAULT_CONFIG, observed_budget_level=3,
        transition_damage=0.8, votes=4, held_damage_ratio=1,
        operating_dd=operating_dd, strategic_damage_guard=False)
    assert account.capital_budget_level == 3
    assert result.freeze_new_risk


@pytest.mark.parametrize('operating_dd,expected', [(0.0, 0), (0.13, 1)])
def test_damage_and_repair_use_the_same_current_exposure(operating_dd, expected):
    account = AccountState.empty(2_000_000)
    account.cash = 1_755_651
    account.capital_peak = 2_024_610
    result = observe_capital_budget(account=account, cfg=DEFAULT_CONFIG,
        sector_guard=SectorGuardTransition(False, False, False, False, 0, 0, None),
        reference_anchor_break=False, held_damage_ratio=0, transition_damage=0.6, votes=2,
        operating_dd=operating_dd, sector_stress=0.2, strategic_active=False)
    assert result.observed_budget_level == expected
    assert account.capital_peak == 2_024_610


@pytest.mark.parametrize('operating_dd,expected', [(0.0, False), (0.20, True)])
def test_historical_loss_does_not_manufacture_current_damage_vote(operating_dd, expected):
    from uquant.risk.market_book import _apply_live_book_votes, _HeldBookState, _VotingState
    state = _VotingState([], dict.fromkeys(('sector_breadth_shock', 'below_ma20_structure',
        'multi_industry_sync', 'correlation_shock', 'volatility_shock', 'leader_failure',
        'index_velocity'), False), {}, 0)
    _apply_live_book_votes(state, held=_HeldBookState([], [], 0.0, 0.0, 1.0),
        guard=SectorGuardTransition(False, False, False, False, 0, 0, None),
        operating_dd=operating_dd, capital_dd=0.25, cfg=DEFAULT_CONFIG)
    assert state.indicators['capital_damage'] is expected


@pytest.mark.parametrize('operating_dd,expected', [(0.0, 'CAUTION'), (0.25, 'CRISIS')])
def test_general_crisis_uses_current_episode_damage(operating_dd, expected):
    from types import SimpleNamespace

    from uquant.risk.transition_resolution import _observed_risk
    account = AccountState.empty(2_000_000)
    account.capital_peak = 2_500_000
    context = SimpleNamespace(account=account, cfg=DEFAULT_CONFIG, shock_rearmed=True,
        capital_dd=0.25, operating_dd=operating_dd, votes=4, narrow_anchor_guard=False,
        sector_stress=0.8, independent_damage=False, reasons=[])
    assert _observed_risk(context).value == expected
    assert account.capital_peak == 2_500_000
