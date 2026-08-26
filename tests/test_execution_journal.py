from __future__ import annotations

import ast
import hashlib
import json
import multiprocessing
from pathlib import Path
from typing import Any

import pytest

from uquant.atomic_io import atomic_write_text
from uquant.cli import main
from uquant.observation import execution_journal as journal_surface
from uquant.observation.execution_journal import (
    JournalStatus,
    append_filled,
    append_planned,
    append_skipped,
    read_execution_journal,
)
from uquant.observation.execution_journal import store as journal_module
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
    hash_field = "record_hash" if payload.get("schema_version") == 2 else "record_sha256"
    unsigned = {key: value for key, value in payload.items() if key != hash_field}
    payload[hash_field] = hashlib.sha256(
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
    payload["planned_price_reference"] = 1.0
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
        journal_surface,
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
    rows[0]["previous_record_hash"] = "0" * 64
    _reseal(rows[0])
    rows[1]["plan_id"] = "resealed-plan"
    rows[1]["previous_record_hash"] = rows[0]["record_hash"]
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


def test_journal_append_completes_after_short_os_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "execution.jsonl"
    original_write = journal_module.os.write

    def short_write(descriptor: int, payload: bytes) -> int:
        return original_write(descriptor, payload[: max(1, len(payload) // 2)])

    monkeypatch.setattr(journal_module.os, "write", short_write)
    appended = append_planned(
        path,
        plan_id="short-write",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )

    assert read_execution_journal(path) == (appended,)


def test_journal_rolls_back_partial_append_after_zero_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="durable-plan",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    before = path.read_bytes()
    original_write = journal_module.os.write
    calls = 0

    def partial_then_zero(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:10])
        return 0

    monkeypatch.setattr(journal_module.os, "write", partial_then_zero)
    with pytest.raises(OSError, match="made no progress"):
        append_planned(
            path,
            plan_id="incomplete-plan",
            recorded_at="2026-08-05T15:02:00+08:00",
            symbol="sz300502",
            side="BUY",
            planned_price=92.0,
            planned_shares=100,
        )

    assert path.read_bytes() == before
    assert [record.plan_id for record in read_execution_journal(path)] == [
        "durable-plan"
    ]


def test_journal_rolls_back_partial_append_and_preserves_write_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="durable-plan",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    before = path.read_bytes()
    original_write = journal_module.os.write
    injected = InterruptedError("injected append interruption")
    calls = 0

    def partial_then_error(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:10])
        raise injected

    monkeypatch.setattr(journal_module.os, "write", partial_then_error)
    with pytest.raises(InterruptedError, match="injected append interruption") as caught:
        append_planned(
            path,
            plan_id="incomplete-plan",
            recorded_at="2026-08-05T15:02:00+08:00",
            symbol="sz300502",
            side="BUY",
            planned_price=92.0,
            planned_shares=100,
        )

    assert caught.value is injected
    assert path.read_bytes() == before
    assert [record.plan_id for record in read_execution_journal(path)] == [
        "durable-plan"
    ]


def test_journal_rolls_back_partial_append_after_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches process-control interruption leaving a truncated JSON record."""

    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="durable-plan",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    before = path.read_bytes()
    original_write = journal_module.os.write
    injected = KeyboardInterrupt("injected append interruption")
    calls = 0

    def partial_then_interrupt(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:10])
        raise injected

    monkeypatch.setattr(journal_module.os, "write", partial_then_interrupt)
    with pytest.raises(KeyboardInterrupt, match="injected append interruption") as caught:
        append_planned(
            path,
            plan_id="incomplete-plan",
            recorded_at="2026-08-05T15:02:00+08:00",
            symbol="sz300502",
            side="BUY",
            planned_price=92.0,
            planned_shares=100,
        )

    assert caught.value is injected
    assert path.read_bytes() == before
    assert [record.plan_id for record in read_execution_journal(path)] == [
        "durable-plan"
    ]


def test_journal_surfaces_a_failed_partial_append_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a corrupted audit tail being hidden behind the write failure."""

    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="durable-plan",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    before = path.read_bytes()
    original_write = journal_module.os.write
    injected = InterruptedError("injected append interruption")
    calls = 0

    def partial_then_error(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:10])
        raise injected

    def fail_truncate(descriptor: int, length: int) -> None:
        raise OSError(f"injected rollback failure at {length} on {descriptor}")

    monkeypatch.setattr(journal_module.os, "write", partial_then_error)
    monkeypatch.setattr(journal_module.os, "ftruncate", fail_truncate)
    with pytest.raises(InterruptedError, match="injected append interruption") as caught:
        append_planned(
            path,
            plan_id="incomplete-plan",
            recorded_at="2026-08-05T15:02:00+08:00",
            symbol="sz300502",
            side="BUY",
            planned_price=92.0,
            planned_shares=100,
        )

    assert caught.value is injected
    assert any(
        "journal rollback also failed" in note
        for note in getattr(caught.value, "__notes__", ())
    )
    assert path.read_bytes().startswith(before)
    assert len(path.read_bytes()) == len(before) + 10


def test_journal_preserves_write_error_when_rollback_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process-control rollback failure must not replace the append failure."""

    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="durable-plan",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    before = path.read_bytes()
    original_write = journal_module.os.write
    primary = InterruptedError("injected append interruption")
    rollback = KeyboardInterrupt("injected rollback interruption")
    calls = 0

    def partial_then_error(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:10])
        raise primary

    with monkeypatch.context() as patch:
        patch.setattr(journal_module.os, "write", partial_then_error)
        patch.setattr(
            journal_module.os,
            "ftruncate",
            lambda *_: (_ for _ in ()).throw(rollback),
        )
        with pytest.raises(InterruptedError, match="append interruption") as caught:
            append_planned(
                path,
                plan_id="incomplete-plan",
                recorded_at="2026-08-05T15:02:00+08:00",
                symbol="sz300502",
                side="BUY",
                planned_price=92.0,
                planned_shares=100,
            )

    assert caught.value is primary
    assert any(
        "journal rollback also failed: KeyboardInterrupt" in note
        for note in getattr(caught.value, "__notes__", ())
    )
    assert path.read_bytes().startswith(before)
    assert len(path.read_bytes()) == len(before) + 10


def test_journal_preserves_write_error_when_descriptor_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Descriptor cleanup must remain secondary to a failed append."""

    path = tmp_path / "execution.jsonl"
    append_planned(
        path,
        plan_id="durable-plan",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    before = path.read_bytes()
    original_write = journal_module.os.write
    original_close = journal_module.os.close
    primary = InterruptedError("injected append interruption")
    calls = 0

    def partial_then_error(descriptor: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, payload[:10])
        raise primary

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        raise OSError("injected close failure")

    with monkeypatch.context() as patch:
        patch.setattr(journal_module.os, "write", partial_then_error)
        patch.setattr(journal_module.os, "close", close_then_fail)
        with pytest.raises(InterruptedError, match="append interruption") as caught:
            append_planned(
                path,
                plan_id="incomplete-plan",
                recorded_at="2026-08-05T15:02:00+08:00",
                symbol="sz300502",
                side="BUY",
                planned_price=92.0,
                planned_shares=100,
            )

    assert caught.value is primary
    assert any(
        "journal descriptor cleanup also failed: OSError" in note
        for note in getattr(caught.value, "__notes__", ())
    )
    assert path.read_bytes() == before


def test_journal_reports_each_release_failure_after_a_complete_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful append still reports lock-release and descriptor-close failures."""

    path = tmp_path / "execution.jsonl"
    original_open = journal_module.os.open
    original_close = journal_module.os.close
    original_release = journal_module.release_file_lock
    append_descriptor: int | None = None

    def capture_open(
        candidate: str | Path,
        flags: int,
        mode: int = 0o777,
    ) -> int:
        nonlocal append_descriptor
        descriptor = original_open(candidate, flags, mode)
        if Path(candidate) == path:
            append_descriptor = descriptor
        return descriptor

    def unlock_then_fail(descriptor: int) -> None:
        original_release(descriptor)
        if descriptor == append_descriptor:
            raise OSError("injected unlock failure")

    def close_then_fail(descriptor: int) -> None:
        original_close(descriptor)
        if descriptor == append_descriptor:
            raise OSError("injected close failure")

    with monkeypatch.context() as patch:
        patch.setattr(journal_module.os, "open", capture_open)
        patch.setattr(journal_module, "release_file_lock", unlock_then_fail)
        patch.setattr(journal_module.os, "close", close_then_fail)
        with pytest.raises(OSError, match="injected unlock failure") as caught:
            append_planned(
                path,
                plan_id="complete-plan",
                recorded_at="2026-08-05T15:01:00+08:00",
                symbol="sz300308",
                side="BUY",
                planned_price=947.74,
                planned_shares=100,
            )

    assert any(
        "journal descriptor cleanup also failed: OSError" in note
        for note in getattr(caught.value, "__notes__", ())
    )
    assert [record.plan_id for record in read_execution_journal(path)] == [
        "complete-plan"
    ]


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
    rows[1]["realized_slippage"] = -100.0
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
