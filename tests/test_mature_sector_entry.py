"""Retired industry-only shortcut cannot replace current ordinary entry proof."""
from dataclasses import replace

import pytest
from test_lifecycle_and_risk import _leader
from test_ordinary_trend_budget import _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.leader import apply_leader_tenure


def _sector(*, failure=None):
    policy, account, dates, original, _, risk = _scenario()
    frame = next(iter(original.values()))
    symbols = ("sz300308", "sz300394", "sz300502")
    panel = {symbol: frame.copy(deep=True) for symbol in symbols}
    base = {symbol: _leader(symbol, .88 - .02 * index, industry="optical")
            for index, symbol in enumerate(symbols)}
    risk.evidence.update(ai_fast_return=.01, tech_speed=.01, broad_speed=.01)
    if failure == "low_peer":
        base[symbols[-1]] = replace(base[symbols[-1]], score=.81)
    elif failure == "other_industry":
        base[symbols[-1]] = replace(base[symbols[-1]], industry="semicap")
    elif failure == "reference_only":
        panel.pop(symbols[-1])
    elif failure == "broken_peer":
        panel[symbols[-1]].loc[dates[:5], "close"] = .001
    elif failure == "illiquid_peer":
        panel[symbols[-1]]["amount"] = 100.
        panel[symbols[-1]]["volume"] = 100.
    elif failure == "missing_market_data":
        risk.evidence.pop("tech_speed")
    elif failure == "frozen_account":
        risk = replace(risk, freeze_new_risk=True,
                       evidence={**risk.evidence, "freeze_new_risk": True})
    targets = []
    for index, date in enumerate(dates[:5]):
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        targets = _decide(policy, account, date, panel, leaders, risk)
        if index < 4:
            assert not account.pending_orders
    return policy, account, dates, panel, leaders, risk, targets


def test_mature_sector_alone_cannot_fund_native_ordinary_entry():
    _, account, dates, panel, leaders, risk, targets = _sector()
    assert not risk.evidence["core_allocation"]["ordinary_market"]["impulse"]
    assert account.strategic_grant is None
    assert all(account.replacement_tenure.get(f"strategic_eligibility:independent_core:{s}", 0) == 0
               for s in leaders)
    assert not targets and not account.pending_orders
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert not fills and not account.positions
    assert account.cash == DEFAULT_CONFIG.initial_cash


@pytest.mark.parametrize("failure", ("low_peer", "other_industry", "reference_only", "broken_peer",
                                     "illiquid_peer", "missing_market_data", "frozen_account"))
def test_incomplete_sector_or_frozen_account_cannot_buy(failure):
    _, account, _, _, _, _, _ = _sector(failure=failure)
    assert not account.pending_orders


def test_stale_sector_proof_cannot_authorize_a_new_session():
    from uquant.portfolio.ordinary import ordinary_core_entry

    policy, account, dates, panel, leaders, risk, _ = _sector()
    symbol = next(iter(leaders))
    entry = ordinary_core_entry(
        policy, symbol=symbol, score=leaders[symbol], date=dates[5], user_panel=panel,
        account=account, confirmation_days=DEFAULT_CONFIG.leader_tenure_days,
        market=risk.evidence["core_allocation"]["ordinary_market"],
    )
    assert entry["block"] == "CONFIRMATION_INCOMPLETE"
