"""Historical-consumption binding checks; full native account readback is separate."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from uquant.account.validation_strategy import _validate_rearm_repair_binding
from uquant.models.trading import AccountOrder


def _history():
    order = AccountOrder(
        order_id="O1", signal_date="2026-04-22", submitted_date="2026-04-22",
        symbol="sh601869", side="BUY", target_weight=.2, reason="repair",
        lifecycle="CORE", status="FILLED", requested_shares=100,
        filled_shares=100, remaining_shares=0, grant_id="grant-old", epoch_id="epoch-old",
    )
    return NS(
        strategic_cash_rearm=NS(
            status="CONSUMED", consumed_order=None, consumed_grant_id="grant-old",
            authorization_id="auth-old", candidate_symbol="sh601869",
            repair_episode_id="repair-old", capital_budget_level=1,
            risk_reference_universe_identity="risk", predicate_results=[NS(
                code="FLAT_BOOK_REPAIR_READY", passed=True, authoritative_state={
                    "repair_episode_id": "repair-old", "repair_status": "READY",
                    "healthy_session_count": 20, "required_healthy_sessions": 20,
                })],
        ),
        flat_book_capital_repair=NS(
            repair_episode_id="repair-new", capital_budget_level=2,
            risk_reference_universe_identity="risk", status="BLOCKED",
            first_observed_session="2026-05-29",
        ),
        strategic_grant=NS(status="COMPLETED", grant_id="grant-old", epoch_id="epoch-old",
                           authorization_id="auth-old", candidate_symbol="sh601869"),
        strategic_epochs=[NS(epoch_id="epoch-old", realized_status="CLOSED",
                             grant_id="grant-old", owner_symbol="sh601869",
                             first_fill_session="2026-04-23", closed_session="2026-05-28")],
        fills=[NS(side="BUY", shares=100, symbol="sh601869", grant_id="grant-old",
                  epoch_id="epoch-old", fill_date="2026-04-23")],
        order_ledger=[order], pending_orders=[],
    )


def test_closed_consumption_preserves_history_without_rebinding_new_episode():
    state = _history()
    before = deepcopy(state)
    _validate_rearm_repair_binding(state)
    assert state == before
    assert state.strategic_cash_rearm.repair_episode_id != state.flat_book_capital_repair.repair_episode_id


@pytest.mark.parametrize("fault", [
    "missing_authorization", "wrong_grant", "active_epoch", "missing_fill",
    "wrong_fill_epoch", "wrong_fill_date", "pending", "late_fill",
    "SUBMITTED", "CANCEL_REQUESTED", "PARTIALLY_FILLED", "wrong_ready_episode",
    "short_ready_count", "boolean_ready_count", "wrong_required", "failed_ready",
    "missing_ready", "unconsumed_authorization", "nonterminal_grant",
    "same_day_episode", "older_episode", "missing_close",
])
def test_historical_exception_requires_complete_settled_binding(fault):
    state = _history()
    proof = state.strategic_cash_rearm.predicate_results[0]
    if fault == "missing_authorization":
        state.strategic_grant.authorization_id = ""
    elif fault == "wrong_grant":
        state.strategic_grant.grant_id = "other"
    elif fault == "active_epoch":
        state.strategic_epochs[0].realized_status = "ACTIVE"
    elif fault == "missing_fill":
        state.fills.clear()
    elif fault == "wrong_fill_epoch":
        state.fills[0].epoch_id = "other"
    elif fault == "wrong_fill_date":
        state.fills[0].fill_date = "2026-04-24"
    elif fault == "pending":
        state.pending_orders.append(state.order_ledger[0])
    elif fault == "late_fill":
        order = state.order_ledger[0]
        order.status = "CANCELLED"
        order.cancel_reason = "strategic partial remainder replaced"
        order.remaining_shares = 100
    elif fault in {"SUBMITTED", "CANCEL_REQUESTED", "PARTIALLY_FILLED"}:
        state.order_ledger[0].status = fault
    elif fault == "wrong_ready_episode":
        proof.authoritative_state["repair_episode_id"] = "other"
    elif fault == "short_ready_count":
        proof.authoritative_state["healthy_session_count"] = 19
    elif fault == "boolean_ready_count":
        proof.authoritative_state["healthy_session_count"] = True
    elif fault == "wrong_required":
        proof.authoritative_state["required_healthy_sessions"] = 40
    elif fault == "failed_ready":
        proof.passed = False
    elif fault == "missing_ready":
        state.strategic_cash_rearm.predicate_results.clear()
    elif fault == "unconsumed_authorization":
        state.strategic_cash_rearm.status = "AUTHORIZED"
    elif fault == "same_day_episode":
        state.flat_book_capital_repair.first_observed_session = "2026-05-28"
    elif fault == "older_episode":
        state.flat_book_capital_repair.first_observed_session = "2026-05-27"
    elif fault == "missing_close":
        state.strategic_epochs[0].closed_session = ""
    elif fault == "nonterminal_grant":
        state.strategic_grant.status = "PENDING_EXECUTION"
    with pytest.raises(ValueError, match="repair episode binding"):
        _validate_rearm_repair_binding(state)
