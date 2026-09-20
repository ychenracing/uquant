"""Historic losses remain facts without permanently freezing repaired exposure."""
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.risk.capital import apply_capital_overlays, observe_capital_budget
from uquant.risk_sector import SectorGuardTransition
from uquant.types import AccountState


@pytest.mark.parametrize('deployed_dd,expected', [(0.0, 0), (0.15, 1)])
def test_repair_uses_current_exposure_and_preserves_history(deployed_dd, expected):
    account = AccountState.empty(2_000_000)
    account.cash = 1_755_651
    account.capital_peak = 2_024_610
    account.operating_peak = account.cash
    account.capital_budget_level = 1
    facts = (account.cash, account.initial_cash, account.capital_peak, account.operating_peak)
    for _ in range(DEFAULT_CONFIG.capital_budget_repair_days):
        apply_capital_overlays(account=account, cfg=DEFAULT_CONFIG, observed_budget_level=0,
            transition_damage=0.1, votes=0, held_damage_ratio=0,
            deployed_dd=deployed_dd, strategic_damage_guard=False)
    assert account.capital_budget_level == expected
    assert (account.cash, account.initial_cash, account.capital_peak, account.operating_peak) == facts
    # Current renewed damage still escalates immediately after repair.
    result = apply_capital_overlays(account=account, cfg=DEFAULT_CONFIG, observed_budget_level=3,
        transition_damage=0.8, votes=4, held_damage_ratio=1,
        deployed_dd=deployed_dd, strategic_damage_guard=False)
    assert account.capital_budget_level == 3
    assert result.freeze_new_risk


def test_confirmed_repair_reopens_admission_inside_remaining_cap_and_refreezes_on_relapse():
    account = AccountState.empty(100)
    account.capital_budget_level = 4
    for day in range(DEFAULT_CONFIG.capital_budget_repair_days):
        result = apply_capital_overlays(account=account, cfg=DEFAULT_CONFIG, observed_budget_level=0,
            transition_damage=0.1, votes=0, held_damage_ratio=0,
            deployed_dd=0, strategic_damage_guard=False)
        assert result.freeze_new_risk == (day + 1 < DEFAULT_CONFIG.capital_budget_repair_days)
    assert account.capital_budget_level == 3
    assert result.overlay_cap == DEFAULT_CONFIG.capital_budget_level3_cap
    result = apply_capital_overlays(account=account, cfg=DEFAULT_CONFIG, observed_budget_level=0,
        transition_damage=0.8, votes=4, held_damage_ratio=1,
        deployed_dd=0.1, strategic_damage_guard=False)
    assert result.freeze_new_risk
    assert account.capital_budget_repair_streak == 0
    assert account.capital_budget_level == 3
    assert result.overlay_cap == DEFAULT_CONFIG.capital_budget_level3_cap


@pytest.mark.parametrize('deployed_dd,expected', [(0.0, 0), (0.13, 1)])
def test_damage_and_repair_use_the_same_current_exposure(deployed_dd, expected):
    account = AccountState.empty(2_000_000)
    account.cash = 1_755_651
    account.capital_peak = 2_024_610
    result = observe_capital_budget(account=account, cfg=DEFAULT_CONFIG,
        sector_guard=SectorGuardTransition(False, False, False, False, 0, 0, None),
        reference_anchor_break=False, held_damage_ratio=0, transition_damage=0.6, votes=2,
        capital_dd=0.13, deployed_dd=deployed_dd, sector_stress=0.2, strategic_active=False)
    assert result.observed_budget_level == expected
    assert account.capital_peak == 2_024_610


@pytest.mark.parametrize('deployed_dd,expected', [(0.0, False), (0.20, True)])
def test_historical_loss_does_not_manufacture_current_damage_vote(deployed_dd, expected):
    from uquant.risk.market_book import _apply_live_book_votes, _HeldBookState, _VotingState
    state = _VotingState([], dict.fromkeys(('sector_breadth_shock', 'below_ma20_structure',
        'multi_industry_sync', 'correlation_shock', 'volatility_shock', 'leader_failure',
        'index_velocity'), False), {}, 0)
    _apply_live_book_votes(state, held=_HeldBookState([], [], 0.0, 0.0, 1.0),
        guard=SectorGuardTransition(False, False, False, False, 0, 0, None),
        operating_dd=0.0, deployed_dd=deployed_dd, cfg=DEFAULT_CONFIG)
    assert state.indicators['capital_damage'] is expected


@pytest.mark.parametrize('deployed_dd,expected', [(0.0, 'CAUTION'), (0.25, 'CRISIS')])
def test_general_crisis_uses_current_episode_damage(deployed_dd, expected):
    from types import SimpleNamespace

    from uquant.risk.transition_resolution import _observed_risk
    account = AccountState.empty(2_000_000)
    account.capital_peak = 2_500_000
    context = SimpleNamespace(account=account, cfg=DEFAULT_CONFIG, shock_rearmed=True,
        capital_dd=0.25, deployed_dd=deployed_dd, operating_dd=0.0, votes=4, narrow_anchor_guard=False,
        sector_stress=0.8, independent_damage=False, reasons=[])
    assert _observed_risk(context).value == expected
    assert account.capital_peak == 2_500_000


def test_deployed_peak_survives_repair_and_restart_until_actual_flat_book():
    from test_account_schema_v3_integrity import _position_state

    from uquant.account import account_from_dict
    from uquant.risk.capital import deployed_drawdown

    account = _position_state()
    account.capital_peak = 3_000_000.0
    assert deployed_drawdown(account, 2_400_000.0) == 0.0
    account.operating_peak = 1_800_000.0  # A confirmed repair starts a shorter operating cycle.
    assert deployed_drawdown(account, 1_800_000.0) == pytest.approx(.25)
    restored = account_from_dict(account.to_dict(), require_hashes=False)
    assert restored.deployed_peak == 2_400_000.0
    assert deployed_drawdown(restored, 1_800_000.0) == pytest.approx(.25)
    restored.positions.clear()
    restored.cash = 1_800_000.0
    assert deployed_drawdown(restored, 1_800_000.0) == 0.0
    assert restored.deployed_peak == 1_800_000.0
    assert restored.capital_peak == 3_000_000.0


@pytest.mark.parametrize('held,expected', [(False, 75.0), (True, 150.0)])
def test_legacy_peak_initialization_uses_existing_facts(held, expected):
    from test_account_schema_v3_integrity import _position_state

    from uquant.account import account_from_dict

    account = _position_state() if held else AccountState.empty(100.0)
    account.cash = 75.0
    account.capital_peak = 150.0
    raw = account.to_dict()
    raw.pop('deployed_peak')
    decoded = account_from_dict(raw, require_hashes=False)
    assert decoded.deployed_peak == expected
    assert {k: v for k, v in decoded.to_dict().items() if k != 'deployed_peak'} == raw


@pytest.mark.parametrize('value', [-1.0, float('nan'), 'invalid'])
def test_deployed_peak_rejects_invalid_durable_values(value):
    from uquant.account import account_from_dict

    raw = AccountState.empty(100.0).to_dict()
    raw['deployed_peak'] = value
    with pytest.raises(RuntimeError, match='deployed_peak'):
        account_from_dict(raw, require_hashes=False)


@pytest.mark.parametrize('capital_dd,deployed_dd,damage,votes,expected', [
    (0.25, 0.0, False, 4, 0),
    (0.13, 0.09, True, 2, 2),
    (0.18, 0.09, True, 4, 3),
    (0.25, 0.09, True, 4, 4),
    (0.25, 0.07, True, 4, 4),
    (0.13, 0.09, False, 2, 1),
])
def test_accumulated_loss_reduces_budget_only_with_independent_damage(capital_dd, deployed_dd, damage, votes, expected):
    from uquant.risk.capital import _observed_capital_budget_level
    assert _observed_capital_budget_level(
        capital_dd=capital_dd, deployed_dd=deployed_dd, independent_damage=damage,
        worsening_damage=damage, votes=votes, sector_stress=0.8,
        transition_damage=0.8, held_damage_ratio=float(damage), cfg=DEFAULT_CONFIG,
    ) == expected
