"""Scarce restoration capital follows current strength without moving held capital."""

from dataclasses import replace

import pytest
from test_core_bounded_risk_restoration import _submit
from test_unified_core_book import _inputs

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, Risk, RiskAssessment, Target


@pytest.mark.parametrize("strong", ["sh688008", "sh688012"])
def test_restoration_uses_current_strength_and_retains_existing_shares(strong):
    _, raw, raw_scores, _ = _inputs()
    source = {"sh688008": raw["sh600001"], "sh688012": raw["sh600002"]}
    scores = {s: replace(raw_scores[old], symbol=s) for s, old in
              (("sh688008", "sh600001"), ("sh688012", "sh600002"))}
    symbols = ["sh688008", "sh688012"]
    panel = {s: source[s].copy() for s in symbols}
    for frame in panel.values():
        for key, value in {"open": 10., "close": 10., "high": 10.1, "low": 9.9,
                           "ma20": 9.5, "ma60": 9., "volume": 100_000_000.}.items():
            frame.loc[frame.index[-6:], key] = value
    dates = panel[symbols[0]].index[-6:]
    leaders = {s: replace(scores[s], score=.95 if s == strong else .8,
                          mature=False) for s in symbols}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    targets = tuple(Target(s, .3, "CORE", .9, .9, "prior core entry",
                           origin_subsystem="LEADER", mechanism="LEADER_SELECTION",
                           origin_lifecycle="CORE") for s in symbols)
    _submit(account, dates[0], targets)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 2 and all(f.side == "BUY" for f in fills)
    account.last_shock_date = str(dates[2].date())
    account.protected_weights = dict.fromkeys(symbols, .3)
    hard = RiskAssessment(Risk.RISK_OFF, .2, 3, {}, ("compression",), "SHOCK",
                          freeze_new_risk=True, reduction_level=2)

    def allocate(day, risk):
        return policy.allocate(date=day, opportunity=Opportunity.RECOVERY, risk=risk,
                               user_panel=panel, leaders=leaders, account=account,
                               prices=dict.fromkeys(symbols, 10.))

    compression = tuple(replace(t, weight=.1) for t in allocate(dates[2], hard))
    _submit(account, dates[2], compression)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
    assert len(fills) == 2 and all(f.side == "SELL" for f in fills)
    held = {s: p.shares for s, p in account.positions.items()}
    assert set(held) == set(symbols)
    account.capital_budget_level = account.capital_budget_repair_streak = 1
    repair = RiskAssessment(Risk.CAUTION, .4, 0, {"transition_damage": .1}, (), "RECOVERY",
                            freeze_new_risk=True, reduction_level=1)
    targets = allocate(dates[4], repair)
    by_symbol = {t.symbol: t for t in targets}
    equity = account.cash + sum(held.values()) * 10.
    other_weight = sum(shares * 10. / equity for s, shares in held.items() if s != strong)
    assert by_symbol[strong].weight == pytest.approx(min(.3, .4 - other_weight))
    assert sum(t.weight for t in targets) <= .4 + 1e-12
    assert {s: p.shares for s, p in account.positions.items()} == held
    orders = _submit(account, dates[4], targets)
    assert len(orders) == 1 and orders[0].symbol == strong and orders[0].side == "BUY"
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == strong and account.cash >= 0
