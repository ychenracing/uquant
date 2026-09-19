"""Active risk memory survives rank-only re-anchoring; admission stays unchanged."""
from uquant.config import DEFAULT_CONFIG
from uquant.risk.anchors import update_dynamic_anchors
from uquant.types import AccountState, LeaderScore


def leaders(reverse=False):
    symbols = ['sz300308', 'sh688008', 'sh688012']
    return {s: LeaderScore(symbol=s, score=.9, confidence=1., mature=True,
                           emerging=False, industry=str(i),
                           components={'secular_score': .7 + .05 * (2-i if reverse else i)})
            for i, s in enumerate(symbols)}


def test_active_members_keep_armed_and_break_memory_after_rank_reconfirmation():
    account = AccountState.empty(2000000.)
    account.risk_anchor_symbols = list(leaders())
    account.risk_anchor_signature = ','.join(account.risk_anchor_symbols)
    account.risk_streaks.update(reference_anchor_armed=1, reference_anchor_break=3)
    for _ in range(DEFAULT_CONFIG.risk_anchor_confirm_days):
        observed = update_dynamic_anchors(leaders=leaders(), account=account,
                                         cfg=DEFAULT_CONFIG, allow_reanchor=True)
    assert observed == tuple(reversed(leaders()))
    assert account.risk_streaks['reference_anchor_armed'] == 1
    assert account.risk_streaks['reference_anchor_break'] == 3


def test_real_membership_replacement_still_clears_old_break_memory():
    account = AccountState.empty(2000000.)
    account.risk_anchor_symbols = ['sz300308', 'sh688008', 'sh688072']
    account.risk_anchor_signature = ','.join(account.risk_anchor_symbols)
    account.risk_streaks.update(reference_anchor_armed=1, reference_anchor_break=3)
    for _ in range(DEFAULT_CONFIG.risk_anchor_confirm_days):
        observed = update_dynamic_anchors(leaders=leaders(), account=account,
                                         cfg=DEFAULT_CONFIG, allow_reanchor=True)
    assert observed == tuple(reversed(leaders()))
    assert account.risk_streaks['reference_anchor_armed'] == 0
    assert account.risk_streaks['reference_anchor_break'] == 0


def test_initial_admission_retains_existing_rank_confirmation_rule():
    account = AccountState.empty(2000000.)
    for day in range(DEFAULT_CONFIG.risk_anchor_confirm_days):
        observed = update_dynamic_anchors(leaders=leaders(bool(day % 2)), account=account,
                                         cfg=DEFAULT_CONFIG, allow_reanchor=True)
        assert observed == ()
        assert account.risk_anchor_candidate_streak == 1
