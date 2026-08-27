"""Assemble and validate compact strategic evidence without changing economics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uquant.atomic_io import atomic_write_text

from .absolute_policy import AbsolutePolicyResult, evaluate_absolute_policy
from .contract import load_contract
from .provenance import (
    canonical_json_bytes,
    read_gzip_shard,
    seal_payload,
    verify_sealed_payload,
)


_ARTIFACT_NAMES = (
    "README.md",
    "analysis.md",
    "compact_summary.json",
    "evidence_manifest.json",
)
_TASK5_LOGICAL_PATH = (
    "artifacts/strategic_evidence_closure/external/"
    "checkpoint5_state_reachability_84.jsonl.gz"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_sealed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    return verify_sealed_payload(decoded, label=label)


def _repository_relative(root: Path, path: Path, *, label: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} lies outside repository") from exc
    return relative.as_posix()


def _source_identity(root: Path, path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "logical_path": _repository_relative(root, path, label="source artifact"),
        "byte_size": path.stat().st_size,
        "bytes_sha256": _sha256_file(path),
        "payload_sha256": payload["payload_sha256"],
        "schema_version": payload.get("schema_version"),
    }


def _external_identity(
    *,
    logical_path: str,
    expected: Mapping[str, Any],
    physical_path: Path | None,
) -> dict[str, Any]:
    expected_sha = expected.get("bytes_sha256") or expected.get("output_bytes_sha256")
    expected_size = expected.get("byte_size") or expected.get("output_byte_size")
    expected_rows = expected.get("row_count") or expected.get("output_row_count")
    actual_sha = _sha256_file(physical_path) if physical_path is not None and physical_path.is_file() else None
    actual_size = physical_path.stat().st_size if physical_path is not None and physical_path.is_file() else None
    return {
        "logical_path": logical_path,
        "expected_byte_size": expected_size,
        "expected_bytes_sha256": expected_sha,
        "expected_row_count": expected_rows,
        "available_for_readback": actual_sha is not None,
        "actual_byte_size": actual_size,
        "actual_bytes_sha256": actual_sha,
        "identity_matches": actual_sha == expected_sha and actual_size == expected_size,
    }


def _forced_owner_answer(summary: Mapping[str, Any]) -> dict[str, Any]:
    cells = summary.get("cells")
    rows = cells if isinstance(cells, list) else []
    native = [row for row in rows if str(row.get("cell_id", "")).endswith("NATIVE_ELIGIBILITY_DATE")]
    common = [row for row in rows if str(row.get("cell_id", "")).endswith("COMMON_ACTIVATION_DATE")]
    native_counts: dict[str, int] = {}
    common_counts: dict[str, int] = {}
    for bucket, target in ((native, native_counts), (common, common_counts)):
        for row in bucket:
            status = str(row.get("status", "MISSING"))
            target[status] = target.get(status, 0) + 1
    established = bool(native) and all(row.get("status") == "SUCCESS" for row in native)
    return {
        "answer": "ESTABLISHED" if established else "NOT_ESTABLISHED",
        "common_date_status_counts": dict(sorted(common_counts.items())),
        "native_date_status_counts": dict(sorted(native_counts.items())),
        "reason": (
            "Every native-date forced owner completed successfully."
            if established
            else "Native-date portability is incomplete or contains preserved terminal failures."
        ),
    }


def _witness_answer(summary: Mapping[str, Any]) -> dict[str, Any]:
    witnesses = summary.get("minimal_witness_sets")
    roles = summary.get("symbol_roles")
    witness_rows = witnesses if isinstance(witnesses, list) else []
    role_map = roles if isinstance(roles, Mapping) else {}
    ghosts = sorted(
        str(symbol)
        for symbol, raw_roles in role_map.items()
        if isinstance(raw_roles, list) and "ghost witness" in raw_roles
    )
    singleton_count = sum(isinstance(item, list) and len(item) == 1 for item in witness_rows)
    return {
        "answer": "SENSITIVE" if singleton_count else "NOT_DEMONSTRATED",
        "minimal_witness_set_count": len(witness_rows),
        "singleton_minimal_witness_count": singleton_count,
        "ghost_witnesses": ghosts,
        "critical_ranking": summary.get("critical_ranking", []),
    }


def _finding_count(rows: tuple[dict[str, Any], ...], observation_id: str) -> int:
    count = 0
    for row in rows:
        analysis = row.get("analysis")
        findings = analysis.get("findings") if isinstance(analysis, Mapping) else None
        if not isinstance(findings, list):
            continue
        if any(
            isinstance(finding, Mapping)
            and finding.get("observation_id") == observation_id
            and finding.get("observed") is True
            for finding in findings
        ):
            count += 1
    return count


def _reachability_answers(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    crowning_count = 0
    target_metric: Any = None
    for row in rows:
        analysis = row.get("analysis")
        repeated = analysis.get("repeated_crowning") if isinstance(analysis, Mapping) else None
        if isinstance(repeated, Mapping) and repeated.get("satisfied") is True:
            crowning_count += 1
        if row.get("state_id") == "S09" and row.get("path_id") == "P05":
            metrics = analysis.get("metrics") if isinstance(analysis, Mapping) else None
            if isinstance(metrics, Mapping):
                target_metric = metrics.get("witness_missing_recovery_fraction")
    status_success = sum(row.get("status") == "SUCCESS" for row in rows)
    r7_count = _finding_count(rows, "R7")
    state_answer = {
        "answer": "PARTIAL_DIAGNOSTIC_ONLY",
        "cell_count": len(rows),
        "successful_cells": status_success,
        "R7_observed_cells": r7_count,
        "repeated_crowning_satisfied_cells": crowning_count,
        "evidence_class": "DIAGNOSTIC_ONLY",
        "historical_return_claim": "FORBIDDEN",
    }
    cash_answer = {
        "answer": "NOT_CLOSED" if target_metric != 1.0 else "CLOSED_FOR_FROZEN_CELL",
        "S09_P05_witness_missing_recovery_fraction": target_metric,
        "threshold": 1.0,
        "evidence_class": "DIAGNOSTIC_ONLY",
    }
    return state_answer, cash_answer


def _analysis_markdown(summary: Mapping[str, Any]) -> str:
    answers = summary["direct_answers"]
    capability = summary["absolute_policy"]["capability_pass"]
    return f"""# Strategic Evidence Closure Analysis

Runner/evidence completion and strategy capability are separate. The assembled evidence
reports runner success as `{str(summary['runner_success']).lower()}` and the literal
capability decision as `{str(capability).lower()}`.

## Owner portability

**{answers['owner_portability']['answer']}**. Common-date controls completed, but native-date
results include preserved failures or absent native eligibility. They cannot be converted into
portable-owner capability by omitting the failed cells.

## Witness sensitivity

**{answers['witness_sensitivity']['answer']}**. The result contains
{answers['witness_sensitivity']['singleton_minimal_witness_count']} singleton minimal witnesses
and the ghost-witness list is retained in the compact summary.

## State reachability

**{answers['state_reachability']['answer']}**. All 84 cells are synthetic
`DIAGNOSTIC_ONLY` experiments. R7 was observed in
{answers['state_reachability']['R7_observed_cells']} cells and repeated crowning satisfied
{answers['state_reachability']['repeated_crowning_satisfied_cells']} of 84 cells. Synthetic
evidence supports no historical return claim.

## Cash vacancy

**{answers['cash_vacancy']['answer']}**. The frozen S09/P05 witness-missing recovery fraction
is `{answers['cash_vacancy']['S09_P05_witness_missing_recovery_fraction']}` versus the literal
threshold `1.0`; a successful runner does not make this capability pass.

## Literal policy

Capability is fail-closed. Missing cells, `REPLAY_ERROR`, `INSUFFICIENT_SAMPLE`, null literal
metrics, and the unregistered p10/p90 percentile method remain explicit machine-readable
failures. No threshold, scenario, or economic result was retuned.
"""


def _readme_markdown() -> str:
    return """# Strategic Evidence Closure Artifacts

This directory contains compact, sealed evidence for Tasks 3–6. Large deterministic route
and reachability shards remain external; `evidence_manifest.json` binds their logical paths,
byte sizes, SHA-256 identities, and row counts. `compact_summary.json` separates experiment
completion from the literal capability result. `analysis.md` answers the four research
questions without converting synthetic diagnostics into historical-return claims.

Validate tracked evidence and any available external shards with:

```bash
python -m scripts.run_strategic_evidence_closure validate
```
"""


def assemble_evidence_artifacts(
    *,
    root: Path,
    output_dir: Path,
    source_paths: Mapping[str, Path],
    task5_shard: Path | None,
    task3_shard: Path | None = None,
    task4_shard: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seal compact evidence even when the literal capability result is false."""

    repository = root.resolve()
    required = {"task3", "task4", "task5"}
    if set(source_paths) != required:
        raise ValueError("Task 6 source artifact keys differ")
    task3_path = source_paths["task3"].resolve()
    task4_path = source_paths["task4"].resolve()
    task5_path = source_paths["task5"].resolve()
    task3 = _load_sealed(task3_path, label="Task 3 compact evidence")
    task4 = _load_sealed(task4_path, label="Task 4 compact evidence")
    task5 = _load_sealed(task5_path, label="Task 5 compact evidence")

    readback_error: str | None = None
    reach_rows: tuple[dict[str, Any], ...] = ()
    if task5_shard is not None:
        try:
            shard = read_gzip_shard(task5_shard)
            reach_rows = tuple(dict(row) for row in shard["rows"])
        except ValueError as exc:
            readback_error = str(exc)

    external = {
        "task3": _external_identity(
            logical_path=str(task3.get("route_shard", {}).get("logical_path") or (
                "artifacts/strategic_evidence_closure/external/"
                "checkpoint3_forced_owner_full_routes.jsonl.gz"
            )),
            expected=task3.get("route_shard", {}),
            physical_path=task3_shard,
        ),
        "task4": _external_identity(
            logical_path=str(task4.get("route_shard", {}).get("logical_path")),
            expected=task4.get("route_shard", {}),
            physical_path=task4_shard,
        ),
        "task5": _external_identity(
            logical_path=_TASK5_LOGICAL_PATH,
            expected=task5,
            physical_path=task5_shard,
        ),
    }
    evidence_integrity = {
        "task3_compact_seal": True,
        "task4_compact_seal": True,
        "task5_compact_seal": True,
        "task5_shard_readback": readback_error is None,
        "task5_shard_identity": external["task5"]["identity_matches"],
        "task5_exact_cell_count": len(reach_rows) == 84,
    }
    contract = load_contract(repository / "benchmarks/strategic_evidence_closure_contract.json")
    policy: AbsolutePolicyResult = evaluate_absolute_policy(
        contract,
        forced_owner=task3,
        witness=task4,
        reachability_rows=reach_rows,
    )
    state_answer, cash_answer = _reachability_answers(reach_rows)
    runner_success = all(evidence_integrity.values()) and policy.runner_success
    compact = seal_payload(
        {
            "schema_version": "uquant.strategic-evidence-closure-summary.v1",
            "contract_payload_sha256": contract.payload_sha256,
            "runner_success": runner_success,
            "capability_pass": policy.capability_pass,
            "dry_run": dry_run,
            "evidence_integrity": evidence_integrity,
            "readback_error": readback_error,
            "direct_answers": {
                "owner_portability": _forced_owner_answer(task3),
                "witness_sensitivity": _witness_answer(task4),
                "state_reachability": state_answer,
                "cash_vacancy": cash_answer,
            },
            "absolute_policy": policy.compact(),
            "source_payload_sha256": {
                "task3": task3["payload_sha256"],
                "task4": task4["payload_sha256"],
                "task5": task5["payload_sha256"],
            },
        }
    )

    target = output_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    compact_path = target / "compact_summary.json"
    analysis_path = target / "analysis.md"
    readme_path = target / "README.md"
    atomic_write_text(compact_path, canonical_json_bytes(compact).decode("utf-8") + "\n")
    atomic_write_text(analysis_path, _analysis_markdown(compact))
    atomic_write_text(readme_path, _readme_markdown())

    source_identities = {
        "task3": _source_identity(repository, task3_path, task3),
        "task4": _source_identity(repository, task4_path, task4),
        "task5": _source_identity(repository, task5_path, task5),
    }
    generated_files = {
        path.name: {
            "byte_size": path.stat().st_size,
            "bytes_sha256": _sha256_file(path),
        }
        for path in (readme_path, analysis_path, compact_path)
    }
    manifest = seal_payload(
        {
            "schema_version": "uquant.strategic-evidence-closure-manifest.v1",
            "contract_payload_sha256": contract.payload_sha256,
            "source_evidence": source_identities,
            "external_shards": external,
            "files": generated_files,
            "runner_success": runner_success,
            "capability_pass": policy.capability_pass,
        }
    )
    manifest_path = target / "evidence_manifest.json"
    atomic_write_text(manifest_path, canonical_json_bytes(manifest).decode("utf-8") + "\n")
    checksum_paths = (readme_path, analysis_path, compact_path, manifest_path)
    checksums = "".join(f"{_sha256_file(path)}  {path.name}\n" for path in checksum_paths)
    atomic_write_text(target / "SHA256SUMS", checksums)
    return {
        "runner_success": runner_success,
        "capability_pass": policy.capability_pass,
        "compact_payload_sha256": compact["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "failed_check_ids": list(policy.failed_check_ids),
    }


def validate_evidence_artifacts(
    output_dir: Path,
    *,
    external_paths: Mapping[str, Path],
    root: Path | None = None,
) -> dict[str, Any]:
    """Validate seals, tracked bytes, checksums, and supplied external shard bytes."""

    target = output_dir.resolve()
    compact = _load_sealed(target / "compact_summary.json", label="compact summary")
    manifest = _load_sealed(target / "evidence_manifest.json", label="evidence manifest")
    if manifest.get("capability_pass") != compact.get("capability_pass"):
        raise ValueError("manifest/compact capability decision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise ValueError("evidence manifest tracked files are malformed")
    for name, identity in files.items():
        path = target / str(name)
        if not isinstance(identity, Mapping) or not path.is_file():
            raise ValueError("evidence manifest tracked file is missing")
        if identity.get("byte_size") != path.stat().st_size or identity.get(
            "bytes_sha256"
        ) != _sha256_file(path):
            raise ValueError(f"evidence manifest tracked file differs: {name}")
    external = manifest.get("external_shards")
    if not isinstance(external, Mapping):
        raise ValueError("evidence manifest external shards are malformed")
    for task, path in external_paths.items():
        identity = external.get(task)
        if not isinstance(identity, Mapping):
            raise ValueError(f"external shard identity is missing: {task}")
        if identity.get("actual_byte_size") != path.stat().st_size or identity.get(
            "actual_bytes_sha256"
        ) != _sha256_file(path):
            raise ValueError(f"external shard bytes differ: {task}")
    if root is not None:
        source_evidence = manifest.get("source_evidence")
        if not isinstance(source_evidence, Mapping):
            raise ValueError("source evidence identities are malformed")
        for task, identity in source_evidence.items():
            if not isinstance(identity, Mapping):
                raise ValueError(f"source evidence identity is malformed: {task}")
            logical = identity.get("logical_path")
            if not isinstance(logical, str) or Path(logical).is_absolute() or ".." in Path(logical).parts:
                raise ValueError(f"source evidence logical path is unsafe: {task}")
            path = root.resolve() / logical
            if identity.get("byte_size") != path.stat().st_size or identity.get(
                "bytes_sha256"
            ) != _sha256_file(path):
                raise ValueError(f"source evidence bytes differ: {task}")

    checksum_path = target / "SHA256SUMS"
    try:
        checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("SHA256SUMS is unreadable") from exc
    expected_lines = [
        f"{_sha256_file(target / name)}  {name}"
        for name in ("README.md", "analysis.md", "compact_summary.json", "evidence_manifest.json")
    ]
    if checksum_lines != expected_lines:
        raise ValueError("SHA256SUMS differs from tracked evidence")
    return {
        "runner_success": manifest.get("runner_success"),
        "capability_pass": manifest.get("capability_pass"),
        "compact_payload_sha256": compact["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
    }


__all__ = ("assemble_evidence_artifacts", "validate_evidence_artifacts")
