"""Real target/order/fill boundaries for one bounded long-pullback entry."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pandas as pd
from test_long_pullback_entry import EPISODES, _native_inputs

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.ordinary_entry import bind_pullback_orders, holding_pullback_entry
from uquant.portfolio import PortfolioAllocator
from uquant.risk.pullback import authorize_pullback_entry
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Risk,
    RiskAssessment,
)


def _submitted():
    symbol, date, frame, leader, _ = _native_inputs(*EPISODES[0])
    frame = frame.copy()
    next_day = date + pd.offsets.BDay(1)
    frame.loc[next_day] = frame.loc[date]
    panel = {symbol: frame}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = 'account_' + 'a' * 64
    account.code_hash = '1' * 64
    account.data_hash = '2' * 64
    risk = RiskAssessment(Risk.CAUTION, 1., 4, {
        'capital_budget_level': 0, 'chronic_level': 0, 'independent_damage': False,
        'sector_guard_active': False, 'acute_sector_evacuation': False,
        'strategic_damage_guard': False, 'freeze_new_risk': True,
    }, ('market-only caution',), 'NONE', freeze_new_risk=True, reduction_level=1)
    risk = authorize_pullback_entry(date=date, risk=risk, account=account,
                                    user_panel=panel, leaders={symbol: leader}, cfg=DEFAULT_CONFIG)
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    risk.evidence['decision_input_identity'] = {'as_of': str(date.date()), 'code_hash': account.code_hash,
                                               'data_hash': account.data_hash}
    # Pre-retirement target fixture, not a current allocation permission. Native
    # planner, ledger, proof binding and execution below still create every actual
    # order/fill; no lifecycle identity or filled quantity is manufactured.
    targets = policy._targets(
        proposed={symbol: .2}, leaders={symbol: leader}, account=account,
        lifecycle=Lifecycle.CORE, reason='bounded ordinary long-pullback entry',
        origin_subsystem=OriginSubsystem.LEADER, mechanism=AttributionMechanism.LEADER_SELECTION)
    targets = tuple(replace(t, reason_code='ordinary_pullback_entry') for t in targets)
    assert len(targets) == 1 and targets[0].weight == .2
    assert targets[0].reason_code == 'ordinary_pullback_entry'
    targets = attach_target_attribution('compute', '3' * 64, signal_date=str(date.date()),
                                        targets=targets, retained_orders=[], cfg=DEFAULT_CONFIG)
    orders = plan_orders(signal_date=str(date.date()), targets=targets, account=account,
                         prices={symbol: float(frame.loc[date, 'close'])}, cfg=DEFAULT_CONFIG)
    orders = reconcile_account_orders(account=account, previous=[], current=orders,
                                     submitted_date=str(date.date()))
    account.pending_orders = list(orders)
    bind_pullback_orders(account=account, orders=orders, risk=risk, date=str(date.date()),
                          cfg=DEFAULT_CONFIG, code_hash=account.code_hash, data_hash=account.data_hash)
    return policy, account, date, next_day, panel, {symbol: leader}, risk


def test_real_submission_proof_has_no_fictitious_filled_shares_and_survives_restart(tmp_path):
    from uquant.account import load_account, save_account
    _, account, date, next_day, panel, leaders, _ = _submitted()
    symbol = next(iter(leaders))
    assert not account.fills and not account.positions
    proof = next(e for e in account.lifecycle_events if e.get('event') == 'ORDINARY_PULLBACK_ENTRY')
    assert 'shares' not in proof
    assert proof['date'] == str(date.date())
    path = tmp_path / 'account.json'
    save_account(account, path)
    restored = load_account(path)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=next_day, account=restored, panel=panel)
    assert len(fills) == 1 and fills[0].shares > 0
    assert not fills[0].grant_id and not fills[0].epoch_id
    assert holding_pullback_entry(restored, symbol)['order_id'] == fills[0].order_id
    save_account(restored, path)
    assert load_account(path).positions[symbol].shares == fills[0].shares


def test_final_sentinel_veto_does_not_consume_base_permission():
    _, account, date, _, panel, leaders, risk = _submitted()
    # Start from the same native pre-order state with no invented fills.
    fresh = AccountState.empty(account.initial_cash)
    fresh.account_identity, fresh.code_hash = account.account_identity, account.code_hash
    risk.evidence['sentinel_freeze_new_risk'] = True
    risk.evidence['base_freeze_new_risk'] = True
    before = deepcopy(fresh.lifecycle_events)
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date, opportunity=Opportunity.CHOPPY, risk=risk, user_panel=panel, leaders=leaders,
        account=fresh, prices={s: float(f.loc[date, 'close']) for s, f in panel.items()})
    assert not any(t.weight > 0 for t in targets)
    assert fresh.lifecycle_events == before


def test_partial_loses_current_proof_cancels_and_cannot_revive_after_restart(tmp_path):
    from uquant.account import load_account, save_account
    from uquant.application.decision import _retained_allocation_orders

    policy, account, _, day, panel, leaders, risk = _submitted()
    symbol = next(iter(leaders))
    frame = panel[symbol]
    frame.loc[day, 'volume'] = 1_000_000.
    frame.loc[day, 'amount'] = float(frame.loc[day, 'close']) * 1_000_000.
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    assert executor.execute_open(date=day, account=account, panel=panel)
    assert account.pending_orders and account.order_ledger[0].status == 'PARTIALLY_FILLED'
    shares, cash = account.positions[symbol].shares, account.cash
    # A still healthy MA120 holding need not retain the short pullback entry setup.
    frame.loc[day, 'ret20'] = 0.
    previous = list(account.pending_orders)
    targets = policy.allocate(date=day, opportunity=Opportunity.CHOPPY, risk=risk,
                              user_panel=panel, leaders=leaders, account=account,
                              prices={symbol: float(frame.loc[day, 'close'])})
    assert risk.evidence['core_allocation']['symbols'][symbol]['pending_buy_rejected']
    retained = _retained_allocation_orders(previous_orders=previous, risk=risk)
    assert not retained
    account.pending_orders = list(reconcile_account_orders(account=account, previous=previous,
                                                          current=(), submitted_date=str(day.date())))
    assert account.order_ledger[0].status == 'CANCELLED'
    path = tmp_path / 'cancelled.json'
    save_account(account, path)
    account = load_account(path)
    next_day = day + pd.offsets.BDay(1)
    frame.loc[next_day] = frame.loc[day]
    frame.loc[next_day, 'ret20'] = -.18
    targets = policy.allocate(date=next_day, opportunity=Opportunity.CHOPPY, risk=risk,
                              user_panel=panel, leaders=leaders, account=account,
                              prices={symbol: float(frame.loc[next_day, 'close'])})
    orders = plan_orders(signal_date=str(next_day.date()), targets=targets, account=account,
                         prices={symbol: float(frame.loc[next_day, 'close'])}, cfg=DEFAULT_CONFIG)
    assert not any(o.side == 'BUY' for o in orders)
    assert (account.positions[symbol].shares, account.cash) == (shares, cash)


def test_resealed_pre_fill_audit_cannot_invent_a_fill_or_remove_the_stock_values(tmp_path):
    import pytest

    from uquant.account import save_account
    from uquant.contracts.strict_json import canonical_json_sha256

    _, original, _, _, _, _, _ = _submitted()
    for mutation in ('shares', 'values'):
        account = deepcopy(original)
        proof = account.lifecycle_events[0]
        if mutation == 'shares':
            proof['shares'] = 100
        else:
            proof['proof']['values'] = {}
        proof['canonical_sha256'] = canonical_json_sha256({k: v for k, v in proof.items()
                                                         if k != 'canonical_sha256'})
        with pytest.raises(RuntimeError, match='ordinary entry audit'):
            save_account(account, tmp_path / (mutation + '.json'))


def test_current_partial_permission_keeps_the_original_order_then_really_finishes():
    from uquant.execution import merge_pending_orders

    policy, account, _, day, panel, leaders, risk = _submitted()
    symbol = next(iter(leaders))
    frame = panel[symbol]
    frame.loc[day, 'volume'] = 1_000_000.
    frame.loc[day, 'amount'] = float(frame.loc[day, 'close']) * 1_000_000.
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    first = executor.execute_open(date=day, account=account, panel=panel)
    assert first and account.pending_orders
    previous = list(account.pending_orders)
    target_price = float(frame.loc[day, 'close'])
    targets = policy.allocate(date=day, opportunity=Opportunity.CHOPPY, risk=risk,
                              user_panel=panel, leaders=leaders, account=account, prices={symbol: target_price})
    targets = attach_target_attribution('compute', '3' * 64, signal_date=str(day.date()),
                                        targets=targets, retained_orders=previous, cfg=DEFAULT_CONFIG)
    planned = plan_orders(signal_date=str(day.date()), targets=targets, account=account,
                          prices={symbol: target_price}, cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=targets, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(account=account, previous=previous,
                                                          current=merged, submitted_date=str(day.date())))
    assert account.pending_orders[0].order_id == first[0].order_id
    assert account.pending_orders[0].event_id == first[0].event_id
    tomorrow = day + pd.offsets.BDay(1)
    frame.loc[tomorrow] = frame.loc[day]
    frame.loc[tomorrow, 'volume'] = 100_000_000.
    second = executor.execute_open(date=tomorrow, account=account, panel=panel)
    assert second and second[0].order_id == first[0].order_id
    assert account.order_ledger[0].filled_shares == first[0].shares + second[0].shares
    assert len(account.order_ledger) == 1


def test_smaller_cap_cancels_remainder_instead_of_replacing_it_with_an_unproven_buy():
    from dataclasses import replace

    policy, account, _, day, panel, leaders, risk = _submitted()
    symbol = next(iter(leaders))
    frame = panel[symbol]
    frame.loc[day, 'volume'] = 1_000_000.
    frame.loc[day, 'amount'] = float(frame.loc[day, 'close']) * 1_000_000.
    assert ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=day, account=account, panel=panel)
    assert account.pending_orders
    price = float(frame.loc[day, 'close'])
    held = account.positions[symbol].shares * price
    weight = held / (account.cash + held)
    risk = replace(risk, target_gross_cap=weight + .01)
    targets = policy.allocate(date=day, opportunity=Opportunity.CHOPPY, risk=risk,
                              user_panel=panel, leaders=leaders, account=account, prices={symbol: price})
    assert risk.evidence['core_allocation']['symbols'][symbol]['pending_buy_rejected']
    assert all(t.weight <= weight + 1e-12 for t in targets)
    assert not any(o.side == 'BUY' for o in plan_orders(signal_date=str(day.date()), targets=targets,
                   account=account, prices={symbol: price}, cfg=DEFAULT_CONFIG))


def test_real_graduation_keeps_its_observed_fill_boundary_after_same_day_exit(tmp_path):
    from dataclasses import replace

    from test_ordinary_pullback_lifecycle import _bar, _ordinary_target, _plan

    from uquant.account import load_account, save_account
    from uquant.models.ordinary_entry import GRADUATION
    from uquant.portfolio.leaders.lifecycle import ordinary_pullback_exit

    policy, account, _, day, panel, leaders, _ = _submitted()
    executor = ExecutionPlanner(DEFAULT_CONFIG)
    first = executor.execute_open(date=day, account=account, panel=panel)
    assert first
    symbol = first[0].symbol
    frame = panel[symbol]
    price = account.positions[symbol].avg_cost * 1.01
    later = day + pd.offsets.BDay(1)
    _bar(frame, later, price)
    frame.loc[later, ['ma60', 'ma120', 'ret60']] = (price * .9, price * .9, .2)
    assert ordinary_pullback_exit(policy, symbol=symbol, date=later, user_panel=panel,
            leaders={symbol: replace(leaders[symbol], mature=True)}, account=account) is None
    graduation = next(e for e in account.lifecycle_events if e.get('event') == GRADUATION)
    assert graduation['observed_fill_count'] == len(account.fills) == 1
    # A real order/fill appends after observation with the same calendar date.
    # This isolates the event's observed ledger prefix; it is not a strategy replay.
    _plan(account, day, panel, (_ordinary_target(symbol, 0),))
    assert executor.execute_open(date=later, account=account, panel=panel)
    assert not holding_pullback_entry(account, symbol)
    path = tmp_path / 'graduation-then-flat.json'
    save_account(account, path)
    restored = load_account(path)
    assert graduation in restored.lifecycle_events
