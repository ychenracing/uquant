from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import date
from typing import Any

from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.market import ReplayUniverse
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.models.trading import AccountOrder, Fill
from uquant.types import AccountState
from uquant.validation.absolute_generalization.contract import (
    ABSOLUTE_GENERALIZATION_CONTRACT_SHA256,
)
from uquant.validation.absolute_generalization.replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayAccountSnapshot,
    AbsoluteGeneralizationReplayManifestSnapshot,
    AbsoluteGeneralizationReplayObservation,
    AbsoluteGeneralizationReplayPayload,
    AbsoluteGeneralizationReplayRoleSnapshot,
)
from uquant.validation.absolute_generalization.scenarios import (
    AbsoluteGeneralizationScenario,
)

OWNER = "sh600487"
REMOVED = "sz300308"
GRANT_ID = "grant_" + "1" * 64
EPOCH_ID = "epoch_" + "2" * 64
ORDER_ID = "order_" + "3" * 64
FILL_ID = "fill_" + "4" * 64
AUTHORIZATION_ID = "rearm_" + "5" * 64
QUALIFICATION_SIGNATURE = "strategic_qualification:test"
QUALIFICATION_EVIDENCE_SHA256 = "6" * 64


def payload(value: object) -> AbsoluteGeneralizationReplayPayload:
    encoded = canonical_json_bytes(value)
    return AbsoluteGeneralizationReplayPayload(
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def scenario() -> AbsoluteGeneralizationScenario:
    return AbsoluteGeneralizationScenario(
        cell_id=f"remove-{REMOVED}",
        removed_symbol=REMOVED,
        window_start=date(2023, 1, 3),
        window_end=date(2026, 8, 5),
        shard="loo-f",
        is_critical=True,
        is_witness=False,
        contract_sha256=ABSOLUTE_GENERALIZATION_CONTRACT_SHA256,
    )


def _current_account(
    *, cash: float, last_session: str, shares: int = 0
) -> dict[str, Any]:
    raw = AccountState.empty(1_000.0).to_dict()
    for field in (
        "account_migrations",
        "fills",
        "lifecycle_events",
        "order_ledger",
        "reconciliation_events",
        "replacement_events",
        "risk_events",
        "rotation_dates",
        "sector_shock_dates",
        "strategic_epochs",
    ):
        raw.pop(field)
    raw["cash"] = cash
    raw["last_successful_run"] = last_session
    if shares:
        raw["positions"] = {
            OWNER: {
                "symbol": OWNER,
                "shares": shares,
                "avg_cost": 10.0,
                "entry_date": "2023-01-04",
                "highest_close": 20.0,
                "lifecycle": "STRATEGIC",
                "tranches": [],
                "grant_id": GRANT_ID,
                "epoch_id": EPOCH_ID,
            }
        }
    return raw


def _snapshot(
    *, cash: float, last_session: str, shares: int = 0
) -> AbsoluteGeneralizationReplayAccountSnapshot:
    empty_chain = hashlib.sha256(
        canonical_json_bytes({"kind": "empty_entity_ledger"})
    ).hexdigest()
    return AbsoluteGeneralizationReplayAccountSnapshot(
        account_payload=payload(
            _current_account(cash=cash, last_session=last_session, shares=shares)
        ),
        changed_order_payloads=(),
        changed_epoch_payloads=(),
        removed_order_keys=(),
        removed_epoch_keys=(),
        order_ledger_chain_sha256=empty_chain,
        epoch_ledger_chain_sha256=empty_chain,
    )


def _roles(session: str) -> AbsoluteGeneralizationReplayRoleSnapshot:
    roles = build_strategic_universe_roles(
        as_of=session,
        tradable_symbols=(OWNER,),
        qualification_reference_symbols=(OWNER,),
        risk_reference_symbols=(OWNER, "sh000300", "sh000682"),
        available_symbols=(OWNER, "sh000300", "sh000682"),
        industries={OWNER: "optical"},
    )
    return AbsoluteGeneralizationReplayRoleSnapshot(
        as_of=roles.as_of,
        tradable_symbols=roles.tradable_symbols,
        qualification_reference_symbols=roles.qualification_reference_symbols,
        risk_reference_symbols=roles.risk_reference_symbols,
        available_symbols=roles.available_symbols,
        unavailable_reference_symbols=roles.unavailable_reference_symbols,
        point_in_time_industries=roles.point_in_time_industries,
        tradable_identity=roles.tradable_identity,
        qualification_reference_identity=roles.qualification_reference_identity,
        risk_reference_identity=roles.risk_reference_identity,
        point_in_time_industry_identity=roles.point_in_time_industry_identity,
    )


def _replay_universe_identity() -> str:
    return ReplayUniverse.from_symbols(
        tradable_symbols=(OWNER,),
        reference_symbols=(OWNER,),
        index_symbols=("sh000300", "sh000682"),
    ).identity_sha256


def _manifest(session: str) -> AbsoluteGeneralizationReplayManifestSnapshot:
    files = ((OWNER, "a" * 64),)
    digest = hashlib.sha256(
        json.dumps(dict(files), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return AbsoluteGeneralizationReplayManifestSnapshot(
        generated_at=session,
        source="fixture",
        adjustment="none",
        files=files,
        symbols=tuple(sorted((OWNER, "sh000300", "sh000682"))),
        start="2023-01-03",
        end=session,
        digest=digest,
    )


def _qualification() -> dict[str, object]:
    return {
        "candidate_symbol": OWNER,
        "qualification_signature": QUALIFICATION_SIGNATURE,
        "qualification_route": "ABSOLUTE_SINGLE",
        "qualification_quorum": "ABSOLUTE_SINGLE",
        "qualification_evidence_sha256": QUALIFICATION_EVIDENCE_SHA256,
        "qualification_ready": True,
        "unavailable_reference_symbols": [],
    }


def _grant() -> dict[str, object]:
    return {
        "grant_id": GRANT_ID,
        "candidate_symbol": OWNER,
        "qualification_signature": QUALIFICATION_SIGNATURE,
        "qualification_route": "ABSOLUTE_SINGLE",
        "qualification_quorum": "ABSOLUTE_SINGLE",
        "qualification_evidence_sha256": QUALIFICATION_EVIDENCE_SHA256,
        "created_session": "2023-01-03",
        "status": "ACTIVE",
        "previous_grant_id": "",
        "authorization_id": AUTHORIZATION_ID,
    }


def _decision(session: str, *, target: bool) -> dict[str, object]:
    target_rows: list[dict[str, object]] = []
    order_rows: list[dict[str, object]] = []
    if target:
        target_rows.append(
            {
                "symbol": OWNER,
                "weight": 0.2,
                "origin_subsystem": "STRATEGIC",
                "grant_id": GRANT_ID,
                "epoch_id": EPOCH_ID,
                "event_id": "event-1",
            }
        )
        order_rows.append(
            {
                "order_id": ORDER_ID,
                "signal_date": session,
                "symbol": OWNER,
                "side": "BUY",
                "target_weight": 0.2,
                "origin_subsystem": "STRATEGIC",
                "grant_id": GRANT_ID,
                "epoch_id": EPOCH_ID,
                "event_id": "event-1",
            }
        )
    return {
        "date": session,
        "opportunity": "TREND",
        "risk": "NORMAL",
        "target_gross": 0.2 if target else 0.0,
        "targets": target_rows,
        "pending_orders": order_rows,
        "risk_summary": {
            "target_gross_cap": 0.8,
            "market_wide_execution_block": False,
            "strategic_qualification": _qualification(),
            "strategic_grant": _grant(),
            "strategic_cash_rearm": {
                "authorization_id": AUTHORIZATION_ID,
                "authorized_session": "2023-01-03",
            },
            "flat_book_capital_repair": {
                "repair_episode_id": "repair-1",
                "capital_budget_level": 1,
                "repair_target_level": 0,
                "required_healthy_sessions": 1,
                "healthy_session_count": 1,
                "first_observed_session": "2023-01-03",
                "last_ready_session": "2023-01-03",
                "status": "READY",
                "reset_reason": "",
            },
        },
    }


def complete_replay() -> AbsoluteGeneralizationReplay:
    order = AccountOrder(
        order_id=ORDER_ID,
        signal_date="2023-01-03",
        submitted_date="2023-01-03",
        symbol=OWNER,
        side="BUY",
        target_weight=0.2,
        reason="strategic fixture",
        lifecycle="STRATEGIC",
        status="FILLED",
        requested_shares=10,
        filled_shares=10,
        remaining_shares=0,
        last_update_date="2023-01-04",
        last_event="FILL",
        event_id="event-1",
        origin_subsystem="STRATEGIC",
        grant_id=GRANT_ID,
        epoch_id=EPOCH_ID,
    )
    fill = Fill(
        signal_date="2023-01-03",
        fill_date="2023-01-04",
        symbol=OWNER,
        side="BUY",
        shares=10,
        price=10.0,
        gross_value=100.0,
        commission=0.0,
        stamp_duty=0.0,
        transfer_fee=0.0,
        slippage_cost=0.0,
        reason="strategic fixture",
        lifecycle="STRATEGIC",
        order_id=ORDER_ID,
        fill_id=FILL_ID,
        event_id="event-1",
        origin_subsystem="STRATEGIC",
        grant_id=GRANT_ID,
        epoch_id=EPOCH_ID,
    )
    final_account = AccountState.empty(1_000.0).to_dict()
    final_account["cash"] = 900.0
    final_account["order_ledger"] = [asdict(order)]
    final_account["fills"] = [asdict(fill)]
    final_account["positions"] = {
        OWNER: {
            "symbol": OWNER,
            "shares": 10,
            "avg_cost": 10.0,
            "entry_date": "2023-01-04",
            "highest_close": 20.0,
            "lifecycle": "STRATEGIC",
            "tranches": [],
            "grant_id": GRANT_ID,
            "epoch_id": EPOCH_ID,
        }
    }
    final_account["strategic_epochs"] = [
        {
            "epoch_id": EPOCH_ID,
            "owner_symbol": OWNER,
            "qualification_signature": QUALIFICATION_SIGNATURE,
            "qualification_route": "ABSOLUTE_SINGLE",
            "qualification_quorum": "ABSOLUTE_SINGLE",
            "grant_id": GRANT_ID,
            "opened_session": "2023-01-03",
            "first_fill_session": "2023-01-04",
            "active_session": "2023-01-04",
            "closed_session": "",
            "close_reason": "",
            "previous_epoch_id": "",
            "realized_status": "ACTIVE",
        }
    ]
    observations = (
        AbsoluteGeneralizationReplayObservation(
            session="2023-01-03",
            equity=1_000.0,
            closing_marks=(),
            decision_payload=payload(_decision("2023-01-03", target=True)),
            new_fills=(),
            post_open_account=_snapshot(cash=1_000.0, last_session=""),
            post_decision_account=_snapshot(cash=1_000.0, last_session="2023-01-03"),
            roles=_roles("2023-01-03"),
            intentional_role_absent_symbols=(REMOVED,),
            expected_but_unavailable_symbols=(),
            replay_universe_identity=_replay_universe_identity(),
            data_manifest=_manifest("2023-01-03"),
            loaded_symbols=(OWNER, "sh000300", "sh000682"),
        ),
        AbsoluteGeneralizationReplayObservation(
            session="2023-01-04",
            equity=1_100.0,
            closing_marks=((OWNER, 20.0),),
            decision_payload=payload(_decision("2023-01-04", target=False)),
            new_fills=(payload(asdict(fill)),),
            post_open_account=_snapshot(
                cash=900.0, last_session="2023-01-03", shares=10
            ),
            post_decision_account=_snapshot(
                cash=900.0, last_session="2023-01-04", shares=10
            ),
            roles=_roles("2023-01-04"),
            intentional_role_absent_symbols=(REMOVED,),
            expected_but_unavailable_symbols=(),
            replay_universe_identity=_replay_universe_identity(),
            data_manifest=_manifest("2023-01-04"),
            loaded_symbols=(OWNER, "sh000300", "sh000682"),
        ),
    )
    return AbsoluteGeneralizationReplay(
        scenario=scenario(),
        status="COMPLETE",
        replay_error="",
        initial_cash=1_000.0,
        final_equity=1_100.0,
        observations=observations,
        final_account_payload=payload(final_account),
    )


def completed_sale_replay() -> AbsoluteGeneralizationReplay:
    """Extend the canonical fixture with one attributed, fee-bearing close."""

    replay = complete_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    assert isinstance(account, dict)
    sell_order = dict(account["order_ledger"][0])
    sell_order.update(
        {
            "order_id": "order_" + "8" * 64,
            "signal_date": "2023-01-04",
            "submitted_date": "2023-01-04",
            "side": "SELL",
            "origin_subsystem": "",
            "event_id": "",
            "grant_id": "",
            "epoch_id": "",
            "last_update_date": "2023-01-05",
        }
    )
    sell_fill = dict(account["fills"][0])
    sell_fill.update(
        {
            "signal_date": "2023-01-04",
            "fill_date": "2023-01-05",
            "side": "SELL",
            "price": 20.0,
            "gross_value": 200.0,
            "commission": 2.0,
            "stamp_duty": 1.0,
            "order_id": sell_order["order_id"],
            "fill_id": "fill_" + "8" * 64,
            "origin_subsystem": "",
            "event_id": "",
            "grant_id": "",
            "epoch_id": "",
            "sold_tranches": [
                {
                    "tranche_id": f"2023-01-04:{OWNER}:1",
                    "shares": 10,
                    "cost": 10.0,
                    "unit_cost": 10.0,
                    "avg_cost": 10.0,
                    "cost_basis": 100.0,
                    "commission": 2.0,
                    "stamp_duty": 1.0,
                    "transfer_fee": 0.0,
                    "slippage_cost": 0.0,
                }
            ],
        }
    )
    account["cash"] = 1_097.0
    account["positions"] = {}
    account["order_ledger"].append(sell_order)
    account["fills"].append(sell_fill)
    final = AbsoluteGeneralizationReplayObservation(
        session="2023-01-05",
        equity=1_097.0,
        closing_marks=(),
        decision_payload=payload(_decision("2023-01-05", target=False)),
        new_fills=(payload(sell_fill),),
        post_open_account=_snapshot(cash=1_097.0, last_session="2023-01-04"),
        post_decision_account=_snapshot(cash=1_097.0, last_session="2023-01-05"),
        roles=_roles("2023-01-05"),
        intentional_role_absent_symbols=(REMOVED,),
        expected_but_unavailable_symbols=(),
        replay_universe_identity=_replay_universe_identity(),
        data_manifest=_manifest("2023-01-05"),
        loaded_symbols=(OWNER, "sh000300", "sh000682"),
    )
    return replace(
        replay,
        final_equity=1_097.0,
        observations=(*replay.observations, final),
        final_account_payload=payload(account),
    )


def replay_error() -> AbsoluteGeneralizationReplay:
    return AbsoluteGeneralizationReplay(
        scenario=scenario(),
        status="REPLAY_ERROR",
        replay_error="DataContractError: fixture missing",
        initial_cash=1_000.0,
        final_equity=1_000.0,
        observations=(),
        final_account_payload=payload(AccountState.empty(1_000.0).to_dict()),
    )
