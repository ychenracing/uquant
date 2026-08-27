"""Executable Task-5 reachability matrix and readback verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from uquant.atomic_io import atomic_write_text
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.universe import default_ai_universe

from .contract import StrategicEvidenceContract, load_contract
from .forced_owner_runner import verify_frozen_inputs
from .models import canonical_sha256
from .provenance import (
    build_provenance,
    canonical_json_bytes,
    read_gzip_shard,
    seal_payload,
)
from .reachability import (
    INITIAL_STATE_IDS,
    PATH_IDS,
    HistoricalCheckpoint,
    ReachabilityCellResult,
    ReachabilityCellSpec,
    ReachabilityState,
    SyntheticPath,
    build_diagnostic_observations,
    build_initial_states,
    build_synthetic_paths,
    enumerate_reachability_specs,
    extract_historical_checkpoints,
    path_after_checkpoint,
    read_reachability_shard,
    run_reachability_cell,
    write_reachability_shard,
)


def repository_root() -> Path:
    """Return the repository containing this executable module."""

    return Path(__file__).resolve().parents[2]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(root: Path) -> str:
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("Task 5 experiment commit is malformed")
    return commit


def build_executable_source_manifest(
    root: str | Path,
    *,
    require_clean: bool = True,
) -> dict[str, Any]:
    """Seal every executable research source and optionally bind exact HEAD bytes."""

    repository = Path(root).resolve()
    paths = [repository / "research" / "candidate_runner.py"]
    paths.extend(sorted((repository / "research" / "strategic_evidence").glob("*.py")))
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Task 5 source manifest paths are empty or duplicated")
    files: dict[str, str] = {}
    for path in sorted(paths):
        resolved = path.resolve()
        if not resolved.is_relative_to(repository) or not resolved.is_file():
            raise ValueError("Task 5 source manifest path is missing or escapes repository")
        files[resolved.relative_to(repository).as_posix()] = _sha256_file(resolved)
    if require_clean:
        relative_paths = tuple(files)
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--", *relative_paths],
            cwd=repository,
            text=True,
        )
        if status.strip():
            raise ValueError("Task 5 executable research source is dirty")
        head = _git_commit(repository)
        for relative, digest in files.items():
            committed = subprocess.check_output(
                ["git", "show", f"{head}:{relative}"],
                cwd=repository,
            )
            if hashlib.sha256(committed).hexdigest() != digest:
                raise ValueError("Task 5 executable source differs from exact HEAD")
    return {"files": files, "manifest_sha256": canonical_sha256({"files": files})}


def recompute_reachability_identities(
    root: str | Path,
    *,
    contract: StrategicEvidenceContract,
    research_source_sha256: str,
) -> dict[str, str]:
    """Recompute every Task-5 runtime identity from live bytes and contracts."""

    repository = Path(root).resolve()
    fixed = (
        repository / "pyproject.toml",
        repository / "requirements.txt",
        repository / "uv.lock",
        repository / "benchmarks" / "reference_registry.json",
        repository / "benchmarks" / "config_parameter_governance.json",
    )
    production_paths = (*fixed, *sorted((repository / "uquant").rglob("*.py")))
    production_files = {
        path.relative_to(repository).as_posix(): _sha256_file(path)
        for path in sorted(production_paths)
    }
    universe = default_ai_universe()
    industries = {
        symbol: universe.industry_of(symbol, contract.window["end"])
        for symbol in contract.canonical_universe
    }
    if any(industry == "unknown" for industry in industries.values()):
        raise ValueError("Task 5 canonical industry mapping is incomplete")
    return {
        "production_source_sha256": canonical_sha256({"files": production_files}),
        "research_source_sha256": research_source_sha256,
        "config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "data_manifest_sha256": _sha256_file(
            repository / "data" / "frozen" / "DATA_MANIFEST.json"
        ),
        "universe_sha256": canonical_sha256(
            {"symbols": list(contract.canonical_universe)}
        ),
        "industry_mapping_sha256": canonical_sha256(
            {"as_of": contract.window["end"], "industries": industries}
        ),
        "window_sha256": canonical_sha256(dict(contract.window)),
        "uv_lock_sha256": _sha256_file(repository / "uv.lock"),
    }


def select_specs(
    *,
    state_ids: Sequence[str] = INITIAL_STATE_IDS,
    path_ids: Sequence[str] = PATH_IDS,
) -> tuple[ReachabilityCellSpec, ...]:
    """Select a deterministic exact subset without admitting unknown identities."""

    if not state_ids or len(state_ids) != len(set(state_ids)):
        raise ValueError("Task 5 selected states are empty or duplicated")
    if not path_ids or len(path_ids) != len(set(path_ids)):
        raise ValueError("Task 5 selected paths are empty or duplicated")
    if not set(state_ids) <= set(INITIAL_STATE_IDS):
        raise ValueError("Task 5 selected state lies outside S01-S14")
    if not set(path_ids) <= set(PATH_IDS):
        raise ValueError("Task 5 selected path lies outside P01-P06")
    selected_states = set(state_ids)
    selected_paths = set(path_ids)
    return tuple(
        spec
        for spec in enumerate_reachability_specs()
        if spec.state_id in selected_states and spec.path_id in selected_paths
    )


def run_matrix(
    *,
    states: Iterable[ReachabilityState],
    paths: Iterable[SyntheticPath],
    specs: Iterable[ReachabilityCellSpec],
) -> tuple[ReachabilityCellResult, ...]:
    """Execute selected diagnostic cells while retaining each terminal outcome."""

    state_by_id = {state.state_id: state for state in states}
    path_by_id = {path.path_id: path for path in paths}
    selected = tuple(specs)
    missing = tuple(
        spec.cell_id
        for spec in selected
        if spec.state_id not in state_by_id or spec.path_id not in path_by_id
    )
    if missing:
        raise ValueError("reachability matrix input is missing")
    results: list[ReachabilityCellResult] = []
    for spec in selected:
        state = state_by_id[spec.state_id]
        path = path_after_checkpoint(
            state=state,
            path=path_by_id[spec.path_id],
        )
        results.append(
            run_reachability_cell(
                spec,
                state=state,
                path=path,
                observe=partial(build_diagnostic_observations, state=state, path=path),
            )
        )
    return tuple(results)


def _historical_checkpoints(path: Path | None) -> tuple[HistoricalCheckpoint, ...]:
    if path is None:
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Task 5 historical checkpoints are unreadable") from exc
    if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
        raise ValueError("Task 5 historical checkpoints must be an array of objects")
    return extract_historical_checkpoints(payload)


def _runtime_metadata(*, generated_at: str) -> dict[str, str]:
    uv = subprocess.check_output(["uv", "--version"], text=True).strip()
    if not uv:
        raise ValueError("Task 5 uv runtime version is empty")
    return {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "uv": uv,
        "generated_at": generated_at,
    }


def _resume_generated_at(output: Path) -> str | None:
    if not output.exists():
        return None
    payload = read_gzip_shard(output)
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("Task 5 resume provenance is malformed")
    generated_at = provenance.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        raise ValueError("Task 5 resume generated_at is malformed")
    return generated_at


def _scenario(
    *,
    contract: StrategicEvidenceContract,
    specs: Sequence[ReachabilityCellSpec],
    session_count: int,
    checkpoints: Sequence[HistoricalCheckpoint],
) -> dict[str, Any]:
    return {
        "task": "state-reachability-and-cash-vacancy",
        "evidence_class": "DIAGNOSTIC_ONLY",
        "contract_payload_sha256": contract.payload_sha256,
        "cells": [spec.cell_id for spec in specs],
        "session_count": session_count,
        "historical_checkpoints_sha256": canonical_sha256(
            {
                "checkpoints": [
                    {
                        "state_id": checkpoint.state_id,
                        "date": checkpoint.date,
                        "account_sha256": checkpoint.account_sha256,
                    }
                    for checkpoint in checkpoints
                ]
            }
        ),
        "future_holdout_boundary": contract.future_holdout_boundary,
        "synthetic_historical_return_claims": "FORBIDDEN",
    }


def execute(
    *,
    root: Path,
    contract_path: Path,
    output: Path,
    state_ids: Sequence[str],
    path_ids: Sequence[str],
    session_count: int,
    historical_checkpoints_path: Path | None,
    resume: bool,
) -> dict[str, Any]:
    """Execute or verify one exact-HEAD matrix selection and sealed shard."""

    contract = load_contract(contract_path)
    if tuple(contract.initial_state_ids) != INITIAL_STATE_IDS or tuple(contract.path_ids) != PATH_IDS:
        raise ValueError("Task 5 contract state/path coverage differs")
    verify_frozen_inputs(root, contract)
    source_manifest = build_executable_source_manifest(root, require_clean=True)
    research_sha = str(source_manifest["manifest_sha256"])
    observed_identities = recompute_reachability_identities(
        root,
        contract=contract,
        research_source_sha256=research_sha,
    )
    specs = select_specs(state_ids=state_ids, path_ids=path_ids)
    checkpoints = _historical_checkpoints(historical_checkpoints_path)
    scenario = _scenario(
        contract=contract,
        specs=specs,
        session_count=session_count,
        checkpoints=checkpoints,
    )
    resumed_generated_at = _resume_generated_at(output) if resume else None
    generated_at = resumed_generated_at or datetime.now(UTC).isoformat()
    provenance = build_provenance(
        contract,
        experiment_commit=_git_commit(root),
        research_source_sha256=research_sha,
        scenario=scenario,
        generated_at=generated_at,
        observed_identities=observed_identities,
        runtime_metadata=_runtime_metadata(generated_at=generated_at),
    )
    states = build_initial_states(
        checkpoints=checkpoints,
        initial_cash=1_000_000.0,
        synthetic_seed=contract.random_seed,
    )
    paths = build_synthetic_paths(
        seed=contract.random_seed,
        start="2024-01-02",
        session_count=session_count,
    )
    if resume and output.exists():
        rows = read_reachability_shard(
            output,
            expected_specs=specs,
            expected_states=states,
            expected_paths=paths,
            expected_provenance=provenance,
        )
    else:
        cells = run_matrix(states=states, paths=paths, specs=specs)
        output.parent.mkdir(parents=True, exist_ok=True)
        write_reachability_shard(
            str(output),
            cells=cells,
            provenance=provenance,
            expected_specs=specs,
            expected_states=states,
            expected_paths=paths,
        )
        rows = read_reachability_shard(
            str(output),
            expected_specs=specs,
            expected_states=states,
            expected_paths=paths,
            expected_provenance=provenance,
        )
    statuses = Counter(str(row["status"]) for row in rows)
    return seal_payload(
        {
            "schema_version": "uquant.strategic-evidence-reachability-summary.v1",
            "evidence_class": "DIAGNOSTIC_ONLY",
            "experiment_commit": provenance["experiment_commit"],
            "research_source_sha256": research_sha,
            "scenario_sha256": provenance["scenario_sha256"],
            "cell_count": len(rows),
            "statuses": dict(sorted(statuses.items())),
            "output_bytes_sha256": _sha256_file(output),
            "output_byte_size": output.stat().st_size,
            "output_row_count": len(rows),
            "synthetic_historical_return_claims": "FORBIDDEN",
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("benchmarks/strategic_evidence_closure_contract.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--state-id", action="append", choices=INITIAL_STATE_IDS)
    parser.add_argument("--path-id", action="append", choices=PATH_IDS)
    parser.add_argument("--session-count", type=int, default=60)
    parser.add_argument("--historical-checkpoints", type=Path)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for one selected sentinel or all 84 frozen cells."""

    args = _parser().parse_args(argv)
    root = args.repository_root.resolve()
    contract_path = args.contract
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    summary = execute(
        root=root,
        contract_path=contract_path,
        output=args.output.resolve(),
        state_ids=tuple(args.state_id or INITIAL_STATE_IDS),
        path_ids=tuple(args.path_id or PATH_IDS),
        session_count=args.session_count,
        historical_checkpoints_path=args.historical_checkpoints,
        resume=args.resume,
    )
    encoded = canonical_json_bytes(summary).decode("utf-8")
    if args.summary is not None:
        summary_path = args.summary.resolve()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(summary_path, encoded + "\n")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "build_executable_source_manifest",
    "execute",
    "main",
    "recompute_reachability_identities",
    "repository_root",
    "run_matrix",
    "select_specs",
)
