"""Reproducible orchestration for the approved Pareto sprint evidence."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research.generalization_smoke import run_generalization_smoke
from uquant.leader import INDUSTRY

_REQUIRED_COMPETITOR_CELLS = 5 * 7 * 3


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
        len(generalization_references)
        if isinstance(generalization_references, (dict, list))
        else 0
    )
    competitor_status = (
        "present"
        if competitor_path.is_file() and competitor_cells == _REQUIRED_COMPETITOR_CELLS
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
    smoke = _load_json(benchmarks / "generalization_smoke_reference.json")
    smoke_observations = smoke.get("observations", [])
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
            "diagnostic_smoke_cases": (
                len(smoke_observations) if isinstance(smoke_observations, list) else 0
            ),
        },
        "can_run_fail_closed_gates": (
            competitor_status == "present" and generalization_status == "present"
        ),
    }


def smoke_inputs(repository_root: str | Path) -> dict[str, Any]:
    """Load the reviewed Pool E smoke contract from committed repository inputs."""
    root = Path(repository_root)
    promotion = _load_json(root / "benchmarks" / "promotion_baseline.json")
    pools = promotion.get("pools", {})
    scenarios = promotion.get("scenarios", {})
    if not isinstance(pools, Mapping) or not isinstance(scenarios, Mapping):
        raise RuntimeError("promotion baseline is missing pools or scenarios")
    universe_raw = pools.get("e", ())
    continuous = scenarios.get("continuous", {})
    if not isinstance(universe_raw, list) or not isinstance(continuous, Mapping):
        raise RuntimeError("promotion baseline is missing Pool E or continuous window")
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


def _write(payload: Mapping[str, Any], output: str | Path | None) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8")
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
    args = _parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    if args.command == "reference-audit":
        payload = audit_references(root)
    else:
        payload = run_generalization_smoke(**smoke_inputs(root))
    _write(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

