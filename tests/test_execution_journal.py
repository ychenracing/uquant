from __future__ import annotations

import ast
import hashlib
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from uquant import execution_journal as journal_module
from uquant.atomic_io import atomic_write_text
from uquant.cli import main
from uquant.execution_journal import (
    JournalStatus,
    append_filled,
    append_planned,
    append_skipped,
    read_execution_journal,
)
from uquant.report import render_execution_journal


def _concurrent_plan_worker(path: str, index: int, barrier: Any) -> None:
    barrier.wait()
    append_planned(
        path,
        plan_id=f"concurrent-{index}",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )


def _reseal(payload: dict[str, object]) -> None:
    unsigned = {key: value for key, value in payload.items() if key != "record_sha256"}
    payload["record_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


def test_journal_appends_planned_and_filled_with_directional_slippage(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    planned = append_planned(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-05T08:00:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    filled = append_filled(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=950.0,
        actual_time="2026-08-06T09:31:05+08:00",
        actual_price=951.0,
        actual_shares=100,
    )

    records = read_execution_journal(path)
    assert [item.status for item in records] == [JournalStatus.PLANNED, JournalStatus.FILLED]
    assert planned.planned_price == 947.74
    assert filled.next_open == 950.0
    assert filled.actual_price == 951.0
    assert filled.actual_shares == 100
    assert filled.slippage_per_share == pytest.approx(1.0)
    assert filled.slippage_value == pytest.approx(100.0)
    assert filled.slippage_bps == pytest.approx(10.526315789473685)
    assert "10.5263 bps" in render_execution_journal(records)


def test_sell_slippage_and_manual_skip_are_observational_events(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="sell-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="SELL",
        planned_price=947.74,
        planned_shares=100,
    )
    filled = append_filled(
        path,
        plan_id="sell-1",
        recorded_at="2026-08-06T09:35:00+08:00",
        next_open=950.0,
        actual_time="2026-08-06T09:34:00+08:00",
        actual_price=949.0,
        actual_shares=40,
    )
    skipped = append_skipped(
        path,
        plan_id="sell-1",
        recorded_at="2026-08-06T14:59:00+08:00",
        next_open=950.0,
        manual_skip="operator declined remaining shares",
    )

    assert filled.slippage_value == pytest.approx(40.0)
    assert skipped.manual_skip == "operator declined remaining shares"
    assert skipped.next_open == 950.0
    assert "operator declined remaining shares" in render_execution_journal(
        read_execution_journal(path)
    )
    with pytest.raises(ValueError, match="terminal"):
        append_filled(
            path,
            plan_id="sell-1",
            recorded_at="2026-08-06T15:00:00+08:00",
            next_open=950.0,
            actual_time="2026-08-06T14:59:30+08:00",
            actual_price=949.0,
            actual_shares=60,
        )


def test_journal_rejects_tampering_and_never_imports_strategy_or_state(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["planned_price"] = 1.0
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        read_execution_journal(path)

    source = Path("uquant/execution_journal.py").read_text(encoding="utf-8")
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
        name.startswith(
            (
                "uquant.account",
                "uquant.config",
                "uquant.engine",
                "uquant.execution",
                "uquant.portfolio",
                "uquant.types",
            )
        )
        for name in imported
    )


def test_retained_checkpoint_detects_truncation_and_full_chain_reseal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    append_filled(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=950.0,
        actual_time="2026-08-06T09:31:00+08:00",
        actual_price=951.0,
        actual_shares=100,
    )
    checkpoint_factory = getattr(
        journal_module,
        "execution_journal_checkpoint",
        None,
    )
    assert checkpoint_factory is not None
    checkpoint = checkpoint_factory(read_execution_journal(path))
    original_lines = path.read_text(encoding="utf-8").splitlines()

    path.write_text(original_lines[0] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="trusted checkpoint"):
        read_execution_journal(path, trusted_checkpoint=checkpoint)

    rows = [json.loads(line) for line in original_lines]
    rows[0]["plan_id"] = "resealed-plan"
    rows[0]["previous_sha256"] = "0" * 64
    _reseal(rows[0])
    rows[1]["plan_id"] = "resealed-plan"
    rows[1]["previous_sha256"] = rows[0]["record_sha256"]
    _reseal(rows[1])
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert len(read_execution_journal(path)) == 2
    with pytest.raises(ValueError, match="trusted checkpoint"):
        read_execution_journal(path, trusted_checkpoint=checkpoint)


def test_cli_uses_the_ignored_default_journal_and_renders_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(
        [
            "execution-journal",
            "planned",
            "--plan-id",
            "cli-1",
            "--recorded-at",
            "2026-08-05T15:01:00+08:00",
            "--symbol",
            "sz300308",
            "--side",
            "BUY",
            "--planned-price",
            "947.74",
            "--planned-shares",
            "100",
        ]
    ) == 0
    assert main(["execution-journal", "report"]) == 0

    assert (tmp_path / "execution_journal.jsonl").is_file()
    assert "Manual Execution Journal" in capsys.readouterr().out


def test_concurrent_unique_plans_form_one_valid_chain(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(16)
    processes = [
        context.Process(target=_concurrent_plan_worker, args=(str(path), index, barrier))
        for index in range(16)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0] * 16
    records = read_execution_journal(path)
    assert len(records) == 16
    assert {record.plan_id for record in records} == {
        f"concurrent-{index}" for index in range(16)
    }


def test_readback_recomputes_slippage_and_rejects_forged_derived_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    append_filled(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=950.0,
        actual_time="2026-08-06T09:31:00+08:00",
        actual_price=951.0,
        actual_shares=100,
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1]["slippage_value"] = -100.0
    _reseal(rows[1])
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="derived slippage"):
        read_execution_journal(path)


def test_journal_enforces_chronology_and_one_next_open_per_plan(tmp_path: Path) -> None:
    chronology = tmp_path / "chronology.jsonl"
    append_planned(
        chronology,
        plan_id="plan-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    with pytest.raises(ValueError, match="chronology"):
        append_filled(
            chronology,
            plan_id="plan-1",
            recorded_at="2026-08-06T09:32:00+08:00",
            next_open=950.0,
            actual_time="2026-08-05T14:59:00+08:00",
            actual_price=951.0,
            actual_shares=40,
        )

    opens = tmp_path / "opens.jsonl"
    append_planned(
        opens,
        plan_id="plan-2",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    append_filled(
        opens,
        plan_id="plan-2",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=950.0,
        actual_time="2026-08-06T09:31:00+08:00",
        actual_price=951.0,
        actual_shares=40,
    )
    with pytest.raises(ValueError, match="next open"):
        append_skipped(
            opens,
            plan_id="plan-2",
            recorded_at="2026-08-06T14:59:00+08:00",
            next_open=949.0,
            manual_skip="declined remainder",
        )


def test_manual_skip_is_markdown_escaped(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    append_skipped(
        path,
        plan_id="plan-1",
        recorded_at="2026-08-06T09:32:00+08:00",
        next_open=950.0,
        manual_skip="operator | declined\nremaining",
    )

    report = render_execution_journal(read_execution_journal(path))
    assert "operator \\| declined<br>remaining" in report


def test_atomic_outputs_reject_symlink_and_hardlink_aliases(tmp_path: Path) -> None:
    protected = tmp_path / "protected.jsonl"
    protected.write_text("preserve\n", encoding="utf-8")
    hardlink = tmp_path / "hardlink.md"
    hardlink.hardlink_to(protected)
    with pytest.raises(ValueError, match="alias"):
        atomic_write_text(hardlink, "replace\n", protected_paths=(protected,))

    symlink = tmp_path / "symlink.md"
    symlink.symlink_to(protected)
    with pytest.raises(ValueError, match="symlink"):
        atomic_write_text(symlink, "replace\n", protected_paths=(protected,))
    assert protected.read_text(encoding="utf-8") == "preserve\n"


def test_cli_report_cannot_alias_the_append_only_journal(tmp_path: Path) -> None:
    journal = tmp_path / "execution.jsonl"
    append_planned(
        journal,
        plan_id="plan-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    before = journal.read_bytes()
    alias = tmp_path / "report.md"
    alias.hardlink_to(journal)

    with pytest.raises(ValueError, match="alias"):
        main(
            [
                "execution-journal",
                "report",
                "--journal",
                str(journal),
                "--output",
                str(alias),
            ]
        )
    assert journal.read_bytes() == before
