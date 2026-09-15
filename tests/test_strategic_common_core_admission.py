"""Native qualification plus initial-funding seams; not economic acceptance."""
from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_grant_observation import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import qualification_candidates as candidates
from uquant.portfolio.strategic.discovery import current_core_qualification
from uquant.portfolio.strategic.ownership import _fund_owner_targets, activate_strategic_cohort
from uquant.types import AccountState, Opportunity


def _confirmed_entry():
    dates = pd.bdate_range("2023-01-02", periods=247)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {symbol: _leader(symbol, .90 - index * .05, industry="optical", mature=False)
               for index, symbol in enumerate(symbols)}
    account = AccountState.empty(2_000_000.0)
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-DEFAULT_CONFIG.strategic_cohort_confirm_days:]:
        policy.allocate(date=date, opportunity=Opportunity.TREND, risk=_risk(frozen=True),
                        user_panel=panel, leaders=leaders, account=account,
                        prices={s: float(panel[s].loc[date, "close"]) for s in symbols})
    date = dates[-1]
    certificates = current_core_qualification(
        policy, date=date, user_panel=panel, leaders=leaders,
        account=account, risk=_risk(frozen=False),
    )
    owner = account.strategic_qualification.candidate_symbol
    assert owner in certificates
    assert account.strategic_grant is None
    return policy, account, date, panel, leaders, certificates, owner


def _entry(policy, account, date, panel, score, certificate):
    return candidates.candidate_entry(
        policy, symbol=score.symbol, score=score, date=date, user_panel=panel,
        account=account, confirmation_days=DEFAULT_CONFIG.leader_tenure_days,
        certificate=certificate,
    )


def test_nonmature_owner_uses_real_shared_certificate_without_independent_days():
    policy, account, date, panel, leaders, certificates, owner = _confirmed_entry()
    score = leaders[owner]
    assert score.mature is False
    assert candidates.strategic_candidate_confirmation(
        account=account, symbol=owner, route="independent_core") < DEFAULT_CONFIG.leader_tenure_days
    before = deepcopy((account, certificates))
    entry = _entry(policy, account, date, panel, score, certificates[owner])
    assert (account, certificates) == before
    assert entry is not certificates[owner]
    assert set(entry) == set(certificates[owner]) | {"checks"}
    assert {key: entry[key] for key in certificates[owner]} == certificates[owner]
    assert entry["block"] == "READY"
    assert entry["qualification_evidence_sha256"] == certificates[owner]["qualification_evidence_sha256"]
    assert entry["required_confirmation"] == certificates[owner]["required_confirmation"]


def test_no_own_certificate_does_not_borrow_witness_confirmation():
    policy, account, date, panel, leaders, certificates, owner = _confirmed_entry()
    peer = next(s for s in certificates[owner]["witnesses"] if s != owner and s in leaders)
    score = leaders[peer]
    assert score.mature is False
    # Explicit no-own-certificate seam: do not fabricate a peer certificate
    # merely because the genuine owner's evidence mentions it as a witness.
    entry = _entry(policy, account, date, panel, score, None)
    assert entry["block"] == "NOT_MATURE"
    assert "qualification_evidence_sha256" not in entry


def test_real_certificate_does_not_override_current_stock_structure():
    policy, account, date, panel, leaders, certificates, owner = _confirmed_entry()
    panel[owner].loc[date, "close"] = panel[owner].loc[date, "ma60"] * .5
    entry = _entry(policy, account, date, panel, leaders[owner], certificates[owner])
    assert entry["block"] == "STRUCTURE_NOT_REPAIRED"
    assert list(entry["checks"]) == ["confidence", "industry", "current_data", "history", "structure"]
    assert entry["checks"]["structure"] == {"passed": False, "as_of": str(date.date())}
    assert certificates[owner]["block"] == "READY"
    assert entry["qualification_evidence_sha256"] == certificates[owner]["qualification_evidence_sha256"]


def _qualified(account, symbols):
    observed = account.strategic_qualification
    return candidates.QualifiedStrategicRoute(
        symbols=list(symbols), route=observed.qualification_route,
        admission_state="EMERGING_SECULAR", signature=observed.qualification_signature,
        decisive_reversal_symbol=None, admission_authorized=True,
        quorum_route="FULL_COHORT", restricted_initial_weight=None,
        cash_rearm_authorized=False,
    )


def test_denied_peer_funding_remains_cash_without_redistributing_weight():
    policy, account, date, panel, leaders, _, owner = _confirmed_entry()
    peer = next(s for s in leaders if s != owner)
    desired = {owner: .20, peer: .20}
    committed = {}
    # Isolate the initial-capital seam with explicit eligibility outcomes.
    targets = _fund_owner_targets(
        policy, qualified=_qualified(account, desired), owner=owner,
        held=set(), reserved=set(), dominant_symbol=None, desired=desired,
        committed=committed, cash=1.0, leaders=leaders, user_panel=panel,
        date=date, risk=_risk(frozen=False),
        entry_eligibility={owner: {"block": "READY"}, peer: {"block": "NOT_MATURE"}},
    )
    assert targets == {owner: .20}
    assert committed == {owner: .20}
    assert sum(targets.values()) == pytest.approx(.20)
    assert desired == {owner: .20, peer: .20}
    assert account.cash == 2_000_000.0 and not account.positions


def test_denied_initial_owner_creates_no_grant_or_epoch():
    policy, account, date, panel, leaders, _, owner = _confirmed_entry()
    activate_strategic_cohort(
        policy, qualified=_qualified(account, leaders), snapshots={}, leaders=leaders,
        account=account, date=date, risk=_risk(frozen=False), user_panel=panel,
        entry_eligibility={s: {"block": "STRUCTURE_NOT_REPAIRED" if s == owner else "READY"}
                           for s in leaders},
    )
    assert account.strategic_grant is None
    assert not account.strategic_epochs and not account.active_strategic_epoch_id
    assert not account.strategic_cohort_targets
    assert account.strategic_qualification.deployment_blocked
    assert account.cash == 2_000_000.0 and not account.pending_orders
