"""Generate the Task 1 characterization contracts from the frozen baseline."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd

from tests.architecture._analysis import (
    FINAL_BUDGETS,
    INVENTORY_PATH,
    PUBLIC_API_PATH,
    ROOT,
    architecture_snapshot,
    canonical_sha256,
    measured_debt,
    public_api_snapshot,
    representative_replay,
    sha256_file,
    sha256_tree,
    tracked_file_inventory,
)
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import code_fingerprint
from uquant.validation.universe import default_ai_universe

BASELINE_COMMIT = "f9fd489806a86b3a56f62b8668aafa252012d405"
BASELINE_BRANCH = "codex/uquant-modular-governance-20260822"
BASELINE_WORKTREE = (
    "/workspace/scratch/5353d1b571d9/uquant-base/.worktrees/"
    "uquant-modular-governance-baseline"
)

class ReplaySpec(TypedDict):
    name: str
    start: str
    end: str
    symbols: tuple[str, ...]


REPLAY_SPECS: tuple[ReplaySpec, ...] = (
    {
        "name": "early_ai_entry",
        "start": "2023-01-03",
        "end": "2023-01-20",
        "symbols": ("sz300308", "sz300502", "sz300394"),
    },
    {
        "name": "late_2024_rotation",
        "start": "2024-08-01",
        "end": "2024-09-02",
        "symbols": ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    },
    {
        "name": "recent_shock",
        "start": "2026-06-30",
        "end": "2026-07-30",
        "symbols": ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    },
)
CORE_PYTEST_COMMAND = (
    "UV_CACHE_DIR=/workspace/scratch/5353d1b571d9/.uv-cache uv run pytest -q "
    "tests/test_config_contracts.py tests/test_account_broker_schema.py "
    "tests/test_engine_contracts.py tests/test_execution.py"
)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify_frozen_data() -> tuple[int, str]:
    frozen = ROOT / "data" / "frozen"
    rows = []
    for line in (frozen / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, filename = line.split(maxsplit=1)
        filename = filename.strip()
        observed = sha256_file(frozen / filename)
        if observed != digest:
            raise RuntimeError(f"frozen data digest mismatch: {filename}")
        rows.append((filename, digest))
    return len(rows), canonical_sha256(rows)


def _measured_replay(spec: ReplaySpec) -> tuple[dict[str, object], float, int]:
    program = """
import json
import resource
import sys
import time

from tests.architecture._analysis import representative_replay

started = time.perf_counter()
observed = representative_replay(
    name=sys.argv[1],
    start=sys.argv[2],
    end=sys.argv[3],
    symbols=tuple(sys.argv[4:]),
)
print(json.dumps({
    "observed": observed,
    "wall_seconds": time.perf_counter() - started,
    "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
}, allow_nan=False, sort_keys=True))
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            spec["name"],
            spec["start"],
            spec["end"],
            *spec["symbols"],
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    observed = payload["observed"]
    if not isinstance(observed, dict):
        raise RuntimeError("representative replay measurement returned a malformed payload")
    return observed, float(payload["wall_seconds"]), int(payload["peak_rss_kib"])


def _source_surface() -> dict[str, object]:
    root = ROOT / "uquant"
    entries = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(root.rglob("*.py"))
    ]
    return {
        "entries": entries,
        "entry_count": len(entries),
        "canonical_sha256": canonical_sha256(entries),
        "tree_sha256": canonical_sha256(
            [(entry["path"], entry["sha256"]) for entry in entries]
        ),
    }


def generate(*, pytest_wall_seconds: float, pytest_peak_rss_kib: int) -> None:
    if _git("rev-parse", "HEAD") != BASELINE_COMMIT:
        raise RuntimeError("Task 1 baselines must be generated while HEAD is the frozen baseline")
    if _git("rev-parse", "origin/main") != BASELINE_COMMIT:
        raise RuntimeError("origin/main no longer matches the reviewed Task 1 baseline")
    if pytest_wall_seconds <= 0 or pytest_peak_rss_kib <= 0:
        raise ValueError("pytest wall time and peak RSS must be positive measured values")

    public_contract = public_api_snapshot()
    flat_config = public_contract["flat_config_serialization"]
    assert isinstance(flat_config, Mapping)
    config_field_order = flat_config["field_order"]
    assert isinstance(config_field_order, list)
    public_payload = {
        "schema_version": 1,
        "contract_id": "uquant-modular-governance-public-api-v1",
        "baseline_commit": BASELINE_COMMIT,
        "recorded_on": "2026-08-22",
        "contract_sha256": canonical_sha256(public_contract),
        "contract": public_contract,
    }
    _write_json(PUBLIC_API_PATH, public_payload)

    frozen_count, frozen_checksums_sha256 = _verify_frozen_data()
    data_manifest_path = ROOT / "data" / "frozen" / "DATA_MANIFEST.json"
    data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    universe_manifest_path = ROOT / "uquant" / "validation" / "resources" / "ai_universe_manifest.json"
    universe_manifest = json.loads(universe_manifest_path.read_text(encoding="utf-8"))
    universe = default_ai_universe()
    architecture = architecture_snapshot()
    initial_debt = measured_debt(architecture)
    temporary_allowlist = {
        category: [str(row["id"]) for row in rows]
        for category, rows in initial_debt.items()
    }

    first_replay, replay_wall_seconds, replay_peak_rss_kib = _measured_replay(REPLAY_SPECS[0])
    replays = [first_replay]
    replays.extend(
        [
            representative_replay(
                name=str(spec["name"]),
                start=str(spec["start"]),
                end=str(spec["end"]),
                symbols=tuple(str(symbol) for symbol in spec["symbols"]),
            )
            for spec in REPLAY_SPECS[1:]
        ]
    )

    uv_version = subprocess.run(
        ["uv", "--version"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    architecture_modules = architecture["modules"]
    architecture_functions = architecture["functions"]
    architecture_globals = architecture["module_globals"]
    architecture_type_ignores = architecture["type_ignores"]
    assert isinstance(architecture_modules, Mapping)
    assert isinstance(architecture_functions, list)
    assert isinstance(architecture_globals, list)
    assert isinstance(architecture_type_ignores, list)
    inventory = {
        "schema_version": 1,
        "inventory_id": "uquant-modular-governance-baseline-v1",
        "recorded_on": "2026-08-22",
        "baseline": {
            "commit": BASELINE_COMMIT,
            "branch": BASELINE_BRANCH,
            "origin_main": BASELINE_COMMIT,
            "baseline_worktree": BASELINE_WORKTREE,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_executable": sys.executable,
            "uv_version": uv_version.removeprefix("uv "),
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
            "platform": platform.platform(),
            "uv_lock_sha256": sha256_file(ROOT / "uv.lock"),
            "requirements_sha256": sha256_file(ROOT / "requirements.txt"),
            "code_fingerprint": code_fingerprint(),
            "production_source_surface": _source_surface(),
            "config": {
                "sha256": config_fingerprint(DEFAULT_CONFIG),
                "field_count": len(config_field_order),
            },
            "data": {
                "snapshot_id": data_manifest["snapshot_id"],
                "manifest_sha256": sha256_file(data_manifest_path),
                "sha256sums_sha256": sha256_file(ROOT / "data" / "frozen" / "SHA256SUMS"),
                "verified_csv_count": frozen_count,
                "verified_checksums_sha256": frozen_checksums_sha256,
                "tree_sha256": sha256_tree(ROOT / "data" / "frozen"),
            },
            "universe": {
                "manifest_id": universe_manifest["manifest_id"],
                "manifest_sha256": sha256_file(universe_manifest_path),
                "canonical_sha256": universe.sha256,
                "symbols": list(universe.symbols),
                "symbol_count": len(universe.symbols),
            },
        },
        "analysis_methodology": {
            "scope": "tracked production Python under uquant/",
            "module_lines": "physical UTF-8 lines measured with splitlines()",
            "function_lines": "first decorator through AST end_lineno, inclusive",
            "function_branch_points": (
                "if/if-expression/loop/except/comprehension/match branches plus boolean short-circuit edges; "
                "nested function and class bodies excluded"
            ),
            "fan_in_out": "unique internal uquant module import edges",
            "scc": "Tarjan strongly connected components over internal import edges",
            "cross_module_private_import": (
                "from-import of a leading-underscore name from another uquant module"
            ),
            "mutable_module_global": (
                "module binding initialized by a mutable container or observed with a container mutation site"
            ),
            "duplicate_helper": "same leading-underscore top-level function name in multiple modules",
            "tracked_file_authority": "git ls-tree -r --long of the immutable baseline commit",
        },
        "architecture": architecture,
        "architecture_summary": {
            "module_count": len(architecture_modules),
            "function_count": len(architecture_functions),
            "module_global_count": len(architecture_globals),
            "type_ignore_count": len(architecture_type_ignores),
            "debt_counts": {category: len(rows) for category, rows in initial_debt.items()},
        },
        "architecture_debt": {
            "policy": (
                "Initial entries are exact measured current debt. The temporary allowlist must be a subset of "
                "these immutable identities, must equal live debt, may not grow in measured severity, and must "
                "decrease monotonically to empty by final acceptance."
            ),
            "final_budgets": FINAL_BUDGETS,
            "initial": initial_debt,
            "temporary_allowlist": temporary_allowlist,
            "final_acceptance_allowlist": {
                category: [] for category in temporary_allowlist
            },
        },
        "tracked_file_authority": tracked_file_inventory(ROOT, BASELINE_COMMIT),
        "representative_replays": replays,
        "performance_baseline": {
            "representative_replay": {
                "scenario": first_replay["name"],
                "command": (
                    "ProductionEngine(data/frozen).backtest("
                    "symbols=[sz300308,sz300502,sz300394],start=2023-01-03,end=2023-01-20)"
                ),
                "wall_seconds": round(replay_wall_seconds, 6),
                "peak_rss_kib": replay_peak_rss_kib,
                "measurement": (
                    "fresh child process measured with time.perf_counter and "
                    "resource.RUSAGE_SELF.ru_maxrss"
                ),
            },
            "pytest_core_contracts": {
                "command": CORE_PYTEST_COMMAND,
                "tests_passed": 266,
                "result": "passed",
                "wall_seconds": round(pytest_wall_seconds, 6),
                "peak_rss_kib": pytest_peak_rss_kib,
                "measurement": (
                    "fresh child process measured with time.perf_counter and "
                    "resource.RUSAGE_CHILDREN.ru_maxrss"
                ),
            },
        },
        "public_api_contract": {
            "path": PUBLIC_API_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(PUBLIC_API_PATH),
            "contract_sha256": public_payload["contract_sha256"],
        },
    }
    _write_json(INVENTORY_PATH, inventory)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pytest-wall-seconds", required=True, type=float)
    parser.add_argument("--pytest-peak-rss-kib", required=True, type=int)
    arguments = parser.parse_args(argv)
    generate(
        pytest_wall_seconds=arguments.pytest_wall_seconds,
        pytest_peak_rss_kib=arguments.pytest_peak_rss_kib,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
