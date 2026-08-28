from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from research.strategic_evidence.replay import ReplayRequest, ReplayResult
from research.strategic_evidence.trace import RouteTraceRow
from scripts.run_strategic_ownership_acceptance import (
    SHARD_NAMES,
    actual_epoch_facts,
    load_contract,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]


def _row(
    session: str,
    *,
    grant_id: str = "",
    epoch_id: str = "",
    owner: str = "",
    fill: bool = False,
) -> RouteTraceRow:
    target = (
        {
            "authorization_id": "",
            "epoch_id": epoch_id,
            "grant_id": grant_id,
            "lifecycle": "CORE",
            "mechanism": "STRATEGIC_COHORT",
            "origin_subsystem": "STRATEGIC",
            "symbol": owner,
            "weight": 0.20,
        },
    ) if grant_id else ()
    order = (
        {
            "epoch_id": epoch_id,
            "grant_id": grant_id,
            "order_id": "O000000001",
            "side": "BUY",
            "symbol": owner,
            "target_weight": 0.20,
        },
    ) if grant_id else ()
    fills = (
        {
            "epoch_id": epoch_id,
            "fill_date": session,
            "grant_id": grant_id,
            "order_id": "O000000001",
            "shares": 100,
            "side": "BUY",
            "symbol": owner,
        },
    ) if fill else ()
    strategic_risk = (
        {
            "strategic_grant": {
                "authorization_id": "",
                "grant_id": grant_id,
                "previous_grant_id": "",
            },
            "strategic_qualification": {
                "candidate_symbol": owner,
                "qualification_ready": True,
                "qualification_signature": "",
            },
        }
        if grant_id
        else {}
    )
    return RouteTraceRow(
        date=session,
        reference_context={},
        leaders=(),
        risk={"state": "NORMAL", **strategic_risk},
        opportunity="TREND",
        targets=target,
        orders=order,
        fills=fills,
        account_sha256="a" * 64,
        equity=2_000_000.0,
        target_gross=0.20 if grant_id else 0.0,
        intervention_provenance=None,
        cash=2_000_000.0,
        position_shares={},
        close_marks={},
    )


def _result(*, filled: bool = True) -> ReplayResult:
    grant_id = "grant_" + "a" * 64
    epoch_id = "epoch_" + "b" * 64
    owner = "sz300308"
    epoch = {
        "active_session": "2026-01-06" if filled else "",
        "closed_session": "",
        "epoch_id": epoch_id,
        "first_fill_session": "2026-01-06" if filled else "",
        "grant_id": grant_id,
        "opened_session": "2026-01-05",
        "owner_symbol": owner,
        "previous_epoch_id": "",
        "realized_status": "ACTIVE" if filled else "PROBE",
    }
    return ReplayResult(
        request=ReplayRequest(
            symbols=(owner,),
            start="2026-01-05",
            end="2026-01-06",
        ),
        metrics={"final_equity": 2_000_000.0, "max_drawdown": 0.0},
        trace=(
            _row("2026-01-05", grant_id=grant_id, epoch_id=epoch_id, owner=owner),
            _row(
                "2026-01-06",
                grant_id=grant_id,
                epoch_id=epoch_id,
                owner=owner,
                fill=filled,
            ),
        ),
        final_account={
            "fills": list(_row(
                "2026-01-06",
                grant_id=grant_id,
                epoch_id=epoch_id,
                owner=owner,
                fill=filled,
            ).fills),
            "initial_cash": 2_000_000.0,
            "order_ledger": [{"order_id": "O000000001"}],
            "strategic_epochs": [epoch],
        },
        intervention_provenance=None,
    )


def test_ownership_contract_is_the_exact_bounded_shard_set() -> None:
    contract = load_contract()

    validate_contract(contract)

    assert SHARD_NAMES == (
        "champion",
        "critical",
        "ghost-a",
        "ghost-b",
        "continuity",
    )
    assert set(contract["shards"]) == set(SHARD_NAMES)
    scenario_ids = {
        item["scenario_id"]
        for items in contract["shards"].values()
        for item in items
    }
    assert scenario_ids == {
        "champion-5",
        "report-13",
        "remove-sz300308",
        "remove-sz300502",
        "remove-sz300394",
        "remove-sh603688",
        "remove-sh688008",
        "remove-sh688082",
        "remove-sz002409",
        "remove-sz300666",
        "same-industry-crowning",
        "cross-industry-crowning",
        "failed-first-grant",
    }
    encoded = json.dumps(contract, sort_keys=True).lower()
    assert "234" not in encoded
    assert "extended performance" not in encoded
    assert "extended economic" not in encoded
    assert "generalization acceptance" not in encoded


def test_ownership_workflow_is_bounded_cached_and_blocking() -> None:
    path = ROOT / ".github" / "workflows" / "strategic-ownership-acceptance.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert workflow["name"] == "Strategic Ownership Acceptance"
    assert workflow["on"] == {
        "pull_request": "",
        "push": {"branches": ["main"]},
        "workflow_dispatch": {},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["UV_VERSION"] == "0.11.33"
    shard = workflow["jobs"]["ownership-shard"]
    assert shard["strategy"]["fail-fast"] == "false"
    assert tuple(shard["strategy"]["matrix"]["shard"]) == SHARD_NAMES
    rendered = str(workflow).lower()
    assert "actions/cache@" in rendered
    assert "scripts/run_strategic_ownership_acceptance.py" in rendered
    assert "strategic ownership acceptance" in rendered
    assert "234" not in rendered
    assert "extended performance" not in rendered
    assert "extended economic" not in rendered
    assert "generalization-matrix" not in rendered
    aggregate = workflow["jobs"]["strategic-ownership-acceptance"]
    assert aggregate["name"] == "Strategic Ownership Acceptance"
    assert aggregate["if"] == "${{ always() }}"
    assert set(aggregate["needs"]) == {"ownership-tests", "ownership-shard"}


def test_actual_epoch_facts_require_target_order_and_matching_real_fill() -> None:
    facts = actual_epoch_facts(_result())

    assert len(facts) == 1
    assert facts[0]["owner_symbol"] == "sz300308"
    assert facts[0]["target_session"] == "2026-01-05"
    assert facts[0]["order_session"] == "2026-01-05"
    assert facts[0]["fill_session"] == "2026-01-06"
    assert facts[0]["active_session"] == "2026-01-06"
    assert facts[0]["previous_grant_id"] == ""


def test_probe_ledger_without_fill_is_not_an_actual_epoch() -> None:
    assert actual_epoch_facts(_result(filled=False)) == []


def test_actual_epoch_facts_reject_duplicate_epoch_identity() -> None:
    result = _result()
    duplicate = dict(result.final_account["strategic_epochs"][0])  # type: ignore[index]
    broken = replace(
        result,
        final_account={**result.final_account, "strategic_epochs": [duplicate, duplicate]},
    )

    with pytest.raises(ValueError, match="duplicate strategic epoch"):
        actual_epoch_facts(broken)
