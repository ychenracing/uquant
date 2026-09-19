"""Policy fixtures only; economic evidence comes from native paired replays."""
from types import SimpleNamespace

import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio.strategic.lifecycle import _arm_dominant_profit_lock
from uquant.types import AccountState


def context(weight=.95):
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.strategic_cohort_targets = {'sh600001': weight}
    return SimpleNamespace(policy=SimpleNamespace(cfg=DEFAULT_CONFIG), account=account,
                           dominant_symbol='sh600001', dominant_profit_locked=False,
                           dominant_profit_lock_armed_now=False)


def test_transition_is_continuous_and_never_increases_rights():
    caps = []
    for mfe in [2.099999, 2.10, 2.15, 2.199999, 2.20, 2.200001]:
        ctx = context()
        _arm_dominant_profit_lock(ctx, symbol='sh600001', peak_mfe=mfe, atr_mfe=.1)
        caps.append(ctx.account.strategic_cohort_targets['sh600001'])
        assert .70 <= caps[-1] <= .95
        assert ctx.account.cash == DEFAULT_CONFIG.initial_cash
        assert not ctx.account.positions and not ctx.account.fills
    assert caps == sorted(caps, reverse=True)
    assert caps[2] == pytest.approx(.825)
    assert caps[3] - caps[4] < .00001
    ctx = context(.50)
    _arm_dominant_profit_lock(ctx, symbol='sh600001', peak_mfe=3., atr_mfe=.1)
    assert ctx.account.strategic_cohort_targets['sh600001'] == .50


def test_loss_cannot_arm_profit_protection():
    ctx = context()
    before = ctx.account.to_dict()
    _arm_dominant_profit_lock(ctx, symbol='sh600001', peak_mfe=-.1, atr_mfe=10.)
    assert ctx.account.to_dict() == before
    assert not ctx.dominant_profit_lock_armed_now
