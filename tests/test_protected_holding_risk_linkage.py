"""Restoration risk and shock snapshots follow the current holding episode."""

from __future__ import annotations

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _reference_context, _risk_frame

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.risk import assess_risk
from uquant.risk.confirmed_break import ConfirmedBreakContext, _prepare_confirmed_break
from uquant.risk.recovery_state import assess_recovery_state
from uquant.risk.transition_resolution import _prepare_new_crisis, _RiskTransitionContext
from uquant.risk.transitions import _acute_evacuation_assessment, _AcuteContext
from uquant.risk_sector import SectorGuardTransition
from uquant.types import AccountState, Risk, Target
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

SYMBOL = "sh688008"
NEW_SYMBOL = "sh688012"
FLAT_SYMBOL = "sh688200"


def _fill_target(account, dates, signal_index, symbol, weight, mechanism="LEADER_SELECTION"):
    signal = str(dates[signal_index].date())
    origin = "RISK" if mechanism == "RISK_OFF" else "RECOVERY" if mechanism == "POST_SHOCK_RESTORATION" else "LEADER"
    targets = attach_target_attribution(
        "semiconductor", REQUIRED_AI_UNIVERSE_SHA256, signal_date=signal,
        targets=(Target(symbol, weight, "CORE", 0.9, 0.9, "fixture holding history",
                        origin_subsystem=origin, mechanism=mechanism, origin_lifecycle="CORE"),),
    )
    orders = plan_orders(signal_date=signal, targets=targets, account=account,
                         prices=dict.fromkeys({*account.positions, symbol}, 10.0), cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=account.pending_orders, current=orders, submitted_date=signal,
    ))
    panel = {symbol: pd.DataFrame(
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
         "volume": 100_000_000.0, "amount": 1_000_000_000.0}, index=dates,
    )}
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[signal_index + 1], account=account, panel=panel,
    )
    assert len(fills) == 1 and not account.pending_orders
    return fills[0]


def _holding(history):
    dates = pd.bdate_range("2025-01-02", periods=200)
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = "code:fixture", "data:fixture"
    first = _fill_target(account, dates, -131, SYMBOL, 0.2)
    assert first.shares == 39_900
    account.last_shock_date = str(dates[-129].date())
    account.protected_weights = {SYMBOL: 0.6}
    if history == "fifo":
        restored = _fill_target(account, dates, -129, SYMBOL, 0.6, "POST_SHOCK_RESTORATION")
        reduced = _fill_target(account, dates, -128, SYMBOL, 0.3, "RISK_OFF")
        assert (restored.shares, reduced.shares) == (79_900, 59_800)
        assert account.positions[SYMBOL].shares == 60_000
        assert account.positions[SYMBOL].entry_date > account.last_shock_date
        assert {lot.entry_date for lot in account.positions[SYMBOL].tranches} == {restored.fill_date}
    elif history == "stale_flat":
        account.protected_weights = {FLAT_SYMBOL: 0.6}
        account.last_shock_date = "2023-04-27"
    elif history == "reentered":
        account.last_shock_date = "2023-04-27"
    account.risk = Risk.CAUTION.value
    account.risk_events = [{
        "date": str(dates[-20].date()), "from": Risk.CRISIS.value, "to": Risk.CAUTION.value,
    }]
    return account, dates


@pytest.mark.parametrize("relapse", ("capital_impaired", "market_backed"))
@pytest.mark.parametrize("history", ("stale_flat", "reentered", "held_through_shock", "fifo"))
def test_restoration_relapse_requires_a_protected_continuous_holding(history, relapse):
    account, dates = _holding(history)
    price = 5.0 if relapse == "capital_impaired" else 20.0
    frame = _risk_frame(dates, close=price, ma20=price * 1.2, ret5=-0.10)
    equity = account.cash + account.positions[SYMBOL].shares * price
    account.operating_peak = equity / 0.9
    account.capital_peak = equity / 0.75
    before_cash, before_shares = account.cash, account.positions[SYMBOL].shares

    observed = assess_recovery_state(
        date=dates[-1], tech=frame, user_panel={SYMBOL: frame},
        leaders={SYMBOL: _leader(SYMBOL, 0.8)}, account=account, equity=equity, cfg=DEFAULT_CONFIG,
        shock_rearmed=True, strategic_active=False, operating_dd=0.10, capital_dd=0.25,
        recovery_anchor_elapsed=0, emergency_tail_break=False, concentrated_structure_break=False,
        immediate_severe_break=False, persistent_market_break=False, reference_anchor_armed=False,
        held_damage_ratio=1.0, votes=4, sector_stress=1.0, immediate_reference_break=False,
        anchor_break_key="unused_reference_break", held_cohort_break_confirmed=False, strategic_tail_break=False,
    )

    linked = history in {"held_through_shock", "fifo"}
    assert observed.capital_impaired_restoration_relapse is (linked and relapse == "capital_impaired")
    assert observed.market_backed_restoration_relapse is (linked and relapse == "market_backed")
    assert observed.capital_drawdown_relapse is linked
    assert account.cash == before_cash and account.positions[SYMBOL].shares == before_shares


def _capture_shock(route, *, date, panel, account, equity):
    common = dict(
        date=date, user_panel=panel, account=account, equity=equity, cfg=DEFAULT_CONFIG,
        votes=4, sector_stress=1.0, operating_dd=0.20, capital_dd=0.25,
        held_ret5=[-0.15], strategic_active=False,
    )
    sector_guard = SectorGuardTransition(False, False, False, False, 0, 0, None)
    if route == "risk_transition":
        _prepare_new_crisis(_RiskTransitionContext(
            **common, previous=Risk.NORMAL, shock_rearmed=True, narrow_anchor_guard=False,
            independent_damage=True, reasons=[], sector_guard=sector_guard,
            credible_reserve=False, overlay_cap=1.0,
        ))
        return
    evidence = dict(
        continuous_evidence={}, market_context={}, average_fast=-0.15,
        declining=1.0, below=1.0, correlation=0.8, vol_ratio=2.0, leader_failure=1.0,
        held_damage_ratio=1.0, held_repair_ratio=0.0, tech_speed=-0.15, broad_speed=-0.15,
        strategic_current_gross=0.0,
    )
    if route == "confirmed_break":
        _prepare_confirmed_break(ConfirmedBreakContext(
            **common, **evidence, leaders={symbol: _leader(symbol, 0.8) for symbol in panel},
            previous=Risk.NORMAL, overlay_cap=1.0, credible_reserve=False,
            capital_impaired_restoration_relapse=False, market_backed_restoration_relapse=False,
            terminal_market_backed_restoration_relapse=False, incomplete_universe_tail_break=False,
            reference_anchor_confirmed=False, held_cohort_break_confirmed=False,
            capital_drawdown_relapse=False, immediate_reference_break=False,
        ))
    else:
        _acute_evacuation_assessment(
            _AcuteContext(**common, **evidence, sector_guard=sector_guard,
                          concentrated_confirmed=False, held_loss_ratio=1.0),
            previous=Risk.NORMAL, single_observation=None,
        )


@pytest.mark.parametrize("route", ("confirmed_break", "acute_evacuation", "risk_transition"))
@pytest.mark.parametrize("mixed", (False, True))
def test_new_shock_captures_current_holdings_without_reusing_unlinked_old_rights(route, mixed):
    account, dates = _holding("held_through_shock" if mixed else "reentered")
    if mixed:
        _fill_target(account, dates, -30, NEW_SYMBOL, 0.1)
        account.protected_weights[NEW_SYMBOL] = 0.4
    account.protected_weights[FLAT_SYMBOL] = 0.4
    panel = {symbol: pd.DataFrame({"close": 10.0}, index=dates) for symbol in account.positions}
    equity = account.cash + sum(position.shares * 10.0 for position in account.positions.values())
    expected = {symbol: position.shares * 10.0 / equity for symbol, position in account.positions.items()}
    if mixed:
        expected[SYMBOL] = 0.6
    before_cash = account.cash
    before_shares = {symbol: position.shares for symbol, position in account.positions.items()}

    _capture_shock(route, date=dates[-1], panel=panel, account=account, equity=equity)

    assert account.protected_weights == pytest.approx(expected)
    assert account.last_shock_date == str(dates[-1].date())
    assert account.cash == before_cash
    assert {symbol: position.shares for symbol, position in account.positions.items()} == before_shares


@pytest.mark.parametrize("strategic_owner", (False, True))
def test_base_risk_sector_observation_ignores_ordinary_tombstones_but_retains_strategic_rights(strategic_owner):
    account, dates = _holding("stale_flat")
    account.sector_guard_active = True
    account.sector_guard_started = str(dates[-2].date())
    account.sector_guard_symbols = [SYMBOL]
    if strategic_owner:
        account.strategic_epoch = 1
        account.strategic_cohort_symbols = [FLAT_SYMBOL]
        account.strategic_cohort_targets = {FLAT_SYMBOL: 0.6}
    healthy = _risk_frame(dates, close=120.0, ma20=100.0, ret5=0.05)
    healthy["ret120"] = 0.10
    reference_panel, reference_leaders = _reference_context(healthy)
    held = _risk_frame(dates, close=12.0, ma20=10.0, ret5=0.05)
    held.loc[dates[-2], "close"] = 12.0 / 1.02
    old = _risk_frame(dates, close=9.0, ma20=10.0, ret5=-0.10)
    old.loc[dates[-2], "close"] = 10.0
    equity = account.cash + account.positions[SYMBOL].shares * 12.0
    account.operating_peak = equity
    account.capital_peak = equity / 0.95

    assessment = assess_risk(
        date=dates[-1], broad=healthy, tech=healthy, reference_panel=reference_panel,
        reference_returns=None, user_panel={SYMBOL: held, FLAT_SYMBOL: old},
        leaders={**reference_leaders, SYMBOL: _leader(SYMBOL, 0.8), FLAT_SYMBOL: _leader(FLAT_SYMBOL, 0.8)},
        account=account, equity=equity, cfg=DEFAULT_CONFIG,
    )

    assert assessment.evidence["sector_guard_equal_return"] == pytest.approx(-0.04 if strategic_owner else 0.02)
    assert account.protected_weights == {FLAT_SYMBOL: 0.6}
