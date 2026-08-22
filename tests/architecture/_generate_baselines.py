"""Generate the Task 1 characterization contracts from the frozen baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd

from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import code_fingerprint
from uquant.validation.universe import default_ai_universe

from ._analysis import (
    FINAL_BUDGETS,
    INVENTORY_PATH,
    PUBLIC_API_PATH,
    ROOT,
    architecture_snapshot,
    canonical_sha256,
    git_python_sources,
    measured_debt,
    production_source_surface,
    public_api_snapshot,
    representative_replay,
    sha256_file,
    sha256_tree,
    tracked_file_inventory,
)
from ._baseline import BASELINE_COMMIT

BASELINE_BRANCH = "codex/uquant-modular-governance-20260822"

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
    "python",
    "-m",
    "pytest",
    "-q",
    "tests/test_config_contracts.py",
    "tests/test_account_broker_schema.py",
    "tests/test_engine_contracts.py",
    "tests/test_execution.py",
)
PRODUCTION_INPUT_PATHS = (
    "uquant",
    "data/frozen",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "benchmarks/reference_registry.json",
    "benchmarks/config_parameter_governance.json",
)


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def verify_generation_context(
    *, baseline_root: Path, baseline_commit: str, candidate_root: Path
) -> str:
    """Verify a candidate may characterize the independently anchored Git tree."""

    baseline_root = baseline_root.resolve()
    candidate_root = candidate_root.resolve()
    if baseline_commit != BASELINE_COMMIT:
        raise RuntimeError(
            f"baseline commit must match the reviewed Task 1 anchor {BASELINE_COMMIT}"
        )
    resolved = _git(baseline_root, "rev-parse", f"{baseline_commit}^{{commit}}")
    if resolved != BASELINE_COMMIT:
        raise RuntimeError(f"baseline commit did not resolve exactly: {resolved}")
    candidate_resolved = _git(candidate_root, "rev-parse", f"{baseline_commit}^{{commit}}")
    if candidate_resolved != BASELINE_COMMIT:
        raise RuntimeError("candidate repository cannot resolve the reviewed baseline commit")

    changed = _git(
        candidate_root,
        "diff",
        "--name-only",
        baseline_commit,
        "--",
        *PRODUCTION_INPUT_PATHS,
    )
    if changed:
        raise RuntimeError(
            "candidate production inputs differ from the reviewed baseline: "
            + ", ".join(changed.splitlines())
        )
    untracked = _git(
        candidate_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        *PRODUCTION_INPUT_PATHS,
    )
    if untracked:
        raise RuntimeError(
            "candidate has untracked production inputs: " + ", ".join(untracked.splitlines())
        )

    expected_sources = git_python_sources(baseline_root, baseline_commit)
    observed_sources = {
        path.relative_to(candidate_root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((candidate_root / "uquant").rglob("*.py"))
    }
    if observed_sources != expected_sources:
        raise RuntimeError("candidate production Python bytes differ from the baseline Git tree")
    return resolved


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


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise RuntimeError("pytest did not emit a machine-readable test suite")
    total = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    return {
        "total": total,
        "passed": total - failures - errors - skipped,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def _measured_core_pytest(*, environment: Mapping[str, object]) -> dict[str, object]:
    wrapper = """
import json
import resource
import subprocess
import sys
import time

command = json.loads(sys.argv[1])
started = time.perf_counter()
completed = subprocess.run(command, check=False, capture_output=True, text=True)
print(json.dumps({
    "exit_status": completed.returncode,
    "peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    "stderr": completed.stderr,
    "stdout": completed.stdout,
    "wall_seconds": time.perf_counter() - started,
}, allow_nan=False, sort_keys=True))
"""
    with tempfile.TemporaryDirectory(prefix="uquant-task1-pytest-") as directory:
        junit_path = Path(directory) / "pytest.xml"
        actual_command = [
            sys.executable,
            *CORE_PYTEST_COMMAND[1:],
            f"--junitxml={junit_path}",
        ]
        measured = subprocess.run(
            [sys.executable, "-c", wrapper, json.dumps(actual_command)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(measured.stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("pytest measurement returned a malformed payload")
        exit_status = int(payload["exit_status"])
        if not junit_path.is_file():
            raise RuntimeError(
                "pytest measurement did not retain its JUnit evidence: "
                + str(payload.get("stderr", ""))
            )
        counts = _junit_counts(junit_path)
        junit_sha256 = sha256_file(junit_path)
    if exit_status != 0 or counts["failures"] or counts["errors"]:
        raise RuntimeError(
            "core pytest cohort failed while generating the baseline: "
            + str(payload.get("stdout", ""))
            + str(payload.get("stderr", ""))
        )
    if counts["total"] <= 0 or counts["passed"] + counts["skipped"] != counts["total"]:
        raise RuntimeError(f"core pytest cohort returned invalid test counts: {counts}")
    stdout = str(payload["stdout"])
    stderr = str(payload["stderr"])
    peak_rss_kib = int(payload["peak_rss_kib"])
    wall_seconds = round(float(payload["wall_seconds"]), 6)
    raw_evidence = {
        "command": list(CORE_PYTEST_COMMAND),
        "environment": dict(environment),
        "exit_status": exit_status,
        "junit_sha256": junit_sha256,
        "peak_rss_kib": peak_rss_kib,
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "test_counts": counts,
        "wall_seconds": wall_seconds,
    }
    if peak_rss_kib <= 0 or wall_seconds <= 0:
        raise RuntimeError("pytest measurement returned non-positive timing or RSS evidence")
    return {
        "raw_evidence": raw_evidence,
        "evidence_sha256": canonical_sha256(raw_evidence),
        "measurement": (
            "generator-owned fresh child process; JUnit counts, exit status, command, "
            "environment, time.perf_counter wall time, and RUSAGE_CHILDREN peak RSS"
        ),
    }


def generate(
    *, baseline_root: Path, baseline_commit: str, output_root: Path = ROOT
) -> None:
    verify_generation_context(
        baseline_root=baseline_root,
        baseline_commit=baseline_commit,
        candidate_root=ROOT,
    )
    baseline_sources = git_python_sources(baseline_root, baseline_commit)
    public_api_path = output_root / PUBLIC_API_PATH.relative_to(ROOT)
    inventory_path = output_root / INVENTORY_PATH.relative_to(ROOT)

    public_contract = public_api_snapshot()
    flat_config = public_contract["flat_config_serialization"]
    assert isinstance(flat_config, Mapping)
    config_field_order = flat_config["field_order"]
    assert isinstance(config_field_order, list)
    public_payload = {
        "schema_version": 1,
        "contract_id": "uquant-modular-governance-public-api-v1",
        "baseline_commit": baseline_commit,
        "recorded_on": "2026-08-22",
        "contract_sha256": canonical_sha256(public_contract),
        "contract": public_contract,
    }
    _write_json(public_api_path, public_payload)

    frozen_count, frozen_checksums_sha256 = _verify_frozen_data()
    data_manifest_path = ROOT / "data" / "frozen" / "DATA_MANIFEST.json"
    data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    universe_manifest_path = ROOT / "uquant" / "validation" / "resources" / "ai_universe_manifest.json"
    universe_manifest = json.loads(universe_manifest_path.read_text(encoding="utf-8"))
    universe = default_ai_universe()
    architecture = architecture_snapshot(
        root=baseline_root,
        source_texts=baseline_sources,
    )
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
    environment = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "uv_lock_sha256": sha256_file(ROOT / "uv.lock"),
    }
    pytest_evidence = _measured_core_pytest(environment=environment)
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
            "commit": baseline_commit,
            "branch": BASELINE_BRANCH,
            "origin_main_at_freeze": baseline_commit,
            "source": "explicit immutable Git tree",
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_executable_name": Path(sys.executable).name,
            "uv_version": uv_version.removeprefix("uv "),
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
            "platform": platform.platform(),
            "uv_lock_sha256": sha256_file(ROOT / "uv.lock"),
            "requirements_sha256": sha256_file(ROOT / "requirements.txt"),
            "code_fingerprint": code_fingerprint(),
            "production_source_surface": production_source_surface(baseline_sources),
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
                "Initial entries are exact measured current debt. The temporary allowlist equals those "
                "immutable identities and remains the exact Task 1 allowance. Live debt may only be a "
                "subset of the initial identities, may not grow in measured severity, and must be empty at "
                "final acceptance."
            ),
            "final_budgets": FINAL_BUDGETS,
            "initial": initial_debt,
            "initial_sha256": canonical_sha256(initial_debt),
            "temporary_allowlist": temporary_allowlist,
            "final_acceptance_allowlist": {
                category: [] for category in temporary_allowlist
            },
        },
        "tracked_file_authority": tracked_file_inventory(baseline_root, baseline_commit),
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
            "pytest_core_contracts": pytest_evidence,
        },
        "public_api_contract": {
            "path": PUBLIC_API_PATH.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(public_api_path),
            "contract_sha256": public_payload["contract_sha256"],
        },
    }
    _write_json(inventory_path, inventory)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--output-root", default=ROOT, type=Path)
    arguments = parser.parse_args(argv)
    generate(
        baseline_root=arguments.baseline_root,
        baseline_commit=arguments.baseline_commit,
        output_root=arguments.output_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
