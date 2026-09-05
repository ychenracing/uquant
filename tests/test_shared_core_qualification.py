"""A confirmed group certificate can fund a separate ordinary CORE position."""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest
from test_lifecycle_and_risk import _leader
from test_strategic_probe_holding import OWNER, _filled_probe
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.discovery import (
    _route_confirmation,
    _strategic_route_quorum,
    current_core_qualification,
    strategic_candidate_certificates,
)
from uquant.portfolio.strategic.qualification_candidates import strategic_route_candidates
from uquant.types import Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

CHALLENGER = "sh688110"
WITNESSES = (CHALLENGER, "sh688498", "sh601869")


def _held_book():
    allocator, account, dates, original_panel, original_leaders, _roles = _filled_probe()
    panel = {OWNER: original_panel[OWNER], **{
        symbol: original_panel[OWNER].copy() for symbol in WITNESSES
    }}
    leaders = {OWNER: replace(original_leaders[OWNER], score=.50)}
    for symbol, score, industry in zip(WITNESSES, (.94, .93, .92), ("foundry", "equipment", "optical"), strict=True):
        leader = _leader(symbol, score, industry=industry)
        leaders[symbol] = replace(leader, components={**leader.components, "secular_score": .79})
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=tuple(panel),
        qualification_reference_symbols=tuple(panel), risk_reference_symbols=("sh000300", "sh000682"),
        industries={symbol: leader.industry for symbol, leader in leaders.items()},
        available_symbols=(*panel, "sh000300", "sh000682"),
    )
    return allocator, account, dates, panel, leaders, roles


def _decide(allocator, account, date, panel, leaders, roles, *, risk=None):
    previous = list(account.pending_orders)
    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}
    targets = allocator.allocate(
        date=date, opportunity=Opportunity.TREND, risk=risk or _risk(),
        user_panel=panel, leaders=leaders, account=account, prices=prices,
        qualification_panel=panel, qualification_leaders=leaders, strategic_universe=roles,
    )
    attributed = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=previous,
    )
    planned = plan_orders(signal_date=str(date.date()), targets=attributed,
                          account=account, prices=prices, cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=attributed, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged, submitted_date=str(date.date()),
    ))
    return targets


def _current_full_certificate(allocator, account, date, panel, leaders, roles):
    snapshots = allocator._strategic_qualification_snapshots(date=date, user_panel=panel, leaders=leaders)
    for route in strategic_route_candidates(allocator, snapshots=snapshots, leaders=leaders, risk=_risk()):
        if route.owner_symbol != CHALLENGER:
            continue
        quorum, _ = _strategic_route_quorum(
            allocator, route=route, snapshots=snapshots, leaders=leaders, risk=_risk(),
            reference_snapshots=snapshots, strategic_universe=roles,
        )
        if quorum is not None and quorum.route.value == "FULL_COHORT":
            return route, quorum, _route_confirmation(
                account=account, candidate=CHALLENGER, route=route.route, quorum=quorum,
            )
    return None


def test_current_full_certificate_funds_ordinary_core_beside_healthy_strategic_owner():
    allocator, account, dates, panel, leaders, roles = _held_book()
    held_shares = account.positions[OWNER].shares
    owner_identity = (account.strategic_grant.grant_id, account.strategic_epochs[0].epoch_id)
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(allocator, account, date, panel, leaders, roles)
    certificate = _current_full_certificate(allocator, account, date, panel, leaders, roles)
    assert certificate is not None and certificate[2] >= certificate[1].required_confirm_days
    assert set(certificate[0].symbols) == set(WITNESSES)
    assert account.replacement_tenure.get(f"strategic_eligibility:independent_core:{CHALLENGER}", 0) == 0
    buys = [order for order in account.pending_orders if order.symbol == CHALLENGER and order.side == "BUY"]
    assert len(buys) == 1
    assert buys[0].target_weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    assert buys[0].mechanism == "LEADER_SELECTION"
    assert buys[0].lifecycle == "CORE"
    assert buys[0].grant_id == buys[0].epoch_id == ""
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[DEFAULT_CONFIG.strategic_cohort_confirm_days], account=account, panel=panel,
    )
    assert any(fill.symbol == CHALLENGER and fill.side == "BUY" for fill in fills)
    assert account.positions[OWNER].shares == held_shares
    assert (account.strategic_grant.grant_id, account.strategic_epochs[0].epoch_id) == owner_identity
    assert len(account.strategic_epochs) == 1
    assert account.positions[CHALLENGER].grant_id == account.positions[CHALLENGER].epoch_id == ""


@pytest.mark.parametrize("block", ("risk_freeze", "no_cash", "unconfirmed", "witness_lost"))
def test_shared_certificate_preserves_existing_admission_limits(block):
    allocator, account, dates, panel, leaders, roles = _held_book()
    risk = replace(_risk(), freeze_new_risk=True) if block == "risk_freeze" else _risk()
    if block == "no_cash":
        # An account with no spendable cash cannot count a new SELL as settled funding.
        account.cash = 0.0
    elif block == "witness_lost":
        leaders[WITNESSES[-1]] = replace(leaders[WITNESSES[-1]], score=.10)
    count = DEFAULT_CONFIG.strategic_cohort_confirm_days - (block == "unconfirmed")
    for date in dates[:count]:
        _decide(allocator, account, date, panel, leaders, roles, risk=risk)
    certificate = _current_full_certificate(allocator, account, date, panel, leaders, roles)
    if block in {"risk_freeze", "no_cash"}:
        assert certificate is not None and certificate[2] >= certificate[1].required_confirm_days
    elif block == "unconfirmed":
        assert certificate is not None and certificate[2] < certificate[1].required_confirm_days
    else:
        assert certificate is None
    assert not any(order.symbol == CHALLENGER and order.side == "BUY" for order in account.pending_orders)
    assert CHALLENGER not in account.positions


@pytest.mark.parametrize("qualification_survives", (True, False), ids=("full-still-ready", "all-routes-lost"))
def test_partial_common_core_continuation_uses_the_same_current_certificate(qualification_survives):
    allocator, account, dates, panel, leaders, roles = _held_book()
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(allocator, account, date, panel, leaders, roles)
    original = next(order for order in account.pending_orders if order.symbol == CHALLENGER and order.side == "BUY")
    fill_day = dates[DEFAULT_CONFIG.strategic_cohort_confirm_days]
    panel[CHALLENGER].loc[fill_day, "volume"] = 100_000.0
    fills = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002)).execute_open(
        date=fill_day, account=account, panel=panel,
    )
    assert any(fill.symbol == CHALLENGER and fill.side == "BUY" for fill in fills)
    ledger = next(order for order in account.order_ledger if order.order_id == original.order_id)
    assert ledger.status == "PARTIALLY_FILLED" and ledger.remaining_shares > 0
    shares = account.positions[CHALLENGER].shares
    if not qualification_survives:
        leaders = {symbol: replace(leader, score=.10, components={
            **leader.components, "momentum60": 0.0, "momentum120": 0.0,
            "breakout_quality": 0.0, "secular_score": .10,
        }) if symbol in WITNESSES else leader for symbol, leader in leaders.items()}
    continuation_day = dates[DEFAULT_CONFIG.strategic_cohort_confirm_days + 1]
    _decide(allocator, account, continuation_day, panel, leaders, roles)
    assert account.replacement_tenure.get(f"strategic_eligibility:independent_core:{CHALLENGER}", 0) == 0
    remainder = [order for order in account.pending_orders if order.symbol == CHALLENGER and order.side == "BUY"]
    if qualification_survives:
        certificate = _current_full_certificate(allocator, account, continuation_day, panel, leaders, roles)
        assert certificate is not None and certificate[2] >= certificate[1].required_confirm_days
        assert len(remainder) == 1
        assert (remainder[0].order_id, remainder[0].event_id) == (original.order_id, original.event_id)
    else:
        assert not remainder
        assert ledger.status == "CANCELLED"
    assert account.positions[CHALLENGER].shares == shares
    next_fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[DEFAULT_CONFIG.strategic_cohort_confirm_days + 2], account=account, panel=panel,
    )
    assert any(fill.symbol == CHALLENGER and fill.side == "BUY" for fill in next_fills) is qualification_survives


def _shared_evidence(allocator, account, date, panel, leaders, roles):
    return current_core_qualification(
        allocator, date=date, user_panel=panel, leaders=leaders, account=account, risk=_risk(),
        qualification_panel=panel, qualification_leaders=leaders, strategic_universe=roles,
    )


def test_shared_confirmation_is_idempotent_after_same_day_restart_and_evaluation_is_read_only():
    allocator, account, dates, panel, leaders, roles = _held_book()
    _decide(allocator, account, dates[0], panel, leaders, roles)
    clocks = dict(account.replacement_tenure)
    assert _current_full_certificate(allocator, account, dates[0], panel, leaders, roles)[2] == 1
    restarted = account_from_dict(asdict(account))
    _decide(allocator, restarted, dates[0], panel, leaders, roles)
    assert restarted.replacement_tenure == clocks
    assert not restarted.pending_orders
    _decide(allocator, restarted, dates[1], panel, leaders, roles)
    before_evaluation = asdict(restarted)
    evidence = _shared_evidence(allocator, restarted, dates[1], panel, leaders, roles)
    assert evidence[CHALLENGER]["block"] == "READY"
    assert evidence[CHALLENGER]["confirmations"]["established"] == DEFAULT_CONFIG.strategic_cohort_confirm_days
    assert asdict(restarted) == before_evaluation
    repeated = _shared_evidence(allocator, restarted, dates[1], panel, leaders, roles)
    assert repeated == evidence
    assert asdict(restarted) == before_evaluation


def test_multiple_certificates_do_not_duplicate_budget_or_hide_candidate_behind_held_leader():
    allocator, account, dates, panel, leaders, roles = _held_book()
    leaders[OWNER] = replace(leaders[OWNER], score=.99, components={
        **leaders[OWNER].components, "secular_score": .79,
    })
    # A real current industry reference makes the existing PAIR contract valid,
    # alongside FULL; the low-score reference cannot itself become an owner.
    reference = "sh688981"
    panel[reference] = panel[CHALLENGER].copy()
    leaders[reference] = _leader(reference, .10, industry="foundry")
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=tuple(panel),
        qualification_reference_symbols=tuple(panel), risk_reference_symbols=("sh000300", "sh000682"),
        industries={symbol: leader.industry for symbol, leader in leaders.items()},
        available_symbols=(*panel, "sh000300", "sh000682"),
    )
    held_shares = account.positions[OWNER].shares
    for date in dates[:DEFAULT_CONFIG.strategic_two_name_confirm_days]:
        _decide(allocator, account, date, panel, leaders, roles)
    snapshots = allocator._strategic_qualification_snapshots(date=date, user_panel=panel, leaders=leaders)
    accepted = [(route, quorum) for route, quorum, streak in strategic_candidate_certificates(
        allocator, snapshots=snapshots, leaders=leaders, risk=_risk(), account=account,
        reference_snapshots=snapshots, strategic_universe=roles,
    ) if streak >= quorum.required_confirm_days]
    assert accepted[0][0].owner_symbol == OWNER
    assert len([route for route, _ in accepted if route.owner_symbol == CHALLENGER]) > 1
    buys = [order for order in account.pending_orders if order.symbol == CHALLENGER and order.side == "BUY"]
    assert len(buys) == 1 and buys[0].target_weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    assert len([order for order in account.order_ledger if order.symbol == CHALLENGER and order.side == "BUY"]) == 1
    assert account.positions[OWNER].shares == held_shares
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[DEFAULT_CONFIG.strategic_two_name_confirm_days], account=account, panel=panel,
    )
    assert len([fill for fill in fills if fill.symbol == CHALLENGER and fill.side == "BUY"]) == 1
    assert account.positions[OWNER].shares == held_shares


def test_disabled_strategic_dynamic_does_not_enable_ready_shared_certificate():
    allocator, account, dates, panel, leaders, roles = _held_book()
    frozen = replace(_risk(), freeze_new_risk=True)
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(allocator, account, date, panel, leaders, roles, risk=frozen)
    assert _shared_evidence(allocator, account, date, panel, leaders, roles)[CHALLENGER]["block"] == "READY"
    assert account.replacement_tenure.get(f"strategic_eligibility:independent_core:{CHALLENGER}", 0) == 0
    disabled = PortfolioAllocator(DEFAULT_CONFIG.override(strategic_dynamic_enabled=False))
    assert _shared_evidence(disabled, account, date, panel, leaders, roles) == {}
    _decide(disabled, account, dates[DEFAULT_CONFIG.strategic_cohort_confirm_days], panel, leaders, roles)
    assert not any(order.symbol == CHALLENGER and order.side == "BUY" for order in account.pending_orders)
