"""Recovery observations remain causal and bounded while funding is frozen."""
from types import SimpleNamespace

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio.recovery.cohort_admission import scan_recovery_evidence
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
    candidates, _ = scan_recovery_evidence(policy, date=dates[-1], user_panel={'a': frame},
                                           leaders={'a': _leader('a', .9)}, account=account)
    assert bool(candidates) is expected
    # Future prices cannot create or invalidate an earlier observation.
    frame.loc[dates[-1] + pd.offsets.BDay()] = [200., 105., -.4]
    again, _ = scan_recovery_evidence(policy, date=dates[-1], user_panel={'a': frame},
                                      leaders={'a': _leader('a', .9)}, account=account)
    assert again == candidates
