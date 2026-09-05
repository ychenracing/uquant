"""Synthetic ledger proofs for current continuity; no market/economic replay."""

from __future__ import annotations

import copy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import scripts.run_strategic_ownership_acceptance as runner
from research.strategic_evidence.replay import ReplayRequest, ReplayResult
from research.strategic_evidence.trace import RouteTraceRow
from uquant.contracts.universe import AIUniverse, default_ai_universe
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.types import (
    AccountOrder,
    AccountState,
    Fill,
    PendingOrder,
    Position,
    Target,
    Tranche,
    derive_attribution_event_id,
)


def continuity_replay(
    owners: tuple[str, ...] = ("sz300308", "sz300394", "sh688008"),
) -> ReplayResult:
    """Native targets/orders/BUYs and terminal SELLs, with complete entry lots."""
    contract = runner.load_contract()
    symbols = tuple(symbol for symbol in contract["canonical_universe"] if symbol != "sz300502")
    universe = default_ai_universe()
    sessions = pd.bdate_range("2026-01-05", periods=4 * len(owners)).strftime("%Y-%m-%d").tolist()
    account = AccountState(initial_cash=10_000.0, cash=10_000.0, code_hash=runner.code_fingerprint())
    rows: dict[str, dict[str, Any]] = {
        session: {"targets": [], "orders": [], "fills": [], "risk": {}}
        for session in ("2023-01-03", *sessions, "2026-08-05")
    }
    epochs = []
    for index, owner in enumerate(owners):
        signal, filled, sell_signal, sold = sessions[index * 4:index * 4 + 4]
        epoch_id, grant_id = f"epoch_{index + 1:064x}", f"grant_{index + 1:064x}"
        qualification = {
            "candidate_symbol": owner, "qualification_ready": True,
            "qualification_signature": f"signature-{index}",
            "qualification_route": "route", "qualification_quorum": "quorum",
            "qualification_evidence_sha256": f"{index + 1:064x}",
        }
        grant = {
            **qualification, "grant_id": grant_id, "created_session": signal,
            "previous_grant_id": f"grant_{index:064x}" if index else "",
            "authorization_id": "",
        }
        rows[signal]["risk"] = {"strategic_qualification": qualification, "strategic_grant": grant}
        identities = {
            "epoch_id": epoch_id, "grant_id": grant_id,
            "origin_subsystem": "STRATEGIC", "mechanism": "STRATEGIC_COHORT",
            "origin_lifecycle": "CORE", "replaces_symbol": None,
            "industry_at_entry": universe.industry_of(owner, signal),
            "industry_manifest_sha256": universe.sha256,
        }
        buy_lot: Tranche | None = None
        for side, admission, execution, weight, price in (
            ("BUY", signal, filled, 0.1, 10.0),
            *(([("SELL", sell_signal, sold, 0.0, 11.0)]) if index < len(owners) - 1 else []),
        ):
            event = derive_attribution_event_id(
                signal_date=admission, symbol=owner, target_weight=weight, lifecycle="CORE",
                reduction_policy="FIFO", reason_code="strategy_target", exit_kind="strategy",
                **{key: value for key, value in identities.items() if key not in {"grant_id", "epoch_id"}},
            )
            attribution = {**identities, "event_id": event}
            order_id = f"O{len(account.order_ledger) + 1:09d}"
            pending = PendingOrder(
                signal_date=admission, symbol=owner, side=side, target_weight=weight,
                reason="synthetic continuity", lifecycle="CORE", order_id=order_id, **attribution,
            )
            order = AccountOrder(
                **{key: value for key, value in asdict(pending).items() if key not in {"attempts", "remaining_shares"}},
                submitted_date=admission, status="FILLED", requested_shares=100, filled_shares=100,
                last_update_date=execution, last_event="FILLED",
            )
            target = Target(
                symbol=owner, weight=weight, lifecycle="CORE", alpha_score=0.0,
                confidence=0.0, reason="synthetic continuity", **attribution,
            )
            fill = Fill(
                signal_date=admission, fill_date=execution, symbol=owner, side=side,
                shares=100, price=price, gross_value=100 * price, commission=0.0,
                stamp_duty=0.0, transfer_fee=0.0, slippage_cost=0.0,
                reason="synthetic continuity", lifecycle="CORE", order_id=order_id, **attribution,
            )
            if side == "BUY":
                buy_lot = Tranche(
                    tranche_id=f"lot-{index}", lifecycle="CORE", shares=100, avg_cost=price,
                    entry_date=execution, sellable_date=sell_signal, highest_close=price,
                    lowest_close=price,
                    **attribution,
                )
                account.cash -= fill.gross_value
                account.positions[owner] = Position(
                    symbol=owner, shares=100, avg_cost=price, entry_date=execution,
                    highest_close=price, tranches=[buy_lot], grant_id=grant_id, epoch_id=epoch_id,
                )
            else:
                assert buy_lot is not None
                fill.sold_tranches = [asdict(buy_lot)]
                account.cash += fill.gross_value
                del account.positions[owner]
            rows[admission]["targets"].append(asdict(target))
            rows[admission]["orders"].append(asdict(pending))
            rows[execution]["fills"].append(asdict(fill))
            account.order_ledger.append(order)
            account.fills.append(fill)
        epochs.append({
            "epoch_id": epoch_id, "grant_id": grant_id, "owner_symbol": owner,
            "opened_session": signal, "first_fill_session": filled, "active_session": filled,
            "closed_session": sold if index < len(owners) - 1 else "",
            "close_reason": "owner_exit" if index < len(owners) - 1 else "",
            "realized_status": "CLOSED" if index < len(owners) - 1 else "ACTIVE",
            "previous_epoch_id": f"epoch_{index:064x}" if index else "",
            "qualification_signature": qualification["qualification_signature"],
            "qualification_route": "route", "qualification_quorum": "quorum",
        })
    raw_account = account.to_dict()
    raw_account["strategic_epochs"] = epochs
    trace = []
    cash = account.initial_cash
    held: dict[str, int] = {}
    marks: dict[str, float] = {}
    for session, values in rows.items():
        for fill in values["fills"]:
            owner = fill["symbol"]
            if fill["side"] == "BUY":
                cash -= fill["gross_value"]
                held[owner] = fill["shares"]
                marks[owner] = fill["price"]
            else:
                cash += fill["gross_value"]
                del held[owner]
                del marks[owner]
        if session == "2026-08-05":
            marks = {owner: 11.0 for owner in held}
        references = tuple(symbol for symbol in symbols if symbol in universe.symbols_as_of(session))
        roles = build_strategic_universe_roles(
            as_of=session, tradable_symbols=symbols, qualification_reference_symbols=references,
            risk_reference_symbols=(*references, "sh000300", "sh000682"),
            industries={symbol: universe.industry_of(symbol, session) for symbol in references},
            available_symbols=(*symbols, "sh000300", "sh000682"),
        )
        trace.append(RouteTraceRow(
            date=session, reference_context={"reference_coverage": 1.0}, leaders=(),
            risk={"state": "NORMAL", "strategic_universe_identities": {
                "tradable": roles.tradable_identity,
                "qualification_reference": roles.qualification_reference_identity,
                "risk_reference": roles.risk_reference_identity,
            }, **values["risk"]}, opportunity="TREND",
            targets=tuple(values["targets"]), orders=tuple(values["orders"]), fills=tuple(values["fills"]),
            account_sha256=runner._canonical_sha256({"cash": cash, "shares": held}),
            equity=cash + sum(shares * marks[owner] for owner, shares in held.items()),
            cash=cash, position_shares=dict(held), close_marks=dict(marks),
            target_gross=sum(target["weight"] for target in values["targets"]),
        ))
    return ReplayResult(
        request=ReplayRequest(
            symbols=symbols, start="2023-01-03", end="2026-08-05",
            scenario="strategic-ownership:remove-sz300502",
            qualification_reference_symbols=symbols, risk_reference_symbols=symbols,
        ),
        metrics={"final_equity": account.cash + 1_100.0, "max_drawdown": 0.0},
        trace=tuple(trace), final_account=raw_account, intervention_provenance=None,
    )


def test_same_industry_witness_survives_later_cross_industry_epoch() -> None:
    contract = runner.load_contract()
    result = continuity_replay()
    summary = runner._continuity_summary(contract, result)
    witness = runner._validate_repeated(contract, summary=summary, same_industry=True)

    assert witness is not None
    assert [entry["owner_symbol"] for entry in witness["admissions"]] == ["sz300308", "sz300394"]
    assert witness["industry_at_entry"] == "optical"
    assert witness["source_scenario_id"] == "remove-sz300502"
    assert witness["raw_sha256"] == summary["continuity"]["raw_sha256"]
    assert len(summary["epochs"]) == 3
    assert summary["raw_replay"] == asdict(result)


@pytest.mark.parametrize("owners", [
    ("sz300308", "sh688008"),
    ("sz300308", "sh688008", "sz300394"),
])
def test_cross_industry_source_passes_but_alias_requires_adjacent_same_industry(
    owners: tuple[str, ...],
) -> None:
    contract = runner.load_contract()
    summary = runner._continuity_summary(contract, continuity_replay(owners))

    assert runner._validate_repeated(contract, summary=summary, same_industry=False) is None
    with pytest.raises(RuntimeError, match=r"no adjacent.*same-industry"):
        runner._validate_repeated(contract, summary=summary, same_industry=True)


@pytest.mark.parametrize("mutation, reason", [
    ("target", "lacks a target or order"),
    ("order", "lacks a target or order"),
    ("fill", "no matching real fill"),
    ("zero_fill", "simulated fill identity shares"),
    ("same_day", "causality differs"),
    ("predecessor", "epoch identity chain differs"),
    ("duplicate_epoch", "duplicate strategic epoch"),
    ("grant", "no matching real fill"),
    ("target_industry", "admission target attribution differs"),
    ("fill_industry", "trace and ledger fills differ"),
    ("lot_industry", "does not chain to an originating BUY"),
    ("manifest", "invalid industry manifest"),
    ("daily_roles", "daily role identity differs"),
    ("reference_removal", "request or removal roles differ"),
    ("removed_target", "reference-only symbol received capital authority"),
    ("pending_identity", "pending order attribution differs"),
    ("qualification", "lacks a production qualification"),
    ("intervention", "research intervention"),
    ("raw_source", "production source differs"),
    ("fill_trace_session", "physical fill trace session differs"),
    ("missing_live_lot", "tranche"),
    ("missing_close_reason", "terminal epoch lacks close evidence"),
])
def test_complete_raw_chain_fails_closed(mutation: str, reason: str) -> None:
    contract = runner.load_contract()
    raw = asdict(continuity_replay())
    account = raw["final_account"]
    first = raw["trace"][1]
    match mutation:
        case "target":
            first["targets"] = ()
        case "order":
            first["orders"] = ()
        case "fill":
            del account["fills"][0]
        case "zero_fill":
            account["fills"][0]["shares"] = 0
        case "same_day":
            account["fills"][0]["fill_date"] = first["date"]
            account["strategic_epochs"][0]["first_fill_session"] = first["date"]
            account["strategic_epochs"][0]["active_session"] = first["date"]
        case "predecessor":
            account["strategic_epochs"][1]["previous_epoch_id"] = "epoch_" + "f" * 64
        case "duplicate_epoch":
            account["strategic_epochs"].append(copy.deepcopy(account["strategic_epochs"][0]))
        case "grant":
            account["fills"][0]["grant_id"] = "grant_" + "f" * 64
        case "target_industry":
            first["targets"][0]["industry_at_entry"] = "compute"
        case "fill_industry":
            account["fills"][0]["industry_at_entry"] = "compute"
        case "lot_industry":
            account["fills"][1]["sold_tranches"][0]["industry_at_entry"] = "compute"
            raw["trace"][4]["fills"][0]["sold_tranches"][0]["industry_at_entry"] = "compute"
        case "manifest":
            account["order_ledger"][0]["industry_manifest_sha256"] = "f" * 64
        case "daily_roles":
            first["risk"]["strategic_universe_identities"]["risk_reference"] = "f" * 64
        case "reference_removal":
            raw["request"]["qualification_reference_symbols"] = tuple(contract["canonical_universe"])
        case "removed_target":
            first["targets"][0]["symbol"] = "sz300502"
        case "pending_identity":
            pending = copy.deepcopy(first["orders"][0])
            pending["industry_at_entry"] = "compute"
            account["pending_orders"] = [pending]
        case "qualification":
            first["risk"]["strategic_qualification"]["qualification_ready"] = False
        case "intervention":
            raw["intervention_provenance"] = {"synthetic": True}
        case "raw_source":
            account["code_hash"] = "f" * 64
        case "fill_trace_session":
            raw["trace"][3]["fills"] = raw["trace"][2]["fills"]
            raw["trace"][2]["fills"] = ()
        case "missing_live_lot":
            account["positions"]["sh688008"]["tranches"] = []
        case "missing_close_reason":
            del account["strategic_epochs"][0]["close_reason"]
        case _:
            raise AssertionError(mutation)

    with pytest.raises((RuntimeError, ValueError), match=reason):
        summary = runner._continuity_summary(contract, runner._continuity_result(raw))
        runner._validate_repeated(contract, summary=summary, same_industry=True)


def test_industry_is_bound_to_original_admission_before_registry_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = continuity_replay()
    original = AIUniverse.industry_of

    def revised_industry(self: AIUniverse, symbol: str, as_of: Any) -> str:
        if symbol == "sz300308" and str(as_of) >= "2026-01-06":
            return "compute"
        return original(self, symbol, as_of)

    monkeypatch.setattr(AIUniverse, "industry_of", revised_industry)
    summary = runner._continuity_summary(runner.load_contract(), result)
    witness = runner._validate_repeated(runner.load_contract(), summary=summary, same_industry=True)

    assert witness is not None
    assert witness["admissions"][0]["admission_session"] == "2026-01-05"
    assert witness["admissions"][0]["fill_session"] == "2026-01-06"
    assert witness["industry_at_entry"] == "optical"


def test_even_consistent_relabeling_to_fill_date_industry_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = continuity_replay()
    original = AIUniverse.industry_of

    def revised_industry(self: AIUniverse, symbol: str, as_of: Any) -> str:
        if symbol == "sz300308" and str(as_of) == "2026-01-05":
            return "compute"
        return original(self, symbol, as_of)

    monkeypatch.setattr(AIUniverse, "industry_of", revised_industry)
    with pytest.raises(RuntimeError, match="industry_at_entry differs from point-in-time membership"):
        runner._continuity_summary(runner.load_contract(), result)


@pytest.mark.parametrize("drawdown", [0.30000000000000004, 0.31])
def test_current_continuity_keeps_literal_drawdown_failures(drawdown: float) -> None:
    result = continuity_replay()
    result = replace(result, metrics={**result.metrics, "max_drawdown": drawdown})

    with pytest.raises(RuntimeError, match="maximum drawdown exceeded the contract"):
        runner._continuity_summary(runner.load_contract(), result)


@pytest.mark.parametrize("owners, reason", [
    (("sz300308",), "fewer than two actual epochs"),
    (("sz300308", "sz300308"), "fewer than two owners"),
])
def test_source_still_requires_two_real_epochs_and_distinct_owners(
    owners: tuple[str, ...], reason: str,
) -> None:
    contract = runner.load_contract()
    summary = runner._continuity_summary(contract, continuity_replay(owners))

    with pytest.raises(RuntimeError, match=reason):
        runner._validate_repeated(contract, summary=summary, same_industry=False)


@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("tamper", ["missing_raw", "summary", "witness", "basis", "raw_fill"])
def test_resealed_cache_cannot_assert_current_continuity_pass(
    tmp_path: Path, tamper: str, alias: bool,
) -> None:
    summary = runner._continuity_summary(runner.load_contract(), continuity_replay())
    if alias:
        summary["scenario_id"] = "same-industry-crowning"
        summary["source_scenario_id"] = "remove-sz300502"
        summary["same_industry_witness"] = runner._validate_repeated(
            runner.load_contract(), summary=summary, same_industry=True,
        )
    path = tmp_path / "cache.json"
    runner._write_cache(path, identity="current", payload=summary)
    assert runner._read_cache(path, identity="current") is not None
    match tamper:
        case "missing_raw":
            del summary["raw_replay"]
        case "summary":
            summary["epochs"][1]["owner_symbol"] = "sz300308"
        case "witness":
            summary["same_industry_witness"] = {"status": "PASS"}
        case "basis":
            summary["continuity"]["basis"]["alias_mode"] = "all_pass"
        case "raw_fill":
            summary["raw_replay"]["final_account"]["fills"][0]["shares"] = 0
            summary["continuity"]["raw_sha256"] = runner._canonical_sha256(summary["raw_replay"])
    runner._write_cache(path, identity="current", payload=summary)

    assert runner._read_cache(path, identity="current") is None


def test_alias_cache_readback_rederives_witness_from_one_complete_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def frozen_replay(*args: Any, **kwargs: Any) -> ReplayResult:
        calls.append(kwargs["scenario_id"])
        return continuity_replay()

    monkeypatch.setattr(runner, "_frozen_replay", frozen_replay)
    monkeypatch.setattr(runner, "_cache_identity_context", lambda contract: {"test": "synthetic raw"})
    options = {
        "shard": "continuity", "scenario": "same-industry-crowning",
        "output": tmp_path / "alias.json", "cache_dir": tmp_path / "cache",
    }
    first = runner.run_acceptance_shard(**options)
    second = runner.run_acceptance_shard(**options)

    assert calls == ["remove-sz300502"]
    assert not first["cache_hit"] and second["cache_hit"]
    assert first["scenarios"][0]["same_industry_witness"] == second["scenarios"][0]["same_industry_witness"]
    assert set(second["cache_dependencies"]) == {"remove-sz300502"}
    assert len(second["scenarios"][0]["epochs"]) == 3
    assert second["cache_identity_payload"]["continuity_basis"]["contract_id"] == "cross-ai-core-strategy-20260905-v1"
