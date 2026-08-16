from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from uquant.cli import main
from uquant.execution_journal import (
    JournalStatus,
    append_filled,
    append_planned,
    append_skipped,
    read_execution_journal,
)
from uquant.report import render_execution_journal


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
