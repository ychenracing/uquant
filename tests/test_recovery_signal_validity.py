"""Recovery observations remain causal and bounded while funding is frozen."""
from types import SimpleNamespace

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio.recovery.cohort_admission import _recovery_selection, scan_recovery_evidence
from uquant.types import AccountState


@pytest.mark.parametrize('delay,close,liquid,expected', [
    (1, 108., True, True),
    (6, 108., True, False),
    (1, 100., True, False),
    (1, 108., False, False),
])
def test_recent_breakout_expires_and_requires_current_structure_and_liquidity(delay, close, liquid, expected):
    prices = [100.] * 10 + [110.] + [108.] * delay
    prices[-1] = close
    dates = pd.bdate_range('2025-01-02', periods=len(prices))
    frame = pd.DataFrame({'close': prices, 'ma20': 105., 'ret120': -.4}, index=dates)
    policy = SimpleNamespace(cfg=DEFAULT_CONFIG, _liquidity_confirmed=lambda frame, date: liquid)
    account = AccountState.empty(100)
    account.anchor_weights = {'held': .5}
    candidates, _ = scan_recovery_evidence(policy, date=dates[-1], user_panel={'a': frame},
                                           leaders={'a': _leader('a', .9)}, account=account)
    assert bool(candidates) is expected
    # Future prices cannot create or invalidate an earlier observation.
    frame.loc[dates[-1] + pd.offsets.BDay()] = [200., 105., -.4]
    again, _ = scan_recovery_evidence(policy, date=dates[-1], user_panel={'a': frame},
                                      leaders={'a': _leader('a', .9)}, account=account)
    assert again == candidates


def test_selection_preserves_owned_members_and_ranks_eligible_additions_by_strength():
    account = AccountState.empty(100)
    account.anchor_weights = {'held': .6}
    leaders = {name: _leader(name, score) for name, score in
               [('held', .05), ('strong', .9), ('second', .8), ('deepest', .1)]}
    policy = SimpleNamespace(cfg=DEFAULT_CONFIG)
    selected, targets = _recovery_selection(policy, leaders=leaders, account=account,
        anchored_held={'held': .6}, candidates=[leaders[s] for s in ('deepest', 'strong', 'second')],
        crash_depth={'held': -.1, 'strong': -.2, 'second': -.3, 'deepest': -.5},
        recovery_elapsed=1, deep_count=2, admission_depth=-.15,
        risk=SimpleNamespace(), freeze_active=False)
    assert targets is None
    assert selected.selected == ['held', 'strong', 'second']
    assert selected.lead == 'held'
    assert account.anchor_weights == {'held': .6}


def test_empty_book_requires_a_fresh_breakout():
    dates = pd.bdate_range('2025-01-02', periods=12)
    frame = pd.DataFrame({'close': [100.] * 10 + [110., 108.],
                          'ma20': 105., 'ret120': -.4}, index=dates)
    policy = SimpleNamespace(cfg=DEFAULT_CONFIG, _liquidity_confirmed=lambda frame, date: True)
    candidates, _ = scan_recovery_evidence(policy, date=dates[-1], user_panel={'a': frame},
        leaders={'a': _leader('a', .9)}, account=AccountState.empty(100))
    assert candidates == []

def test_empty_cohort_preserves_depth_order_before_strength():
    leaders = {name: _leader(name, score) for name, score in
               [('deep', .1), ('second', .8), ('third', .9)]}
    selected, targets = _recovery_selection(SimpleNamespace(cfg=DEFAULT_CONFIG),
        leaders=leaders, account=AccountState.empty(100), anchored_held={},
        candidates=list(leaders.values()), crash_depth={'deep': -.5, 'second': -.4, 'third': -.3},
        recovery_elapsed=0, deep_count=3, admission_depth=-.15,
        risk=SimpleNamespace(), freeze_active=False)
    assert targets is None
    assert selected.selected == ['deep', 'second', 'third']
