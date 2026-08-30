"""Production-semantic reachability lifecycle integration."""

from __future__ import annotations

import hashlib
from copy import deepcopy

import pandas as pd
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_cash_rearm import _risk, _roles, _strict_inputs

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.decision import Target
from uquant.models.strategic_epoch import StrategicEpoch
from uquant.models.strategic_grant import StrategicGrantIntent
from uquant.models.trading import AccountOrder, Fill
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.authority import assess_strategic_capital_authority
from uquant.types import AccountState, Opportunity
from uquant.validation.absolute_generalization import analyze_terminal_scc
from uquant.validation.absolute_generalization.replay import (
    AbsoluteGeneralizationReplayPayload,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _payload(account: AccountState) -> AbsoluteGeneralizationReplayPayload:
    encoded = canonical_json_bytes(account.to_dict())
    return AbsoluteGeneralizationReplayPayload(
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _state(
    account: AccountState,
    *,
    session: str,
    outlet: tuple[
        Target,
        StrategicGrantIntent,
        StrategicEpoch,
        tuple[AccountOrder, ...],
        tuple[Fill, ...],
    ]
    | None = None,
) -> dict[str, object]:
    snapshots, leaders = _strict_inputs()
    risk = _risk()
    universe = _roles(session)
    authority = assess_strategic_capital_authority(account)
    active_epoch_state = "NONE"
    if account.active_strategic_epoch_id:
        active_epoch_state = next(
            epoch.realized_status
            for epoch in account.strategic_epochs
            if epoch.epoch_id == account.active_strategic_epoch_id
        )
    outlet_evidence: dict[str, object] | None = None
    if outlet is not None:
        target, grant, epoch, orders, fills = outlet
        outlet_evidence = {
            "target": target,
            "grant": grant,
            "epoch": epoch,
            "orders": orders,
            "fills": fills,
        }
    observation = account.strategic_qualification
    return {
        "account_payload": _payload(account),
        "cfg": DEFAULT_CONFIG,
        "risk": risk,
        "universe": universe,
        "snapshots": snapshots,
        "leaders": leaders,
        "flat_all_cash": authority.all_cash,
        "capital_budget_level": account.capital_budget_level,
        "repair_status": account.flat_book_capital_repair.status,
        "risk_state": risk.state.value,
        "opportunity_state": account.opportunity,
        "qualification_ready": observation.qualification_ready,
        "qualification_route": observation.qualification_route,
        "qualification_quorum": observation.qualification_quorum,
        "grant_state": (
            "NONE" if account.strategic_grant is None else account.strategic_grant.status
        ),
        "active_epoch_state": active_epoch_state,
        "pending_execution": bool(
            authority.pending_execution_symbols
            or authority.unsettled_order_ids
            or authority.late_fill_order_ids
        ),
        "unknown_execution": False,
        "reference_available": not set(
            (*universe.qualification_reference_symbols, *universe.risk_reference_symbols)
        ).difference(universe.available_symbols),
        "protected_authority": bool(
            account.protected_weights or account.protected_weight_epoch_ids
        ),
        "recovery_authority": bool(
            account.recovery_conviction_symbol or account.recovery_owner_epoch_id
        ),
        "restore_authority": bool(
            account.strategic_restore_weights or account.strategic_restore_epoch_ids
        ),
        "outlet_evidence": outlet_evidence,
    }


def test_production_repair_qualification_probe_fill_reaches_active() -> None:
    dates = pd.bdate_range("2023-01-02", periods=247)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    for frame in panel.values():
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * 0.99
        frame["volume"] = 100_000_000.0
    leaders = {
        symbol: _leader(symbol, 0.95 - index * 0.01, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:cash-rearm-lifecycle"
    account.code_hash = "code:production"
    account.capital_budget_level = 3
    account.opportunity = Opportunity.TREND.value
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    transitions: list[dict[str, object]] = []
    statuses: list[str] = []
    attributed: tuple[Target, ...] = ()

    for session in dates[-85:-24]:
        session_text = str(session.date())
        if account.strategic_qualification.qualification_route:
            transitions.append(
                {
                    "edge_kind": "OBSERVED",
                    "phase": "POST_OPEN",
                    "session": session_text,
                    "state": _state(deepcopy(account), session=session_text),
                }
            )
        targets = allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=deepcopy(_risk()),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={
                symbol: float(panel[symbol].loc[session, "close"])
                for symbol in symbols
            },
        )
        statuses.append(account.flat_book_capital_repair.status)
        positive = tuple(target for target in targets if target.weight > 0.0)
        if positive:
            attributed = attach_target_attribution(
                "optical",
                REQUIRED_AI_UNIVERSE_SHA256,
                signal_date=session_text,
                targets=positive,
            )
            planned = plan_orders(
                signal_date=session_text,
                targets=attributed,
                account=account,
                prices={
                    symbol: float(panel[symbol].loc[session, "close"])
                    for symbol in symbols
                },
                cfg=DEFAULT_CONFIG,
            )
            account.pending_orders = list(
                reconcile_account_orders(
                    account=account,
                    previous=[],
                    current=planned,
                    submitted_date=session_text,
                )
            )
        if account.strategic_qualification.qualification_route:
            transitions.append(
                {
                    "edge_kind": "OBSERVED",
                    "phase": "POST_DECISION",
                    "session": session_text,
                    "state": _state(deepcopy(account), session=session_text),
                }
            )
        if attributed:
            break

    assert "ACCUMULATING" in statuses
    assert account.flat_book_capital_repair.status == "CONSUMED"
    assert account.flat_book_capital_repair.last_ready_session == (
        account.pending_orders[0].signal_date
    )
    assert account.strategic_qualification.qualification_ready is True
    assert account.strategic_cash_rearm.status == "CONSUMED"
    assert len(account.strategic_epochs) == 1
    assert account.strategic_epochs[0].realized_status == "PROBE"
    assert account.pending_orders
    assert attributed

    signal_date = account.pending_orders[0].signal_date
    fill_session = next(session for session in dates if str(session.date()) > signal_date)
    fills = tuple(
        ExecutionPlanner(DEFAULT_CONFIG).execute_open(
            date=fill_session,
            account=account,
            panel=panel,
        )
    )
    assert fills
    assert account.strategic_grant is not None
    assert account.strategic_epochs[0].realized_status == "ACTIVE"
    assert account.active_strategic_epoch_id == account.strategic_epochs[0].epoch_id
    orders = tuple(account.order_ledger)
    outlet = (
        attributed[0],
        account.strategic_grant,
        account.strategic_epochs[0],
        orders,
        fills,
    )
    fill_session_text = str(fill_session.date())
    transitions.append(
        {
            "edge_kind": "OBSERVED",
            "phase": "POST_OPEN",
            "session": fill_session_text,
            "state": _state(
                deepcopy(account),
                session=fill_session_text,
                outlet=outlet,
            ),
        }
    )

    analysis = analyze_terminal_scc(transitions)

    assert analysis.passed is True
    assert analysis.no_positive_strategic_target_exit_count == 0
