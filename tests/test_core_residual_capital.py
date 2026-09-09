"""A smaller native CORE admission can consume genuinely unreserved cash."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_universe_quorum import _risk
from test_unified_core_book import _inputs, _ordinary_market_risk

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, Target
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe


def _funded_book(cash_fraction: float):
    _, panel, leaders, risk = _inputs()
    universe = default_ai_universe()
    as_of = str(next(iter(panel.values())).index[-4].date())
    by_industry = {}
    for symbol in sorted(universe.symbols_as_of(as_of)):
        by_industry.setdefault(universe.industry_of(symbol, as_of), symbol)
    selected = list(by_industry.values())[:3]
    names = dict(zip(panel, selected, strict=True))
    panel = {names[s]: frame for s, frame in panel.items()}
    leaders = {names[s]: replace(score, symbol=names[s], industry=universe.industry_of(names[s], as_of))
               for s, score in leaders.items()}
    symbols = tuple(panel)
    dates = panel[symbols[0]].index
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = "code:fixture", "data:fixture"
    initial_signal = str(dates[-4].date())
    initial = tuple(target for symbol in symbols[:2] for target in attach_target_attribution(
        leaders[symbol].industry, REQUIRED_AI_UNIVERSE_SHA256, signal_date=initial_signal,
        targets=(Target(
            symbol, (1.0 - cash_fraction) / 2, "CORE", .9, .9, "prior ordinary entry",
            origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE",
        ),),
    ))
    prices = dict.fromkeys(symbols, 10.0)
    execution = {symbol: pd.DataFrame(
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
         "volume": 100_000_000.0, "amount": 1_000_000_000.0}, index=dates[-4:],
    ) for symbol in symbols}
    orders = plan_orders(signal_date=initial_signal, targets=initial, account=account,
                         prices=prices, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=initial_signal,
    ))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-3], account=account, panel=execution,
    )
    assert len(fills) == 2 and all(fill.side == "BUY" for fill in fills)
    assert not account.pending_orders
    candidate = symbols[-1]
    panel[candidate] = _strategic_frame(dates)
    leaders[candidate] = _leader(candidate, .95, industry=leaders[candidate].industry)
    risk = _ordinary_market_risk(replace(_risk(), target_gross_cap=1.0))
    # Consume an existing current-session observation just as the production
    # allocation-book fixture does; this test isolates capital, not discovery.
    account.replacement_tenure[f"strategic_eligibility:independent_core:{candidate}"] = (
        DEFAULT_CONFIG.leader_tenure_days
    )
    account.candidate_tenure["strategic_eligibility_session"] = dates[-2].toordinal()
    return account, dates, panel, leaders, risk, prices, execution, candidate


@pytest.mark.parametrize("restriction", ["none", "below_minimum", "freeze", "ineligible"])
def test_native_residual_admission_preserves_healthy_holdings(restriction):
    account, dates, panel, leaders, risk, prices, execution, candidate = _funded_book(
        .04 if restriction == "below_minimum" else .12
    )
    if restriction == "freeze":
        risk = replace(risk, freeze_new_risk=True)
    if restriction == "ineligible":
        leaders[candidate] = replace(leaders[candidate], mature=False)
    before = {symbol: position.shares for symbol, position in account.positions.items()}
    available = account.cash / (account.cash + sum(shares * prices[s] for s, shares in before.items()))
    if restriction != "below_minimum":
        assert DEFAULT_CONFIG.min_trade_weight < available < DEFAULT_CONFIG.core_admission_weight
    else:
        assert available < DEFAULT_CONFIG.min_trade_weight
    signal = str(dates[-2].date())
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=dates[-2], opportunity=Opportunity.TREND, risk=risk,
        user_panel=panel, leaders=leaders, account=account, prices=prices,
    )
    weights = {target.symbol: target.weight for target in targets}
    if restriction == "none":
        assert risk.evidence["core_allocation"]["symbols"][candidate]["entry"]["block"] == "READY"
        assert weights.get(candidate, 0.0) == pytest.approx(available)
    else:
        assert weights.get(candidate, 0.0) == 0
    targets = tuple(attributed for target in targets for attributed in attach_target_attribution(
        leaders[target.symbol].industry, REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=signal, targets=(target,),
    ))
    orders = plan_orders(signal_date=signal, targets=targets, account=account,
                         prices=prices, cfg=DEFAULT_CONFIG)
    assert all(order.side == "BUY" and order.symbol == candidate for order in orders)
    assert not account.strategic_grant and not account.strategic_epochs
    if restriction != "none":
        assert not orders
        assert {s: p.shares for s, p in account.positions.items()} == before
        return
    assert len(orders) == 1 and not orders[0].grant_id and not orders[0].epoch_id
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=orders, submitted_date=signal,
    ))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-1], account=account, panel=execution)
    assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].symbol == candidate
    assert fills[0].shares > 0 and account.cash >= 0
    assert all(account.positions[s].shares == shares for s, shares in before.items())
