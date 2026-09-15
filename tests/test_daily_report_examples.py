# ruff: noqa: RUF001
"""Deterministic examples use native model objects; these are not live signals."""
from copy import deepcopy
from pathlib import Path

import pytest
from test_execution import _canonical_pending

from uquant.report import render_daily_html, render_daily_report
from uquant.types import AccountOrder, AccountState, Decision, Opportunity, Position, Risk, Target

LABEL = '测试夹具示例，决策日 2025-01-06；不是今日交易建议。'


def example(kind):
    symbol = 'sz300308'
    account = AccountState.empty(100000.)
    account.broker_as_of = '2025-01-06'
    frozen = kind == 'frozen'
    sell = kind == 'sell-blocked'
    weight = 0. if sell else .2
    order = _canonical_pending('2025-01-06', symbol, 'SELL' if sell else 'BUY', weight,
                               'risk-off reduction' if sell else 'new independent core')
    if sell:
        account.cash = 80000.
        account.positions[symbol] = Position(symbol, shares=2000, avg_cost=10.)
        account.order_ledger = [AccountOrder('O000000001', '2025-01-03', '2025-01-06', symbol,
            'SELL', 0., 'risk-off reduction', 'CORE', status='PARTIAL', requested_shares=3000,
            filled_shares=1000, remaining_shares=2000, last_update_date='2025-01-06', last_event='LIMIT_BLOCKED')]
    summary = {'fixture_label': LABEL, 'system_gross_cap': 1., 'target_gross_cap': .6,
        'freeze_new_risk': frozen, 'base_freeze_new_risk': False, 'sentinel_freeze_new_risk': frozen,
        'sentinel_causal_coverage_status': 'READY', 'sentinel_mode': 'FREEZE_ONLY',
        'sentinel_causal_active_families': ['covariance_stress'] if frozen else [],
        'decision_input_identity': {'as_of': '2025-01-06'},
        'leader_ranking': [{'symbol': symbol, 'mature': True}],
        'core_allocation': {'scope': 'FINAL_DECISION', 'final_freeze_new_risk': frozen,
            'symbols': {symbol: {'held_weight': .2 if sell else 0., 'entry': {
                'block': 'READY', 'required_confirmation': 3, 'confirmations': {'independent_core': 3},
                'checks': {'confidence': {'passed': True, 'value': .9, 'minimum': .65, 'as_of': '2025-01-06'},
                           'history': {'passed': True, 'value': 160, 'minimum': 121, 'as_of': '2025-01-06'}}},
                'allocation_reason': 'FROZEN' if frozen else 'HOLD' if sell else 'NEW_ENTRY',
                'order_planning': {'block': 'NO_ORDER' if frozen else 'READY'}}}}}
    orders = () if frozen else (order,)
    decision = Decision('2025-01-06', Opportunity.TREND, Risk.RISK_OFF if sell else Risk.NORMAL,
                        weight, int(weight > 0), (Target(symbol, weight, 'CORE', .8, .9, order.reason),),
                        orders, summary, 'TEST_FIXTURE_' + kind)
    account.pending_orders = list(orders)
    return decision, account


def rendered_example(kind):
    decision, account = example(kind)
    md = '> ' + LABEL + '\n\n' + render_daily_report(decision, account)
    html = render_daily_html(decision, account).replace('<main>', '<main><p>' + LABEL + '</p>', 1)
    return md, html


@pytest.mark.parametrize('kind', ['buy', 'frozen', 'sell-blocked'])
def test_examples_preserve_objects_and_render_truthful_actions(kind):
    decision, account = example(kind)
    before = deepcopy((decision, account))
    markdown, html = rendered_example(kind)
    assert render_daily_report(decision, account) == render_daily_report(decision, account)
    assert render_daily_html(decision, account) == render_daily_html(decision, account)
    assert (decision, account) == before
    assert LABEL in markdown and LABEL in html
    assert '<script' not in html and 'src=' not in html and 'href=' not in html
    if kind == 'buy':
        assert '有买入意图' in markdown and '不是上涨概率' in markdown
    elif kind == 'frozen':
        assert '本次没有生成买卖意图' in markdown and '系统暂不允许增加持仓' in markdown
    else:
        assert '准备清仓' in markdown and '部分成交' in markdown
        assert '执行当日涨跌停限制阻止成交' in markdown
        assert '不预测下一开盘可否成交' in markdown


def write_examples(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for kind in ['buy', 'frozen', 'sell-blocked']:
        markdown, html = rendered_example(kind)
        (destination / (kind + '.md')).write_text(markdown, encoding='utf-8')
        (destination / (kind + '.html')).write_text(html, encoding='utf-8')
