# ruff: noqa: RUF001
from copy import deepcopy

from uquant.report import render_daily_html, render_daily_report
from uquant.types import AccountState, Decision, Opportunity, Risk, Target


def test_positive_target_without_order_is_not_a_buy_and_missing_risk_is_not_safe():
    decision = Decision('2025-01-06', Opportunity.TREND, Risk.NORMAL, .2, 1,
        (Target('<script>x</script>|A', .2, 'CORE', .8, 1., 'recorded'),), (), {}, 'digest')
    account = AccountState.empty(100000.)
    before = deepcopy((decision, account))
    report = render_daily_report(decision, account)
    assert '未生成买入意图' in report
    assert '未取得' in report
    assert '<script>' not in report
    html = render_daily_html(decision, account)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert (decision, account) == before


def test_freeze_is_not_a_sell_instruction():
    decision = Decision('2025-01-06', Opportunity.TREND, Risk.NORMAL, 0., 0, (), (),
                        {'freeze_new_risk': True, 'sentinel_freeze_new_risk': True}, 'digest')
    report = render_daily_report(decision, AccountState.empty(100000.))
    assert '系统暂不允许增加持仓' in report
    assert '不代表要求清仓' in report


def test_cancel_pending_ledger_only_symbol_gets_a_plain_language_section():
    from uquant.types import AccountOrder
    account = AccountState.empty(100000.)
    account.order_ledger = [AccountOrder('O000000001', '2025-01-03', '2025-01-06',
        'sz300308', 'BUY', .2, 'sentinel_freeze_new_risk', 'CORE', status='CANCEL_REQUESTED',
        remaining_shares=1000, last_update_date='2025-01-06', last_event='CANCEL_REQUESTED')]
    decision = Decision('2025-01-06', Opportunity.TREND, Risk.NORMAL, 0., 0, (), (), {}, 'digest')
    text = render_daily_report(decision, account).split('## 详细依据', 1)[0]
    assert '逐只股票：sz300308' in text
    assert '撤销待确认' in text
    assert '1000 股' in text


def test_native_restoration_block_requires_new_qualification():
    symbol = 'sz300308'
    decision = Decision('2025-01-06', Opportunity.TREND, Risk.NORMAL, 0., 0, (), (),
        {'core_allocation': {'scope': 'FINAL_DECISION', 'symbols': {symbol: {
            'restore_block': 'NEW_ENTRY_REQUIRES_QUALIFICATION'}}}}, 'digest')
    report = render_daily_report(decision, AccountState.empty(100000.))
    assert '新增买入必须重新通过建仓资格' in report
    assert '尚无直白释义' not in report.split('## 详细依据', 1)[0]
