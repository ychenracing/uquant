"""Reproducible Task-3 forced-owner execution, resume, and readback entrypoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess  # nosec B404
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path, PurePosixPath
from typing import Any

from uquant.atomic_io import atomic_write_text
from uquant.config import DEFAULT_CONFIG, config_fingerprint

from .contract import StrategicEvidenceContract, load_contract
from .forced_owner import (
    COMMON_ACTIVATION_DATE,
    NATIVE_ELIGIBILITY_DATE,
    NO_NATIVE_ELIGIBILITY,
    ForcedOwnerCell,
    ForcedOwnerControl,
    enumerate_forced_owner_controls,
    first_native_eligibility,
    forced_owner_cell_from_compact,
    replay_trace_sha256,
    required_forced_owner_cell_ids,
    routes_canonically_equal,
    run_forced_owner_economic_cell,
    scan_native_eligibilities,
    select_negative_controls,
    validate_required_coverage,
    verify_forced_owner_trace_shard,
    write_forced_owner_trace_shard,
)
from .models import canonical_sha256
from .provenance import (
    build_provenance,
    seal_payload,
    validate_provenance,
    verify_sealed_payload,
)
from .replay import (
    ReplayRequest,
    ReplayResult,
    common_activation_date,
    common_activation_target_gross,
    run_replay,
    validate_replay_accounting,
)
from .trace import strip_intervention_provenance

_GENERATED_AT = "2026-08-26T00:00:00Z"
_TASK3_TEMP_ROOT = Path(tempfile.gettempdir()) / "uquant-strategic-evidence" / "task3"
_DEFAULT_TRACE_SHARD = _TASK3_TEMP_ROOT / "forced_owner_full_routes.jsonl.gz"
_DEFAULT_RESUME_DIR = _TASK3_TEMP_ROOT / "resume"
_DEFAULT_SUMMARY = Path(
    "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_full.json"
)
_DEFAULT_MANIFEST = Path(
    "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_manifest.json"
)
_RUNTIME_ROUTE_METADATA_FIELDS = frozenset({"path"})


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit(root: Path) -> str:
    commit = subprocess.check_output(  # nosec B603, B607
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if len(commit) != 40:
        raise ValueError("forced-owner experiment commit is malformed")
    return commit


def _research_source_sha256(root: Path) -> str:
    files = sorted((root / "research" / "strategic_evidence").glob("*.py"))
    if not files:
        raise ValueError("forced-owner research source is missing")
    return canonical_sha256(
        {
            str(path.relative_to(root)): _sha256_file(path)
            for path in files
        }
    )


def verify_frozen_inputs(
    root: str | Path,
    contract: StrategicEvidenceContract,
) -> dict[str, Any]:
    """Verify sealed data/config/runtime inputs and protected-path cleanliness."""

    repository = Path(root).resolve()
    identities = contract.raw.get("identities")
    if not isinstance(identities, Mapping):
        raise ValueError("strategic evidence input identities are missing")
    manifest_path = repository / "data" / "frozen" / "DATA_MANIFEST.json"
    observed_manifest_sha = _sha256_file(manifest_path)
    if observed_manifest_sha != identities.get("data_manifest_sha256"):
        raise ValueError("frozen data manifest identity differs from sealed v1")
    if _sha256_file(repository / "uv.lock") != identities.get("uv_lock_sha256"):
        raise ValueError("uv lock identity differs from sealed v1")
    if config_fingerprint(DEFAULT_CONFIG) != identities.get("config_sha256"):
        raise ValueError("production config identity differs from sealed v1")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("frozen data manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise ValueError("frozen data manifest is malformed")
    if manifest.get("snapshot_id") != identities.get("data_snapshot_id"):
        raise ValueError("frozen data snapshot identity differs from sealed v1")
    results = manifest.get("results")
    if not isinstance(results, list):
        raise ValueError("frozen data manifest results are malformed")
    manifest_rows: dict[str, Mapping[str, Any]] = {}
    for raw in results:
        if not isinstance(raw, Mapping):
            raise ValueError("frozen data manifest result is malformed")
        symbol = raw.get("symbol")
        if not isinstance(symbol, str) or not symbol or symbol in manifest_rows:
            raise ValueError("frozen data manifest symbol identifiers differ")
        manifest_rows[symbol] = raw
    expected_symbols = {"sh000300", "sh000682", *contract.canonical_universe}
    if set(manifest_rows) != expected_symbols:
        raise ValueError("frozen data manifest universe differs from sealed v1")
    checksum_path = repository / "data" / "frozen" / "SHA256SUMS"
    checksums: dict[str, str] = {}
    try:
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            digest, filename = line.split("  ", maxsplit=1)
            if filename in checksums:
                raise ValueError("duplicate frozen checksum filename")
            checksums[filename] = digest
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("frozen data checksums are unreadable") from exc
    expected_filenames = {f"{symbol}.csv" for symbol in expected_symbols}
    if set(checksums) != expected_filenames:
        raise ValueError("frozen data checksum coverage differs")
    for symbol in sorted(expected_symbols):
        filename = f"{symbol}.csv"
        path = repository / "data" / "frozen" / filename
        digest = _sha256_file(path)
        raw = manifest_rows[symbol]
        if digest != checksums[filename] or digest != raw.get("sha256"):
            raise ValueError(f"frozen data file identity differs: {symbol}")
        if raw.get("last_date") != contract.window["end"]:
            raise ValueError(f"frozen data window differs: {symbol}")
    protected = subprocess.check_output(  # nosec B603, B607
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "uquant",
            "data/frozen",
            "uv.lock",
            "benchmarks/strategic_evidence_closure_contract.json",
        ],
        cwd=repository,
        text=True,
    )
    if protected.strip():
        raise ValueError("forced-owner protected inputs are dirty")
    return {
        "data_manifest_sha256": observed_manifest_sha,
        "data_checksums_sha256": _sha256_file(checksum_path),
        "data_snapshot_id": manifest["snapshot_id"],
        "verified_data_files": len(expected_symbols),
        "config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "uv_lock_sha256": _sha256_file(repository / "uv.lock"),
        "protected_inputs_clean": True,
    }


def build_forced_owner_scenario(
    *,
    contract_payload_sha256: str,
    controls: Sequence[ForcedOwnerControl],
    universe: Sequence[str],
    window: Mapping[str, str],
    activation_date: str,
    target_gross: float,
    random_seed: int,
) -> dict[str, Any]:
    """Build the exact scenario identity shared by execution and resume shards."""

    return {
        "kind": "task3-forced-owner-full-route-matrix",
        "contract_payload_sha256": contract_payload_sha256,
        "universe": list(universe),
        "window": dict(window),
        "activation_date": activation_date,
        "target_gross": target_gross,
        "random_seed": random_seed,
        "controls": [asdict(control) for control in controls],
        "required_cell_ids": list(required_forced_owner_cell_ids(controls)),
    }


def economically_compatible_provenance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    """Allow rebind only when every economic/runtime/scenario identity is exact."""

    first = validate_provenance(left)
    second = validate_provenance(right)
    behavior_neutral_fields = {"experiment_commit", "research_source_sha256"}
    return all(
        first[field] == second[field]
        for field in first.keys() - behavior_neutral_fields
    )


def economically_compatible_selection_evidence(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    """Recognize only the JSON-safe absent-to-empty evidence metadata migration."""

    first = dict(left)
    second = dict(right)
    if "nonfinite_fields" not in first and second.get("nonfinite_fields") == []:
        first["nonfinite_fields"] = []
    return first == second


def _resume_path(resume_dir: Path, *, index: int, cell_id: str) -> Path:
    suffix = hashlib.sha256(cell_id.encode("utf-8")).hexdigest()[:16]
    return resume_dir / f"{index:02d}-{suffix}.jsonl.gz"


def _expected_request(
    *,
    symbols: tuple[str, ...],
    control: ForcedOwnerControl,
    mode: str,
    intervention_date: str,
    start: str,
    end: str,
) -> ReplayRequest:
    return ReplayRequest(
        symbols=symbols,
        start=start,
        end=end,
        scenario=f"forced-owner:{control.control_id}:{control.owner}:{mode}",
        intervention_date=intervention_date,
    )


def _load_resumed_cell(
    path: Path,
    *,
    provenance: Mapping[str, Any],
    control: ForcedOwnerControl,
    mode: str,
    intervention_date: str | None,
    selection_evidence: Mapping[str, Any],
    expected_request: ReplayRequest | None,
    allow_provenance_rebind: bool,
) -> tuple[ForcedOwnerCell, ReplayResult | None]:
    readback = verify_forced_owner_trace_shard(
        path,
    )
    if readback.provenance != provenance and (
        not allow_provenance_rebind
        or not economically_compatible_provenance(readback.provenance, provenance)
    ):
        raise ValueError("forced-owner resume shard provenance is not economically compatible")
    if len(readback.cells) != 1:
        raise ValueError("forced-owner resume shard must contain exactly one cell")
    cell = readback.cells[0]
    selection_matches = cell.selection_evidence == selection_evidence
    if allow_provenance_rebind and not selection_matches:
        selection_matches = economically_compatible_selection_evidence(
            cell.selection_evidence,
            selection_evidence,
        )
    if (
        cell.control_id != control.control_id
        or cell.owner != control.owner
        or cell.mode != mode
        or cell.intervention_date != intervention_date
        or not selection_matches
    ):
        raise ValueError("forced-owner resume shard cell scenario differs")
    if cell.selection_evidence != selection_evidence:
        cell = replace(cell, selection_evidence=dict(selection_evidence))
    result = readback.results.get(cell.cell_id)
    if expected_request is None:
        if result is not None or cell.status != NO_NATIVE_ELIGIBILITY:
            raise ValueError("forced-owner no-native resume shard differs")
    elif result is None or result.request != expected_request:
        raise ValueError("forced-owner resume shard replay request differs")
    if result is not None and result.status == "SUCCESS":
        validate_replay_accounting(result)
    return cell, result


def _execute_or_resume_cell(
    *,
    data_dir: Path,
    symbols: tuple[str, ...],
    control: ForcedOwnerControl,
    mode: str,
    intervention_date: str | None,
    target_gross: float,
    selection_evidence: Mapping[str, Any],
    start: str,
    end: str,
    provenance: Mapping[str, Any],
    checkpoint_path: Path,
    resume_source_path: Path | None,
    resume: bool,
) -> tuple[ForcedOwnerCell, ReplayResult | None, bool, bool]:
    expected_request = (
        None
        if intervention_date is None
        else _expected_request(
            symbols=symbols,
            control=control,
            mode=mode,
            intervention_date=intervention_date,
            start=start,
            end=end,
        )
    )
    if resume and checkpoint_path.exists():
        cell, result = _load_resumed_cell(
            checkpoint_path,
            provenance=provenance,
            control=control,
            mode=mode,
            intervention_date=intervention_date,
            selection_evidence=selection_evidence,
            expected_request=expected_request,
            allow_provenance_rebind=False,
        )
        return cell, result, True, False
    if resume and resume_source_path is not None and resume_source_path.exists():
        cell, result = _load_resumed_cell(
            resume_source_path,
            provenance=provenance,
            control=control,
            mode=mode,
            intervention_date=intervention_date,
            selection_evidence=selection_evidence,
            expected_request=expected_request,
            allow_provenance_rebind=True,
        )
        write_forced_owner_trace_shard(
            checkpoint_path,
            cells=(cell,),
            results={} if result is None else {cell.cell_id: result},
            provenance=provenance,
        )
        return cell, result, True, True
    if checkpoint_path.exists():
        raise ValueError(
            f"forced-owner checkpoint exists; pass --resume or use a fresh directory: {checkpoint_path}"
        )
    if intervention_date is None:
        cell = ForcedOwnerCell.no_native(
            control_id=control.control_id,
            owner=control.owner,
            selection_evidence=selection_evidence,
        )
        result = None
    else:
        cell, result = run_forced_owner_economic_cell(
            data_dir,
            control_id=control.control_id,
            symbols=symbols,
            owner=control.owner,
            mode=mode,
            date=intervention_date,
            target_gross=target_gross,
            selection_evidence=selection_evidence,
            start=start,
            end=end,
            cfg=DEFAULT_CONFIG,
        )
    write_forced_owner_trace_shard(
        checkpoint_path,
        cells=(cell,),
        results={} if result is None else {cell.cell_id: result},
        provenance=provenance,
    )
    return cell, result, False, False


def _baseline_reproduction(
    baseline: ReplayResult,
    *,
    cells: Sequence[ForcedOwnerCell],
    results: Mapping[str, ReplayResult],
) -> dict[str, Any]:
    cell = next(
        (
            item
            for item in cells
            if item.control_id == "POSITIVE_CONTROL:sz300308"
            and item.mode == COMMON_ACTIVATION_DATE
        ),
        None,
    )
    if cell is None:
        raise ValueError("forced-owner baseline reproduction cell is absent")
    forced = results.get(cell.cell_id)
    if forced is None or baseline.status != "SUCCESS" or forced.status != "SUCCESS":
        raise ValueError("forced-owner baseline reproduction did not complete")
    validate_replay_accounting(baseline)
    validate_replay_accounting(forced)
    base_rows = strip_intervention_provenance(baseline.trace)
    forced_rows = strip_intervention_provenance(forced.trace)
    equality = {
        "route": routes_canonically_equal(base_rows, forced_rows),
        "metrics": baseline.metrics == forced.metrics,
        "account": baseline.trace[-1].account_sha256 == forced.trace[-1].account_sha256,
        "targets": [row.targets for row in base_rows] == [row.targets for row in forced_rows],
        "orders": [row.orders for row in base_rows] == [row.orders for row in forced_rows],
        "fills": [row.fills for row in base_rows] == [row.fills for row in forced_rows],
        "equity": [row.equity for row in base_rows] == [row.equity for row in forced_rows],
    }
    if not all(equality.values()):
        raise ValueError("forced-owner sz300308 baseline reproduction differs")
    return {
        "cell_id": cell.cell_id,
        "equality": equality,
        "baseline_final_account_sha256": baseline.trace[-1].account_sha256,
        "forced_final_account_sha256": forced.trace[-1].account_sha256,
        "baseline_trace_sha256": replay_trace_sha256(baseline),
        "forced_trace_sha256": replay_trace_sha256(forced),
        "baseline_metrics_sha256": canonical_sha256({"metrics": dict(baseline.metrics)}),
        "forced_metrics_sha256": canonical_sha256({"metrics": dict(forced.metrics)}),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n",
    )


def _portable_route_identity(route_metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Exclude runtime locators from the sealed external-route identity."""

    return {
        key: value
        for key, value in route_metadata.items()
        if key not in _RUNTIME_ROUTE_METADATA_FIELDS
    }


def _relative_artifact_identity(
    repository: Path,
    artifact: Path,
    *,
    label: str,
) -> str:
    """Return one canonical repository-relative POSIX artifact identity."""

    try:
        relative = artifact.resolve().relative_to(repository.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must be inside the repository") from exc
    identity = relative.as_posix()
    if not identity or identity == ".":
        raise ValueError(f"{label} identity is malformed")
    return identity


def _resolve_artifact_identity(
    repository: Path,
    value: object,
    *,
    label: str,
) -> Path:
    """Resolve a canonical repository-relative POSIX identity under root."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} identity is malformed")
    identity = PurePosixPath(value)
    if (
        identity.is_absolute()
        or identity.as_posix() != value
        or any(part in {".", ".."} for part in identity.parts)
    ):
        raise ValueError(f"{label} identity is not repository-relative POSIX")
    resolved = (repository.resolve() / Path(*identity.parts)).resolve()
    if not resolved.is_relative_to(repository.resolve()):
        raise ValueError(f"{label} identity escapes the repository")
    return resolved


def _write_summary_and_manifest(
    *,
    repository: Path,
    summary_path: Path,
    manifest_path: Path,
    summary_payload: Mapping[str, Any],
    route_metadata: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    portable_route_identity = _portable_route_identity(route_metadata)
    summary = seal_payload(
        {
            **summary_payload,
            "route_shard": portable_route_identity,
            "large_trace_absence_reason": (
                "full route shard identity is sealed; runtime locator is supplied separately"
            ),
        }
    )
    _write_json(summary_path, summary)
    observed_summary = verify_sealed_payload(
        json.loads(summary_path.read_text(encoding="utf-8")),
        label="checkpoint3 forced-owner summary",
    )
    if observed_summary != summary:
        raise ValueError("checkpoint3 forced-owner summary readback differs")
    manifest = seal_payload(
        {
            "schema_version": 1,
            "checkpoint": "TASK3_FORCED_OWNER_FULL_ROUTE",
            "summary": {
                "path": _relative_artifact_identity(
                    repository,
                    summary_path,
                    label="checkpoint3 forced-owner summary",
                ),
                "byte_size": summary_path.stat().st_size,
                "bytes_sha256": _sha256_file(summary_path),
                "payload_sha256": summary["payload_sha256"],
            },
            "route_shard": portable_route_identity,
        }
    )
    _write_json(manifest_path, manifest)
    observed_manifest = verify_sealed_payload(
        json.loads(manifest_path.read_text(encoding="utf-8")),
        label="checkpoint3 forced-owner manifest",
    )
    if observed_manifest != manifest:
        raise ValueError("checkpoint3 forced-owner manifest readback differs")
    return summary, manifest


def execute_forced_owner_matrix(
    root: str | Path,
    *,
    summary_path: str | Path,
    manifest_path: str | Path,
    trace_shard_path: str | Path,
    resume_dir: str | Path,
    resume_from_dir: str | Path | None,
    resume: bool,
) -> dict[str, Any]:
    """Execute or resume all 16 production-backed Task-3 cells and seal outputs."""

    started = time.monotonic()
    repository = Path(root).resolve()
    summary_target = Path(summary_path)
    if not summary_target.is_absolute():
        summary_target = repository / summary_target
    manifest_target = Path(manifest_path)
    if not manifest_target.is_absolute():
        manifest_target = repository / manifest_target
    trace_target = Path(trace_shard_path).resolve()
    resume_target = Path(resume_dir).resolve()
    resume_source = None if resume_from_dir is None else Path(resume_from_dir).resolve()
    if (
        trace_target.is_relative_to(repository)
        or resume_target.is_relative_to(repository)
        or (resume_source is not None and resume_source.is_relative_to(repository))
    ):
        raise ValueError("large forced-owner route and resume shards must remain outside Git")
    contract = load_contract(
        repository / "benchmarks" / "strategic_evidence_closure_contract.json"
    )
    input_verification = verify_frozen_inputs(repository, contract)
    symbols = contract.canonical_universe
    start, end = contract.window["start"], contract.window["end"]
    baseline = run_replay(
        repository / "data" / "frozen",
        ReplayRequest(symbols=symbols, start=start, end=end),
    )
    if baseline.status != "SUCCESS":
        raise ValueError(f"forced-owner baseline failed: {baseline.status}: {baseline.error}")
    validate_replay_accounting(baseline)
    activation = common_activation_date(baseline)
    target_gross = common_activation_target_gross(baseline)
    scans = scan_native_eligibilities(
        repository / "data" / "frozen",
        symbols=symbols,
        owners=symbols,
        start=start,
        end=end,
    )
    activation_observations = tuple(
        item
        for owner in symbols
        for item in scans[owner]
        if item.date == activation
    )
    if len({item.symbol for item in activation_observations}) != len(
        activation_observations
    ):
        raise ValueError("activation-date eligibility observations contain duplicate symbols")
    negatives = select_negative_controls(activation_observations)
    controls = enumerate_forced_owner_controls(
        positive_controls=contract.positive_controls,
        negative_controls=negatives,
    )
    scenario = build_forced_owner_scenario(
        contract_payload_sha256=contract.payload_sha256,
        controls=controls,
        universe=symbols,
        window=contract.window,
        activation_date=activation,
        target_gross=target_gross,
        random_seed=contract.random_seed,
    )
    provenance = build_provenance(
        contract,
        experiment_commit=_git_commit(repository),
        research_source_sha256=_research_source_sha256(repository),
        scenario=scenario,
        generated_at=_GENERATED_AT,
    )
    resume_target.mkdir(parents=True, exist_ok=True)
    trace_target.parent.mkdir(parents=True, exist_ok=True)
    cells: list[ForcedOwnerCell] = []
    results: dict[str, ReplayResult] = {}
    reused = 0
    rebound = 0
    executed = 0
    checkpoint_metadata: dict[str, Mapping[str, Any]] = {}
    cell_index = 0
    for control in controls:
        native = first_native_eligibility(scans[control.owner])
        specifications = (
            (
                COMMON_ACTIVATION_DATE,
                activation,
                {
                    "baseline_activation_date": activation,
                    "baseline_target_gross": target_gross,
                    "owner_role": control.owner_role,
                },
            ),
            (
                NATIVE_ELIGIBILITY_DATE,
                None if native is None else native.date,
                (
                    {
                        "daily_scan_sessions": len(scans[control.owner]),
                        "first_native_eligibility": None,
                        "owner_role": control.owner_role,
                    }
                    if native is None
                    else {**native.evidence(), "owner_role": control.owner_role}
                ),
            ),
        )
        for mode, intervention_date, selection_evidence in specifications:
            checkpoint_path = _resume_path(
                resume_target,
                index=cell_index,
                cell_id=f"{control.control_id}:{control.owner}:{mode}",
            )
            source_path = (
                None
                if resume_source is None
                else _resume_path(
                    resume_source,
                    index=cell_index,
                    cell_id=f"{control.control_id}:{control.owner}:{mode}",
                )
            )
            cell, result, was_reused, was_rebound = _execute_or_resume_cell(
                data_dir=repository / "data" / "frozen",
                symbols=symbols,
                control=control,
                mode=mode,
                intervention_date=intervention_date,
                target_gross=target_gross,
                selection_evidence=selection_evidence,
                start=start,
                end=end,
                provenance=provenance,
                checkpoint_path=checkpoint_path,
                resume_source_path=source_path,
                resume=resume,
            )
            cells.append(cell)
            if result is not None:
                results[cell.cell_id] = result
            reused += int(was_reused)
            rebound += int(was_rebound)
            executed += int(not was_reused)
            checkpoint_metadata[cell.cell_id] = verify_forced_owner_trace_shard(
                checkpoint_path,
                expected_cells=(cell,),
                expected_provenance=provenance,
            ).metadata
            print(
                json.dumps(
                    {
                        "cell": cell.cell_id,
                        "index": cell_index + 1,
                        "of": 16,
                        "resumed": was_reused,
                        "status": cell.status,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            cell_index += 1
    validate_required_coverage(cells, controls=controls)
    reproduction = _baseline_reproduction(baseline, cells=cells, results=results)
    route_metadata = write_forced_owner_trace_shard(
        trace_target,
        cells=cells,
        results=results,
        provenance=provenance,
    )
    final_readback = verify_forced_owner_trace_shard(
        trace_target,
        expected_cells=cells,
        expected_provenance=provenance,
    )
    if dict(final_readback.metadata) != route_metadata:
        raise ValueError("forced-owner final route shard verifier metadata differs")
    selected_evidence = {
        role: next(item.evidence() for item in activation_observations if item.symbol == owner)
        for role, owner in negatives.items()
    }
    summary_payload = {
        "schema_version": 2,
        "checkpoint": "TASK3_FORCED_OWNER_FULL_ROUTE",
        "contract_payload_sha256": contract.payload_sha256,
        "provenance": provenance,
        "input_verification": input_verification,
        "window": {**contract.window, "future_holdout_boundary": contract.future_holdout_boundary},
        "universe": list(symbols),
        "activation": {
            "date": activation,
            "target_gross": target_gross,
            "observation_count": len(activation_observations),
            "observations_sha256": canonical_sha256(
                {"observations": [item.evidence() for item in activation_observations]}
            ),
        },
        "negative_controls_at_activation": negatives,
        "negative_control_evidence": selected_evidence,
        "controls": [asdict(control) for control in controls],
        "required_cell_ids": list(required_forced_owner_cell_ids(controls)),
        "status_counts": dict(sorted(Counter(cell.status for cell in cells).items())),
        "baseline": {
            "status": baseline.status,
            "activation_date": activation,
            "target_gross": target_gross,
            "final_account_sha256": baseline.trace[-1].account_sha256,
            "trace_sha256": replay_trace_sha256(baseline),
            "metrics_sha256": canonical_sha256({"metrics": dict(baseline.metrics)}),
            "total_return": baseline.metrics.get("total_return"),
        },
        "baseline_reproduction": reproduction,
        "cells": [cell.compact() for cell in cells],
        "route_shard": route_metadata,
        "resume": {
            "executed_cell_count": executed,
            "reused_cell_count": reused,
            "rebound_cell_count": rebound,
            "checkpoint_count": len(checkpoint_metadata),
            "checkpoint_identities_sha256": canonical_sha256(
                {"checkpoints": checkpoint_metadata}
            ),
        },
        "large_traces_committed": False,
        "large_trace_absence_reason": "full route shard is sealed at the external path above",
        "execution_entrypoint": "python -m research.strategic_evidence.forced_owner run --resume",
    }
    summary, manifest = _write_summary_and_manifest(
        repository=repository,
        summary_path=summary_target,
        manifest_path=manifest_target,
        summary_payload=summary_payload,
        route_metadata=route_metadata,
    )
    verification = verify_task3_outputs(
        repository,
        summary_path=summary_target,
        manifest_path=manifest_target,
        trace_shard_path=trace_target,
    )
    return {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "summary_payload_sha256": summary["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "route_shard": route_metadata,
        "status_counts": summary["status_counts"],
        "verification": verification,
    }


def _control_from_mapping(value: object) -> ForcedOwnerControl:
    if not isinstance(value, Mapping) or set(value) != {
        "control_id",
        "owner",
        "owner_role",
    }:
        raise ValueError("checkpoint3 forced-owner control is malformed")
    return ForcedOwnerControl(
        control_id=str(value["control_id"]),
        owner=str(value["owner"]),
        owner_role=str(value["owner_role"]),
    )


def verify_task3_outputs(
    root: str | Path,
    *,
    summary_path: str | Path,
    manifest_path: str | Path,
    trace_shard_path: str | Path,
) -> dict[str, Any]:
    """Separately verify compact seals, 16 IDs, route linkage, and byte identities."""

    repository = Path(root).resolve()
    summary_target = Path(summary_path)
    if not summary_target.is_absolute():
        summary_target = repository / summary_target
    manifest_target = Path(manifest_path)
    if not manifest_target.is_absolute():
        manifest_target = repository / manifest_target
    summary = verify_sealed_payload(
        json.loads(summary_target.read_text(encoding="utf-8")),
        label="checkpoint3 forced-owner summary",
    )
    manifest = verify_sealed_payload(
        json.loads(manifest_target.read_text(encoding="utf-8")),
        label="checkpoint3 forced-owner manifest",
    )
    contract = load_contract(
        repository / "benchmarks" / "strategic_evidence_closure_contract.json"
    )
    if summary.get("contract_payload_sha256") != contract.payload_sha256:
        raise ValueError("checkpoint3 forced-owner contract identity differs")
    raw_controls = summary.get("controls")
    raw_cells = summary.get("cells")
    provenance = summary.get("provenance")
    if (
        not isinstance(raw_controls, list)
        or not isinstance(raw_cells, list)
        or not isinstance(provenance, Mapping)
    ):
        raise ValueError("checkpoint3 forced-owner summary shape differs")
    controls = tuple(_control_from_mapping(value) for value in raw_controls)
    cells = tuple(forced_owner_cell_from_compact(value) for value in raw_cells)
    if summary.get("required_cell_ids") != list(required_forced_owner_cell_ids(controls)):
        raise ValueError("checkpoint3 forced-owner required cell identities differ")
    validate_required_coverage(cells, controls=controls)
    expected_counts = dict(sorted(Counter(cell.status for cell in cells).items()))
    if summary.get("status_counts") != expected_counts:
        raise ValueError("checkpoint3 forced-owner status counts differ")
    validated_provenance = validate_provenance(provenance)
    shard_readback = verify_forced_owner_trace_shard(
        trace_shard_path,
        expected_cells=cells,
        expected_provenance=validated_provenance,
    )
    route_metadata = dict(shard_readback.metadata)
    portable_route_identity = _portable_route_identity(route_metadata)
    if summary.get("route_shard") != portable_route_identity:
        raise ValueError("checkpoint3 forced-owner route manifest differs")
    manifest_summary = manifest.get("summary")
    if not isinstance(manifest_summary, Mapping):
        raise ValueError("checkpoint3 forced-owner compact summary identity differs")
    manifest_summary_target = _resolve_artifact_identity(
        repository,
        manifest_summary.get("path"),
        label="checkpoint3 forced-owner summary",
    )
    if manifest_summary_target != summary_target.resolve():
        raise ValueError("checkpoint3 forced-owner compact summary path differs")
    expected_manifest_summary = {
        "path": _relative_artifact_identity(
            repository,
            summary_target,
            label="checkpoint3 forced-owner summary",
        ),
        "byte_size": summary_target.stat().st_size,
        "bytes_sha256": _sha256_file(summary_target),
        "payload_sha256": summary["payload_sha256"],
    }
    if manifest.get("summary") != expected_manifest_summary:
        raise ValueError("checkpoint3 forced-owner compact summary identity differs")
    if manifest.get("route_shard") != portable_route_identity:
        raise ValueError("checkpoint3 forced-owner external route identity differs")
    reproduction = summary.get("baseline_reproduction")
    if (
        not isinstance(reproduction, Mapping)
        or not isinstance(reproduction.get("equality"), Mapping)
        or not all(reproduction["equality"].values())
    ):
        raise ValueError("checkpoint3 forced-owner baseline reproduction differs")
    return {
        "summary_payload_sha256": summary["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "route_bytes_sha256": route_metadata["bytes_sha256"],
        "route_row_count": route_metadata["row_count"],
        "route_cell_count": route_metadata["cell_count"],
        "cell_count": len(cells),
        "status_counts": expected_counts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="execute or resume all Task-3 cells")
    verify_parser = subparsers.add_parser("verify", help="verify committed compact and external routes")
    for item in (run_parser, verify_parser):
        item.add_argument("--root", default=".")
        item.add_argument("--summary", default=str(_DEFAULT_SUMMARY))
        item.add_argument("--manifest", default=str(_DEFAULT_MANIFEST))
        item.add_argument("--trace-shard", default=str(_DEFAULT_TRACE_SHARD))
    run_parser.add_argument("--resume-dir", default=str(_DEFAULT_RESUME_DIR))
    run_parser.add_argument("--resume-from-dir")
    run_parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the requested fail-closed Task-3 command."""

    args = _parser().parse_args(argv)
    if args.command == "run":
        result = execute_forced_owner_matrix(
            args.root,
            summary_path=args.summary,
            manifest_path=args.manifest,
            trace_shard_path=args.trace_shard,
            resume_dir=args.resume_dir,
            resume_from_dir=args.resume_from_dir,
            resume=args.resume,
        )
    else:
        result = verify_task3_outputs(
            args.root,
            summary_path=args.summary,
            manifest_path=args.manifest,
            trace_shard_path=args.trace_shard,
        )
    print(json.dumps(result, allow_nan=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "build_forced_owner_scenario",
    "economically_compatible_provenance",
    "economically_compatible_selection_evidence",
    "execute_forced_owner_matrix",
    "main",
    "verify_frozen_inputs",
    "verify_task3_outputs",
)
