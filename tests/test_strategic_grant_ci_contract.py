from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest
import yaml

import scripts.run_strategic_grant_acceptance as grant_runner
from scripts.run_strategic_grant_acceptance import (
    GRANT_CASE_IDS,
    _baseline_views,
    _canonical_sha256,
    run_diagnostic_case,
)
from uquant.contracts.strict_json import strict_json_loads
from uquant.engine import performance_metrics
from uquant.types import AccountOrder

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict[str, Any]:
    path = ROOT / ".github" / "workflows" / name
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_extended_economic_workflows_are_manual_without_weakening_commands() -> None:
    performance = _workflow("strategy-performance.yml")
    generalization = _workflow("strategy-generalization.yml")

    assert performance["name"] == "Extended Performance Matrix"
    assert generalization["name"] == "Extended Economic Matrix"
    assert performance["on"] == {"workflow_dispatch": {}}
    assert generalization["on"] == {"workflow_dispatch": {}}
    performance_text = str(performance)
    generalization_text = str(generalization)
    assert "--profile full" in performance_text
    assert "generalization-matrix" in generalization_text


def test_strategic_grant_acceptance_is_bounded_and_automatic() -> None:
    workflow = _workflow("strategic-grant-acceptance.yml")

    assert workflow["name"] == "Strategic Grant Acceptance"
    assert workflow["on"] == {
        "pull_request": "",
        "push": {"branches": ["main"]},
        "workflow_dispatch": {},
    }
    rendered = str(workflow)
    assert "scripts/run_strategic_grant_acceptance.py" in rendered
    assert "--case" not in rendered
    assert "tests/test_strategic_grant_recovery.py" in rendered
    assert "tests/test_account_schema_v3_integrity.py" in rendered
    assert "tests/test_broker_sync.py" in rendered
    assert "promotion --data-dir" not in rendered
    assert "generalization-matrix" not in rendered
    assert "234" not in rendered


def test_baseline_views_compare_economic_orders_across_physical_retry_ids() -> None:
    def result(order_ids: tuple[str, str]) -> dict[str, object]:
        return {
            "decision_trace": [],
            "order_ledger": [
                {"event_id": "event-a", "order_id": order_ids[0], "shares": 100},
                {"event_id": "event-b", "order_id": order_ids[1], "shares": 200},
            ],
            "final_account": {
                "fills": [
                    {"event_id": "event-a", "order_id": order_ids[0], "shares": 100},
                    {"event_id": "event-b", "order_id": order_ids[1], "shares": 200},
                ]
            },
            "daily_replay_evidence": [],
            "equity_curve": [],
        }

    main = _baseline_views(result(("O000000001", "O000000002")), frozenset())
    candidate = _baseline_views(result(("O000000004", "O000000005")), frozenset())

    assert candidate == main


def test_champion_raw_fixture_freezes_terminal_strategic_remainder() -> None:
    contract = json.loads(
        (ROOT / "benchmarks/strategic_grant_acceptance_contract.json").read_text(
            encoding="utf-8"
        )
    )
    encoded = gzip.decompress(
        (ROOT / "tests/fixtures/absolute_champion_runtime_raw.json.gz").read_bytes()
    )
    raw = strict_json_loads(encoded)
    assert isinstance(raw, dict)
    ignored = frozenset(str(item) for item in contract["ignored_non_economic_fields"])

    views = _baseline_views(raw, ignored)
    orders = cast(list[dict[str, object]], views["orders"])
    assert {
        "requested_shares": orders[0]["requested_shares"],
        "filled_shares": orders[0]["filled_shares"],
        "remaining_shares": orders[0]["remaining_shares"],
        "status": orders[0]["status"],
        "cancel_reason": orders[0]["cancel_reason"],
    } == {
        "requested_shares": 104_500,
        "filled_shares": 104_300,
        "remaining_shares": 200,
        "status": "CANCELLED",
        "cancel_reason": "target already satisfied",
    }

    actual_sha256 = {
        name: _canonical_sha256(value) for name, value in views.items()
    }
    assert actual_sha256 == {
        "targets": "7f33eca7246df9af6895865b526e7e754f9a3a78ffc5dd9b7a293d78cd8c0f95",
        "orders": "24befbce7f2a2eb46b82d2dcd9ef1351d628616ba848a167deff4dc36c857a00",
        "fills": "e4927cfbce9202e488dfc3c0cbadf412c527a68314b499eab4e9d916d5037fd1",
        "positions": "8819f3e2c32e9076bf6007040510c93ae02cbef8d6c41159bf12ffccec9782d0",
        "equity": "654142a4a217d243c53104ac6636a1778314c2e04497cfd0456a6385ea3aab39",
    }
    assert actual_sha256 == contract["baseline"]["expected_sha256"]


def test_performance_metrics_preserves_terminal_strategic_remainder() -> None:
    chain = (
        ("O000000001", 104_500, 33_600, 70_900, "strategic partial remainder replaced"),
        ("O000000002", 70_900, 34_300, 36_600, "strategic partial remainder replaced"),
        ("O000000003", 36_600, 34_500, 2_100, "strategic partial remainder replaced"),
        ("O000000004", 2_100, 1_900, 200, "target already satisfied"),
    )
    orders = [
        AccountOrder(
            order_id=order_id,
            signal_date="2023-01-04",
            submitted_date="2023-01-04",
            symbol="sz300308",
            side="BUY",
            target_weight=0.95,
            reason="strategic cohort",
            lifecycle="CORE",
            status="CANCELLED",
            requested_shares=requested,
            filled_shares=filled,
            remaining_shares=remaining,
            attempts=index,
            cancel_reason=cancel_reason,
            event_id="evt_same_strategic_target",
            grant_id="grant_same_strategic_target",
        )
        for index, (order_id, requested, filled, remaining, cancel_reason) in enumerate(
            chain, start=1
        )
    ]

    observed = performance_metrics(
        equity_rows=[
            (pd.Timestamp("2023-01-04"), 2_000_000.0),
            (pd.Timestamp("2023-01-05"), 2_000_000.0),
        ],
        fills=[],
        orders=orders,
        initial_cash=2_000_000.0,
        risk_events=[],
        benchmark_total_return=0.0,
    )

    assert observed["account_orders"] == 1
    ledger = cast(list[dict[str, object]], observed["order_ledger"])
    assert len(ledger) == 1
    assert {
        "requested_shares": ledger[0]["requested_shares"],
        "filled_shares": ledger[0]["filled_shares"],
        "remaining_shares": ledger[0]["remaining_shares"],
        "status": ledger[0]["status"],
        "cancel_reason": ledger[0]["cancel_reason"],
    } == {
        "requested_shares": 104_500,
        "filled_shares": 104_300,
        "remaining_shares": 200,
        "status": "CANCELLED",
        "cancel_reason": "target already satisfied",
    }


def test_single_grant_case_is_diagnostic_and_reuses_only_complete_identity_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert GRANT_CASE_IDS == (
        "baseline",
        "native-sz300308",
        "native-sz300502",
        "native-sz300394",
    )
    calls: list[str] = []

    def execute(
        _contract: object,
        *,
        case_id: str,
    ) -> dict[str, object]:
        calls.append(case_id)
        return {"case_id": case_id, "status": "PASS"}

    monkeypatch.setattr(grant_runner, "_execute_case", execute)
    output = tmp_path / "case.json"
    cache = tmp_path / "cache"
    result = run_diagnostic_case(
        case_id="native-sz300502",
        output=output,
        cache_dir=cache,
    )

    assert calls == ["native-sz300502"]
    assert result["authoritative_acceptance"] is False
    assert result["diagnostic_only"] is True
    assert result["selected_case"] == "native-sz300502"
    assert result["cache_hit"] is False
    assert result["case"] == {"case_id": "native-sz300502", "status": "PASS"}
    identity = result["cache_identity_payload"]
    assert isinstance(identity, dict)
    assert set(identity) == {
        "case",
        "closure_contract_sha256",
        "config_sha256",
        "frozen_data",
        "full_package_source_sha256",
        "grant_contract_sha256",
        "production_source_sha256",
        "runner_source_sha256",
        "runtime",
        "schema_version",
        "source_surface_registry_sha256",
        "validation_runner_source_sha256",
    }
    assert identity["case"] == {
        "case_id": "native-sz300502",
        "date": "2024-03-04",
        "kind": "native_eligibility",
        "owner": "sz300502",
    }

    monkeypatch.setattr(
        grant_runner,
        "_execute_case",
        lambda *_args, **_kwargs: pytest.fail("complete-identity cache was not reused"),
    )
    cached = run_diagnostic_case(
        case_id="native-sz300502",
        output=output,
        cache_dir=cache,
    )
    assert cached["cache_hit"] is True


def test_grant_cli_dispatches_one_diagnostic_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        grant_runner,
        "run_diagnostic_case",
        lambda **options: observed.update(options),
    )
    output = tmp_path / "grant.json"
    cache = tmp_path / "cache"

    assert grant_runner.main(
        [
            "--case",
            "native-sz300502",
            "--output",
            str(output),
            "--cache-dir",
            str(cache),
        ]
    ) == 0
    assert observed == {
        "cache_dir": cache,
        "case_id": "native-sz300502",
        "output": output,
    }
