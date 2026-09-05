from __future__ import annotations

from copy import deepcopy

import pytest

from uquant.report import render_daily_report
from uquant.types import (
    AccountOrder,
    AccountState,
    Decision,
    Opportunity,
    PendingOrder,
    Position,
    Risk,
    Target,
)


def _decision(*, summary: dict[str, object]) -> Decision:
    return Decision(
        date="2025-01-06",
        opportunity=Opportunity.TREND,
        risk=Risk.NORMAL,
        target_gross=0.2,
        target_k=1,
        targets=(Target("new", 0.2, "CORE", 0.8, 1.0, "recorded admission reason"),),
        pending_orders=(
            PendingOrder("2025-01-06", "new", "BUY", 0.2, "recorded order reason", "CORE", order_id="O000000002"),
        ),
        risk_summary=summary,
        decision_digest="recorded-digest",
    )


def test_daily_report_separates_account_balances_from_targets_and_capital_room() -> None:
    """Targets and cash must not be displayed as settled holdings or free admission room."""
    account = AccountState.empty(100_000.0)
    account.cash = 40_000.0
    account.broker_as_of = "2025-01-06"
    account.positions = {
        "held": Position("held", shares=100, avg_cost=12.5),
        "closed": Position("closed", shares=0, avg_cost=15.0),
    }
    account.order_ledger = [
        AccountOrder(
            "O000000001", "2025-01-03", "2025-01-06", "blocked", "BUY", 0.1,
            "cancel requested", "CORE", status="CANCEL_REQUESTED",
        )
    ]
    decision = _decision(summary={
        "target_gross_cap": 0.6,
        "system_gross_cap": 0.9,
        "reasons": ["recorded account risk reason"],
    })
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account)

    holdings = report.split("## Holdings and capital", 1)[1].split("## Risk Sentinel", 1)[0]
    assert "| held | 100 | 12.50 | CORE |" in holdings
    assert "| new |" not in holdings
    assert "| closed |" not in holdings
    assert "Recorded cash: 40,000.00" in holdings
    assert "Broker snapshot: 2025-01-06" in holdings
    assert "Risk gross cap: 60.0%; system gross cap: 90.0%" in holdings
    assert "not unreserved buying power" in holdings
    assert "O000000001 (blocked)" in holdings
    assert "recorded account risk reason" in holdings
    assert "recorded admission reason" in report
    assert "O000000002" in report
    assert "recorded order reason" in report
    assert (decision, account) == before


def test_daily_report_scopes_recorded_block_to_its_candidate_and_admits_missing_reasons() -> None:
    """A primary-candidate block must not become a guessed explanation for every ranked name."""
    account = AccountState.empty(100_000.0)
    account.strategic_qualification.candidate_symbol = "stale-account-candidate"
    decision = _decision(summary={
        "leader_ranking": [{"symbol": "unknown"}, {"symbol": "blocked"}, {"symbol": "new"}],
        "strategic_qualification": {
            "candidate_symbol": "blocked",
            "qualification_route": "established",
            "qualification_ready": True,
            "qualification_streak": 3,
            "qualification_last_observed_session": "2025-01-06",
            "deployment_blocked": True,
            "deployment_block_reason": "reference_coverage_or_confirmation",
            "unavailable_reference_symbols": ["reference"],
        },
    })
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account)

    candidates = report.split("## Candidate explanation", 1)[1].split("## Risk\n", 1)[0]
    assert "Candidate observation: blocked; route: established; ready: YES" in candidates
    assert "Observed session: 2025-01-06; confirmation streak: 3" in candidates
    assert "Deployment block for blocked: reference_coverage_or_confirmation" in candidates
    assert "Unavailable references: reference" in candidates
    assert "Ranked symbols without a BUY intent: unknown, blocked" in candidates
    assert "Per-candidate admission reasons and remaining capital limits are not recorded" in candidates
    assert "Ranking alone does not establish qualification" in candidates
    assert "stale-account-candidate" not in report
    assert "Only a new daily Decision can change targets" in candidates
    assert (decision, account) == before


def test_daily_report_does_not_infer_no_trade_bands_or_zero_limits_from_missing_evidence() -> None:
    """No order can mean many things; missing evidence must not be rendered as a known cause."""
    decision = Decision(
        date="2025-01-06", opportunity=Opportunity.CHOPPY, risk=Risk.NORMAL,
        target_gross=0.0, target_k=0, targets=(), pending_orders=(),
        risk_summary={}, decision_digest="empty-evidence",
    )

    report = render_daily_report(decision, AccountState.empty(100_000.0))

    assert "No held shares recorded" in report
    assert "Risk gross cap: UNAVAILABLE; system gross cap: UNAVAILABLE" in report
    assert "Candidate qualification evidence: NOT RECORDED" in report
    assert "No executable account order was recorded" in report
    assert "remain inside no-trade bands" not in report


def test_report_uses_final_orders_and_recorded_limits_when_a_provisional_buy_is_frozen() -> None:
    account = AccountState.empty(100_000.0)
    trace = {
        "as_of": "2025-01-06", "scope": "FINAL_DECISION",
        "planning_scope": "SENTINEL_PLANNING_ONLY", "final_freeze_new_risk": True,
        "symbols": {"blocked": {
            "held_weight": 0.0, "proposal_weight": .2, "final_target_weight": 0.0,
            "final_target_reason": "NO_TARGET", "orders": [],
            "allocation_reason": "CORE_ADMISSION", "rank_score": .9,
            "entry": {"block": "READY", "confirmations": {"established": 5},
                      "required_confirmation": 5},
            "budget_checks": [{"cash_room": .4, "gross_room": .4, "symbol_room": .6,
                               "industry_room": .15, "correlation_room": .15,
                               "funded_increment": .15, "minimum_increment": .2}],
            "order_planning": {"block": "NO_TARGET"},
        }},
    }
    decision = _decision(summary={"core_allocation": trace})
    before = deepcopy((decision, account))
    report = render_daily_report(decision, account)
    assert "blocked | 0.0% → 0.0% | NO ORDER; NO_TARGET" in report
    assert "NEW_RISK_FROZEN" in report
    assert "confirmation established 5/5" in report
    assert "industry 15.0%, correlation 15.0%" in report
    assert "funded increment 15.0%; minimum 20.0%" in report
    assert "The recorded risk freeze must clear" in report
    assert "limits are not recorded for every ranked" not in report
    assert (decision, account) == before


def _core_allocation_decision(row: dict[str, object], *, frozen: bool = False) -> Decision:
    return _decision(summary={"core_allocation": {
        "as_of": "2025-01-06", "scope": "FINAL_DECISION",
        "planning_scope": "SENTINEL_PLANNING_ONLY" if frozen else "STRATEGY",
        "final_freeze_new_risk": frozen,
        "symbols": {"partial": {
            "held_weight": .1, "final_target_weight": .1,
            "final_target_reason": "retained holding", "orders": [],
            "entry": {"block": "CONFIRMATION_INCOMPLETE", "confirmations": {"established": 1},
                      "required_confirmation": 5},
            **row,
        }},
    }})


def test_report_explains_pending_current_quality_without_repeating_fresh_confirmation() -> None:
    decision = _core_allocation_decision({
        "final_target_weight": .2, "final_target_reason": "pending core buy",
        "orders": [{"side": "BUY", "order_id": "O000000003"}],
        "allocation_reason": "PENDING_CORE_BUY",
        "restore_block": "PENDING_CORE_BUY_ALREADY_EVALUATED",
        "pending_entry": {"block": "READY", "confirmations": {"established": 1},
                          "required_confirmation": 1},
    })
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account)
    row = report.split("| partial |", 1)[1].split("\n", 1)[0]

    assert "10.0% → 20.0% | BUY O000000003; pending core buy" in row
    assert "pending current quality READY; confirmation established 1/1" in row
    assert "CONFIRMATION_INCOMPLETE" not in row
    assert "1/5" not in row
    assert "PENDING_CORE_BUY_ALREADY_EVALUATED" not in row
    assert (decision, account) == before


@pytest.mark.parametrize("block", ["CONFIRMATION_INCOMPLETE", "NOT_MATURE"])
def test_report_does_not_hide_pending_quality_rejection_behind_restoration_routing(block: str) -> None:
    pending_entry: dict[str, object] = {"block": block, "required_confirmation": 1}
    if block == "CONFIRMATION_INCOMPLETE":
        pending_entry["confirmations"] = {"established": 0}
    decision = _core_allocation_decision({
        "pending_entry": pending_entry,
        "restore_block": "PENDING_CORE_BUY_ALREADY_EVALUATED",
    })
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account)
    row = report.split("| partial |", 1)[1].split("\n", 1)[0]

    assert f"NO ORDER; retained holding | {block}; pending current quality {block}" in row
    assert "PENDING_CORE_BUY_ALREADY_EVALUATED" not in row
    assert "1/5" not in row
    if block == "CONFIRMATION_INCOMPLETE":
        assert "confirmation established 0/1" in row
    else:
        assert "Leadership must become mature" in row
    assert (decision, account) == before


@pytest.mark.parametrize("frozen", [False, True])
def test_report_preserves_final_freeze_and_capital_limit_over_pending_quality(frozen: bool) -> None:
    decision = _core_allocation_decision({
        "pending_entry": {"block": "READY", "confirmations": {"established": 1},
                          "required_confirmation": 1},
        "restore_block": "PENDING_CORE_BUY_ALREADY_EVALUATED",
        "allocation_reason": "CAPITAL_LIMIT",
        "budget_checks": [{"cash_room": .0, "gross_room": .4, "symbol_room": .5,
                           "industry_room": .3, "correlation_room": .3,
                           "funded_increment": .0, "minimum_increment": .05}],
    }, frozen=frozen)
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account)
    row = report.split("| partial |", 1)[1].split("\n", 1)[0]

    expected = "NEW_RISK_FROZEN" if frozen else "CAPITAL_LIMIT"
    assert f"NO ORDER; retained holding | {expected}; pending current quality READY" in row
    assert "confirmation established 1/1" in row
    assert "cash 0.0%" in row
    if frozen:
        assert "The recorded risk freeze must clear" in row
    else:
        assert "Available settled capital or a binding cap must change" in row
    assert (decision, account) == before


def test_report_explains_missing_link_between_restoration_episode_and_current_holding() -> None:
    decision = _core_allocation_decision({
        "restore_block": "RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING",
    })
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account)
    row = report.split("| partial |", 1)[1].split("\n", 1)[0]

    assert "NO ORDER; retained holding | RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING" in row
    assert "uninterrupted holding must span the recorded risk episode" in row
    assert "verify actual fills" in row
    assert (decision, account) == before
