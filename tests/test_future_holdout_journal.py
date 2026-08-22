from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from uquant.validation.execution_journal import (
    append_filled,
    append_planned,
    append_skipped,
    read_execution_journal,
)

_V1_PLANNED_BYTES = (
    b'{"actual_price":null,"actual_shares":null,"actual_time":null,'
    b'"manual_skip":null,"next_open":null,"plan_id":"frozen-plan-1",'
    b'"planned_price":947.74,"planned_shares":100,'
    b'"previous_sha256":"0000000000000000000000000000000000000000000000000000000000000000",'
    b'"record_sha256":"625f4800c03588a453b1c137a49bf6f8ecc1f9480eb1e094049e1135ae8a5b40",'
    b'"recorded_at":"2026-08-05T15:01:00+08:00","schema_version":1,'
    b'"sequence":1,"side":"BUY","slippage_bps":null,'
    b'"slippage_per_share":null,"slippage_value":null,"status":"PLANNED",'
    b'"symbol":"sz300308"}\n'
)

_CLI_SPEC = importlib.util.spec_from_file_location(
    "future_holdout_cli",
    Path(__file__).parents[1] / "scripts/future_holdout.py",
)
assert _CLI_SPEC is not None and _CLI_SPEC.loader is not None
_CLI_MODULE = importlib.util.module_from_spec(_CLI_SPEC)
_CLI_SPEC.loader.exec_module(_CLI_MODULE)
future_holdout_main = _CLI_MODULE.main
render_execution_journal = _CLI_MODULE.render_execution_journal
summarize_execution_journal = _CLI_MODULE.summarize_execution_journal


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
    legacy.write_bytes(_V1_PLANNED_BYTES)
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


def test_execution_summary_classifies_each_plan_and_weights_real_slippage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "execution.jsonl"
    plans = (
        ("skipped-after-fill", "BUY", 10.0, 100),
        ("filled", "SELL", 20.0, 200),
        ("open", "BUY", 40.0, 100),
        ("partial", "BUY", 30.0, 100),
    )
    for offset, (plan_id, side, price, shares) in enumerate(plans, start=1):
        append_planned(
            path,
            plan_id=plan_id,
            decision_date="2026-08-05",
            recorded_at=f"2026-08-05T15:0{offset}:00+08:00",
            symbol="sz300308",
            side=side,
            planned_weight=0.10,
            planned_price=price,
            planned_shares=shares,
        )
    append_filled(
        path,
        plan_id="skipped-after-fill",
        recorded_at="2026-08-06T09:31:00+08:00",
        next_open=10.0,
        actual_time="2026-08-06T09:30:30+08:00",
        actual_price=11.0,
        actual_shares=40,
        broker_order_id="broker-partial-1",
    )
    append_skipped(
        path,
        plan_id="skipped-after-fill",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=10.0,
        manual_skip="operator declined remainder",
    )
    append_filled(
        path,
        plan_id="filled",
        recorded_at="2026-08-06T09:33:00+08:00",
        next_open=20.0,
        actual_time="2026-08-06T09:32:30+08:00",
        actual_price=19.0,
        actual_shares=200,
        broker_order_id="broker-filled-1",
    )
    append_filled(
        path,
        plan_id="partial",
        recorded_at="2026-08-06T09:34:00+08:00",
        next_open=30.0,
        actual_time="2026-08-06T09:33:30+08:00",
        actual_price=30.0,
        actual_shares=50,
        broker_order_id="broker-partial-2",
    )

    records = read_execution_journal(path)
    summary = summarize_execution_journal(records)

    assert summary == {
        "schema_version": 1,
        "plan_count": 4,
        "filled_plans": 1,
        "partial_plans": 1,
        "open_plans": 1,
        "skipped_plans": 1,
        "planned_shares": 500,
        "filled_shares": 290,
        "fill_ratio": 0.58,
        "reference_notional": 5900.0,
        "realized_slippage": 240.0,
        "weighted_slippage_bps": pytest.approx(406.77966101694915),
    }
    report = render_execution_journal(records)
    assert "## Execution Summary" in report
    assert "| Plans | 4 |" in report
    assert "| Filled / Partial / Open / Skipped | 1 / 1 / 1 / 1 |" in report
    assert "| Fill ratio | 58.00% |" in report
    assert "| Weighted slippage | 406.7797 bps |" in report


def test_journal_cli_emits_and_verifies_an_external_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    journal = tmp_path / "future_holdout_execution_journal.jsonl"
    checkpoint = tmp_path / "future_holdout_execution_journal.checkpoint.json"
    _append_complete_plan(journal)

    assert future_holdout_main(["journal", "checkpoint"]) == 0
    checkpoint_payload = json.loads(capsys.readouterr().out)
    assert checkpoint_payload == {
        "schema_version": 1,
        "sequence": 1,
        "record_sha256": read_execution_journal(journal)[0].record_sha256,
    }
    assert json.loads(checkpoint.read_text(encoding="utf-8")) == checkpoint_payload

    assert future_holdout_main(["journal", "verify"]) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification == {
        "checkpoint": checkpoint_payload,
        "records": 1,
        "status": "VALID",
    }

    journal.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="behind the trusted checkpoint"):
        future_holdout_main(
            ["journal", "verify", "--checkpoint", str(checkpoint)]
        )


def test_journal_cli_rejects_a_nonempty_journal_without_a_trusted_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    journal = tmp_path / "future_holdout_execution_journal.jsonl"
    _append_complete_plan(journal)

    with pytest.raises(ValueError, match=r"nonempty.*trusted checkpoint"):
        future_holdout_main(["journal", "verify"])
