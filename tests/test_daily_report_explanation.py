from __future__ import annotations

from copy import deepcopy

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
