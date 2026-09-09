"""Policy fixtures exercise identity/retry boundaries; no economic performance claims."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_grant_observation import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_grant import MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS, StrategicGrantStatus
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.grant_lifecycle import revalidate_strategic_grant
from uquant.types import AccountState, Opportunity, PendingOrder


def _pending():
    dates = pd.bdate_range('2023-01-02', periods=250)
    symbols = ('sz300308', 'sz300502', 'sz300394')
    panel = {s: _strategic_frame(dates) for s in symbols}
    leaders = {s: _leader(s, .96 - i * .01, industry='optical') for i, s in enumerate(symbols)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = 'account:primary'
    account.code_hash = 'code:production'
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-4:-2]:
        policy._initialize_strategic_cohort(date=date,user_panel=panel,leaders=leaders,account=account,risk=_risk(frozen=False))
    assert account.strategic_grant is not None
    account.strategic_grant.status = StrategicGrantStatus.PARTIALLY_FILLED.value
    return policy, dates, panel, leaders, account


def test_partial_grant_keeps_original_candidate_when_competing_route_ranks_first() -> None:
    policy, dates, panel, leaders, account = _pending()
    grant = account.strategic_grant
    identity = (grant.grant_id, grant.epoch_id, grant.qualification_signature, grant.qualification_route)
    for symbol in ('sh688008', 'sh688012', 'sh688200'):
        frame = _strategic_frame(dates)
        frame['close'] = frame['close'] ** 2
        panel[symbol] = frame
        leaders[symbol] = _leader(symbol, .99, industry='semiconductor')
    assert revalidate_strategic_grant(policy,date=dates[-2],user_panel=panel,leaders=leaders,
                                     account=account,risk=_risk(frozen=False),admission_open=True,weights_now={})
    assert account.strategic_grant is grant
    assert (grant.grant_id, grant.epoch_id, grant.qualification_signature, grant.qualification_route) == identity
    assert grant.status == StrategicGrantStatus.PARTIALLY_FILLED.value
    assert account.strategic_qualification.qualification_signature == grant.qualification_signature


def test_repeated_healthy_session_counts_once_and_retry_budget_expires_locally() -> None:
    policy, dates, panel, leaders, account = _pending()
    grant = account.strategic_grant
    for _ in range(2):
        assert revalidate_strategic_grant(policy,date=dates[-2],user_panel=panel,leaders=leaders,
                                         account=account,risk=_risk(frozen=False),admission_open=True,weights_now={})
    assert grant.healthy_retry_sessions == 1
    grant.healthy_retry_sessions = MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS
    assert not revalidate_strategic_grant(policy,date=dates[-1],user_panel=panel,leaders=leaders,
                                         account=account,risk=_risk(frozen=False),admission_open=True,weights_now={})
    assert grant.expiry_reason == 'qualification_observation_window_elapsed'
    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sz300308'] == 0
    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sz300502'] == 2


def test_missing_original_witness_blocks_retry_without_downgrading_full_grant() -> None:
    policy, dates, panel, leaders, account = _pending()
    grant = account.strategic_grant
    panel.pop('sz300394')
    # Current market/reference evidence remains the same; full authority still needs its witnesses.
    risk = replace(_risk(frozen=False), evidence={**_risk(frozen=False).evidence, 'risk_anchor_symbols': ['sh000300']})
    assert revalidate_strategic_grant(policy,date=dates[-2],user_panel=panel,leaders=leaders,
                                     account=account,risk=risk,admission_open=True,weights_now={})
    assert account.strategic_qualification.deployment_blocked
    assert account.strategic_qualification.deployment_block_reason == 'reference_coverage_or_confirmation'
    assert grant.healthy_retry_sessions == 0
    assert grant.qualification_quorum == 'FULL_COHORT'
    account.pending_orders = [PendingOrder(
        str(dates[-3].date()), grant.candidate_symbol, 'BUY', grant.target_weight,
        'pending grant', 'CORE', grant_id=grant.grant_id, epoch_id=grant.epoch_id,
    )]
    targets = policy.allocate(
        date=dates[-2], opportunity=Opportunity.TREND, risk=risk, user_panel=panel,
        leaders=leaders, account=account,
        prices={s: float(frame.loc[dates[-2], 'close']) for s, frame in panel.items()},
    )
    assert all(target.weight == 0.0 for target in targets)


def test_declared_unavailable_original_witness_blocks_even_with_cached_frame() -> None:
    from uquant.models.strategic_universe import build_strategic_universe_roles
    policy, dates, panel, leaders, account = _pending()
    roles = build_strategic_universe_roles(
        as_of=str(dates[-2].date()), tradable_symbols=panel,
        qualification_reference_symbols=panel, risk_reference_symbols=(),
        industries={s: 'optical' for s in panel}, available_symbols=('sz300308', 'sz300502'),
    )
    assert revalidate_strategic_grant(policy,date=dates[-2],user_panel=panel,leaders=leaders,
                                     account=account,risk=_risk(frozen=False),admission_open=True,
                                     weights_now={},strategic_universe=roles)
    assert account.strategic_qualification.deployment_blocked
    assert account.strategic_qualification.deployment_block_reason == 'reference_coverage_or_confirmation'


def test_decisive_reversal_retains_original_pair_and_requires_synchronized_witness() -> None:
    import numpy as np
    from test_lifecycle_and_risk import _trend_frame

    from uquant.types import Risk, RiskAssessment
    dates = pd.bdate_range('2023-01-02', periods=250)
    panel = {}
    for symbol, base in (('dominant', .69), ('runner', .725), ('reserve', .73)):
        close = np.concatenate((np.linspace(1., .68, len(dates) - 5), np.linspace(.69, .74, 5)))
        close[-61:-5] = np.linspace(base, .68, 56)
        panel[symbol] = _trend_frame(dates, close=close, ma20=.70, ma60=.72, ret20=.08, ret60=.07)
        panel[symbol]['atr'] = .02
    leaders = {s: _leader(s, score, industry='optical')
               for s, score in (('dominant', .70), ('runner', .60), ('reserve', .20))}
    leaders['runner'].components['trend_persistence'] = 1 / 3
    risk = RiskAssessment(Risk.NORMAL, 1., 1, {'tech_ret120': -.10, 'risk_anchor_symbols': [],
                          'risk_anchor_group_count': 0, 'configured_user_universe_size': 3}, (), 'NONE')
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = 'account:primary'
    account.code_hash = 'code:production'
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-2:]:
        policy._initialize_strategic_cohort(date=date,user_panel=panel,leaders=leaders,account=account,risk=risk)
    grant = account.strategic_grant
    assert grant is not None and grant.candidate_symbol == 'dominant'
    assert grant.qualification_quorum == 'FULL_COHORT'
    assert revalidate_strategic_grant(policy,date=dates[-1],user_panel=panel,leaders=leaders,
                                     account=account,risk=risk,admission_open=True,weights_now={})
    assert not account.strategic_qualification.deployment_blocked
    assert account.strategic_qualification.candidate_symbols == ['dominant', 'reserve', 'runner']
    panel.pop('reserve')
    assert revalidate_strategic_grant(policy,date=dates[-1],user_panel=panel,leaders=leaders,
                                     account=account,risk=risk,admission_open=True,weights_now={})
    assert account.strategic_qualification.deployment_blocked
    assert account.strategic_grant is grant and not grant.terminal
