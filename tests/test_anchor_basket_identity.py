"""Ranking changes must not erase evidence for unchanged risk sentinels."""
from uquant.config import DEFAULT_CONFIG
from uquant.risk.anchors import update_dynamic_anchors
from uquant.types import AccountState, LeaderScore


def leaders(reverse=False):
    symbols = ['sz300308', 'sh688008', 'sh688012']
    return {s: LeaderScore(symbol=s, score=.9, confidence=1., mature=True,
                          emerging=False, industry=str(i),
                          components={'secular_score': .7 + .05 * (2-i if reverse else i)})
            for i, s in enumerate(symbols)}


def test_same_basket_rank_changes_complete_confirmation():
    account = AccountState.empty(2000000.)
    for day in range(DEFAULT_CONFIG.risk_anchor_confirm_days):
        observed = update_dynamic_anchors(leaders=leaders(bool(day % 2)), account=account,
                                         cfg=DEFAULT_CONFIG, allow_reanchor=True)
    assert set(observed) == set(leaders())


def test_same_active_basket_rank_changes_preserve_break_memory():
    account = AccountState.empty(2000000.)
    account.risk_anchor_symbols = list(leaders())
    account.risk_anchor_signature = ','.join(account.risk_anchor_symbols)
    account.risk_streaks.update(reference_anchor_armed=1, reference_anchor_break=3)
    for _ in range(DEFAULT_CONFIG.risk_anchor_confirm_days):
        observed = update_dynamic_anchors(leaders=leaders(), account=account,
                                         cfg=DEFAULT_CONFIG, allow_reanchor=True)
    assert observed == tuple(leaders())
    assert account.risk_streaks['reference_anchor_armed'] == 1
    assert account.risk_streaks['reference_anchor_break'] == 3


def test_changed_member_restarts_confirmation():
    account = AccountState.empty(2000000.)
    account.risk_anchor_candidate_signature = 'sz300308,sh688008,sh688072'
    account.risk_anchor_candidate_streak = DEFAULT_CONFIG.risk_anchor_confirm_days - 1
    observed = update_dynamic_anchors(leaders=leaders(), account=account,
                                     cfg=DEFAULT_CONFIG, allow_reanchor=True)
    assert observed == ()
    assert account.risk_anchor_candidate_streak == 1
