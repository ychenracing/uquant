from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from research import window_competitor_adapter as implementation

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_window_competitor_adapter.py"
SPEC = importlib.util.spec_from_file_location("window_competitor_adapter_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)


def test_target_contract_is_exact_and_contains_only_pools_a_to_e() -> None:
    assert adapter.TARGET_START == "2025-01-02"
    assert adapter.TARGET_END == "2026-07-31"
    assert tuple(adapter.POOLS) == ("a", "b", "c", "d", "e")
    assert tuple(adapter.SYSTEMS) == ("aquant", "qwenquant", "trade")


def test_five_window_contract_preserves_the_inclusive_july_boundary() -> None:
    assert adapter.WINDOWS == {
        "h1_2023": ("2023-01-03", "2023-06-30"),
        "h2_2023": ("2023-07-03", "2023-12-29"),
        "h1_2024": ("2024-01-02", "2024-07-01"),
        "h2_2024": ("2024-07-01", "2024-12-31"),
        "bull_crash_2025_2026": ("2025-01-02", "2026-07-31"),
    }


def test_public_replay_capability_binds_and_restores_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_pools = implementation.POOLS
    original_windows = implementation.WINDOWS
    observed: dict[str, object] = {}
    task = implementation.Task(
        "trade",
        "probe",
        "period",
        "qwen",
        "aquant",
        "trade",
        "data",
        "trade-data",
    )

    def capture(current: implementation.Task) -> dict[str, object]:
        observed["task"] = current
        observed["pools"] = implementation.POOLS
        observed["windows"] = implementation.WINDOWS
        return {"status": "captured"}

    monkeypatch.setattr(implementation, "_run", capture)
    result = implementation.run_replay_task(
        task,
        pools={"probe": ("sz300308",)},
        windows={"period": ("2025-01-02", "2026-07-31")},
        repository_root=Path(__file__).resolve().parents[1],
    )

    assert result == {"status": "captured"}
    assert observed == {
        "task": task,
        "pools": {"probe": ("sz300308",)},
        "windows": {"period": ("2025-01-02", "2026-07-31")},
    }
    assert implementation.POOLS is original_pools
    assert implementation.WINDOWS is original_windows


def test_source_locks_match_the_reviewed_frozen_repositories() -> None:
    assert adapter.LOCKED_SOURCES == {
        "aquant": {
            "repository": "ychenracing/aquant",
            "commit": "3c38fbbf679a0fb1b4ee8f3d47b6931d3eb8fdbd",
            "python_sha256": "0fdc39c40239e51b5c91024507bef1bed222cd83575e4d9f870b8ada2f73a50a",
        },
        "qwenquant": {
            "repository": "ychenracing/qwenquant",
            "commit": "0b3681e10b75425ad8600e75835677a6a125ed13",
            "python_sha256": "66fc531989e294990d40dae5f0c0ff867fe4e144ab2bae81863b42e7113c46c0",
        },
        "trade": {
            "repository": "ychenracing/trade",
            "commit": "cee1620f40af3af8f839e15db188a9e388a78dd0",
            "python_sha256": "03e33e1396ca31d61e724bcd9cf58971ae656134740eb8929313167aa8ed8597",
        },
    }


def test_default_source_roots_point_to_the_reconstructed_competitor_directory() -> None:
    roots = adapter._default_source_roots()

    assert set(roots) == set(adapter.SYSTEMS)
    assert roots["aquant"].as_posix().endswith("/competitors/aquant")
    assert roots["qwenquant"].as_posix().endswith("/competitors/qwenquant")
    assert roots["trade"].as_posix().endswith("/competitors/trade")


def test_complete_rows_requires_exactly_seventy_five_unique_cells() -> None:
    rows = [
        {
            "system": system,
            "pool": pool,
            "window": window,
            "start": adapter.WINDOWS[window][0],
            "end": adapter.WINDOWS[window][1],
        }
        for system in adapter.SYSTEMS
        for pool in adapter.POOLS
        for window in adapter.WINDOWS
    ]

    adapter._validate_complete_rows(rows)

    with pytest.raises(RuntimeError, match="missing competitor cells"):
        adapter._validate_complete_rows(rows[:-1])
    with pytest.raises(RuntimeError, match="duplicate competitor cell"):
        adapter._validate_complete_rows([*rows, dict(rows[0])])
    with pytest.raises(RuntimeError, match="window interval mismatch"):
        adapter._validate_complete_rows([dict(rows[0], end="2023-06-29"), *rows[1:]])


def test_complete_rows_honors_an_explicit_cli_subset() -> None:
    row = {
        "system": "trade",
        "pool": "b",
        "window": "h1_2024",
        "start": adapter.WINDOWS["h1_2024"][0],
        "end": adapter.WINDOWS["h1_2024"][1],
    }

    adapter._validate_complete_rows(
        [row],
        systems=("trade",),
        pools=("b",),
        windows=("h1_2024",),
    )


def test_broker_order_ledger_nets_virtual_fills_by_execution_key() -> None:
    fills = [
        {
            "fill_date": "2026-01-06",
            "signal_date": "2026-01-05",
            "symbol": "300308",
            "side": "buy",
            "reason": "fast",
            "price": 10.0,
            "shares": 100,
        },
        {
            "fill_date": "2026-01-06",
            "signal_date": "2026-01-05",
            "symbol": "300308",
            "side": "BUY",
            "reason": "slow",
            "price": 10.0,
            "shares": 200,
        },
    ]

    ledger, linked = adapter._broker_order_ledger("trade", fills)

    assert len(ledger) == 1
    assert ledger[0]["filled_shares"] == 300
    assert ledger[0]["internal_fills"] == 2
    assert linked[0]["order_id"] == linked[1]["order_id"]


def test_missing_fill_signal_date_fails_closed_when_mapping_is_ambiguous() -> None:
    fills = [
        {
            "fill_date": "2026-01-06",
            "signal_date": None,
            "symbol": "sz300308",
            "side": "BUY",
        }
    ]
    submissions = [
        {
            "signal_date": signal_date,
            "attempt_date": "2026-01-06",
            "symbol": "sz300308",
            "side": "BUY",
        }
        for signal_date in ("2026-01-04", "2026-01-05")
    ]

    with pytest.raises(RuntimeError, match="exactly one close submission"):
        adapter._link_missing_signal_dates(fills, submissions)


def test_workers_must_be_positive_before_source_validation(capsys) -> None:
    """Catches a late ProcessPool error obscuring an invalid CLI argument."""

    with pytest.raises(SystemExit):
        adapter.main(["--workers", "0"])

    assert "--workers must be positive" in capsys.readouterr().err


def test_adapter_output_refuses_a_symlink_target(tmp_path: Path) -> None:
    """Catches evidence output escaping through a caller-controlled symlink."""

    canonical_data = tmp_path / "canonical"
    canonical_data.mkdir()
    (canonical_data / "sz300308.csv").write_text(
        "date,open,high,low,close,volume,amount\n"
        "2025-01-02,1,1,1,1,1,1\n",
        encoding="utf-8",
    )
    victim = tmp_path / "victim.json"
    victim.write_text("preserve me", encoding="utf-8")
    output = tmp_path / "output.json"
    output.symlink_to(victim)
    args = SimpleNamespace(
        systems=(),
        pools=(),
        windows=(),
        workers=1,
        output=output,
        data_dir=canonical_data,
        trade_data_dir=None,
    )

    with pytest.raises(ValueError, match="symlink"):
        adapter._execute_matrix(args, {}, canonical_data, tmp_path / "trade")

    assert victim.read_text(encoding="utf-8") == "preserve me"


def test_adapter_output_cannot_name_a_competitor_source_file(tmp_path: Path) -> None:
    """Catches evidence output replacing a consumed file inside a source tree."""

    source_root = tmp_path / "qwen"
    source_root.mkdir()
    source = source_root / "engine.py"
    source.write_text("preserve source\n", encoding="utf-8")
    canonical_data = tmp_path / "canonical"
    canonical_data.mkdir()
    (canonical_data / "sz300308.csv").write_text(
        "date,open,high,low,close,volume,amount\n"
        "2025-01-02,1,1,1,1,1,1\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        systems=(),
        pools=(),
        windows=(),
        workers=1,
        output=source,
        data_dir=canonical_data,
        trade_data_dir=None,
    )

    with pytest.raises(ValueError, match="input tree"):
        adapter._execute_matrix(
            args,
            {"qwenquant": source_root},
            canonical_data,
            tmp_path / "trade",
        )

    assert source.read_text(encoding="utf-8") == "preserve source\n"


def test_adapter_output_cannot_hardlink_to_caller_trade_data(tmp_path: Path) -> None:
    """Catches an output alias to a consumed caller-provided trade CSV."""

    canonical_data = tmp_path / "canonical"
    canonical_data.mkdir()
    (canonical_data / "sz300308.csv").write_text(
        "date,open,high,low,close,volume,amount\n"
        "2025-01-02,1,1,1,1,1,1\n",
        encoding="utf-8",
    )
    trade_data = tmp_path / "trade"
    trade_data.mkdir()
    trade_csv = trade_data / "300308.csv"
    trade_csv.write_text("preserve trade data\n", encoding="utf-8")
    output = tmp_path / "result.json"
    os.link(trade_csv, output)
    args = SimpleNamespace(
        systems=(),
        pools=(),
        windows=(),
        workers=1,
        output=output,
        data_dir=canonical_data,
        trade_data_dir=trade_data,
    )

    with pytest.raises(ValueError, match="protected path"):
        adapter._execute_matrix(args, {}, canonical_data, trade_data)

    assert trade_csv.read_text(encoding="utf-8") == "preserve trade data\n"
