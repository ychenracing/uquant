from __future__ import annotations

from dataclasses import replace

import pandas as pd
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_grant_observation import _risk

from uquant.account import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import qualification_candidates as candidates
from uquant.portfolio.strategic.discovery import strategic_qualification_snapshots
from uquant.types import AccountState


def test_absolute_confirmation_survives_other_candidate_change_freeze_and_restart() -> None:
    observer = getattr(candidates, 'observe_strategic_candidate_eligibility', None)
    assert callable(observer), 'all visible candidates need independent confirmation'
    dates = pd.bdate_range('2023-01-02', periods=250)
    symbols = ('sz300308', 'sz300502', 'sz300394')
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {symbol: _leader(symbol, .95, industry='optical') for symbol in symbols}
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash = 'code:test'
    account.data_hash = 'data:test'
    for index, date in enumerate(dates[-3:]):
        leaders[symbols[0]] = replace(leaders[symbols[0]], score=.95 - index * .01)
        leaders[symbols[1]] = replace(leaders[symbols[1]], score=.93 + index * .01)
        snapshots = strategic_qualification_snapshots(policy, date=date, user_panel=panel, leaders=leaders)
        if index:
            snapshots.pop(symbols[-1])
        values = observer(date=date, snapshots=snapshots, leaders=leaders, risk=_risk(frozen=True),
                          account=account, cfg=DEFAULT_CONFIG)
        assert values[symbols[0]]['persistent_industry'] == index + 1
        assert values[symbols[1]]['persistent_industry'] == index + 1
        assert observer(date=date, snapshots=snapshots, leaders=leaders, risk=_risk(frozen=False),
                        account=account, cfg=DEFAULT_CONFIG) == values
        account = account_from_dict(account.to_dict())
    assert account.capital_budget_level == 0


def test_initialization_observes_visible_candidates_before_active_owner_return() -> None:
    dates = pd.bdate_range('2023-01-02', periods=250)
    symbols = ('sz300308', 'sz300502', 'sz300394')
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {symbol: _leader(symbol, .95, industry='optical') for symbol in symbols}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.candidate_tenure['strategic_cohort_active'] = 1
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-3:]:
        allocator._initialize_strategic_cohort(date=date, user_panel=panel, leaders=leaders,
                                             account=account, risk=_risk(frozen=True))
    assert account.replacement_tenure.get('strategic_eligibility:persistent_industry:sz300502') == 3
    assert account.strategic_grant is None
    assert account.strategic_successor_qualification.candidate_symbol == ''


def test_strategic_retirement_resets_only_exiting_symbol_evidence() -> None:
    from uquant.portfolio.strategic.lifecycle import _retire_strategic_member
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.replacement_tenure.update({
        'strategic_eligibility:persistent_industry:sz300308': 8,
        'strategic_eligibility:established:sz300308': 7,
        'strategic_eligibility:persistent_industry:sz300502': 9,
    })
    account.capital_budget_level = 3
    _retire_strategic_member(account, 'sz300308')
    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sz300308'] == 0
    assert account.replacement_tenure['strategic_eligibility:established:sz300308'] == 0
    assert account.replacement_tenure['strategic_eligibility:persistent_industry:sz300502'] == 9
    assert account.capital_budget_level == 3
