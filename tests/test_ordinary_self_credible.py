"""Ordinary maturity and current execution proof have separate market-witness roles."""
from dataclasses import replace

from test_ordinary_trend_budget import _decide, _scenario

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.leader import apply_leader_tenure


def _open_pair(*, low_score=.81):
    policy, account, dates, panel, base, risk = _scenario()
    low = tuple(base)[-1]
    base[low] = replace(base[low], score=low_score)
    for date in dates[:5]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        targets = _decide(policy, account, date, panel, leaders, risk)
    return policy, account, dates, panel, leaders, risk, targets, low


def test_peer_market_permission_does_not_fund_low_score_self_maturity():
    _, account, dates, panel, leaders, risk, targets, low = _open_pair()
    assert risk.evidence['core_allocation']['ordinary_market']['confirmed']
    assert all(leader.mature for leader in leaders.values())
    assert account.replacement_tenure.get(f'strategic_eligibility:independent_core:{low}', 0) == 0
    high = next(symbol for symbol in leaders if symbol != low)
    assert {target.symbol for target in targets} == {high}
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == high and fills[0].side == 'BUY'
    assert low not in account.positions
    assert account.strategic_grant is None and not account.strategic_epochs


def test_filled_holding_score_loss_does_not_force_sale_or_relabel():
    policy, account, dates, panel, leaders, risk, _, low = _open_pair(low_score=.84)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 2
    before = {symbol: (p.shares, p.grant_id, p.epoch_id) for symbol, p in account.positions.items()}
    leaders[low] = replace(leaders[low], score=.81)
    _decide(policy, account, dates[5], panel, leaders, risk)
    assert not account.pending_orders
    assert {symbol: (p.shares, p.grant_id, p.epoch_id) for symbol, p in account.positions.items()} == before


def test_partial_current_credible_continues_then_score_loss_cancels_without_restart_revival(tmp_path):
    from uquant.account import load_account, save_account

    policy, account, dates, panel, leaders, risk, _, low = _open_pair(low_score=.84)
    original = next(order for order in account.pending_orders if order.symbol == low)
    frame = panel[low]
    frame.loc[dates[5], 'volume'] = 1_000_000.
    frame.loc[dates[5], 'amount'] = frame.loc[dates[5], 'close'] * 1_000_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert any(fill.symbol == low and fill.shares > 0 for fill in fills)
    _decide(policy, account, dates[5], panel, leaders, risk)
    remaining = [o for o in account.pending_orders if o.symbol == low]
    assert len(remaining) == 1
    assert (remaining[0].order_id, remaining[0].event_id) == (original.order_id, original.event_id)
    before = (account.positions[low].shares, account.cash)
    leaders[low] = replace(leaders[low], score=.81)
    _decide(policy, account, dates[6], panel, leaders, risk)
    assert risk.evidence['core_allocation']['ordinary_market']['confirmed']  # Healthy peer still witnesses.
    assert not any(o.symbol == low for o in account.pending_orders)
    assert next(o for o in account.order_ledger if o.order_id == original.order_id).status == 'CANCELLED'
    path = tmp_path / 'partial.json'
    save_account(account, path)
    restored = load_account(path)
    _decide(policy, restored, dates[7], panel, leaders, risk)
    assert not any(o.symbol == low for o in restored.pending_orders)
    assert (restored.positions[low].shares, restored.cash) == before


def test_real_shared_certificate_retains_priority_and_original_identity():
    from test_shared_core_qualification import CHALLENGER, _held_book
    from test_shared_core_qualification import _decide as shared_decide

    from uquant.portfolio.ordinary import ordinary_core_entry
    from uquant.portfolio.strategic.discovery import current_core_qualification

    policy, account, dates, panel, leaders, roles = _held_book()
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        shared_decide(policy, account, date, panel, leaders, roles)
    from test_strategic_universe_quorum import _risk
    certificates = current_core_qualification(policy, date=date, user_panel=panel, leaders=leaders,
                                              account=account, risk=_risk())
    certificate = certificates[CHALLENGER]
    assert certificate['qualification_route'] != 'mature_core'
    result = ordinary_core_entry(policy, symbol=CHALLENGER, score=leaders[CHALLENGER], date=date,
                                 user_panel=panel, account=account,
                                 confirmation_days=DEFAULT_CONFIG.leader_tenure_days, certificate=certificate)
    assert result == certificate and result['block'] == 'READY'


def test_real_strict_clock_remains_fallback_without_self_maturity_tenure():
    from test_ordinary_cash_rearm import SYMBOL
    from test_ordinary_cash_rearm import _scenario as repair_scenario

    from uquant.portfolio.ordinary import ordinary_core_entry

    policy, account, dates, panel, leaders, _ = repair_scenario()
    assert account.leader_tenure.get(SYMBOL, 0) == 0
    assert account.replacement_tenure[f'strategic_eligibility:independent_core:{SYMBOL}'] >= 5
    result = ordinary_core_entry(policy, symbol=SYMBOL, score=leaders[SYMBOL], date=dates[0],
                                 user_panel=panel, account=account, confirmation_days=5)
    assert result['block'] == 'READY'
    assert 'qualification_route' not in result
    assert result['confirmations']['independent_core'] >= 5


def test_native_shared_certificate_retains_its_route_without_strict_clock():
    from test_shared_core_qualification import CHALLENGER, _held_book
    from test_strategic_universe_quorum import _risk

    policy, account, dates, panel, leaders, roles = _held_book()
    risk = _risk()
    risk.evidence.update(ai_fast_return=.01, declining_ratio=.05, below_ma20_ratio=.05,
                         tech_speed=.02, broad_speed=.02)
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(policy, account, date, panel, leaders, risk, roles=roles)
    assert risk.evidence["core_allocation"]["ordinary_market"]["confirmed"]
    assert account.replacement_tenure.get(f"strategic_eligibility:independent_core:{CHALLENGER}", 0) == 0
    order = next(order for order in account.pending_orders if order.symbol == CHALLENGER)
    assert not order.grant_id and not order.epoch_id
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[DEFAULT_CONFIG.strategic_cohort_confirm_days], account=account, panel=panel)
    assert any(fill.symbol == CHALLENGER and fill.side == "BUY" and fill.shares > 0 for fill in fills)


def test_native_strict_candidate_buys_without_mature_shortcut():
    from test_ordinary_cash_rearm import SYMBOL
    from test_ordinary_cash_rearm import _decide as repair_decide
    from test_ordinary_cash_rearm import _scenario as repair_scenario

    from uquant.types import Risk

    policy, account, dates, panel, leaders, risk = repair_scenario()
    assert account.leader_tenure.get(SYMBOL, 0) == 0
    risk = replace(risk, state=Risk.NORMAL, freeze_new_risk=False, reduction_level=0,
                   evidence={**risk.evidence, "freeze_new_risk": False, "ai_fast_return": .01})
    repair_decide(policy, account, dates[0], panel, leaders, risk)
    assert risk.evidence["core_allocation"]["ordinary_market"]["confirmed"]
    assert account.replacement_tenure[f"strategic_eligibility:independent_core:{SYMBOL}"] >= 5
    assert account.pending_orders and account.strategic_cash_rearm.consumed_order is None
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == SYMBOL and fills[0].shares > 0
    assert not fills[0].grant_id and not fills[0].epoch_id
