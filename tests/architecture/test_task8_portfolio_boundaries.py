from __future__ import annotations

import json

from ._analysis import ROOT

_TASK8_START = "4b6bedb03fb7c58914d9d5032a2514c67f41f6ba"
_TASK8_START_TREE = "d3824f7c5d89521b8284b5de08cc1e82e3ab7ebd"
_INVENTORY = ROOT / "artifacts" / "architecture_refactor" / "task8_cleanup_inventory.json"
_DAILY_TRACE = ROOT / "benchmarks" / "task8_daily_portfolio_trace.json"
_LEGACY_IMPLEMENTATIONS = (
    "uquant/portfolio.py",
    "uquant/portfolio_leaders.py",
    "uquant/portfolio_strategic.py",
    "uquant/portfolio_recovery.py",
)


def test_task8_cleanup_inventory_is_bound_before_any_portfolio_replacement() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert payload["baseline_commit"] == _TASK8_START
    assert payload["baseline_tree"] == _TASK8_START_TREE
    assert tuple(entry["path"] for entry in payload["entries"]) == _LEGACY_IMPLEMENTATIONS


def test_task8_daily_allocation_oracle_is_bound_to_the_immutable_start() -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    assert payload["baseline_commit"] == _TASK8_START
    assert payload["baseline_tree"] == _TASK8_START_TREE
    assert payload["contract"] == "uquant-task8-daily-allocation-trace-v1"
