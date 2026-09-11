"""A recovered sector guard needs fresh market proof for mature-only admission."""
from dataclasses import asdict, replace

from test_ordinary_trend_budget import _decide, _scenario
from uquant.account.codec import account_from_dict
from uquant.types import Risk


def _inputs():
    policy, account, dates, panel, base, risk = _scenario()
    low = tuple(base)[-1]
    base[low] = replace(base[low], score=.81)
    account.leader_tenure.update({symbol: 20 for symbol in base})
    risk.evidence.update(ai_fast_return=.01, tech_speed=.02, broad_speed=.02,
                         breadth20=.8, broad_ret20=.02, tech_ret20=.1,
                         broad_ret120=.1, tech_ret120=.4)
    return policy, account, dates, panel, base, risk


def _guarded(risk):
    return replace(risk, state=Risk.RISK_OFF, freeze_new_risk=True,
                   evidence={**risk.evidence, 'sector_guard_active': True})


def test_sector_recovery_does_not_reuse_old_tenure_or_duplicate_observations():
    policy, account, dates, panel, base, risk = _inputs()
    assert not _decide(policy, account, dates[0], panel, base, _guarded(risk))
    for date in dates[1:5]:
        assert not _decide(policy, account, date, panel, base, risk)
        assert not _decide(policy, account, date, panel, base, risk)
        account = account_from_dict(asdict(account))
    assert _decide(policy, account, dates[5], panel, base, risk)
    assert len(account.pending_orders) == 1
    assert account.pending_orders[0].target_weight == policy.cfg.single_core_entry_cap


def test_unhealthy_market_resets_fresh_sector_recovery_confirmation():
    policy, account, dates, panel, base, risk = _inputs()
    assert not _decide(policy, account, dates[0], panel, base, _guarded(risk))
    for date in dates[1:4]:
        assert not _decide(policy, account, date, panel, base, risk)
    weak = replace(risk, evidence={**risk.evidence, 'breadth20': .1})
    assert not _decide(policy, account, dates[4], panel, base, weak)
    for date in dates[5:9]:
        assert not _decide(policy, account, date, panel, base, risk)
    assert not account.pending_orders


def test_no_sector_episode_does_not_add_a_market_wait():
    policy, account, dates, panel, base, risk = _inputs()
    assert _decide(policy, account, dates[0], panel, base, risk)
