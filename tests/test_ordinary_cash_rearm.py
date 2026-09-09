"""Native ordinary CORE consumption of account-owned repair; no economic claim."""
from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader
from test_strategic_cash_rearm import _risk
from test_unified_core_book import _inputs

from uquant.account.codec import account_from_dict
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic import rearm
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

SYMBOL = "sh688146"


def _roles(date):
    return build_strategic_universe_roles(
        as_of=str(date.date()), tradable_symbols=(SYMBOL,),
        qualification_reference_symbols=(SYMBOL,),
        risk_reference_symbols=("sh000300", "sh000682"),
        industries={SYMBOL: "optical"},
        available_symbols=(SYMBOL, "sh000300", "sh000682"),
    )


def _allocate(policy, account, date, panel, leaders, risk, *, bind_inputs=True):
    if bind_inputs:
        risk.evidence["decision_input_identity"] = {
            "as_of": str(date.date()), "code_hash": account.code_hash,
            "data_hash": sha256(panel[SYMBOL].loc[:date].to_csv().encode()).hexdigest(),
        }
    return policy.allocate(
        date=date, opportunity=Opportunity.TREND, risk=risk, user_panel=panel,
        leaders=leaders, account=account,
        prices={SYMBOL: float(panel[SYMBOL].loc[date, "close"])},
        qualification_panel=panel, qualification_leaders=leaders,
        strategic_universe=_roles(date),
    )


def _scenario(*, sessions=19):
    _, source, _, _ = _inputs()
    frame = source["sh600001"].copy()
    frame.index = pd.bdate_range(end="2026-06-19", periods=len(frame))
    frame["open"] = frame["close"]
    frame["high"] = frame["close"] * 1.01
    frame["low"] = frame["close"] * .99
    frame["volume"] = 100_000_000.
    panel = {SYMBOL: frame}
    leaders = {SYMBOL: _leader(SYMBOL, .95)}
    account = AccountState.empty(2_000_000.)
    account.account_identity = "account:ordinary-repair"
    account.code_hash = "code:native-regression"
    account.data_hash = "data:synthetic-regression"
    account.opportunity = Opportunity.TREND.value
    account.capital_budget_level = 1
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    risk = _risk(target_gross_cap=1., reduction_level=1)
    # Complete references and healthy market, but no same-industry witness:
    # independent CORE is valid while strategic single-name quorum is not.
    dates = frame.index[-30:]
    for date in dates[:sessions]:
        rearm.observe_flat_book_capital_repair_state(
            account=account, risk=risk, universe=_roles(date),
            observed_session=str(date.date()), cfg=DEFAULT_CONFIG,
        )
        assert not _allocate(policy, account, date, panel, leaders, risk)
    assert account.strategic_grant is None
    assert not account.strategic_epochs
    assert not account.strategic_qualification.qualification_ready
    return policy, account, dates[sessions:], panel, leaders, risk


def _decide(policy, account, date, panel, leaders, risk):
    previous = list(account.pending_orders)
    targets = _allocate(policy, account, date, panel, leaders, risk)
    attributed = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=previous,
    )
    planned = plan_orders(
        signal_date=str(date.date()), targets=attributed, account=account,
        prices={SYMBOL: float(panel[SYMBOL].loc[date, "close"])}, cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(retained=previous, planned=planned, targets=attributed, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged, submitted_date=str(date.date()),
    ))
    # Same post-reconciliation boundary as the application: consume only a
    # concrete ledger order, never a proposal or an unrounded target.
    rearm.consume_ordinary_cash_rearm_authorization(
        account=account, orders=account.pending_orders, observed_session=str(date.date()),
    )
    return targets


def test_twentieth_healthy_session_funds_current_independent_core_without_strategic_grant():
    policy, account, dates, panel, leaders, risk = _scenario()
    assert str(dates[0].date()) == "2026-06-05"
    before = (account.capital_budget_level, account.capital_peak, account.operating_peak, account.cash)
    targets = _decide(policy, account, dates[0], panel, leaders, risk)
    assert len(targets) == 1 and targets[0].weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    assert targets[0].mechanism == "LEADER_SELECTION"
    assert len(account.pending_orders) == 1
    order = account.pending_orders[0]
    assert order.side == "BUY" and order.lifecycle == "CORE"
    assert order.grant_id == order.epoch_id == ""
    reference = account.strategic_cash_rearm.consumed_order
    assert (reference.order_id, reference.event_id) == (order.order_id, order.event_id)
    assert account.strategic_cash_rearm.consumed_grant_id == ""
    assert account.flat_book_capital_repair.status == "CONSUMED"
    assert (account.capital_budget_level, account.capital_peak, account.operating_peak, account.cash) == before
    restored = account_from_dict(asdict(account))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=restored, panel=panel)
    assert len(fills) == 1 and fills[0].shares > 0
    assert fills[0].order_id == reference.order_id and fills[0].event_id == reference.event_id
    assert restored.positions[SYMBOL].grant_id == restored.positions[SYMBOL].epoch_id == ""
    assert restored.strategic_grant is None and not restored.strategic_epochs


def test_nineteenth_healthy_session_cannot_fund_even_ready_independent_core():
    policy, account, dates, panel, leaders, risk = _scenario(sessions=18)
    assert not _allocate(policy, account, dates[0], panel, leaders, risk)
    assert account.flat_book_capital_repair.healthy_session_count == 19
    assert risk.evidence["core_allocation"]["symbols"][SYMBOL]["entry"]["block"] == "READY"
    assert not account.pending_orders and not account.order_ledger


@pytest.mark.parametrize("denial", ("current_quality", "sentinel", "shock", "reference_gap"))
def test_ready_account_repair_never_substitutes_for_current_entry_or_risk_permission(denial):
    policy, account, dates, panel, leaders, risk = _scenario()
    if denial == "current_quality":
        leaders[SYMBOL] = replace(leaders[SYMBOL], score=.1)
    elif denial == "sentinel":
        risk = replace(risk, evidence={**risk.evidence, "sentinel_freeze_new_risk": True})
    elif denial == "shock":
        risk = replace(risk, shock_state="RECOVERY")
    else:
        risk = replace(risk, evidence={**risk.evidence, "reference_coverage": .99})
    assert not _allocate(policy, account, dates[0], panel, leaders, risk)
    assert not account.pending_orders and not account.order_ledger
    assert account.strategic_grant is None


def test_cancelled_ordinary_rearm_cannot_revive_saturated_repair_after_codec():
    policy, account, dates, panel, leaders, risk = _scenario()
    _decide(policy, account, dates[0], panel, leaders, risk)
    original = account.pending_orders[0]
    healthy = leaders[SYMBOL]
    leaders[SYMBOL] = replace(healthy, score=.1)
    _decide(policy, account, dates[1], panel, leaders, risk)
    assert not account.pending_orders
    assert account.order_ledger[0].status == "CANCELLED"
    assert not account.fills and not account.positions
    account = account_from_dict(asdict(account))
    leaders[SYMBOL] = healthy
    for date in dates[2:]:
        _decide(policy, account, date, panel, leaders, risk)
        assert not account.pending_orders
    assert risk.evidence["core_allocation"]["symbols"][SYMBOL]["entry"]["block"] == "READY"
    assert len(account.order_ledger) == 1 and account.order_ledger[0].order_id == original.order_id
    assert not account.fills


def test_partial_ordinary_rearm_keeps_exact_order_and_unfilled_quantity_after_restart():
    policy, account, dates, panel, leaders, risk = _scenario()
    _decide(policy, account, dates[0], panel, leaders, risk)
    original = account.pending_orders[0]
    panel[SYMBOL].loc[dates[1], "volume"] = 100_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002)).execute_open(
        date=dates[1], account=account, panel=panel,
    )
    assert len(fills) == 1 and fills[0].shares > 0
    assert account.order_ledger[0].status == "PARTIALLY_FILLED"
    remaining = account.order_ledger[0].remaining_shares
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[1], panel, leaders, risk)
    assert len(account.pending_orders) == 1
    assert account.pending_orders[0].order_id == original.order_id
    assert account.pending_orders[0].event_id == original.event_id
    assert account.order_ledger[0].remaining_shares == remaining
    assert len(account.order_ledger) == 1
    assert rearm.ordinary_cash_rearm_order_open(
        account=account, risk=risk, cfg=DEFAULT_CONFIG, order=account.pending_orders[0],
    )


def test_consumed_ordinary_authority_is_bound_to_actual_order_and_event():
    policy, account, dates, panel, leaders, risk = _scenario()
    _decide(policy, account, dates[0], panel, leaders, risk)
    original = account.pending_orders[0]
    assert rearm.ordinary_cash_rearm_order_open(
        account=account, risk=risk, cfg=DEFAULT_CONFIG, order=original,
    )
    for forged in (replace(original, order_id="O999999999"),
                   replace(original, event_id="evt_" + "0" * 64)):
        assert not rearm.ordinary_cash_rearm_order_open(
            account=account, risk=risk, cfg=DEFAULT_CONFIG, order=forged,
        )


def test_partial_attempt_losing_current_quality_is_cancelled_without_reopening_capital():
    policy, account, dates, panel, leaders, risk = _scenario()
    _decide(policy, account, dates[0], panel, leaders, risk)
    panel[SYMBOL].loc[dates[1], "volume"] = 100_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002)).execute_open(
        date=dates[1], account=account, panel=panel,
    )
    assert len(fills) == 1 and fills[0].shares > 0
    shares = account.positions[SYMBOL].shares
    cash = account.cash
    healthy = leaders[SYMBOL]
    leaders[SYMBOL] = replace(healthy, score=.1)
    _decide(policy, account, dates[1], panel, leaders, risk)
    assert not any(order.side == "BUY" for order in account.pending_orders)
    assert account.order_ledger[0].status == "CANCELLED"
    assert SYMBOL not in account.protected_weights
    account = account_from_dict(asdict(account))
    leaders[SYMBOL] = healthy
    for date in dates[2:]:
        _decide(policy, account, date, panel, leaders, risk)
        assert not any(order.side == "BUY" for order in account.pending_orders)
    assert account.positions[SYMBOL].shares == shares
    assert account.cash == cash
    assert len(account.fills) == 1


def test_proposal_without_native_reconciliation_does_not_consume_repair():
    policy, account, dates, panel, leaders, risk = _scenario()
    targets = _allocate(policy, account, dates[0], panel, leaders, risk)
    assert len(targets) == 1 and targets[0].weight > 0
    assert not account.pending_orders and not account.order_ledger
    assert account.flat_book_capital_repair.status == "READY"
    assert account.strategic_cash_rearm.consumed_order is None
    rearm.consume_ordinary_cash_rearm_authorization(
        account=account, orders=(), observed_session=str(dates[0].date()),
    )
    assert account.flat_book_capital_repair.status == "READY"
    assert account.strategic_cash_rearm.consumed_order is None
    assert account.strategic_cash_rearm.status != "CONSUMED"


@pytest.mark.parametrize("mutation", ("oversize", "symbol", "lifecycle", "origin", "mechanism", "grant"))
def test_bound_order_reference_cannot_expand_or_change_ordinary_authority(mutation):
    policy, account, dates, panel, leaders, risk = _scenario()
    _decide(policy, account, dates[0], panel, leaders, risk)
    original = account.pending_orders[0]
    changes = {
        "oversize": {"target_weight": original.target_weight + .1},
        "symbol": {"symbol": "sh688498"},
        "lifecycle": {"lifecycle": "ACTIVE"},
        "origin": {"origin_subsystem": "STRATEGIC"},
        "mechanism": {"mechanism": "STRATEGIC_COHORT"},
        "grant": {"grant_id": "grant_" + "0" * 64},
    }
    assert not rearm.ordinary_cash_rearm_order_open(
        account=account, risk=risk, cfg=DEFAULT_CONFIG, order=replace(original, **changes[mutation]),
    )


@pytest.mark.parametrize("mutation", ("order_id", "event_id", "unspent", "oversize"))
def test_codec_rejects_consumed_order_reference_without_matching_native_evidence(mutation):
    policy, account, dates, panel, leaders, risk = _scenario()
    _decide(policy, account, dates[0], panel, leaders, risk)
    raw = asdict(account)
    if mutation in {"order_id", "event_id"}:
        raw["strategic_cash_rearm"]["consumed_order"][mutation] = "unrelated-native-reference"
    elif mutation == "unspent":
        raw["flat_book_capital_repair"]["status"] = "READY"
    else:
        raw["pending_orders"][0]["target_weight"] += .1
        raw["order_ledger"][0]["target_weight"] += .1
    with pytest.raises((ValueError, RuntimeError)):
        account_from_dict(raw)


@pytest.mark.parametrize("mutation", ("missing", "stale"))
def test_current_decision_input_identity_is_required_for_ordinary_rearm(mutation):
    policy, account, dates, panel, leaders, risk = _scenario()
    if mutation == "missing":
        risk.evidence.pop("decision_input_identity", None)
    else:
        assert risk.evidence["decision_input_identity"]["as_of"] < str(dates[0].date())
    targets = _allocate(policy, account, dates[0], panel, leaders, risk, bind_inputs=False)
    assert not targets
    assert not account.pending_orders and not account.order_ledger
    assert account.strategic_cash_rearm.consumed_order is None


def _sentinel_only(risk):
    from uquant.risk_sentinel.integration import sentinel_freeze_authorized

    result = replace(risk, freeze_new_risk=True, evidence={
        **risk.evidence, "sentinel_freeze_new_risk": True, "base_freeze_new_risk": False,
    })
    assert sentinel_freeze_authorized(result)
    return result


def test_sentinel_planning_copy_cannot_count_twentieth_repair_session():
    policy, account, dates, panel, leaders, risk = _scenario()
    assert account.flat_book_capital_repair.healthy_session_count == 19
    sentinel = _sentinel_only(risk)
    assert not _allocate(policy, account, dates[0], panel, leaders, sentinel)
    assert account.flat_book_capital_repair.healthy_session_count <= 19
    assert account.flat_book_capital_repair.status != "READY"
    assert account.strategic_cash_rearm.consumed_order is None
    assert not account.pending_orders and not account.order_ledger
    account_from_dict(asdict(account))


def test_sentinel_after_completed_native_ordinary_fill_keeps_repair_and_reference_consistent():
    policy, account, dates, panel, leaders, risk = _scenario()
    _decide(policy, account, dates[0], panel, leaders, risk)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].shares > 0
    assert account.order_ledger[0].status == "FILLED" and not account.pending_orders
    shares = account.positions[SYMBOL].shares
    sentinel = _sentinel_only(risk)
    _decide(policy, account, dates[1], panel, leaders, sentinel)
    assert not any(order.side == "BUY" for order in account.pending_orders)
    restored = account_from_dict(asdict(account))
    assert restored.positions[SYMBOL].shares == shares
    assert len(restored.fills) == 1
    if restored.strategic_cash_rearm.consumed_order is not None:
        assert restored.flat_book_capital_repair.status == "CONSUMED"
