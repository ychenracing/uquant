from copy import deepcopy

from uquant.report import render_daily_report
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
    assert (decision, account) == before


def test_freeze_is_not_a_sell_instruction():
    decision = Decision('2025-01-06', Opportunity.TREND, Risk.NORMAL, 0., 0, (), (),
                        {'freeze_new_risk': True, 'sentinel_freeze_new_risk': True}, 'digest')
    report = render_daily_report(decision, AccountState.empty(100000.))
    assert '系统暂不允许增加持仓' in report
    assert '不代表要求清仓' in report
