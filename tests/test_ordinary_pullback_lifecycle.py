"""Native pullback holdings retain only their actual continuous execution basis."""
import pandas as pd
from test_ordinary_pullback_execution import _submitted

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.ordinary_entry import holding_pullback_entry
from uquant.types import Opportunity


def _plan(account, date, panel, targets):
    targets = attach_target_attribution('compute', '3' * 64, signal_date=str(date.date()),
                                        targets=targets, retained_orders=[], cfg=DEFAULT_CONFIG)
    orders = plan_orders(signal_date=str(date.date()), targets=targets, account=account,
                         prices={s: float(f.loc[date, 'close']) for s, f in panel.items()}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=account.pending_orders, current=orders,
        submitted_date=str(date.date())))
    return orders


def _bar(frame, date, close):
    frame.loc[date] = frame.iloc[-1]
    frame.loc[date, ['open', 'close', 'high', 'low', 'volume', 'amount']] = (
        close, close, close * 1.01, close * .99, 100_000_000., close * 100_000_000.)


def test_missing_leader_cannot_hide_pullback_disaster_and_sell_waits_for_execution():
    policy, account, _, filled_day, panel, _, risk = _submitted()
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    fills = executor.execute_open(date=filled_day, account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == 'BUY'
    symbol = fills[0].symbol
    assert holding_pullback_entry(account, symbol)
    frame = panel[symbol]
    price = account.positions[symbol].avg_cost * .79
    _bar(frame, filled_day, price)
    targets = policy.allocate(date=filled_day, opportunity=Opportunity.CHOPPY, risk=risk,
                              user_panel=panel, leaders={}, account=account, prices={symbol: price})
    assert any(t.symbol == symbol and t.weight == 0 for t in targets)
    orders = _plan(account, filled_day, panel, targets)
    assert len(orders) == 1 and orders[0].side == 'SELL'
    shares = account.positions[symbol].shares
    assert not executor.execute_open(date=filled_day, account=account, panel=panel)
    assert account.positions[symbol].shares == shares
    next_day = filled_day + pd.offsets.BDay(1)
    _bar(frame, next_day, price)
    sells = executor.execute_open(date=next_day, account=account, panel=panel)
    assert len(sells) == 1 and sells[0].side == 'SELL' and sells[0].shares == shares
    assert not holding_pullback_entry(account, symbol)


def _ordinary_target(symbol, weight):
    from uquant.types import Target

    return Target(symbol, weight, 'CORE', .9, .9, 'native quantity-history exercise',
                  origin_subsystem='LEADER', mechanism='LEADER_SELECTION', origin_lifecycle='CORE')


def test_fifo_removing_first_fill_keeps_continuous_original_proof_after_restart(tmp_path):
    from uquant.account import load_account, save_account

    _, account, _, day, panel, leaders, _ = _submitted()
    symbol = next(iter(leaders))
    frame = panel[symbol]
    price = float(frame.loc[day, 'close'])
    frame.loc[day, 'volume'] = 1_000_000.
    frame.loc[day, 'amount'] = price * 1_000_000.
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    first = executor.execute_open(date=day, account=account, panel=panel)
    assert len(first) == 1 and account.pending_orders
    first_shares = first[0].shares
    entry = holding_pullback_entry(account, symbol)
    second_day = day + pd.offsets.BDay(1)
    _bar(frame, second_day, price)
    second = executor.execute_open(date=second_day, account=account, panel=panel)
    assert len(second) == 1 and second[0].order_id == first[0].order_id
    assert not account.pending_orders
    held = account.positions[symbol].shares
    nav = account.cash + held * price
    weight = (held - first_shares) * price / nav
    _plan(account, second_day, panel, (_ordinary_target(symbol, weight),))
    sell_day = second_day + pd.offsets.BDay(1)
    _bar(frame, sell_day, price)
    sold = executor.execute_open(date=sell_day, account=account, panel=panel)
    assert len(sold) == 1 and sold[0].side == 'SELL' and sold[0].shares == first_shares
    assert account.positions[symbol].shares > 0
    assert holding_pullback_entry(account, symbol) == entry
    path = tmp_path / 'fifo.json'
    save_account(account, path)
    restored = load_account(path)
    assert holding_pullback_entry(restored, symbol) == entry


def test_same_day_zero_then_real_ordinary_rebuy_cannot_borrow_old_proof(tmp_path):
    from uquant.account import load_account, save_account

    _, account, _, day, panel, leaders, _ = _submitted()
    symbol = next(iter(leaders))
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    assert executor.execute_open(date=day, account=account, panel=panel)
    assert holding_pullback_entry(account, symbol)
    frame = panel[symbol]
    price = float(frame.loc[day, 'close'])
    _plan(account, day, panel, (_ordinary_target(symbol, 0),))
    sell_day = day + pd.offsets.BDay(1)
    _bar(frame, sell_day, price)
    sold = executor.execute_open(date=sell_day, account=account, panel=panel)
    assert len(sold) == 1 and sold[0].side == 'SELL'
    assert not holding_pullback_entry(account, symbol)
    # The second genuine order uses a prior signal date so both fills can occur
    # in this day's native open. This isolates ledger ordering from strategy entry.
    _plan(account, day, panel, (_ordinary_target(symbol, .1),))
    bought = executor.execute_open(date=sell_day, account=account, panel=panel)
    assert len(bought) == 1 and bought[0].side == 'BUY'
    assert bought[0].fill_date == sold[0].fill_date
    assert bought[0].order_id != account.lifecycle_events[0]['order_id']
    assert account.positions[symbol].shares > 0
    assert holding_pullback_entry(account, symbol) is None
    path = tmp_path / 'new-episode.json'
    save_account(account, path)
    assert holding_pullback_entry(load_account(path), symbol) is None


def test_ma120_damage_requires_three_observed_sessions_and_ten_held_sessions():
    from uquant.portfolio.leaders.lifecycle import ordinary_pullback_exit

    policy, account, _, day, panel, leaders, _ = _submitted()
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=day, account=account, panel=panel)
    symbol = next(iter(leaders))
    frame = panel[symbol]
    price = account.positions[symbol].avg_cost * .95
    dates = pd.bdate_range(day, periods=14)
    for date in dates:
        _bar(frame, date, price)
        frame.loc[date, 'ma120'] = price * 1.05
    entry = holding_pullback_entry(account, symbol)
    key = 'pullback_exit:' + entry['canonical_sha256']

    def observe(index):
        return ordinary_pullback_exit(policy, symbol=symbol, date=dates[index],
                                      user_panel=panel, leaders={}, account=account)

    for index in range(3):
        assert observe(index) == ''
    assert account.replacement_tenure[key] == 3  # Still only three held sessions.
    assert observe(2) == '' and account.replacement_tenure[key] == 3
    frame.loc[dates[3], 'ma120'] = float('nan')
    assert observe(3) == '' and account.replacement_tenure[key] == 3
    # Missing day was not a fourth confirmation, and the next valid day starts over.
    assert observe(4) == '' and account.replacement_tenure[key] == 1
    assert observe(6) == '' and account.replacement_tenure[key] == 1  # Skipped actual session.
    assert observe(7) == '' and account.replacement_tenure[key] == 2
    assert observe(8) == '' and account.replacement_tenure[key] == 3  # Nine held sessions.
    assert 'MA120 deterioration' in observe(9)
    assert account.replacement_tenure[key] == 4


def test_graduation_is_once_and_irreversible_after_restart_with_disaster_retained(tmp_path):
    from dataclasses import replace

    from uquant.account import load_account, save_account
    from uquant.models.ordinary_entry import pullback_graduated
    from uquant.portfolio.leaders.lifecycle import ordinary_pullback_exit

    policy, account, _, day, panel, leaders, _ = _submitted()
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=day, account=account, panel=panel)
    symbol = next(iter(leaders))
    frame = panel[symbol]
    price = account.positions[symbol].avg_cost * 1.01
    date = day + pd.offsets.BDay(1)
    _bar(frame, date, price)
    frame.loc[date, ['ma120', 'ma60', 'ret60']] = (price * .95, price * .98, .1)
    leaders[symbol] = replace(leaders[symbol], mature=True)
    entry = holding_pullback_entry(account, symbol)
    for _ in range(2):
        assert ordinary_pullback_exit(policy, symbol=symbol, date=date,
                                      user_panel=panel, leaders=leaders, account=account) is None
    assert pullback_graduated(account, entry)
    assert sum(e.get('event') == 'ORDINARY_PULLBACK_GRADUATION' for e in account.lifecycle_events) == 1
    path = tmp_path / 'graduated.json'
    save_account(account, path)
    restored = load_account(path)
    leaders[symbol] = replace(leaders[symbol], mature=False)
    next_day = date + pd.offsets.BDay(1)
    _bar(frame, next_day, price)
    frame.loc[next_day, ['ma120', 'ma60', 'ret60']] = (price * .95, price * 1.05, -.1)
    assert ordinary_pullback_exit(policy, symbol=symbol, date=next_day,
                                  user_panel=panel, leaders=leaders, account=restored) is None
    assert pullback_graduated(restored, entry)  # Delegate ordinary exits, never restore long-basis hold.
    crash_day = next_day + pd.offsets.BDay(1)
    _bar(frame, crash_day, restored.positions[symbol].avg_cost * .79)
    assert 'disaster' in ordinary_pullback_exit(policy, symbol=symbol, date=crash_day,
                                               user_panel=panel, leaders={}, account=restored)
    assert sum(e.get('event') == 'ORDINARY_PULLBACK_GRADUATION' for e in restored.lifecycle_events) == 1
