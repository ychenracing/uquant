"""Reproducible orchestration for the maintained Pareto evidence."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research.generalization_smoke import run_generalization_smoke
from uquant.atomic_io import atomic_write_text, validate_atomic_output_boundary
from uquant.leader import INDUSTRY
from uquant.validation.ai_era import AI_ERA_WINDOWS
from uquant.validation.competitor import REQUIRED_COMPETITORS, REQUIRED_POOLS

_REQUIRED_COMPETITOR_CELLS = len(REQUIRED_POOLS) * len(AI_ERA_WINDOWS) * len(REQUIRED_COMPETITORS)
_REQUIRED_COMPETITOR_WINDOWS = {
    name: {"start": start, "end": end} for name, (start, end) in AI_ERA_WINDOWS.items()
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"evidence JSON must contain an object: {path}")
    return payload


def _metric_cell_count(value: object) -> int:
    if not isinstance(value, Mapping):
        return 0
    if {"final_wealth", "max_drawdown", "account_orders"} <= set(value):
        return 1
    return sum(_metric_cell_count(item) for item in value.values())


def audit_references(repository_root: str | Path) -> dict[str, Any]:
    """Report whether reviewed full gates exist without creating replacements."""
    root = Path(repository_root)
    benchmarks = root / "benchmarks"
    competitor_path = benchmarks / "competitor_matrix_reference.json"
    generalization_path = benchmarks / "generalization_baseline.json"
    competitor = _load_json(competitor_path)
    generalization = _load_json(generalization_path)
    competitor_cells = _metric_cell_count(competitor.get("results", {}))
    generalization_references = generalization.get("references", {})
    generalization_count = (
        len(generalization_references) if isinstance(generalization_references, (dict, list)) else 0
    )
    competitor_windows = competitor.get("windows", {})
    competitor_status = (
        "present"
        if (
            competitor_path.is_file()
            and competitor_cells == _REQUIRED_COMPETITOR_CELLS
            and competitor_windows == _REQUIRED_COMPETITOR_WINDOWS
        )
        else "incomplete"
        if competitor_path.is_file()
        else "missing"
    )
    generalization_status = (
        "present"
        if generalization_path.is_file() and generalization_count > 0
        else "incomplete"
        if generalization_path.is_file()
        else "missing"
    )
    bull = _load_json(benchmarks / "competitor_bull_reference.json")
    return {
        "schema_version": 1,
        "competitor": {
            "required_cells": _REQUIRED_COMPETITOR_CELLS,
            "reviewed_reference": competitor_status,
            "reviewed_cells": competitor_cells,
            "partial_bull_cells": _metric_cell_count(bull.get("results", {})),
        },
        "generalization": {
            "reviewed_reference": generalization_status,
            "reviewed_cases": generalization_count,
            "diagnostic_smoke_cases": 0,
        },
        "can_run_fail_closed_gates": (competitor_status == "present" and generalization_status == "present"),
    }


def smoke_inputs(repository_root: str | Path) -> dict[str, Any]:
    """Load the reviewed Pool E smoke contract from committed repository inputs."""
    root = Path(repository_root)
    promotion = _load_json(root / "benchmarks" / "promotion_baseline.json")
    pools = promotion.get("pools", {})
    contract = promotion.get("contract", {})
    windows = contract.get("windows", {}) if isinstance(contract, Mapping) else {}
    if not isinstance(pools, Mapping) or not isinstance(windows, Mapping):
        raise RuntimeError("promotion baseline is missing pools or AI-era windows")
    universe_raw = pools.get("e", ())
    continuous = windows.get("continuous_ai_era", {})
    if not isinstance(universe_raw, list) or not isinstance(continuous, Mapping):
        raise RuntimeError("promotion baseline is missing Pool E or continuous_ai_era")
    universe = tuple(str(symbol) for symbol in universe_raw)
    industries = {symbol: INDUSTRY.get(symbol, "unknown") for symbol in universe}
    if any(industry == "unknown" for industry in industries.values()):
        raise RuntimeError("Pool E contains symbols without reviewed industry evidence")
    return {
        "data_dir": root / "data" / "frozen",
        "universe": universe,
        "industries": industries,
        "prior_symbols": tuple(sorted(str(symbol) for symbol in pools.get("a", ()))),
        "start": str(continuous["start"]),
        "end": str(continuous["end"]),
    }


def _write(
    payload: Mapping[str, Any],
    output: str | Path | None,
    *,
    protected_paths: tuple[Path, ...] = (),
) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        atomic_write_text(output, encoded, protected_paths=protected_paths)
    print(encoded, end="")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_pareto_evidence")
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    subcommands = parser.add_subparsers(dest="command", required=True)
    for name in ("reference-audit", "smoke"):
        command = subcommands.add_parser(name)
        command.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the selected evidence audit or deterministic smoke replay."""

    args = _parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    payload: Mapping[str, Any]
    protected_paths: tuple[Path, ...]
    if args.command == "reference-audit":
        exact_inputs = (
            root / "benchmarks" / "competitor_matrix_reference.json",
            root / "benchmarks" / "generalization_baseline.json",
            root / "benchmarks" / "competitor_bull_reference.json",
        )
        protected_paths = validate_atomic_output_boundary(
            args.output,
            protected_paths=exact_inputs,
        )
        payload = audit_references(root)
    else:
        inputs = smoke_inputs(root)
        protected_paths = validate_atomic_output_boundary(
            args.output,
            protected_paths=(root / "benchmarks" / "promotion_baseline.json",),
            protected_roots=(Path(inputs["data_dir"]),),
        )
        payload = run_generalization_smoke(**inputs)
    _write(payload, args.output, protected_paths=protected_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
