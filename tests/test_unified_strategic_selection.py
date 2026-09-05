"""Strategic selection ranks independently qualified current candidates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import discovery, grant_lifecycle
from uquant.portfolio.strategic.rearm import observe_flat_book_capital_repair_state
from uquant.types import (
    AccountOrder,
    AccountState,
    LeaderScore,
    Opportunity,
    OrderStatus,
    PendingOrder,
    Risk,
    RiskAssessment,
)

OWNER = "sh600001"
REFERENCE = "sh600002"
EARLY_GROUP = ("sz000001", "sz000002", "sz000003")
ROUTES = ("established", "transition", "transition_impulse", "persistent_industry", "reversal_industry")


def _risk():
    return RiskAssessment(Risk.NORMAL, 0.95, 0, {
        "breadth20": 0.8, "broad_ret20": 0.05, "tech_ret20": 0.08,
        "broad_ret120": 0.12, "tech_ret120": 0.20, "risk_anchor_group_count": 3,
    }, (), "NONE")


def _snapshot(*, score):
    return {
        "leader_score": score, "leader_confidence": 0.9,
        "secular_score": 0.9, "secular_confidence": 0.9,
        "momentum60": 0.85, "momentum120": 0.85, "relative_strength": 0.85,
        "trend_persistence": 0.9, "ret20": 0.10, "ret60": 0.20, "ret120": 0.30,
        "industry_confidence": 0.95, "liquidity_confirmation": 1.0,
    }


def _leader(symbol, *, score, industry):
    return LeaderScore(symbol=symbol, score=score, confidence=0.9,
                       mature=False, emerging=True, industry=industry,
                       components={"unknown_industry": 0.0})


def _strategic_frame(dates):
    # Nonconstant returns also provide finite common-book correlation evidence.
    close = pd.Series([10.0 + i * 0.1 + i * i * 0.0001 for i in range(len(dates))], index=dates)
    return pd.DataFrame({"close": close, "amount": 1_000_000_000.0}, index=dates)


def _inputs(monkeypatch, *, early_score=None, early_confirmed=False, reverse=False, owner_score=0.95):
    dates = pd.bdate_range("2024-01-02", periods=250)
    symbols = [OWNER, REFERENCE, *(EARLY_GROUP if early_score is not None else ())]
    if reverse:
        symbols.reverse()
    snapshots = {}
    leaders = {}
    for symbol in symbols:
        early = symbol in EARLY_GROUP
        score = early_score if early else owner_score
        values = _snapshot(score=score)
        values.update(history=250.0, ret240=2.0 if early else 0.4, ret5=0.01,
                      persistent_ret240=2.0 if early else 0.4,
                      short_relative_strength=0.85, breakout_quality=0.85,
                      transition_score=0.0)
        snapshots[symbol] = values
        leaders[symbol] = replace(_leader(symbol, score=score, industry="power" if early else "compute"),
                                  mature=False)
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    tradable = {symbol: frame for symbol, frame in panel.items() if symbol != REFERENCE}
    universe = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=tradable,
        qualification_reference_symbols=panel, risk_reference_symbols=(),
        industries={symbol: leader.industry for symbol, leader in leaders.items()},
        available_symbols=panel,
    )
    monkeypatch.setattr(discovery, "strategic_qualification_snapshots",
                        lambda self, **kwargs: deepcopy(snapshots))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = "account:selection-test"
    account.code_hash = "code:selection-test"
    for symbol in symbols:
        # Prior sessions observed both the absolute route and strict owner quality.
        for route in (*ROUTES, "independent_core"):
            account.replacement_tenure[f"strategic_eligibility:{route}:{symbol}"] = (
                8 if symbol not in EARLY_GROUP or early_confirmed else 0
            )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    def initialize(risk=None):
        allocator._initialize_strategic_cohort(
            date=dates[-1], user_panel=tradable, leaders=leaders,
            account=account, risk=risk or _risk(), qualification_panel=panel,
            qualification_leaders=leaders, strategic_universe=universe,
        )

    return account, snapshots, leaders, initialize


@pytest.mark.parametrize("early_score,early_confirmed", ((0.80, True), (0.99, False)))
@pytest.mark.parametrize("reverse", (False, True))
def test_preferred_route_cannot_shadow_better_ready_candidate(monkeypatch, early_score, early_confirmed, reverse):
    account, _, _, initialize = _inputs(
        monkeypatch, early_score=early_score, early_confirmed=early_confirmed, reverse=reverse,
    )
    initialize()

    observed = account.strategic_qualification
    assert observed.candidate_symbol == OWNER
    assert observed.qualification_ready
    assert account.strategic_grant is not None
    assert account.strategic_grant.candidate_symbol == OWNER
    assert account.strategic_cohort_targets.get(OWNER, 0.0) > 0.0
    assert REFERENCE not in account.strategic_cohort_targets


def test_no_independently_qualified_candidate_creates_no_grant(monkeypatch):
    account, _, _, initialize = _inputs(monkeypatch, owner_score=0.89)
    initialize()

    assert not account.strategic_qualification.qualification_ready
    assert account.strategic_grant is None
    assert account.strategic_cohort_targets == {}
    assert not account.strategic_epochs


@pytest.mark.parametrize("freeze_cause", ("capital", "sentinel"))
def test_freeze_records_single_qualification_without_capital_authority(monkeypatch, freeze_cause):
    account, _, _, initialize = _inputs(monkeypatch)
    base = _risk()
    account.capital_budget_level = 3 if freeze_cause == "capital" else 0
    frozen = replace(base, freeze_new_risk=True, evidence={
        **base.evidence, "freeze_new_risk": True,
        "sentinel_freeze_new_risk": freeze_cause == "sentinel",
        "base_freeze_new_risk": freeze_cause == "capital",
    })
    initialize(frozen)

    observed = account.strategic_qualification
    assert observed.candidate_symbol == OWNER
    assert observed.qualification_ready
    assert observed.evidence_family_status["MARKET_CONFIRMATION"] == "CONFIRMED"
    assert observed.deployment_blocked
    assert observed.deployment_block_reason == "freeze_new_risk"
    assert account.strategic_grant is None
    assert account.strategic_cohort_targets == {}
    assert not account.strategic_epochs
    assert account.capital_budget_level == (3 if freeze_cause == "capital" else 0)


@pytest.mark.parametrize("state", (Risk.RISK_OFF, Risk.CRISIS, Risk.CAUTION))
def test_real_market_risk_cannot_authorize_single_owner(monkeypatch, state):
    account, _, _, initialize = _inputs(monkeypatch)
    initialize(replace(_risk(), state=state, votes=3))

    assert not account.strategic_qualification.qualification_ready
    assert account.strategic_grant is None
    assert account.strategic_cohort_targets == {}


def test_new_candidate_ranking_preserves_existing_grant_identity(monkeypatch):
    account, snapshots, leaders, initialize = _inputs(monkeypatch, early_score=0.80)
    later_snapshots = {symbol: snapshots.pop(symbol) for symbol in EARLY_GROUP}
    initialize()
    assert account.strategic_grant is not None
    original = deepcopy(account.strategic_grant)
    original_epochs = deepcopy(account.strategic_epochs)
    original_targets = dict(account.strategic_cohort_targets)
    snapshots.update(later_snapshots)
    for symbol in EARLY_GROUP:
        snapshots[symbol]["leader_score"] = 0.99
        leaders[symbol] = replace(leaders[symbol], score=0.99)
        for route in ROUTES:
            account.replacement_tenure[f"strategic_eligibility:{route}:{symbol}"] = 8
    initialize()

    assert account.strategic_grant == original
    assert account.strategic_epochs == original_epochs
    assert account.strategic_cohort_targets == original_targets


def test_single_owner_cannot_borrow_old_route_streak_for_new_strict_quality(monkeypatch):
    account, _, _, initialize = _inputs(monkeypatch)
    account.replacement_tenure[f"strategic_eligibility:independent_core:{OWNER}"] = 0
    initialize()

    assert account.replacement_tenure[f"strategic_eligibility:established:{OWNER}"] == 9
    assert account.replacement_tenure[f"strategic_eligibility:independent_core:{OWNER}"] == 1
    assert not account.strategic_qualification.qualification_ready
    assert account.strategic_grant is None
    assert account.strategic_cohort_targets == {}


def _ready_single_repair(monkeypatch):
    account, _, leaders, initialize = _inputs(monkeypatch)
    account.opportunity = Opportunity.TREND.value
    account.capital_budget_level = 3
    account.capital_peak = 3_000_000.0
    account.operating_peak = 2_500_000.0
    risk = replace(_risk(), target_gross_cap=0.5, freeze_new_risk=True, evidence={
        **_risk().evidence, "freeze_new_risk": True, "reference_coverage": 1.0,
        "transition_damage": 0.10, "sentinel_freeze_new_risk": False,
    })
    for date in pd.bdate_range("2024-01-02", periods=60):
        universe = build_strategic_universe_roles(
            as_of=str(date.date()), tradable_symbols=(OWNER,),
            qualification_reference_symbols=leaders, risk_reference_symbols=(),
            industries={symbol: leader.industry for symbol, leader in leaders.items()},
            available_symbols=leaders,
        )
        observe_flat_book_capital_repair_state(
            account=account, risk=risk, universe=universe,
            observed_session=str(date.date()), cfg=DEFAULT_CONFIG,
        )
    assert account.flat_book_capital_repair.status == "READY"
    assert account.flat_book_capital_repair.healthy_session_count == 60
    assert all(predicate.passed for predicate in account.flat_book_capital_repair.predicate_results)
    return account, risk, initialize


def test_ready_capital_repair_authorizes_one_bound_single_without_resetting_damage(monkeypatch):
    account, risk, initialize = _ready_single_repair(monkeypatch)
    episode = account.flat_book_capital_repair.repair_episode_id
    initialize(risk)

    qualification = account.strategic_qualification
    assert qualification.qualification_ready
    assert qualification.qualification_quorum == "ABSOLUTE_SINGLE"
    assert not qualification.deployment_blocked
    assert account.strategic_cohort_targets == {OWNER: pytest.approx(DEFAULT_CONFIG.core_admission_weight)}
    grant = account.strategic_grant
    assert grant is not None and grant.candidate_symbol == OWNER
    assert grant.authorization_id == account.strategic_cash_rearm.authorization_id
    assert account.strategic_cash_rearm.status == "CONSUMED"
    assert account.strategic_cash_rearm.consumed_grant_id == grant.grant_id
    assert account.flat_book_capital_repair.status == "CONSUMED"
    assert account.flat_book_capital_repair.repair_episode_id == episode
    assert account.capital_budget_level == 3
    assert account.capital_peak == 3_000_000.0
    assert account.operating_peak == 2_500_000.0
    original_grant = deepcopy(grant)
    initialize(risk)
    assert account.strategic_grant == original_grant
    assert len(account.strategic_epochs) == 1


@pytest.mark.parametrize("hazard,predicate", (
    ("pending", "PENDING_EXECUTION_CLEAR"),
    ("late_fill", "LATE_FILL_CLEAR"),
    ("sentinel", "SENTINEL_FREEZE_CLEAR"),
))
def test_ready_repair_cannot_bypass_current_execution_or_sentinel_hazard(monkeypatch, hazard, predicate):
    account, risk, initialize = _ready_single_repair(monkeypatch)
    if hazard == "pending":
        account.pending_orders.append(PendingOrder(
            signal_date="2024-12-13", symbol=OWNER, side="BUY", target_weight=0.2,
            reason="existing capital request", lifecycle="CORE",
        ))
    elif hazard == "late_fill":
        account.order_ledger.append(AccountOrder(
            order_id="O000000001", signal_date="2024-12-12", submitted_date="2024-12-13",
            symbol=OWNER, side="BUY", target_weight=0.2, reason="old broker remainder",
            lifecycle="CORE", status=OrderStatus.CANCELLED.value,
            requested_shares=1_000, filled_shares=400, remaining_shares=600,
            grant_id="grant_" + "1" * 64, event_id="evt_" + "2" * 64, last_event="CANCELLED",
            cancel_reason="strategic partial remainder replaced",
        ))
    else:
        risk = replace(risk, evidence={**risk.evidence, "sentinel_freeze_new_risk": True})
    initialize(risk)

    assert account.strategic_qualification.qualification_ready
    assert account.strategic_qualification.deployment_blocked
    assert not next(row for row in account.flat_book_capital_repair.predicate_results if row.code == predicate).passed
    assert account.strategic_cash_rearm.status != "CONSUMED"
    assert account.strategic_grant is None
    assert account.strategic_cohort_targets == {}
    assert not account.strategic_epochs
    assert account.capital_budget_level == 3
    assert account.capital_peak == 3_000_000.0


def _observe_group_candidates(monkeypatch, *, snapshots, leaders, counts, risk, strict_counts=None):
    dates = pd.bdate_range("2024-01-02", periods=250)
    panel = {symbol: _strategic_frame(dates) for symbol in snapshots}
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=panel,
        qualification_reference_symbols=panel, risk_reference_symbols=(),
        industries={symbol: leader.industry for symbol, leader in leaders.items()},
        available_symbols=panel,
    )
    monkeypatch.setattr(discovery, "strategic_qualification_snapshots",
                        lambda self, **kwargs: deepcopy(snapshots))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = "account:local-witness-test"
    account.code_hash = "code:local-witness-test"
    for symbol, count in counts.items():
        for route in ROUTES:
            account.replacement_tenure[f"strategic_eligibility:{route}:{symbol}"] = count
    for symbol, count in (strict_counts or {}).items():
        account.replacement_tenure[f"strategic_eligibility:independent_core:{symbol}"] = count
    PortfolioAllocator(DEFAULT_CONFIG)._initialize_strategic_cohort(
        date=dates[-1], user_panel=panel, leaders=leaders, account=account,
        risk=risk, strategic_universe=roles,
    )
    return account


@pytest.mark.parametrize("reverse", (False, True))
def test_full_witnesses_allow_confirmed_owner_below_unconfirmed_top_score(monkeypatch, reverse):
    symbols = list(EARLY_GROUP)
    scores = dict(zip(symbols, (0.89, 0.88, 0.87), strict=True))
    if reverse:
        symbols.reverse()
    snapshots = {}
    leaders = {}
    for symbol in symbols:
        snapshots[symbol] = {
            **_snapshot(score=scores[symbol]), "history": 250.0, "ret240": 0.4,
            "persistent_ret240": 0.4, "ret5": 0.01, "transition_score": 0.0,
            "short_relative_strength": 0.85, "breakout_quality": 0.85,
        }
        leaders[symbol] = _leader(symbol, score=scores[symbol], industry="power")
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders,
        counts={EARLY_GROUP[0]: 0, EARLY_GROUP[1]: 7, EARLY_GROUP[2]: 7}, risk=_risk(),
    )

    assert account.strategic_qualification.candidate_symbol == EARLY_GROUP[1]
    assert account.strategic_qualification.qualification_ready
    assert account.strategic_qualification.qualification_quorum == "FULL_COHORT"
    assert set(account.strategic_qualification.candidate_symbols) == set(EARLY_GROUP)
    assert account.strategic_grant is not None
    assert account.strategic_grant.candidate_symbol == EARLY_GROUP[1]
    assert account.strategic_cohort_targets.get(EARLY_GROUP[1], 0.0) > 0.0


@pytest.mark.parametrize("reverse,present", ((False, "both"), (True, "both"), (False, "valid"), (False, "invalid")))
def test_reversal_cannot_borrow_another_industry_groups_synchronization(monkeypatch, reverse, present):
    valid = ("sh600011", "sh600012", "sh600013")
    invalid = ("sz000011", "sz000012", "sz000013")
    symbols = [*(valid if present != "invalid" else ()), *(invalid if present != "valid" else ())]
    if reverse:
        symbols.reverse()
    snapshots = {}
    leaders = {}
    for symbol in symbols:
        is_valid = symbol in valid
        score = 0.80 if is_valid else 0.95
        snapshots[symbol] = {
            **_snapshot(score=score), "history": 250.0, "ret240": -0.20,
            "persistent_ret240": -0.20, "ret5": 0.06,
            "ret20": 0.0 if is_valid else -0.20, "ret60": -0.10, "ret120": -0.10,
            "transition_score": 0.0, "short_relative_strength": 0.85,
            "breakout_quality": 0.85,
        }
        leaders[symbol] = _leader(symbol, score=score, industry="compute" if is_valid else "power")
    risk = replace(_risk(), evidence={
        **_risk().evidence, "risk_anchor_symbols": [], "tech_ret120": -0.05, "broad_ret120": -0.05,
    })
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders,
        counts={symbol: 8 for symbol in symbols}, risk=risk,
    )

    observed = account.strategic_qualification
    if present == "invalid":
        assert not observed.qualification_ready
        assert account.strategic_grant is None
        assert account.strategic_cohort_targets == {}
        return
    assert observed.candidate_symbol in valid
    assert observed.qualification_ready
    assert observed.qualification_route == "reversal_industry"
    assert set(observed.candidate_symbols) <= set(valid)
    assert account.strategic_grant is not None
    assert account.strategic_grant.candidate_symbol in valid
    assert set(account.strategic_cohort_targets) <= set(valid)


def test_nondecisive_full_reversal_keeps_original_witnesses_on_next_observation(monkeypatch):
    symbols = ("sh600011", "sh600012", "sh600013")
    snapshots = {symbol: {
        **_snapshot(score=0.80), "history": 250.0, "ret240": -0.20,
        "persistent_ret240": -0.20, "ret5": 0.06, "ret20": 0.0,
        "ret60": -0.10, "ret120": -0.10, "transition_score": 0.0,
        "short_relative_strength": 0.85, "breakout_quality": 0.85,
    } for symbol in symbols}
    leaders = {symbol: _leader(symbol, score=0.80, industry="compute") for symbol in symbols}
    risk = replace(_risk(), evidence={
        **_risk().evidence, "risk_anchor_symbols": [], "tech_ret120": -0.05, "broad_ret120": -0.05,
    })
    account = _observe_group_candidates(
        monkeypatch, snapshots=snapshots, leaders=leaders,
        counts={symbol: 8 for symbol in symbols}, risk=risk,
    )
    grant = account.strategic_grant
    assert grant is not None and grant.qualification_quorum == "FULL_COHORT"
    original = (grant.grant_id, grant.epoch_id, grant.qualification_signature, grant.candidate_symbol)
    assert set(account.strategic_qualification.candidate_symbols) == set(symbols)
    dates = pd.bdate_range("2024-01-02", periods=251)
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    universe = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=symbols,
        qualification_reference_symbols=symbols, risk_reference_symbols=(),
        industries={symbol: "compute" for symbol in symbols}, available_symbols=symbols,
    )
    monkeypatch.setattr(grant_lifecycle, "strategic_qualification_snapshots",
                        lambda self, **kwargs: deepcopy(snapshots))
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    evidence = grant_lifecycle._grant_route_evidence(
        policy, grant=grant, date=dates[-1], user_panel=panel, resolved_panel=panel,
        leaders=leaders, risk=risk, universe=universe,
    )
    assert evidence.raw
    assert evidence.route.decisive_reversal_symbol is None
    assert set(evidence.symbols) == set(symbols)
    assert grant_lifecycle.revalidate_strategic_grant(
        policy, date=dates[-1], user_panel=panel, leaders=leaders, account=account,
        risk=risk, admission_open=True, weights_now={}, strategic_universe=universe,
    )
    assert not account.strategic_qualification.deployment_blocked
    assert account.strategic_grant is grant
    assert (grant.grant_id, grant.epoch_id, grant.qualification_signature, grant.candidate_symbol) == original
