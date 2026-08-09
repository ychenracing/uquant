"""Reproducible multi-regime strategy-promotion matrix."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from ..engine import ProductionEngine

_POLICY_FIELDS = {
    "wealth_floor_ratio",
    "drawdown_tolerance",
    "absolute_max_drawdown",
    "order_tolerance",
    "turnover_ceiling_ratio",
    "turnover_tolerance",
}
_REFERENCE_FIELDS = {
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "annual_turnover",
}


@dataclass(frozen=True, slots=True)
class Scenario:
    """One point-in-time replay window plus optional sub-period objective."""

    start: str
    end: str
    urgent_start: str = ""
    urgent_end: str = ""


def _load_spec(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"promotion baseline is missing or corrupt: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("promotion baseline must be a JSON object")
    required = {"policy", "pools", "scenarios", "profiles", "references"}
    missing = sorted(required - payload.keys())
    if missing:
        raise RuntimeError(f"promotion baseline is missing sections: {missing}")
    for name in required:
        if not isinstance(payload[name], dict):
            raise RuntimeError(f"promotion baseline section must be an object: {name}")
    _validate_spec(payload)
    return payload


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"promotion baseline value must be numeric: {label}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RuntimeError(f"promotion baseline value must be finite: {label}")
    return numeric


def _validate_spec(spec: dict[str, Any]) -> None:
    """Reject ambiguous or stale promotion contracts before any replay starts."""
    policy = spec["policy"]
    missing_policy = sorted(_POLICY_FIELDS - policy.keys())
    if missing_policy:
        raise RuntimeError(
            f"promotion policy is missing fields: {missing_policy}"
        )
    numeric_policy = {
        name: _finite_number(policy[name], label=f"policy.{name}")
        for name in _POLICY_FIELDS
    }
    if not 0 < numeric_policy["wealth_floor_ratio"] <= 1:
        raise RuntimeError("promotion wealth_floor_ratio must be in (0, 1]")
    if not 0 <= numeric_policy["absolute_max_drawdown"] <= 1:
        raise RuntimeError("promotion absolute_max_drawdown must be in [0, 1]")
    if numeric_policy["drawdown_tolerance"] < 0:
        raise RuntimeError("promotion drawdown_tolerance cannot be negative")
    if numeric_policy["order_tolerance"] < 0 or int(
        numeric_policy["order_tolerance"]
    ) != numeric_policy["order_tolerance"]:
        raise RuntimeError("promotion order_tolerance must be a nonnegative integer")
    if numeric_policy["turnover_ceiling_ratio"] < 1:
        raise RuntimeError("promotion turnover_ceiling_ratio cannot be below one")
    if numeric_policy["turnover_tolerance"] < 0:
        raise RuntimeError("promotion turnover_tolerance cannot be negative")

    pools = spec["pools"]
    for pool_name, symbols in pools.items():
        if not isinstance(pool_name, str) or not isinstance(symbols, list) or not symbols:
            raise RuntimeError("promotion pools must be named non-empty lists")
        if any(not isinstance(symbol, str) or not symbol for symbol in symbols):
            raise RuntimeError(f"promotion pool has an invalid symbol: {pool_name}")
        if len(symbols) != len(set(symbols)):
            raise RuntimeError(f"promotion pool must contain unique symbols: {pool_name}")

    scenarios: dict[str, Scenario] = {}
    for scenario_name, values in spec["scenarios"].items():
        if not isinstance(scenario_name, str) or not isinstance(values, dict):
            raise RuntimeError("promotion scenarios must be named objects")
        try:
            scenario = Scenario(**values)
            start = date.fromisoformat(scenario.start)
            end = date.fromisoformat(scenario.end)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"promotion scenario is invalid: {scenario_name}"
            ) from exc
        if start > end:
            raise RuntimeError(f"promotion scenario has reversed dates: {scenario_name}")
        if bool(scenario.urgent_start) != bool(scenario.urgent_end):
            raise RuntimeError(
                f"promotion scenario has an incomplete urgent interval: {scenario_name}"
            )
        if scenario.urgent_start:
            try:
                urgent_start = date.fromisoformat(scenario.urgent_start)
                urgent_end = date.fromisoformat(scenario.urgent_end)
            except ValueError as exc:
                raise RuntimeError(
                    f"promotion scenario has an invalid urgent interval: {scenario_name}"
                ) from exc
            if not start <= urgent_start <= urgent_end <= end:
                raise RuntimeError(
                    f"promotion urgent interval is outside its scenario: {scenario_name}"
                )
        scenarios[scenario_name] = scenario

    referenced_cells: set[str] = set()
    for profile_name, cells in spec["profiles"].items():
        if not isinstance(cells, list) or not cells:
            raise RuntimeError(
                f"promotion profile must be a non-empty list: {profile_name}"
            )
        profile_cells: set[str] = set()
        for cell in cells:
            if not isinstance(cell, dict) or set(cell) != {"pool", "scenario"}:
                raise RuntimeError(
                    f"promotion profile cell is invalid: {profile_name}"
                )
            pool_name = cell["pool"]
            scenario_name = cell["scenario"]
            name = f"{pool_name}/{scenario_name}"
            if pool_name not in pools or scenario_name not in scenarios:
                raise RuntimeError(
                    f"promotion cell references an unknown input: {name}"
                )
            if name in profile_cells:
                raise RuntimeError(f"promotion profile repeats a cell: {name}")
            profile_cells.add(name)
        referenced_cells.update(profile_cells)

    references = spec["references"]
    unknown_references: list[str] = []
    for name, reference in references.items():
        if not isinstance(reference, dict):
            raise RuntimeError(f"promotion reference must be an object: {name}")
        parts = name.split("/", maxsplit=1)
        if len(parts) != 2 or parts[0] not in pools or parts[1] not in scenarios:
            unknown_references.append(name)
            continue
        missing_reference = sorted(_REFERENCE_FIELDS - reference.keys())
        if missing_reference:
            raise RuntimeError(
                f"promotion reference is missing metrics: {name} {missing_reference}"
            )
        final_wealth = _finite_number(
            reference["final_wealth"], label=f"references.{name}.final_wealth"
        )
        max_drawdown = _finite_number(
            reference["max_drawdown"], label=f"references.{name}.max_drawdown"
        )
        account_orders = _finite_number(
            reference["account_orders"], label=f"references.{name}.account_orders"
        )
        annual_turnover = _finite_number(
            reference["annual_turnover"], label=f"references.{name}.annual_turnover"
        )
        if final_wealth <= 0 or not 0 <= max_drawdown <= 1:
            raise RuntimeError(f"promotion reference has invalid performance: {name}")
        if account_orders < 0 or int(account_orders) != account_orders:
            raise RuntimeError(f"promotion reference has invalid order count: {name}")
        if annual_turnover < 0:
            raise RuntimeError(f"promotion reference has negative turnover: {name}")
        if "urgent_return_floor" in reference:
            urgent_floor = _finite_number(
                reference["urgent_return_floor"],
                label=f"references.{name}.urgent_return_floor",
            )
            if not -1 < urgent_floor < 1:
                raise RuntimeError(f"promotion urgent floor is invalid: {name}")
    if unknown_references:
        raise RuntimeError(
            f"promotion references contain unknown cells: {sorted(unknown_references)}"
        )
    missing_references = sorted(referenced_cells - references.keys())
    if missing_references:
        raise RuntimeError(
            f"promotion profiles have no frozen references: {missing_references}"
        )


def _urgent_return(result: dict[str, Any], scenario: Scenario) -> float | None:
    if not scenario.urgent_start or not scenario.urgent_end:
        return None
    curve = {
        str(item["date"]): float(item["equity"])
        for item in result.get("equity_curve", [])
        if isinstance(item, dict)
    }
    start = curve.get(scenario.urgent_start)
    end = curve.get(scenario.urgent_end)
    if start is None or end is None or start <= 0:
        raise RuntimeError("promotion urgent interval is absent from the equity curve")
    return end / start - 1.0


def _compact(result: dict[str, Any], scenario: Scenario) -> dict[str, float | int | None]:
    return {
        "final_wealth": float(result["final_wealth"]),
        "max_drawdown": float(result["max_drawdown"]),
        "account_orders": int(result["account_orders"]),
        "annual_turnover": float(result["annual_turnover"]),
        "urgent_return": _urgent_return(result, scenario),
    }


def _violations(
    *,
    name: str,
    result: dict[str, float | int | None],
    reference: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    wealth_floor = float(reference["final_wealth"]) * float(policy["wealth_floor_ratio"])
    if float(result["final_wealth"] or 0.0) < wealth_floor:
        failures.append(f"{name}: final_wealth below {wealth_floor:.6f}")
    drawdown_ceiling = min(
        float(policy["absolute_max_drawdown"]),
        float(reference["max_drawdown"]) + float(policy["drawdown_tolerance"]),
    )
    if float(result["max_drawdown"] or 0.0) > drawdown_ceiling:
        failures.append(f"{name}: max_drawdown above {drawdown_ceiling:.6f}")
    reference_orders = int(reference["account_orders"])
    order_ceiling = max(
        reference_orders + int(policy["order_tolerance"]),
        math.ceil(reference_orders * float(policy.get("order_ceiling_ratio", 1.0))),
    )
    if int(result["account_orders"] or 0) > order_ceiling:
        failures.append(f"{name}: account_orders above {order_ceiling}")
    turnover_ceiling = max(
        float(reference["annual_turnover"]) * float(policy["turnover_ceiling_ratio"]),
        float(reference["annual_turnover"]) + float(policy["turnover_tolerance"]),
    )
    if float(result["annual_turnover"] or 0.0) > turnover_ceiling:
        failures.append(f"{name}: annual_turnover above {turnover_ceiling:.6f}")
    urgent_floor = reference.get("urgent_return_floor")
    if urgent_floor is not None:
        observed = result.get("urgent_return")
        if observed is None or float(observed) < float(urgent_floor):
            failures.append(f"{name}: urgent_return below {float(urgent_floor):.6f}")
    return failures


def run_promotion(
    *,
    data_dir: str | Path,
    baseline: str | Path,
    profile: str = "quick",
) -> dict[str, Any]:
    """Run a frozen matrix and return a machine-readable pass/fail report."""
    baseline_path = Path(baseline)
    spec = _load_spec(baseline_path)
    profiles = spec.get("profiles", {})
    if profile not in profiles:
        raise RuntimeError(f"unknown promotion profile: {profile}")
    selected = profiles[profile]
    pools = spec["pools"]
    scenarios = {
        name: Scenario(**values) for name, values in spec["scenarios"].items()
    }
    references = spec["references"]
    policy = spec["policy"]
    engine = ProductionEngine(data_dir)
    cells: dict[str, dict[str, float | int | None]] = {}
    failures: list[str] = []
    for cell in selected:
        if not isinstance(cell, dict):
            raise RuntimeError("promotion profile cells must be objects")
        pool_name = str(cell["pool"])
        scenario_name = str(cell["scenario"])
        name = f"{pool_name}/{scenario_name}"
        if pool_name not in pools or scenario_name not in scenarios:
            raise RuntimeError(f"promotion cell references an unknown input: {name}")
        if name not in references:
            raise RuntimeError(f"promotion cell has no frozen reference: {name}")
        scenario = scenarios[scenario_name]
        raw = engine.backtest(
            symbols=tuple(pools[pool_name]),
            start=scenario.start,
            end=scenario.end,
        )
        result = _compact(raw, scenario)
        cells[name] = result
        failures.extend(
            _violations(
                name=name,
                result=result,
                reference=references[name],
                policy=policy,
            )
        )

    wealth_values = [float(item["final_wealth"] or 0.0) for item in cells.values()]
    drawdowns = [float(item["max_drawdown"] or 0.0) for item in cells.values()]
    return {
        "profile": profile,
        "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "passed": not failures,
        "failures": failures,
        "summary": {
            "cells": len(cells),
            "median_final_wealth": median(wealth_values),
            "median_max_drawdown": median(drawdowns),
            "total_account_orders": sum(
                int(item["account_orders"] or 0) for item in cells.values()
            ),
        },
        "results": cells,
    }
