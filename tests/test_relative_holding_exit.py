"""Market-relative holding damage is distinct from current buying proof."""
from copy import deepcopy

import pytest
from test_slow_structural_exit import setup_case


@pytest.mark.parametrize("own,reference,expected", [
    (-.05, -.20, False), (.05, .10, False), (-.20, .05, True),
    (-.20, float("nan"), True), (float("nan"), -.20, True),
])
def test_relative_damage_requires_absolute_and_relative_loss(own, reference, expected):
    policy, account, symbol, frame, leader = setup_case()
    frame["ret60"] = own
    outcomes = []
    for date in frame.index[-3:]:
        account = deepcopy(account)
        outcomes.append(policy._leader_lifecycle_exit_confirmed(
            symbol=symbol, date=date, user_panel={symbol: frame},
            leaders={symbol: leader}, account=account, reference_return=reference))
    assert outcomes == [False, False, expected]


def test_relative_recovery_resets_the_same_confirmation_clock():
    policy, account, symbol, frame, leader = setup_case()
    frame["ret60"] = -.10
    outcomes = []
    for date, reference in zip(frame.index[-5:], [.05, .05, -.20, .05, .05], strict=True):
        outcomes.append(policy._leader_lifecycle_exit_confirmed(
            symbol=symbol, date=date, user_panel={symbol: frame},
            leaders={symbol: leader}, account=account, reference_return=reference))
    assert not any(outcomes)
    assert account.replacement_tenure[f"lifecycle_exit:{symbol}"] == 2


@pytest.mark.parametrize("reference,exits", [(-.20, False), (.05, True)])
def test_native_pipeline_uses_reference_and_keeps_restart_order_identity(reference, exits):
    from dataclasses import asdict, replace

    from test_shared_core_qualification import _decide
    from test_strategic_cohort_deployment_settlement import SYMBOLS, _native_full
    from test_strategic_grant_observation import _risk

    from uquant.account.codec import account_from_dict
    from uquant.config import DEFAULT_CONFIG

    policy, account, dates, panel, leaders, roles = _native_full()
    base = _risk(frozen=False)
    risk = replace(base, evidence={**base.evidence, "tech_ret60": reference, "tech_ret60_observed": True})
    _decide(policy, account, dates[0], panel, leaders, roles, risk=risk)
    symbol = SYMBOLS[0]
    leaders[symbol] = replace(leaders[symbol], mature=False)
    start = DEFAULT_CONFIG.min_hold_days + 2
    for day in dates[start:start + DEFAULT_CONFIG.replacement_confirm_days]:
        for key in ("ma20", "ma60"):
            panel[symbol].loc[day, key] = panel[symbol].loc[day, "close"] * 1.1
        panel[symbol].loc[day, "ret60"] = -.05
        account = account_from_dict(asdict(account))
        _decide(policy, account, day, panel, leaders, roles, risk=risk)
    orders = [o for o in account.pending_orders if o.symbol == symbol and o.side == "SELL"]
    assert bool(orders) == exits
    if exits:
        assert orders[0].target_weight == 0
        before = asdict(orders[0])
        account = account_from_dict(asdict(account))
        _decide(policy, account, day, panel, leaders, roles, risk=risk)
        assert asdict(next(o for o in account.pending_orders if o.symbol == symbol)) == before


@pytest.mark.parametrize("reference,exits", [(float("nan"), True), (float("inf"), True), (None, True), (0., False)])
def test_upstream_missing_return_never_becomes_valid_zero_holding_proof(reference, exits):
    from dataclasses import replace

    import pandas as pd
    from test_shared_core_qualification import _decide
    from test_strategic_cohort_deployment_settlement import SYMBOLS, _native_full
    from test_strategic_grant_observation import _risk

    from uquant.config import DEFAULT_CONFIG
    from uquant.portfolio.allocation_book import AllocationBook
    from uquant.portfolio.pipeline import _ordinary_exits
    from uquant.portfolio_core import current_weights
    from uquant.risk.market_book import _market_context

    policy, account, dates, panel, leaders, roles = _native_full()
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    symbol = SYMBOLS[0]
    leaders[symbol] = replace(leaders[symbol], mature=False)
    market = pd.DataFrame({} if reference is None else {"ret60": reference}, index=dates)
    start = DEFAULT_CONFIG.min_hold_days + 2
    for day in dates[start:start + DEFAULT_CONFIG.replacement_confirm_days]:
        for key in ("ma20", "ma60"):
            panel[symbol].loc[day, key] = panel[symbol].loc[day, "close"] * 1.1
        panel[symbol].loc[day, "ret60"] = .05
        evidence = _market_context(day, market, market, DEFAULT_CONFIG)
        assert evidence["tech_ret60"] == 0.  # Existing risk fallback remains intact.
        base = _risk(frozen=False)
        risk = replace(base, evidence={**base.evidence, **evidence})
        prices = {s: float(panel[s].loc[day, "close"]) for s in SYMBOLS}
        weights, _ = current_weights(account, prices)
        book = AllocationBook(policy, day, risk, panel, leaders, account, prices,
                              weights, set(SYMBOLS), {}, dict(weights), dict(weights), 0.)
        _ordinary_exits(book)
    assert (book.proposed[symbol] == 0.) == exits
