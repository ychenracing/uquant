"""Short reversal proof uses current market and common deployment permission."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame, _trend_frame

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.discovery import (
    strategic_qualification_evidence,
    strategic_qualification_snapshots,
    strategic_route_admission_open,
)
from uquant.portfolio.strategic.qualification_candidates import strategic_route_candidates
from uquant.types import AccountState, Risk, RiskAssessment

SYMBOLS = ("sh688008", "sh688012", "sh688200")


def _inputs(*, persistent=False, market=True, coverage=True):
    dates = pd.bdate_range("2023-01-02", periods=250)
    close = np.concatenate((np.linspace(1., .68, 245), np.linspace(.69, .74, 5)))
    panel = {symbol: _strategic_frame(dates) if persistent else _trend_frame(
        dates, close=close, ma20=.70, ma60=.72, ret20=.08, ret60=.07) for symbol in SYMBOLS}
    leaders = {symbol: _leader(symbol, .20 if persistent else .70, industry="compute") for symbol in SYMBOLS}
    risk = RiskAssessment(Risk.NORMAL, 1., 0, {
        "breadth20": .80 if market else .20, "broad_ret20": .05, "tech_ret20": .05,
        "broad_ret120": -.02, "tech_ret120": -.03,
        "risk_anchor_symbols": [], "risk_anchor_group_count": 3 if coverage else 0,
        "configured_user_universe_size": 3,
    }, (), "NONE")
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    snapshots = strategic_qualification_snapshots(policy, date=dates[-1], user_panel=panel, leaders=leaders)
    family = "persistent_industry" if persistent else "reversal_industry"
    route = next(route for route in strategic_route_candidates(policy, snapshots=snapshots, leaders=leaders, risk=risk)
                 if route.route == family and set(route.symbols) == set(SYMBOLS))
    assert route.anchors_not_yet_armed
    if not persistent:
        assert route.synchronized_reversal
    return policy, dates, panel, leaders, risk, snapshots, route


@pytest.mark.parametrize("market,coverage", ((False, True), (True, False), (False, False)))
def test_startup_reversal_cannot_waive_current_market_or_coverage(market, coverage):
    policy, _, _, leaders, risk, snapshots, route = _inputs(market=market, coverage=coverage)
    raw, startup = strategic_qualification_evidence(policy, route=route, snapshots=snapshots,
                                                    leaders=leaders, risk=risk)
    assert not startup and not raw
    assert not strategic_route_admission_open(policy, route=route, symbols=route.symbols,
                                              snapshots=snapshots, admission_open=False,
                                              synchronized_before_anchor=startup)


@pytest.mark.parametrize("admission_open", (False, True))
def test_confirmed_reversal_still_requires_common_admission(admission_open):
    policy, _, _, leaders, risk, snapshots, route = _inputs()
    raw, startup = strategic_qualification_evidence(policy, route=route, snapshots=snapshots,
                                                    leaders=leaders, risk=risk)
    assert raw and not startup
    assert strategic_route_admission_open(policy, route=route, symbols=route.symbols,
                                          snapshots=snapshots, admission_open=admission_open,
                                          synchronized_before_anchor=startup) is admission_open


def test_hard_persistent_startup_exception_keeps_original_long_evidence():
    policy, _, _, leaders, risk, snapshots, route = _inputs(persistent=True, market=False, coverage=False)
    assert all(snapshots[symbol]["persistent_ret240"] >= DEFAULT_CONFIG.strategic_cohort_min_ret240
               for symbol in route.symbols)
    raw, startup = strategic_qualification_evidence(policy, route=route, snapshots=snapshots,
                                                    leaders=leaders, risk=risk)
    assert raw and startup
    assert strategic_route_admission_open(policy, route=route, symbols=route.symbols,
                                          snapshots=snapshots, admission_open=False,
                                          synchronized_before_anchor=startup)


def test_native_reversal_confirmation_is_still_per_session_and_not_preseeded():
    policy, dates, panel, leaders, risk, _, _ = _inputs()
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = "account:reversal-native"
    account.code_hash = "source:reversal-native"
    for index, date in enumerate(dates[-2:]):
        for _ in range(2):
            policy._initialize_strategic_cohort(date=date, user_panel=panel, leaders=leaders,
                                               account=account, risk=risk, admission_open=True)
        if index == 0:
            assert account.strategic_grant is None
        else:
            assert account.strategic_grant is not None
            assert account.strategic_grant.qualification_route == "reversal_industry"
            assert account.strategic_qualification.qualification_streak == DEFAULT_CONFIG.strategic_cohort_confirm_days


def test_current_market_entry_gate_does_not_revoke_continuous_risk_restoration():
    from test_shared_core_qualification import _decide
    from test_strategic_flat_retirement import _compress, _deployed
    from test_strategic_grant_observation import _risk

    from uquant.execution import ExecutionPlanner

    policy, account, dates, panel, leaders, roles = _compress(_deployed(), .30)
    before = {symbol: position.shares for symbol, position in account.positions.items()}
    risk = _risk(frozen=False)
    risk = replace(risk, evidence={**risk.evidence, "breadth20": .20, "risk_anchor_group_count": 0})
    _decide(policy, account, dates[0], panel, leaders, roles, risk=risk)
    assert account.pending_orders and all(order.side == "BUY" and order.mechanism == "STRATEGIC_RESTORATION"
                                         for order in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert fills and all(fill.side == "BUY" for fill in fills)
    assert all(account.positions[symbol].shares >= shares for symbol, shares in before.items())
