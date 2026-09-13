"""Native FULL holdings share the already confirmed structural exit."""
from dataclasses import asdict, replace

import pytest
from test_shared_core_qualification import _decide
from test_strategic_cohort_deployment_settlement import SYMBOLS, _native_full
from test_strategic_grant_observation import _risk

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner


@pytest.mark.parametrize("current_lock", [True, False])
def test_persistent_exit_ownership_does_not_depend_on_dominant_lock(current_lock):
    from uquant.portfolio.allocation_book import AllocationBook
    from uquant.portfolio.pipeline import _ordinary_exits
    from uquant.portfolio_core import current_weights

    policy, account, dates, panel, leaders, roles = _native_full()
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    symbol = SYMBOLS[0]
    # Real, fully filled native entry; isolate the already established dominant
    # lifecycle state to test which exit policy owns this position.
    account.strategic_cohort_targets = {symbol: .7}
    account.candidate_tenure["strategic_dominant_epoch"] = account.strategic_epoch
    account.candidate_tenure["strategic_dominant_profit_lock_epoch"] = (
        account.strategic_epoch if current_lock else account.strategic_epoch - 1
    )
    leaders[symbol] = replace(leaders[symbol], mature=False)
    start = DEFAULT_CONFIG.min_hold_days + 2
    for day in dates[start:start + DEFAULT_CONFIG.replacement_confirm_days]:
        for key in ("ma20", "ma60"):
            panel[symbol].loc[day, key] = panel[symbol].loc[day, "close"] * 1.1
        prices = {s: float(panel[s].loc[day, "close"]) for s in SYMBOLS}
        weights, _ = current_weights(account, prices)
        book = AllocationBook(
            policy, day, _risk(frozen=False), panel, leaders, account,
            prices, weights, {symbol}, {}, dict(weights), dict(weights), 0.,
        )
        _ordinary_exits(book)
    assert book.proposed[symbol] == weights[symbol]


def test_native_persistent_member_keeps_disaster_exit_and_restart_identity():
    policy, account, dates, panel, leaders, roles = _native_full()
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    symbol = SYMBOLS[0]
    # Leave the original entry untouched; hold long enough before deterioration.
    start = DEFAULT_CONFIG.min_hold_days + 1
    for date in dates[1:start]:
        _decide(policy, account, date, panel, leaders, roles, risk=_risk(frozen=False))
    weakened = {**leaders, symbol: replace(leaders[symbol], mature=False)}
    frame = panel[symbol]
    for key in ("ma20", "ma60"):
        frame.loc[dates[start]:, key] = frame.loc[dates[start]:, "close"] * 1.1
    for date in dates[start:start + DEFAULT_CONFIG.replacement_confirm_days]:
        _decide(policy, account, date, panel, weakened, roles, risk=_risk(frozen=False))
    assert not any(o.symbol == symbol and o.side == "SELL" for o in account.pending_orders)
    frame.loc[date, "close"] = account.positions[symbol].avg_cost * .5
    _decide(policy, account, date, panel, weakened, roles, risk=_risk(frozen=False))
    orders = [o for o in account.pending_orders if o.symbol == symbol and o.side == "SELL"]
    assert len(orders) == 1 and orders[0].target_weight == 0
    assert orders[0].mechanism == "STRATEGIC_COHORT"
    assert orders[0].epoch_id == account.positions[symbol].epoch_id
    original = asdict(orders[0])
    restored = account_from_dict(asdict(account))
    _decide(policy, restored, date, panel, weakened, roles, risk=_risk(frozen=False))
    assert asdict(next(o for o in restored.pending_orders if o.symbol == symbol)) == original
    fill_date = dates[dates.get_loc(date) + 1]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=fill_date, account=restored, panel=panel)
    assert any(f.symbol == symbol and f.side == "SELL" for f in fills)
    assert symbol not in restored.positions or restored.positions[symbol].shares == 0
    assert any(p.shares > 0 for s, p in restored.positions.items() if s != symbol)
    # A settled member's actual exit must not permanently lock its remaining peers.
    peer = SYMBOLS[1]
    weakened[peer] = replace(leaders[peer], mature=False)
    for key in ("ma20", "ma60"):
        panel[peer].loc[fill_date:, key] = panel[peer].loc[fill_date:, "close"] * 1.1
    remaining_dates = dates[dates.get_loc(fill_date):]
    for date in remaining_dates[:DEFAULT_CONFIG.replacement_confirm_days]:
        _decide(policy, restored, date, panel, weakened, roles, risk=_risk(frozen=False))
    panel[peer].loc[date, "close"] = restored.positions[peer].avg_cost * .5
    _decide(policy, restored, date, panel, weakened, roles, risk=_risk(frozen=False))
    exits = [o for o in restored.pending_orders if o.symbol == peer and o.side == "SELL"]
    assert len(exits) == 1 and exits[0].target_weight == 0
    assert exits[0].epoch_id == restored.positions[peer].epoch_id
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=remaining_dates[DEFAULT_CONFIG.replacement_confirm_days], account=restored, panel=panel)
    assert any(f.symbol == peer and f.side == "SELL" for f in fills)


def test_one_filled_member_cannot_exit_before_full_deployment_settles():
    from uquant.portfolio.allocation_book import AllocationBook
    from uquant.portfolio.pipeline import _ordinary_exits
    from uquant.portfolio.strategic.grant_lifecycle import completed_strategic_cohort_entry
    from uquant.portfolio_core import current_weights

    policy, account, dates, panel, leaders, _ = _native_full(execution="partial")
    symbol = SYMBOLS[1]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[1], account=account, panel={symbol: panel[symbol]},
    )
    assert fills and all(fill.symbol == symbol for fill in fills)
    members = set(account.strategic_cohort_targets)
    assert completed_strategic_cohort_entry(account, {symbol})
    assert not completed_strategic_cohort_entry(account, members)
    assert account.pending_orders
    leaders[symbol] = replace(leaders[symbol], mature=False)
    start = DEFAULT_CONFIG.min_hold_days + 2
    for day in dates[start:start + DEFAULT_CONFIG.replacement_confirm_days]:
        panel[symbol].loc[day, "ma20"] = panel[symbol].loc[day, "close"] * 1.1
        prices = {s: float(panel[s].loc[day, "close"]) for s in SYMBOLS}
        weights, _ = current_weights(account, prices)
        book = AllocationBook(
            policy, day, _risk(frozen=False), panel, leaders, account,
            prices, weights, members, {}, dict(weights), dict(weights), 0.,
        )
        _ordinary_exits(book)
        assert book.proposed[symbol] == weights[symbol]
        assert book.trace.get(symbol, {}).get("allocation_reason") != "CONFIRMED_STRUCTURAL_EXIT"


@pytest.mark.parametrize("restart", [False, True])
def test_settled_persistent_members_keep_strategic_exit_lifecycle(restart):
    import pandas as pd
    from test_ordinary_trend_budget import _decide as decide_formation
    from test_persistent_formation import _formation_fixture

    from uquant.portfolio.allocation_book import AllocationBook
    from uquant.portfolio.pipeline import _ordinary_exits
    from uquant.portfolio.strategic.grant_lifecycle import completed_strategic_cohort_entry
    from uquant.portfolio_core import current_weights

    policy, account, dates, panel, leaders, risk, _, _ = _formation_fixture()
    decide_formation(policy, account, dates[-2], panel, leaders, risk)
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-1], account=account, panel=panel)
    assert account.strategic_grant.qualification_route == "persistent_industry"
    members = set(account.strategic_cohort_targets)
    assert completed_strategic_cohort_entry(account, members)
    if restart:
        account = account_from_dict(asdict(account))
    future = pd.bdate_range(dates[-1] + pd.offsets.BDay(), periods=30)
    panel = {s: frame.reindex(frame.index.union(future)).ffill() for s, frame in panel.items()}
    for frame in panel.values():
        frame.loc[future, "ma20"] = frame.loc[future, "close"] * 1.1
        frame.loc[future, "ma60"] = frame.loc[future, "close"] * 1.1
    for day in future:
        prices = {s: float(panel[s].loc[day, "close"]) for s in members}
        weights, _ = current_weights(account, prices)
        book = AllocationBook(policy, day, risk, panel, leaders, account,
                              prices, weights, members, {}, dict(weights), dict(weights), 0.)
        _ordinary_exits(book)
        assert book.proposed == weights
