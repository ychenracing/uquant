"""Historical capital loss limits funded exposure after current risk repairs."""
from dataclasses import asdict, replace

import pytest
from test_ordinary_cash_rearm import SYMBOL, _decide, _scenario
from test_risk_transitions import _market_frame, _reference_context

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.reference import build_reference_context
from uquant.risk import assess_risk
from uquant.types import Risk


def _inputs():
    policy, account, dates, panel, leaders, _ = _scenario(sessions=0)
    account.capital_budget_level = 3
    account.capital_peak = account.cash / .78
    market = _market_frame(panel[SYMBOL].index)
    market['ret120'] = .04
    reference, reference_leaders, returns = _reference_context(panel[SYMBOL].index)
    return policy, account, dates, panel, leaders, market, reference, reference_leaders, returns


def _risk(inputs, date, *, context_change=None, with_context=True):
    policy, account, _, panel, leaders, market, reference, reference_leaders, returns = inputs
    context = build_reference_context(date=date, panel=reference, cfg=policy.cfg, reference_returns=returns)
    if context_change:
        context = replace(context, **context_change)
    return assess_risk(date=date, broad=market, tech=market, reference_panel=reference,
                       reference_returns=returns, user_panel=panel,
                       leaders={**reference_leaders, **leaders}, account=account,
                       equity=account.cash, cfg=policy.cfg,
                       reference_context=context if with_context else None)


@pytest.mark.parametrize("with_context", [True, False])
def test_five_healthy_sessions_reopen_cash_without_erasing_loss_or_capital_cap(with_context):
    inputs = _inputs()
    policy, account, dates, panel, leaders, *_ = inputs
    peak, cash = account.capital_peak, account.cash
    for date in dates[:4]:
        risk = _risk(inputs, date, with_context=with_context)
        assert risk.freeze_new_risk
        assert not _decide(policy, account, date, panel, leaders, risk)
    risk = _risk(inputs, dates[4], with_context=with_context)
    assert risk.state is Risk.NORMAL
    assert not risk.freeze_new_risk
    assert risk.target_gross_cap == pytest.approx(DEFAULT_CONFIG.capital_budget_level3_cap)
    targets = _decide(policy, account, dates[4], panel, leaders, risk)
    assert targets and 0 < sum(t.weight for t in targets) <= risk.target_gross_cap
    restored = account_from_dict(asdict(account))
    fills = ExecutionPlanner(policy.cfg).execute_open(date=dates[5], account=restored, panel=panel)
    assert fills and all(fill.side == 'BUY' and fill.shares > 0 for fill in fills)
    assert 0 < cash - restored.cash <= cash * risk.target_gross_cap
    assert restored.capital_peak == peak and restored.capital_budget_level == 3
    assert risk.evidence['capital_drawdown'] == pytest.approx(.22)


@pytest.mark.parametrize("failure", ["missing-session", "nonfinite-reference"])
def test_production_path_without_context_still_requires_current_reference_inputs(failure):
    inputs = _inputs()
    _, account, dates, _, _, _, reference, *_ = inputs
    for date in dates[:4]:
        _risk(inputs, date, with_context=False)
    assert account.risk_streaks["capital_reentry_days"] == 4
    symbol = next(iter(reference))
    if failure == "missing-session":
        reference[symbol] = reference[symbol].drop(dates[4])
    else:
        reference[symbol].loc[dates[4], "ret60"] = float("nan")
    assert _risk(inputs, dates[4], with_context=False).freeze_new_risk
    assert account.risk_streaks["capital_reentry_days"] == 0


def test_duplicate_scans_and_same_day_missing_inputs_cannot_advance_reentry():
    inputs = _inputs()
    dates = inputs[2]
    for _ in range(8):
        assert _risk(inputs, dates[0]).freeze_new_risk
    for date in dates[1:4]:
        assert _risk(inputs, date).freeze_new_risk
    assert not _risk(inputs, dates[4]).freeze_new_risk
    assert _risk(inputs, dates[4], context_change={"coverage": .5}).freeze_new_risk
    assert _risk(inputs, dates[4]).freeze_new_risk
    for date in dates[5:9]:
        assert _risk(inputs, date).freeze_new_risk
    assert not _risk(inputs, dates[9]).freeze_new_risk


@pytest.mark.parametrize("failure", ["skipped-session", "reference-coverage", "nonfinite-input"])
def test_interrupted_or_damaged_repair_keeps_new_capital_frozen(failure):
    inputs = _inputs()
    _, account, dates, _, _, market, *_ = inputs
    for date in dates[:4]:
        assert _risk(inputs, date).freeze_new_risk
    if failure == "skipped-session":
        risk = _risk(inputs, dates[5])
    elif failure == "reference-coverage":
        risk = _risk(inputs, dates[4], context_change={"coverage": .5})
    elif failure == "nonfinite-input":
        market.loc[dates[4], "ret60"] = float("nan")
        risk = _risk(inputs, dates[4])
    assert risk.freeze_new_risk
    assert account.capital_budget_level == 3
    assert risk.target_gross_cap <= DEFAULT_CONFIG.capital_budget_level3_cap


def test_live_chronic_damage_retains_its_independent_capital_freeze():
    from uquant.risk.capital import apply_capital_overlays

    inputs = _inputs()
    _, account, dates, *_ = inputs
    for date in dates[:5]:
        _risk(inputs, date)
    # Assess the active owner at the overlay boundary; the full engine clears
    # stale chronic levels on all-cash books before reaching this boundary.
    account.chronic_level = 2
    overlay = apply_capital_overlays(
        date=dates[5], previous_session=dates[4], current_inputs_complete=True,
        independent_damage=False, account=account, cfg=DEFAULT_CONFIG,
        observed_budget_level=0, transition_damage=.1, votes=0,
        held_damage_ratio=0., capital_dd=.22, operating_dd=0., strategic_damage_guard=False,
    )
    assert overlay.freeze_new_risk
    assert overlay.overlay_cap <= DEFAULT_CONFIG.capital_budget_level3_cap
    assert account.capital_budget_level == 3
