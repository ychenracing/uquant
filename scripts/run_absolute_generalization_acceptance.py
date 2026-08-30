#!/usr/bin/env python3
"""Run deterministic Absolute Generalization Acceptance transport shards."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from uquant.atomic_io import (
    atomic_write_bytes,
    validate_atomic_output_boundary,
    validate_atomic_output_path,
)
from uquant.contracts.strict_json import (
    canonical_json_bytes,
    canonical_json_sha256,
    strict_json_loads,
)
from uquant.validation.absolute_generalization import (
    ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    AbsoluteGeneralizationContract,
    AbsoluteGeneralizationScenario,
    AcceptanceReport,
    CellArtifact,
    aggregate_acceptance,
    build_error_shard_manifest,
    build_leave_one_out_scenarios,
    load_absolute_generalization_contract,
    run_champion_runtime_evidence,
    run_recovery_and_reachability_runtime_evidence,
    run_runtime_cell_artifact,
    seal_shard_manifest,
    validate_cell_artifact,
    validate_shard_manifest,
)

CANONICAL_SHARDS = (
    "champion",
    "loo-a",
    "loo-b",
    "loo-c",
    "loo-d",
    "loo-e",
    "loo-f",
    "recovery-and-reachability",
)
_SHARDS = (*CANONICAL_SHARDS, "final")
_LOO_SHARDS = frozenset(name for name in CANONICAL_SHARDS if name.startswith("loo-"))


@dataclass(frozen=True, slots=True)
class RunnerOptions:
    """Validated CLI transport selection without caller-owned policy facts."""

    shard: str
    symbol: str | None
    run_id: str
    run_attempt: int
    output: Path
    cache_dir: Path | None
    data_dir: Path | None
    shard_root: Path | None
    artifact_prefix: str | None
    upstream_result: str | None

    @property
    def mode(self) -> str:
        return "targeted" if self.symbol is not None else "canonical"


class _UniqueValueAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string} may be supplied only once")
        setattr(namespace, self.dest, values)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard", choices=_SHARDS, required=True, action=_UniqueValueAction
    )
    parser.add_argument("--symbol", action=_UniqueValueAction)
    parser.add_argument("--run-id", required=True, action=_UniqueValueAction)
    parser.add_argument(
        "--run-attempt", type=int, required=True, action=_UniqueValueAction
    )
    parser.add_argument("--output", type=Path, required=True, action=_UniqueValueAction)
    parser.add_argument("--cache-dir", type=Path, action=_UniqueValueAction)
    parser.add_argument("--data-dir", type=Path, action=_UniqueValueAction)
    parser.add_argument("--shard-root", type=Path, action=_UniqueValueAction)
    parser.add_argument("--artifact-prefix", action=_UniqueValueAction)
    parser.add_argument("--upstream-result", action=_UniqueValueAction)
    return parser


def parse_cli(argv: Sequence[str] | None = None) -> RunnerOptions:
    """Parse and reject ambiguous execution/final selector combinations."""

    arguments = list(argv) if argv is not None else None
    parser = _parser()
    parsed = parser.parse_args(arguments)
    options = RunnerOptions(
        shard=str(parsed.shard),
        symbol=None if parsed.symbol is None else str(parsed.symbol),
        run_id=str(parsed.run_id),
        run_attempt=int(parsed.run_attempt),
        output=Path(parsed.output),
        cache_dir=None if parsed.cache_dir is None else Path(parsed.cache_dir),
        data_dir=None if parsed.data_dir is None else Path(parsed.data_dir),
        shard_root=None if parsed.shard_root is None else Path(parsed.shard_root),
        artifact_prefix=(
            None if parsed.artifact_prefix is None else str(parsed.artifact_prefix)
        ),
        upstream_result=(
            None if parsed.upstream_result is None else str(parsed.upstream_result)
        ),
    )
    _validate_transport(options, parser)
    if options.symbol is not None:
        try:
            selected_scenarios(options, load_absolute_generalization_contract())
        except ValueError as exc:
            parser.error(str(exc))
    return options


def _validate_transport(
    options: RunnerOptions, parser: argparse.ArgumentParser
) -> None:
    if options.run_attempt < 1:
        parser.error("--run-attempt must be positive")
    if options.symbol is not None and options.shard not in _LOO_SHARDS:
        parser.error("--symbol is valid only for a fixed LOO shard")
    execution_values = (options.cache_dir, options.data_dir)
    final_values = (
        options.shard_root,
        options.artifact_prefix,
        options.upstream_result,
    )
    if options.shard == "final":
        if any(value is not None for value in execution_values) or any(
            value is None for value in final_values
        ):
            parser.error("final requires only final transport options")
    elif any(value is None for value in execution_values) or any(
        value is not None for value in final_values
    ):
        parser.error("execution shards require only cache and data directories")
    else:
        for path in cast(tuple[Path, Path], execution_values):
            physical = path if path.is_absolute() else Path.cwd() / path
            if any(part.is_symlink() for part in (physical, *physical.parents)):
                parser.error("cache and frozen data paths cannot contain symlinks")
        cache = cast(Path, options.cache_dir).resolve()
        data = cast(Path, options.data_dir).resolve()
        if cache == data or cache in data.parents or data in cache.parents:
            parser.error("cache and frozen data directories must be disjoint")


def selected_scenarios(
    options: RunnerOptions,
    contract: AbsoluteGeneralizationContract,
) -> tuple[AbsoluteGeneralizationScenario, ...]:
    """Select only contract-fixed scenarios, preserving contract order."""

    scenarios = tuple(
        item
        for item in build_leave_one_out_scenarios(contract)
        if item.shard == options.shard
    )
    if options.shard not in _LOO_SHARDS:
        if options.symbol is not None:
            raise ValueError("symbol selection is valid only for a fixed LOO shard")
        return ()
    if options.symbol is None:
        return scenarios
    selected = tuple(item for item in scenarios if item.removed_symbol == options.symbol)
    if len(selected) != 1:
        raise ValueError("symbol does not belong to the selected fixed LOO shard")
    return selected


def _git_identity() -> tuple[str, str]:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve current checkout identity")
    values: list[str] = []
    for revision in ("HEAD", "HEAD^{tree}"):
        try:
            result = subprocess.run(
                [executable, "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", revision],
                check=True,
                capture_output=True,
                text=True,
            )  # nosec B603
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("cannot resolve current checkout identity") from exc
        values.append(result.stdout.strip())
    return values[0], values[1]


def _cache_identity(
    scenario: AbsoluteGeneralizationScenario,
    contract: AbsoluteGeneralizationContract,
) -> str:
    head, tree = _git_identity()
    return canonical_json_sha256(
        {
            "schema_version": 1,
            "cell_id": scenario.cell_id,
            "removed_symbol": scenario.removed_symbol,
            "shard": scenario.shard,
            "window_start": scenario.window_start.isoformat(),
            "window_end": scenario.window_end.isoformat(),
            "head": head,
            "tree": tree,
            "scenario_contract_sha256": contract.canonical_sha256,
            "production_source_sha256": contract.candidate.production_source_sha256,
            "effective_config_sha256": contract.inputs.effective_config_sha256,
            "uv_lock_sha256": contract.inputs.uv_lock_sha256,
            "frozen_data_manifest_sha256": contract.inputs.frozen_data.manifest_sha256,
            "universe_sha256": contract.inputs.ai_universe_sha256,
            "execution_contract_identity": ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
        }
    )


def cache_path_for(
    cache_dir: Path,
    scenario: AbsoluteGeneralizationScenario,
    contract: AbsoluteGeneralizationContract,
) -> Path:
    """Return the sole exact-key cache path for one trusted scenario identity."""

    path = Path(cache_dir) / "cells" / f"{_cache_identity(scenario, contract)}.json"
    return validate_atomic_output_path(path)


def _cache_document(value: object) -> Mapping[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError("absolute generalization cache is malformed")
    raw = cast(Mapping[str, object], value)
    if set(raw) != {"schema_version", "cache_identity", "cell"}:
        raise ValueError("absolute generalization cache fields differ")
    if raw["schema_version"] != 1 or type(raw["cache_identity"]) is not str:
        raise ValueError("absolute generalization cache identity is malformed")
    return raw


def read_cached_cell(
    cache_dir: Path,
    scenario: AbsoluteGeneralizationScenario,
    contract: AbsoluteGeneralizationContract,
) -> CellArtifact | None:
    """Strictly decode and revalidate one exact-key cell cache entry."""

    path = cache_path_for(cache_dir, scenario, contract)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("absolute generalization cache path is a symlink or not a file")
    raw = _cache_document(strict_json_loads(path.read_bytes()))
    expected_identity = _cache_identity(scenario, contract)
    if raw["cache_identity"] != expected_identity:
        raise ValueError("absolute generalization cache identity differs")
    cell_raw = raw["cell"]
    if not isinstance(cell_raw, Mapping):
        raise ValueError("absolute generalization cached cell is malformed")
    artifact = validate_cell_artifact(cast(Mapping[str, object], cell_raw), contract)
    if artifact.cell_id != scenario.cell_id or artifact.removed_symbol != scenario.removed_symbol:
        raise ValueError("absolute generalization cached cell scenario differs")
    return artifact


def write_cached_cell(
    cache_dir: Path,
    artifact: CellArtifact,
    scenario: AbsoluteGeneralizationScenario,
    contract: AbsoluteGeneralizationContract,
) -> Path:
    """Atomically persist only a freshly revalidated complete cell artifact."""

    trusted = validate_cell_artifact(artifact.to_dict(), contract)
    if trusted.status != "COMPLETE" or (
        trusted.cell_id,
        trusted.removed_symbol,
    ) != (scenario.cell_id, scenario.removed_symbol):
        raise ValueError("absolute generalization cache accepts only its exact complete cell")
    identity = _cache_identity(scenario, contract)
    path = cache_path_for(cache_dir, scenario, contract)
    document = {
        "schema_version": 1,
        "cache_identity": identity,
        "cell": trusted.to_dict(),
    }
    atomic_write_bytes(path, canonical_json_bytes(document))
    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(
        canonical_json_bytes(document)
    ).digest():
        raise RuntimeError("absolute generalization cache write differs")
    return path


def build_loo_shard_manifest(
    options: RunnerOptions,
    artifacts: Sequence[CellArtifact],
    contract: AbsoluteGeneralizationContract,
) -> dict[str, object]:
    """Build and immediately revalidate one canonical or targeted LOO manifest."""

    scenarios = selected_scenarios(options, contract)
    trusted = tuple(
        validate_cell_artifact(artifact.to_dict(), contract) for artifact in artifacts
    )
    expected = tuple((item.cell_id, item.removed_symbol) for item in scenarios)
    observed = tuple((item.cell_id, item.removed_symbol) for item in trusted)
    if observed != expected:
        raise ValueError("absolute generalization LOO manifest cell order differs")
    head, tree = _git_identity()
    if any((item.identities.head, item.identities.tree) != (head, tree) for item in trusted):
        raise ValueError("absolute generalization LOO cell checkout identity differs")
    cells = [item.to_dict() for item in trusted]
    raw: dict[str, object] = {
        "schema_version": 1,
        "shard": options.shard,
        "mode": options.mode,
        "status": "COMPLETE",
        "upstream_success": True,
        "error": "",
        "run_id": options.run_id,
        "run_attempt": options.run_attempt,
        "head": head,
        "tree": tree,
        "scenario_contract_sha256": contract.canonical_sha256,
        "production_source_sha256": contract.candidate.production_source_sha256,
        "effective_config_sha256": contract.inputs.effective_config_sha256,
        "uv_lock_sha256": contract.inputs.uv_lock_sha256,
        "frozen_data_manifest_sha256": contract.inputs.frozen_data.manifest_sha256,
        "universe_sha256": contract.inputs.ai_universe_sha256,
        "cells": cells,
        "champion": None,
        "failed_grant_recovery": None,
        "historical_crowning": None,
        "terminal_scc": None,
        "repair_bounds": [],
        "cross_industry_crowning": None,
        "summary": {
            "cell_count": len(cells),
            "complete_cell_count": sum(item.status == "COMPLETE" for item in trusted),
            "replay_error_cell_count": sum(
                item.status == "REPLAY_ERROR" for item in trusted
            ),
        },
    }
    return seal_shard_manifest(raw, contract)


def _special_shard_manifest(
    options: RunnerOptions,
    payload: Mapping[str, object],
    contract: AbsoluteGeneralizationContract,
) -> dict[str, object]:
    head, tree = _git_identity()
    raw: dict[str, object] = {
        "schema_version": 1,
        "shard": options.shard,
        "mode": options.mode,
        "status": "COMPLETE",
        "upstream_success": True,
        "error": "",
        "run_id": options.run_id,
        "run_attempt": options.run_attempt,
        "head": head,
        "tree": tree,
        "scenario_contract_sha256": contract.canonical_sha256,
        "production_source_sha256": contract.candidate.production_source_sha256,
        "effective_config_sha256": contract.inputs.effective_config_sha256,
        "uv_lock_sha256": contract.inputs.uv_lock_sha256,
        "frozen_data_manifest_sha256": contract.inputs.frozen_data.manifest_sha256,
        "universe_sha256": contract.inputs.ai_universe_sha256,
        "cells": [],
        "champion": payload if options.shard == "champion" else None,
        "failed_grant_recovery": payload.get("failed_grant_recovery"),
        "historical_crowning": payload.get("historical_crowning"),
        "terminal_scc": payload.get("terminal_scc"),
        "repair_bounds": payload.get("repair_bounds", []),
        "cross_industry_crowning": payload.get("cross_industry_crowning"),
        "summary": {
            "cell_count": 0,
            "complete_cell_count": 0,
            "replay_error_cell_count": 0,
        },
    }
    return seal_shard_manifest(raw, contract)


def _write_execution_manifest(options: RunnerOptions, raw: Mapping[str, object]) -> None:
    cache_dir = cast(Path, options.cache_dir)
    data_dir = cast(Path, options.data_dir)
    validate_atomic_output_boundary(
        options.output, protected_roots=(cache_dir, data_dir)
    )
    atomic_write_bytes(options.output, canonical_json_bytes(raw))


def _readback_execution_manifest(
    options: RunnerOptions,
    contract: AbsoluteGeneralizationContract,
    expected: Mapping[str, object],
) -> Mapping[str, object]:
    raw = _read_manifest(options.output)
    validate_shard_manifest(raw, contract)
    if raw.get("run_id") != options.run_id or raw.get("run_attempt") != options.run_attempt:
        raise ValueError("absolute generalization execution run identity differs")
    if raw != expected:
        raise ValueError("absolute generalization execution manifest readback differs")
    return raw


def _run_execution(
    options: RunnerOptions, contract: AbsoluteGeneralizationContract
) -> int:
    root = Path(__file__).resolve().parents[1]
    cache_dir = cast(Path, options.cache_dir)
    data_dir = cast(Path, options.data_dir)
    if options.shard in _LOO_SHARDS:
        artifacts: list[CellArtifact] = []
        for scenario in selected_scenarios(options, contract):
            artifact = read_cached_cell(cache_dir, scenario, contract)
            if artifact is None:
                artifact = run_runtime_cell_artifact(
                    scenario,
                    contract,
                    root=root,
                    data_dir=data_dir,
                    cache_dir=cache_dir,
                )
                if artifact.status == "COMPLETE":
                    write_cached_cell(cache_dir, artifact, scenario, contract)
            artifacts.append(artifact)
        manifest = build_loo_shard_manifest(options, artifacts, contract)
    elif options.shard == "champion":
        champion_evidence = run_champion_runtime_evidence(
            root=root,
            data_dir=data_dir,
            cache_dir=cache_dir,
            contract=contract,
        )
        manifest = _special_shard_manifest(
            options, champion_evidence.to_manifest_payload(), contract
        )
    else:
        recovery_evidence = run_recovery_and_reachability_runtime_evidence(
            root=root,
            data_dir=data_dir,
            cache_dir=cache_dir,
            contract=contract,
        )
        manifest = _special_shard_manifest(
            options, recovery_evidence.to_manifest_payload(), contract
        )
    _write_execution_manifest(options, manifest)
    trusted = _readback_execution_manifest(options, contract, manifest)
    if trusted.get("status") != "COMPLETE":
        raise ValueError("absolute generalization execution manifest is incomplete")
    return 0


def _safe_segment(value: str, *, label: str) -> str:
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value):
        raise ValueError(f"absolute generalization {label} is unsafe")
    return value


def _read_manifest(path: Path) -> Mapping[str, object]:
    validate_atomic_output_path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("absolute generalization manifest is missing or unsafe")
    value = strict_json_loads(path.read_bytes())
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError("absolute generalization manifest is malformed")
    return cast(Mapping[str, object], value)


def _discover_final_manifests(
    options: RunnerOptions,
    contract: AbsoluteGeneralizationContract,
) -> tuple[Mapping[str, object], ...]:
    root = cast(Path, options.shard_root)
    validate_atomic_output_path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("absolute generalization shard root is missing or a symlink")
    prefix = _safe_segment(cast(str, options.artifact_prefix), label="artifact prefix")
    run_id = _safe_segment(options.run_id, label="run identity")
    expected_names = tuple(
        f"{prefix}-{run_id}-attempt-{options.run_attempt}-{shard}"
        for shard in CANONICAL_SHARDS
    )
    observed_names = tuple(sorted(item.name for item in root.iterdir()))
    if set(observed_names) != set(expected_names) or len(observed_names) != len(expected_names):
        raise ValueError("absolute generalization final artifact directory set differs")
    manifests: list[Mapping[str, object]] = []
    for shard, directory_name in zip(CANONICAL_SHARDS, expected_names, strict=True):
        directory = root / directory_name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("absolute generalization artifact directory is unsafe")
        entries = tuple(item.name for item in directory.iterdir())
        if entries != ("manifest.json",):
            raise ValueError("absolute generalization artifact contents differ")
        raw = _read_manifest(directory / "manifest.json")
        if raw.get("shard") != shard:
            raise ValueError("absolute generalization artifact shard differs")
        if raw.get("run_id") != options.run_id or raw.get("run_attempt") != options.run_attempt:
            raise ValueError("absolute generalization artifact run identity differs")
        manifests.append(raw)
    return tuple(manifests)


def _write_final_report(
    options: RunnerOptions,
    report: AcceptanceReport,
) -> None:
    root = cast(Path, options.shard_root)
    validate_atomic_output_boundary(options.output, protected_roots=(root,))
    atomic_write_bytes(options.output, canonical_json_bytes(report.to_dict()))


def run(options: RunnerOptions) -> int:
    """Run one validated transport selection and return its blocking exit status."""

    contract = load_absolute_generalization_contract()
    if options.shard != "final":
        try:
            return _run_execution(options, contract)
        except Exception as exc:
            head, tree = _git_identity()
            error = build_error_shard_manifest(
                shard=options.shard,
                mode=options.mode,
                error=f"execution failed: {type(exc).__name__}",
                run_id=options.run_id,
                run_attempt=options.run_attempt,
                head=head,
                tree=tree,
                contract=contract,
            )
            _write_execution_manifest(options, error)
            trusted = _readback_execution_manifest(options, contract, error)
            if trusted.get("status") != "ERROR":
                raise ValueError("absolute generalization error manifest differs") from exc
            return 1
    manifests = _discover_final_manifests(options, contract)
    result = cast(str, options.upstream_result)
    upstream_success = result == "success"
    report = aggregate_acceptance(
        manifests,
        contract,
        upstream_success=upstream_success,
        upstream_failure_codes=(() if upstream_success else (f"matrix-result={result}",)),
    )
    _write_final_report(options, report)
    return 0 if report.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested transport from the command line."""

    return run(parse_cli(argv))


__all__ = (
    "CANONICAL_SHARDS",
    "RunnerOptions",
    "build_loo_shard_manifest",
    "cache_path_for",
    "main",
    "parse_cli",
    "read_cached_cell",
    "run",
    "selected_scenarios",
    "write_cached_cell",
)


if __name__ == "__main__":
    raise SystemExit(main())
