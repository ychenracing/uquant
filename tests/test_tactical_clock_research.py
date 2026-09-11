"""Real allocation calls cannot strand or accelerate the original cooldown."""
from test_caution_exception_research import _decide, _fixture


def test_cooldown_expires_by_distinct_observations_then_rechecks_entry():
    frame, risk, account = _fixture()
    account.candidate_tenure['tactical_cooldown'] = 2
    assert _decide(frame.iloc[:-1], risk, account) == ()
    assert account.candidate_tenure['tactical_cooldown'] == 1
    assert _decide(frame.iloc[:-1], risk, account) == ()
    assert account.candidate_tenure['tactical_cooldown'] == 1
    targets = _decide(frame, risk, account)
    assert account.candidate_tenure['tactical_cooldown'] == 0
    assert len(targets) == 1 and targets[0].mechanism == 'TACTICAL_REBOUND'


def test_expired_cooldown_cannot_unfreeze_an_independent_overlay():
    frame, risk, account = _fixture()
    account.candidate_tenure['tactical_cooldown'] = 1
    risk.evidence['freeze_new_risk'] = True
    assert _decide(frame, risk, account) == ()
    assert account.candidate_tenure['tactical_cooldown'] == 0
    assert account.candidate_tenure.get('tactical_active', 0) == 0


def test_original_positive_reversal_only_releases_overheat_pause():
    for overheat, expected in [(1, 0), (0, 4)]:
        frame, risk, account = _fixture()
        frame['ret5'], frame['ret20'], frame['ret60'] = .1, -.2, .2
        account.candidate_tenure.update(tactical_cooldown=5, tactical_overheat_cooldown=overheat)
        _decide(frame, risk, account)
        assert account.candidate_tenure['tactical_cooldown'] == expected
        if overheat:
            assert account.candidate_tenure['tactical_overheat_cooldown'] == 0
