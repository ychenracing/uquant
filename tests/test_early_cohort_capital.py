"""Founding cohort targets distinguish early members from mature conviction."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame, _trend_frame
from test_strategic_grant_observation import _risk

from uquant.account import load_account, save_account
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio_core import strategic_dominant_symbol
from uquant.types import AccountState, Opportunity, Risk, RiskAssessment
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _mixed_cohort():
    dates = pd.bdate_range("2023-01-02", periods=251)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {symbol: _leader(symbol, .96 - index * .02, industry="optical",
                               mature=index != 2)
               for index, symbol in enumerate(symbols)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity, account.code_hash = "account:fixture", "code:fixture"
    account.data_hash = sha256("".join(
        symbol + frame.to_csv() for symbol, frame in sorted(panel.items())
    ).encode()).hexdigest()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    targets = ()
    for date in dates[-5:-3]:
        targets = policy.allocate(date=date, opportunity=Opportunity.TREND, risk=_risk(frozen=False),
                                  user_panel=panel, leaders=leaders, account=account,
                                  prices={s: float(panel[s].loc[date, "close"]) for s in symbols})
    assert account.strategic_grant is not None
    assert account.strategic_grant.qualification_quorum == "FULL_COHORT"
    assert strategic_dominant_symbol(account) is None
    return policy, account, dates, panel, leaders, targets, symbols


def test_mixed_founding_cohort_caps_only_immature_members_without_redistribution():
    _, account, _, _, _, targets, symbols = _mixed_cohort()
    expected = {symbols[0]: 1 / 3, symbols[1]: 1 / 3,
                symbols[2]: DEFAULT_CONFIG.core_admission_weight}
    assert account.strategic_cohort_targets == pytest.approx(expected)
    assert {t.symbol: t.weight for t in targets} == pytest.approx(expected)
    assert sum(expected.values()) < DEFAULT_CONFIG.max_gross


def test_partial_founding_buy_keeps_its_cap_and_identity_after_restart(tmp_path):
    policy, account, dates, panel, leaders, targets, symbols = _mixed_cohort()
    immature = symbols[-1]
    signal = str(dates[-4].date())
    attributed = attach_target_attribution("optical", REQUIRED_AI_UNIVERSE_SHA256,
                                          signal_date=signal, targets=targets)
    orders = plan_orders(signal_date=signal, targets=attributed, account=account,
                         prices={s: 3.0 for s in symbols}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=signal,
    ))
    execution = {s: pd.DataFrame({
        "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.0,
        "volume": [100_000_000.0, 1_000_000.0, 100_000_000.0, 100_000_000.0],
        "amount": [300_000_000.0, 3_000_000.0, 300_000_000.0, 300_000_000.0],
    }, index=dates[-4:]) for s in symbols}
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-3], account=account, panel=execution)
    assert len(fills) == 3 and all(f.side == "BUY" and f.shares > 0 for f in fills)
    pending = next(o for o in account.pending_orders if o.symbol == immature)
    original_identity = (pending.order_id, pending.event_id, pending.grant_id, pending.epoch_id)
    original_requested = next(o.requested_shares for o in account.order_ledger if o.order_id == pending.order_id)
    assert pending.target_weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    save_account(account, tmp_path / "partial.json")
    restored = load_account(tmp_path / "partial.json")
    # A subsequent maturity observation does not enlarge the founding grant.
    leaders[immature] = replace(leaders[immature], mature=True)
    resumed = policy.allocate(date=dates[-2], opportunity=Opportunity.TREND, risk=_risk(frozen=False),
                              user_panel=panel, leaders=leaders, account=restored,
                              prices={s: 3.0 for s in symbols})
    target = next(t for t in resumed if t.symbol == immature)
    assert target.weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    new_signal = str(dates[-2].date())
    attributed = attach_target_attribution("optical", REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=new_signal, targets=resumed, retained_orders=restored.pending_orders)
    planned = plan_orders(signal_date=new_signal, targets=attributed, account=restored,
                          prices={s: 3.0 for s in symbols}, cfg=DEFAULT_CONFIG)
    restored.pending_orders = list(reconcile_account_orders(
        account=restored, previous=restored.pending_orders, current=planned, submitted_date=new_signal,
    ))
    retained = next(o for o in restored.pending_orders if o.symbol == immature)
    assert (retained.order_id, retained.event_id, retained.grant_id, retained.epoch_id) == original_identity
    assert next(o.requested_shares for o in restored.order_ledger if o.order_id == retained.order_id) == original_requested
    completed = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-1], account=restored, panel=execution)
    assert any(f.symbol == immature and f.side == "BUY" for f in completed)
    assert sum(f.shares for f in restored.fills if f.order_id == retained.order_id) == original_requested


def test_actual_decisive_reversal_keeps_dominant_cap_even_when_immature():
    dates = pd.bdate_range("2023-01-02", periods=250)
    panel = {}
    for symbol, base in (("dominant", .69), ("runner", .725), ("reserve", .73)):
        close = np.concatenate((np.linspace(1., .68, len(dates) - 5), np.linspace(.69, .74, 5)))
        close[-61:-5] = np.linspace(base, .68, 56)
        panel[symbol] = _trend_frame(dates, close=close, ma20=.70, ma60=.72, ret20=.08, ret60=.07)
        panel[symbol]["atr"] = .02
    leaders = {s: _leader(s, score, industry="optical", mature=False)
               for s, score in (("dominant", .70), ("runner", .60), ("reserve", .20))}
    leaders["runner"].components["trend_persistence"] = 1 / 3
    risk = RiskAssessment(Risk.NORMAL, 1., 1, {"tech_ret120": -.10, "risk_anchor_symbols": [],
                          "risk_anchor_group_count": 0, "configured_user_universe_size": 3}, (), "NONE")
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity, account.code_hash = "account:fixture", "code:fixture"
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-2:]:
        policy._initialize_strategic_cohort(date=date, user_panel=panel, leaders=leaders,
                                            account=account, risk=risk)
    assert account.strategic_grant is not None
    assert strategic_dominant_symbol(account) == "dominant"
    assert account.strategic_cohort_targets == {"dominant": DEFAULT_CONFIG.strategic_dominant_max_weight}
