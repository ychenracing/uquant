from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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
