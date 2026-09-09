from dataclasses import replace

import pytest

from uquant.portfolio import pipeline
from uquant.types import LeaderScore


def leader(symbol, industry, strength, momentum, score=.9):
    return LeaderScore(symbol=symbol, industry=industry, score=score, confidence=1.,
                       mature=True, emerging=False,
                       components={"industry_rotation_strength": strength,
                                   "raw_ret120": momentum})


def test_selection_uses_industry_median_then_momentum_without_rewriting_scores():
    rows = [leader("a", "equipment", .8, .3), leader("b", "equipment", .8, .5),
            leader("c", "equipment", .8, .2), leader("d", "equipment", .8, .4),
            leader("e", "memory", .7, 2., 1.)]
    leaders = {x.symbol: x for x in rows}
    assert pipeline._industry_first_candidates(list(leaders), leaders) == ["b", "d", "a"]
    assert leaders["e"].score == 1.
    assert pipeline._industry_first_candidates(["e"], leaders) == ["e"]


def test_selection_is_stable_and_missing_values_cannot_win():
    rows = [leader("b", "equipment", .7, .2), leader("a", "equipment", .7, .2),
            leader("c", "memory", .7, 5.), leader("d", "unknown", 1., 5.),
            leader("e", "compute", float("nan"), 5.),
            leader("f", "compute", 1., float("inf"))]
    leaders = {x.symbol: x for x in rows}
    assert pipeline._industry_first_candidates(list(reversed(leaders)), leaders) == ["a", "b"]
    assert pipeline._industry_first_candidates([], leaders) == []
    assert pipeline._industry_first_candidates(["d", "e", "f"], leaders) == []
    leaders["c"] = replace(leaders["c"], components={})
    assert pipeline._industry_first_candidates(["c"], leaders) == []


@pytest.mark.parametrize("freeze", [False, True])
def test_native_repair_consumes_only_real_order_with_complete_rank_features(freeze):
    from test_ordinary_cash_rearm import SYMBOL, _decide, _scenario

    from uquant.config import DEFAULT_CONFIG
    from uquant.execution import ExecutionPlanner

    policy, account, dates, panel, leaders, risk = _scenario()
    original = leaders[SYMBOL]
    leaders[SYMBOL] = replace(original, components={
        **original.components,
        "raw_ret120": float(panel[SYMBOL].loc[dates[0], "ret120"]),
    })
    if freeze:
        risk.evidence["sentinel_freeze_new_risk"] = True
    _decide(policy, account, dates[0], panel, leaders, risk)
    if freeze:
        assert not account.pending_orders
        assert account.strategic_cash_rearm.consumed_order is None
    else:
        order = account.pending_orders[0]
        reference = account.strategic_cash_rearm.consumed_order
        assert reference is not None and reference.order_id == order.order_id
        assert reference.event_id == order.event_id
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
            date=dates[1], account=account, panel=panel)
        assert len(fills) == 1 and fills[0].symbol == SYMBOL and fills[0].shares > 0
        assert account.cash >= 0
