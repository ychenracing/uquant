from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from uquant.execution_journal import append_planned as append_legacy_planned
from uquant.validation.execution_journal import (
    append_filled,
    append_planned,
    read_execution_journal,
)

_CLI_SPEC = importlib.util.spec_from_file_location(
    "future_holdout_cli",
    Path(__file__).parents[1] / "scripts/future_holdout.py",
)
assert _CLI_SPEC is not None and _CLI_SPEC.loader is not None
_CLI_MODULE = importlib.util.module_from_spec(_CLI_SPEC)
_CLI_SPEC.loader.exec_module(_CLI_MODULE)
future_holdout_main = _CLI_MODULE.main


def _append_complete_plan(path: Path) -> None:
    append_planned(
        path,
        plan_id="phase2-plan-1",
        decision_date="2026-08-05",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_weight=0.08,
        planned_price=947.74,
        planned_shares=100,
    )


def test_phase2_journal_rows_bind_complete_observational_evidence(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    _append_complete_plan(path)
    append_filled(
        path,
        plan_id="phase2-plan-1",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=950.0,
        actual_time="2026-08-06T09:31:05+08:00",
        actual_price=951.0,
        actual_shares=100,
        broker_order_id="manual-broker-001",
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    required = {
        "decision_date",
        "planned_symbol",
        "planned_side",
        "planned_weight",
        "planned_price_reference",
        "next_open",
        "actual_fill_price",
        "actual_fill_shares",
        "manual_skip",
        "manual_skip_reason",
        "realized_slippage",
        "broker_order_id",
        "recorded_at",
        "record_hash",
        "previous_record_hash",
    }
    assert all(required <= set(row) for row in rows)
    assert rows[0]["schema_version"] == 2
    assert rows[0]["planned_weight"] == 0.08
    assert rows[1]["decision_date"] == "2026-08-05"
    assert rows[1]["broker_order_id"] == "manual-broker-001"
    assert rows[1]["realized_slippage"] == pytest.approx(100.0)
    assert rows[1]["previous_record_hash"] == rows[0]["record_hash"]
    assert read_execution_journal(path)[-1].record_sha256 == rows[1]["record_hash"]


def test_phase2_journal_detects_history_edits_and_reads_legacy_rows(tmp_path: Path) -> None:
    phase2 = tmp_path / "phase2.jsonl"
    _append_complete_plan(phase2)
    row = json.loads(phase2.read_text(encoding="utf-8"))
    row["planned_weight"] = 0.09
    phase2.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        read_execution_journal(phase2)

    legacy = tmp_path / "legacy.jsonl"
    append_legacy_planned(
        legacy,
        plan_id="legacy-plan",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    assert read_execution_journal(legacy)[0].schema_version == 1


def test_phase2_journal_module_has_no_production_state_dependencies() -> None:
    source = Path("uquant/validation/execution_journal.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        name.endswith(("account", "config", "engine", "execution", "portfolio", "types"))
        for name in imported
    )


def test_phase2_journal_cli_requires_and_emits_complete_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert future_holdout_main(
        [
            "journal",
            "planned",
            "--plan-id",
            "cli-plan",
            "--decision-date",
            "2026-08-05",
            "--recorded-at",
            "2026-08-05T15:01:00+08:00",
            "--symbol",
            "sz300308",
            "--side",
            "BUY",
            "--planned-weight",
            "0.08",
            "--planned-price",
            "947.74",
            "--planned-shares",
            "100",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert payload["decision_date"] == "2026-08-05"
    assert payload["record_hash"]
    assert future_holdout_main(
        [
            "journal",
            "filled",
            "--plan-id",
            "cli-plan",
            "--recorded-at",
            "2026-08-06T09:32:00+08:00",
            "--next-open",
            "950.0",
            "--actual-time",
            "2026-08-06T09:31:05+08:00",
            "--actual-price",
            "951.0",
            "--actual-shares",
            "100",
            "--broker-order-id",
            "manual-broker-001",
        ]
    ) == 0
    filled = json.loads(capsys.readouterr().out)
    assert filled["broker_order_id"] == "manual-broker-001"
    assert future_holdout_main(["journal", "report"]) == 0
    report = capsys.readouterr().out
    assert "Future Holdout Manual Execution Journal" in report
    assert "manual-broker-001" in report


def test_phase2_journal_cli_records_manual_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "future_holdout_execution_journal.jsonl"
    _append_complete_plan(path)

    assert future_holdout_main(
        [
            "journal",
            "skipped",
            "--plan-id",
            "phase2-plan-1",
            "--recorded-at",
            "2026-08-06T09:32:00+08:00",
            "--next-open",
            "950.0",
            "--manual-skip",
            "operator declined remainder",
        ]
    ) == 0
    skipped = json.loads(capsys.readouterr().out)
    assert skipped["manual_skip"] is True
    assert skipped["manual_skip_reason"] == "operator declined remainder"
