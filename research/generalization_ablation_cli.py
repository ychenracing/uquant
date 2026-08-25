"""Validate and sequentially replay the immutable generalization ablation registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import RLock
from typing import Any, TypeGuard

_RUNNER_ROOT = Path(__file__).resolve().parents[1]
_CAUSAL_STAGES = (
    "reference_context",
    "leaders",
    "risk",
    "opportunity",
    "targets",
    "orders",
    "fills",
)
_METRIC_FIELDS = {
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "acute_return",
    "gross_turnover",
    "annual_turnover",
    "top1_concentration",
    "top3_concentration",
    "pnl_hhi",
}
_CONCENTRATION_TOLERANCE = 1e-12
_SHA256_LENGTH = 64
_BASELINE_CARRIER_SHA256 = "f1049fe9b5db63b2e8df68a9ff87930108ca38eea40ed456aa179eadd79e7bdd"
_EVIDENCE_MANIFEST_PATH = _RUNNER_ROOT / "artifacts" / "phase2" / "ablations" / "evidence_manifest.json"
_EVIDENCE_MANIFEST_CANONICAL_SHA256 = "507e7d9a57654953c2d92e85514ae0274b0985180537ef779e46f995536437a5"
_MINIMAL_EVIDENCE_MANIFEST_PATH = (
    _RUNNER_ROOT / "artifacts" / "phase2" / "ablations" / "minimal_evidence_manifest.json"
)
_MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256 = (
    "58011315ec19111ea2caba0dd1b8cba06608150ca3726d62fbceefdc53fa9a6b"
)
_REPLAY_ERROR_FIELDS = {
    "type",
    "message",
    "date",
    "contract",
    "cell_id",
    "binding_sha256",
    "carrier_sha256",
    "provenance_sha256",
}


def _project_imports() -> tuple[Any, ...]:
    if str(_RUNNER_ROOT) not in sys.path:
        sys.path.insert(0, str(_RUNNER_ROOT))
    from research.ablation_registry import (
        build_contract_schedule,
        isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        verify_carrier_checkout,
    )

    return (
        build_contract_schedule,
        isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        verify_carrier_checkout,
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _is_sha256(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256_mapping(value: Mapping[str, Any]) -> str:
    """Return the canonical digest used to bind nested provenance evidence."""
    return hashlib.sha256(_canonical_bytes(dict(value))).hexdigest()


def _validate_metrics(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("ablation worker valid metrics are missing")
    if set(value) != _METRIC_FIELDS:
        raise ValueError("ablation worker metric coverage differs")
    numeric = tuple(value[name] for name in _METRIC_FIELDS - {"account_orders", "acute_return"})
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item))
        for item in numeric
    ):
        raise ValueError("ablation worker metrics are malformed")
    orders = value["account_orders"]
    if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
        raise ValueError("ablation worker order count is malformed")
    acute = value["acute_return"]
    if acute is not None and (
        isinstance(acute, bool) or not isinstance(acute, (int, float)) or not math.isfinite(float(acute))
    ):
        raise ValueError("ablation worker acute return is malformed")
    if float(value["final_wealth"]) <= 0:
        raise ValueError("ablation worker final wealth is malformed")
    if not 0 <= float(value["max_drawdown"]) <= 1:
        raise ValueError("ablation worker drawdown is malformed")
    if float(value["gross_turnover"]) < 0 or float(value["annual_turnover"]) < 0:
        raise ValueError("ablation worker turnover is malformed")
    top1 = float(value["top1_concentration"])
    top3 = float(value["top3_concentration"])
    hhi = float(value["pnl_hhi"])
    if (
        any(
            not -_CONCENTRATION_TOLERANCE <= item <= 1 + _CONCENTRATION_TOLERANCE
            for item in (top1, top3, hhi)
        )
        or top1 > top3 + _CONCENTRATION_TOLERANCE
    ):
        raise ValueError("ablation worker concentration is malformed")
    if acute is not None and float(acute) <= -1:
        raise ValueError("ablation worker acute return is malformed")


def _validate_worker_payload(
    payload: Mapping[str, Any],
    *,
    schedule: Sequence[Any],
    binding_sha256: str,
    experiment_id: str,
    carrier_sha256: str | None = None,
    frozen_replay_errors: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
) -> None:
    """Reject partial, stale, rewritten, self-signed, or trace-free evidence."""
    if (
        payload.get("schema_version") != 1
        or payload.get("mode") != "contract-replay"
        or payload.get("binding_sha256") != binding_sha256
        or payload.get("experiment_id") != experiment_id
    ):
        raise ValueError("ablation worker provenance is stale")
    raw_cells = payload.get("cells")
    traces = payload.get("traces")
    provenance = payload.get("provenance")
    if (
        not isinstance(raw_cells, list)
        or not isinstance(traces, Mapping)
        or not isinstance(provenance, Mapping)
    ):
        raise ValueError("ablation worker payload is incomplete")
    expected_carrier = carrier_sha256 or (_BASELINE_CARRIER_SHA256 if experiment_id == "baseline" else "")
    if not _is_sha256(expected_carrier):
        raise ValueError("ablation worker expected carrier is malformed")
    provenance_sha256 = _sha256_mapping(provenance)
    expected = tuple((item.contract, item.cell_id, item.status, item.economic) for item in schedule)
    expected_frozen_errors = {
        (item.contract, item.cell_id) for item in schedule if item.status == "REPLAY_ERROR"
    }
    if frozen_replay_errors is not None and set(frozen_replay_errors) != expected_frozen_errors:
        raise ValueError("ablation frozen replay error anchor coverage differs")
    observed: list[tuple[str, str, str, bool]] = []
    expected_trace_keys: set[str] = set()
    for raw, item in zip(raw_cells, schedule, strict=False):
        if not isinstance(raw, Mapping):
            raise ValueError("ablation worker cell is malformed")
        if set(raw) != {
            "contract",
            "cell_id",
            "frozen_status",
            "status",
            "economic",
            "metrics",
            "replay_error",
            "raw_result_sha256",
        }:
            raise ValueError("ablation worker cell fields differ")
        identity = (
            raw.get("contract"),
            raw.get("cell_id"),
            raw.get("frozen_status"),
            raw.get("economic"),
        )
        if not (
            isinstance(identity[0], str)
            and isinstance(identity[1], str)
            and isinstance(identity[2], str)
            and isinstance(identity[3], bool)
        ):
            raise ValueError("ablation worker cell identity is malformed")
        observed.append(identity)  # type: ignore[arg-type]
        if identity[:2] != (item.contract, item.cell_id):
            raise ValueError("ablation worker cell coverage differs")
        if identity[2] != item.status or identity[3] != item.economic:
            raise ValueError("ablation worker frozen contract status differs")
        actual_status = raw.get("status")
        if actual_status not in {"VALID", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}:
            raise ValueError("ablation worker actual status is malformed")
        if experiment_id == "baseline" and actual_status != item.status:
            raise ValueError("ablation worker status differs from frozen contract")
        if item.economic and actual_status == "INSUFFICIENT_SAMPLE":
            raise ValueError("ablation worker economic status is malformed")
        if not item.economic and actual_status != "INSUFFICIENT_SAMPLE":
            raise ValueError("ablation worker insufficient status was rewritten")
        metrics = raw.get("metrics")
        replay_error = raw.get("replay_error")
        result_hash = raw.get("raw_result_sha256")
        key = f"{item.contract}/{item.cell_id}"
        if actual_status == "VALID":
            _validate_metrics(metrics)
            if replay_error is not None or not _is_sha256(result_hash):
                raise ValueError("ablation worker valid cell evidence is malformed")
            expected_trace_keys.add(key)
        elif actual_status == "REPLAY_ERROR":
            if (
                metrics is not None
                or result_hash is not None
                or not isinstance(replay_error, Mapping)
                or set(replay_error) != _REPLAY_ERROR_FIELDS
                or not isinstance(replay_error.get("type"), str)
                or not replay_error.get("type")
                or not isinstance(replay_error.get("message"), str)
                or not replay_error.get("message")
            ):
                raise ValueError("ablation worker replay error evidence is malformed")
            error_date = replay_error.get("date")
            try:
                parsed_error_date = date.fromisoformat(str(error_date))
            except ValueError as exc:
                raise ValueError("ablation worker replay error evidence is malformed") from exc
            if not (
                str(replay_error.get("contract")) == item.contract
                and str(replay_error.get("cell_id")) == item.cell_id
                and replay_error.get("binding_sha256") == binding_sha256
                and replay_error.get("carrier_sha256") == expected_carrier
                and replay_error.get("provenance_sha256") == provenance_sha256
                and date.fromisoformat(item.start) <= parsed_error_date <= date.fromisoformat(item.end)
            ):
                raise ValueError("ablation worker replay error provenance differs")
            anchor = (
                frozen_replay_errors.get((item.contract, item.cell_id))
                if frozen_replay_errors is not None and item.status == "REPLAY_ERROR"
                else None
            )
            if anchor is not None and {
                "type": replay_error.get("type"),
                "message": replay_error.get("message"),
                "date": replay_error.get("date"),
            } != dict(anchor):
                raise ValueError("ablation frozen replay error anchor differs")
            expected_trace_keys.add(key)
        elif metrics is not None or replay_error is not None or result_hash is not None:
            raise ValueError("ablation insufficient cell contains economic evidence")
    if tuple(observed) != expected:
        raise ValueError("ablation worker cell coverage differs")
    if set(traces) != expected_trace_keys:
        raise ValueError("ablation worker trace coverage differs")
    for key in expected_trace_keys:
        rows = traces[key]
        cell = next(
            raw
            for raw in raw_cells
            if isinstance(raw, Mapping) and key == f"{raw.get('contract')}/{raw.get('cell_id')}"
        )
        if not isinstance(rows, list) or (cell.get("status") == "VALID" and not rows):
            raise ValueError("ablation worker decision trace is missing")
        previous = ""
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                raise ValueError("ablation worker decision trace is malformed")
            row_date = raw_row.get("date")
            stages = raw_row.get("stages")
            if (
                not isinstance(row_date, str)
                or not row_date
                or row_date <= previous
                or not isinstance(stages, Mapping)
                or set(stages) != set(_CAUSAL_STAGES)
                or any(not _is_sha256(stages[name]) for name in _CAUSAL_STAGES)
            ):
                raise ValueError("ablation worker decision trace is malformed")
            previous = row_date
        replay_error = cell.get("replay_error")
        if (
            isinstance(replay_error, Mapping)
            and rows
            and str(rows[-1].get("date")) > str(replay_error.get("date"))
        ):
            raise ValueError("ablation worker replay error trace exceeds failure date")


def _frozen_replay_error_anchors(
    registry: Any,
    *,
    source_root: Path,
) -> dict[tuple[str, str], dict[str, str]]:
    """Compile exact known replay failures from the independently hashed frozen artifact."""
    contract = registry.contract("frozen_generalization_status")
    path = source_root / contract.path
    if _sha256(path) != contract.sha256:
        raise ValueError("frozen replay error artifact hash differs")
    payload = _load_json_mapping(path, label="frozen replay error artifact")
    raw_cells = payload.get("cells")
    failures = payload.get("failures")
    if not isinstance(raw_cells, list) or not isinstance(failures, list):
        raise ValueError("frozen replay error artifact is malformed")
    anchors: dict[tuple[str, str], dict[str, str]] = {}
    for raw in raw_cells:
        if not isinstance(raw, Mapping) or raw.get("replay_error") is None:
            continue
        replay_error = raw.get("replay_error")
        window = raw.get("window")
        scenario = raw.get("scenario")
        if (
            not isinstance(replay_error, Mapping)
            or not isinstance(window, str)
            or not isinstance(scenario, str)
            or not isinstance(replay_error.get("exception_type"), str)
            or not isinstance(replay_error.get("message"), str)
        ):
            raise ValueError("frozen replay error artifact is malformed")
        message = str(replay_error["message"])
        dates = tuple(dict.fromkeys(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", message)))
        if len(dates) != 1:
            raise ValueError("frozen replay error date anchor is ambiguous")
        cell_id = f"{window}/{scenario}"
        exception_type = str(replay_error["exception_type"])
        exact_failure = f"cell replay failed: {cell_id}: {exception_type}: {message}"
        if failures.count(exact_failure) != 1:
            raise ValueError("frozen replay error failure anchor differs")
        anchors[("ai_era_generalization", cell_id)] = {
            "type": exception_type,
            "message": message,
            "date": dates[0],
        }
    if len(anchors) != contract.replay_error_count:
        raise ValueError("frozen replay error anchor count differs")
    return anchors


def _write_checkpoint(path: Path, payload: Mapping[str, Any]) -> str:
    """Atomically write a canonical, content-addressed checkpoint envelope."""
    canonical_payload = dict(payload)
    digest = hashlib.sha256(_canonical_bytes(canonical_payload)).hexdigest()
    envelope = {
        "schema_version": 1,
        "payload_sha256": digest,
        "payload": canonical_payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_bytes(envelope) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return digest


def _write_worker_artifact(checkpoint_dir: Path, worker: Mapping[str, Any]) -> dict[str, str]:
    """Atomically persist one canonical worker under its exact content hash."""
    encoded = _canonical_bytes(dict(worker))
    digest = hashlib.sha256(encoded).hexdigest()
    relative = Path("raw") / f"{digest}.worker.json"
    path = checkpoint_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("ablation raw worker content-address collision")
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
    return {
        "path": relative.as_posix(),
        "payload_sha256": digest,
        "file_sha256": digest,
    }


def _read_worker_artifact(
    checkpoint_dir: Path,
    reference: object,
) -> dict[str, Any]:
    """Load a canonical raw worker only from its content-addressed path."""
    if not isinstance(reference, Mapping) or set(reference) != {
        "path",
        "payload_sha256",
        "file_sha256",
    }:
        raise ValueError("ablation raw worker reference is malformed")
    digest = reference.get("payload_sha256")
    if not _is_sha256(digest) or reference.get("file_sha256") != digest:
        raise ValueError("ablation raw worker reference hash is malformed")
    expected_relative = Path("raw") / f"{digest}.worker.json"
    if reference.get("path") != expected_relative.as_posix():
        raise ValueError("ablation raw worker reference path differs")
    path = checkpoint_dir / expected_relative
    try:
        encoded = path.read_bytes()
        payload = json.loads(encoded)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ablation raw worker artifact is unreadable") from exc
    if (
        hashlib.sha256(encoded).hexdigest() != digest
        or not isinstance(payload, Mapping)
        or _canonical_bytes(payload) != encoded
    ):
        raise ValueError("ablation raw worker artifact hash differs")
    return dict(payload)


def _evidence_manifest_anchor(registry_relative: Path) -> tuple[Path, str]:
    """Select one compiled trust root from an exact registry identity."""
    if registry_relative == Path("artifacts/phase2/ablations/registry.json"):
        return _EVIDENCE_MANIFEST_PATH, _EVIDENCE_MANIFEST_CANONICAL_SHA256
    if registry_relative == Path("artifacts/phase2/ablations/minimal_registry.json"):
        return (
            _MINIMAL_EVIDENCE_MANIFEST_PATH,
            _MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256,
        )
    raise ValueError("ablation registry has no compiled evidence manifest")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous JSON objects at compiled evidence trust boundaries."""

    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"ablation JSON contains duplicate key: {key}")
        payload[key] = value
    return payload


def _load_trusted_evidence_manifest(
    path: Path | None = None,
    *,
    trusted_digest: str = _EVIDENCE_MANIFEST_CANONICAL_SHA256,
) -> dict[str, Any]:
    """Load the tracked evidence manifest only when its compiled digest matches."""
    manifest_path = path or _EVIDENCE_MANIFEST_PATH
    try:
        parsed = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ablation evidence manifest is unreadable") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("ablation evidence manifest is malformed")
    manifest = dict(parsed)
    if _sha256_mapping(manifest) != trusted_digest:
        raise ValueError("ablation evidence manifest trusted digest differs")
    if set(manifest) != {"schema_version", "payload_sha256", "payload"}:
        raise ValueError("ablation evidence manifest envelope is malformed")
    payload = manifest.get("payload")
    if (
        manifest.get("schema_version") != 1
        or not isinstance(payload, Mapping)
        or manifest.get("payload_sha256") != _sha256_mapping(payload)
    ):
        raise ValueError("ablation evidence manifest content hash differs")
    return dict(payload)


def _compile_evidence_manifest(
    manifest: Mapping[str, Any],
    *,
    registry: Any,
    evidence_commit: str,
    binding_sha256: str,
    schedule_sha256: str,
) -> dict[str, Any]:
    """Compile the trusted manifest against the exact registry and run binding."""
    required = {
        "schema_version",
        "kind",
        "registry_sha256",
        "evidence_commit",
        "binding_sha256",
        "schedule_sha256",
        "binding_artifact",
        "schedule_artifact",
        "entries",
    }
    if (
        set(manifest) != required
        or manifest.get("schema_version") != 1
        or manifest.get("kind") != "phase2_ablation_evidence_manifest"
        or manifest.get("registry_sha256") != registry.payload_sha256
        or manifest.get("evidence_commit") != evidence_commit
        or manifest.get("binding_sha256") != binding_sha256
        or manifest.get("schedule_sha256") != schedule_sha256
    ):
        raise ValueError("ablation evidence manifest binding differs")
    for name in ("binding", "schedule"):
        reference = manifest.get(f"{name}_artifact")
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"path", "file_sha256"}
            or reference.get("path") != f"{name}.json"
            or not _is_sha256(reference.get("file_sha256"))
        ):
            raise ValueError("ablation evidence manifest shared artifact differs")
    entries = manifest.get("entries")
    expected = [
        ("baseline", _BASELINE_CARRIER_SHA256),
        *((item.experiment_id, item.carrier.sha256) for item in registry.experiments),
    ]
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise ValueError("ablation evidence manifest entry coverage differs")
    raw_hashes: set[str] = set()
    for row, (experiment_id, carrier_sha256) in zip(entries, expected, strict=True):
        if not isinstance(row, Mapping) or set(row) != {
            "experiment_id",
            "evidence_commit",
            "binding_sha256",
            "schedule_sha256",
            "carrier_sha256",
            "artifact",
            "raw",
        }:
            raise ValueError("ablation evidence manifest entry is malformed")
        if (
            row.get("experiment_id") != experiment_id
            or row.get("evidence_commit") != evidence_commit
            or row.get("binding_sha256") != binding_sha256
            or row.get("schedule_sha256") != schedule_sha256
            or row.get("carrier_sha256") != carrier_sha256
        ):
            raise ValueError("ablation evidence manifest entry binding differs")
        artifact = row.get("artifact")
        raw = row.get("raw")
        if (
            not isinstance(artifact, Mapping)
            or set(artifact) != {"path", "kind", "file_sha256", "payload_sha256"}
            or not isinstance(raw, Mapping)
            or set(raw) != {"path", "file_sha256", "canonical_worker_sha256"}
            or not _is_sha256(artifact.get("file_sha256"))
            or not _is_sha256(artifact.get("payload_sha256"))
            or not _is_sha256(raw.get("file_sha256"))
            or not _is_sha256(raw.get("canonical_worker_sha256"))
            or raw.get("file_sha256") != raw.get("canonical_worker_sha256")
        ):
            raise ValueError("ablation evidence manifest entry artifact is malformed")
        kind = artifact.get("kind")
        expected_artifact_path = (
            "baseline.json"
            if experiment_id == "baseline"
            else (
                f"invalid/{experiment_id}.json" if kind == "invalid_experiment" else f"{experiment_id}.json"
            )
        )
        if (
            (experiment_id == "baseline" and kind != "baseline")
            or (experiment_id != "baseline" and kind not in {"experiment", "invalid_experiment"})
            or artifact.get("path") != expected_artifact_path
            or raw.get("path") != f"raw/{raw.get('canonical_worker_sha256')}.worker.json"
        ):
            raise ValueError("ablation evidence manifest entry path or type differs")
        raw_hash = str(raw["canonical_worker_sha256"])
        if raw_hash in raw_hashes:
            raise ValueError("ablation evidence manifest raw worker was reused")
        raw_hashes.add(raw_hash)
    return dict(manifest)


def _validate_evidence_manifest_entry(
    checkpoint_dir: Path,
    entry: Mapping[str, Any],
) -> None:
    """Match one result and its canonical worker against a trusted manifest row."""
    artifact = entry.get("artifact")
    raw = entry.get("raw")
    if not isinstance(artifact, Mapping) or not isinstance(raw, Mapping):
        raise ValueError("ablation evidence manifest entry is malformed")
    artifact_path = checkpoint_dir / str(artifact.get("path"))
    try:
        artifact_bytes = artifact_path.read_bytes()
        envelope = json.loads(artifact_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ablation evidence manifest experiment artifact is unreadable") from exc
    if hashlib.sha256(artifact_bytes).hexdigest() != artifact.get("file_sha256"):
        raise ValueError("ablation evidence manifest experiment artifact hash differs")
    if not isinstance(envelope, Mapping) or not isinstance(envelope.get("payload"), Mapping):
        raise ValueError("ablation evidence manifest experiment artifact is malformed")
    payload = envelope["payload"]
    if envelope.get("payload_sha256") != artifact.get("payload_sha256") or payload.get(
        "kind"
    ) != artifact.get("kind"):
        raise ValueError("ablation evidence manifest experiment artifact seal differs")
    raw_path = checkpoint_dir / str(raw.get("path"))
    try:
        raw_bytes = raw_path.read_bytes()
        worker = json.loads(raw_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ablation evidence manifest raw worker is unreadable") from exc
    if hashlib.sha256(raw_bytes).hexdigest() != raw.get("file_sha256"):
        raise ValueError("ablation evidence manifest raw worker file hash differs")
    if (
        not isinstance(worker, Mapping)
        or _canonical_bytes(worker) != raw_bytes
        or _sha256_mapping(worker) != raw.get("canonical_worker_sha256")
    ):
        raise ValueError("ablation evidence manifest raw worker canonical hash differs")
    reference_name = (
        "worker_artifact" if entry.get("experiment_id") == "baseline" else "variant_worker_artifact"
    )
    if payload.get(reference_name) != {
        "path": raw.get("path"),
        "payload_sha256": raw.get("canonical_worker_sha256"),
        "file_sha256": raw.get("file_sha256"),
    }:
        raise ValueError("ablation evidence manifest raw worker reference differs")


def _validate_replay_command(command: object, *, expected: Sequence[str]) -> None:
    if not isinstance(command, list) or command != list(expected):
        raise ValueError("ablation replay command or evidence commit differs")


def _validate_exact_worker(
    worker: Mapping[str, Any],
    *,
    schedule: Sequence[Any],
    binding_sha256: str,
    experiment_id: str,
    carrier_sha256: str,
    expected_provenance: Mapping[str, Any],
    frozen_replay_errors: Mapping[tuple[str, str], Mapping[str, str]],
) -> None:
    _validate_worker_payload(
        worker,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id=experiment_id,
        carrier_sha256=carrier_sha256,
        frozen_replay_errors=frozen_replay_errors,
    )
    if worker.get("provenance") != expected_provenance:
        raise ValueError("ablation worker provenance differs from expected checkout/config/data/runtime")


def _write_baseline_result(
    *,
    checkpoint_dir: Path,
    binding_sha256: str,
    schedule: Sequence[Any],
    worker: Mapping[str, Any],
    replay_command: Sequence[str],
    expected_provenance: Mapping[str, Any],
    frozen_replay_errors: Mapping[tuple[str, str], Mapping[str, str]],
) -> Path:
    """Persist a baseline checkpoint that references, rather than embeds, raw evidence."""
    _validate_exact_worker(
        worker,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=_BASELINE_CARRIER_SHA256,
        expected_provenance=expected_provenance,
        frozen_replay_errors=frozen_replay_errors,
    )
    worker_reference = _write_worker_artifact(checkpoint_dir, worker)
    payload = {
        "schema_version": 2,
        "kind": "baseline",
        "binding_sha256": binding_sha256,
        "worker_artifact": worker_reference,
        "replay_command": list(replay_command),
    }
    path = checkpoint_dir / "baseline.json"
    _write_checkpoint(path, payload)
    return path


def _read_baseline_result(
    path: Path,
    *,
    checkpoint_dir: Path,
    binding_sha256: str,
    schedule: Sequence[Any],
    expected_replay_command: Sequence[str],
    expected_provenance: Mapping[str, Any],
    frozen_replay_errors: Mapping[tuple[str, str], Mapping[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    checkpoint = _read_checkpoint(path, binding_sha256=binding_sha256, kind="baseline")
    if (
        set(checkpoint)
        != {
            "schema_version",
            "kind",
            "binding_sha256",
            "worker_artifact",
            "replay_command",
        }
        or checkpoint.get("schema_version") != 2
    ):
        raise ValueError("ablation baseline checkpoint is malformed")
    _validate_replay_command(checkpoint.get("replay_command"), expected=expected_replay_command)
    worker = _read_worker_artifact(checkpoint_dir, checkpoint.get("worker_artifact"))
    _validate_exact_worker(
        worker,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=_BASELINE_CARRIER_SHA256,
        expected_provenance=expected_provenance,
        frozen_replay_errors=frozen_replay_errors,
    )
    return checkpoint, worker


def _read_checkpoint(
    path: Path,
    *,
    binding_sha256: str,
    kind: str,
) -> dict[str, Any]:
    """Read and authenticate one checkpoint against the exact run binding."""
    try:
        encoded = path.read_bytes()
        envelope = json.loads(encoded)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("ablation checkpoint is unreadable") from exc
    if _canonical_bytes(envelope) + b"\n" != encoded:
        raise ValueError("ablation checkpoint canonical encoding differs")
    if not isinstance(envelope, Mapping) or set(envelope) != {
        "schema_version",
        "payload_sha256",
        "payload",
    }:
        raise ValueError("ablation checkpoint envelope is malformed")
    payload = envelope.get("payload")
    expected_hash = envelope.get("payload_sha256")
    if (
        envelope.get("schema_version") != 1
        or not isinstance(payload, Mapping)
        or not _is_sha256(expected_hash)
        or hashlib.sha256(_canonical_bytes(payload)).hexdigest() != expected_hash
    ):
        raise ValueError("ablation checkpoint content hash differs")
    if payload.get("binding_sha256") != binding_sha256 or payload.get("kind") != kind:
        raise ValueError("ablation checkpoint is stale")
    return dict(payload)


def _validate_comparison_coverage(
    comparison: Mapping[str, Any],
    *,
    schedule: Sequence[Any],
    binding_sha256: str,
    carrier_sha256: str,
) -> None:
    rows = comparison.get("cells")
    aggregates = comparison.get("aggregates")
    baseline_provenance = comparison.get("baseline_provenance")
    variant_provenance = comparison.get("variant_provenance")
    if (
        not isinstance(rows, list)
        or not isinstance(aggregates, Mapping)
        or not isinstance(baseline_provenance, Mapping)
        or not isinstance(variant_provenance, Mapping)
        or not isinstance(comparison.get("execution_pass"), bool)
    ):
        raise ValueError("ablation comparison cell coverage is malformed")
    expected = tuple((item.contract, item.cell_id, item.status) for item in schedule)
    observed: list[tuple[str, str, str]] = []
    for row, item in zip(rows, schedule, strict=False):
        if not isinstance(row, Mapping):
            raise ValueError("ablation comparison cell coverage is malformed")
        if set(row) != {
            "contract",
            "cell_id",
            "frozen_status",
            "baseline_status",
            "variant_status",
            "status_transition",
            "baseline_metrics",
            "variant_metrics",
            "delta",
            "baseline_replay_error",
            "variant_replay_error",
            "baseline_raw_result_sha256",
            "variant_raw_result_sha256",
        }:
            raise ValueError("ablation comparison cell fields differ")
        identity = (
            row.get("contract"),
            row.get("cell_id"),
            row.get("baseline_status"),
        )
        if not all(isinstance(value, str) for value in identity):
            raise ValueError("ablation comparison cell coverage is malformed")
        observed.append((str(identity[0]), str(identity[1]), str(identity[2])))
        baseline_status = row.get("baseline_status")
        variant_status = row.get("variant_status")
        if row.get("frozen_status") != item.status or baseline_status != item.status:
            raise ValueError("ablation comparison baseline status differs from frozen contract")
        if item.economic:
            if variant_status not in {"VALID", "REPLAY_ERROR"}:
                raise ValueError("ablation comparison variant status is malformed")
        elif variant_status != "INSUFFICIENT_SAMPLE":
            raise ValueError("ablation comparison insufficient status was rewritten")
        expected_transition = (
            None if baseline_status == variant_status else f"{baseline_status}->{variant_status}"
        )
        if row.get("status_transition") != expected_transition:
            raise ValueError("ablation comparison status transition differs")
        delta = row.get("delta")
        common_valid = baseline_status == variant_status == "VALID"
        if common_valid != isinstance(delta, Mapping):
            raise ValueError("ablation comparison delta coverage differs")
        baseline_metrics = row.get("baseline_metrics")
        variant_metrics = row.get("variant_metrics")
        baseline_result_hash = row.get("baseline_raw_result_sha256")
        variant_result_hash = row.get("variant_raw_result_sha256")
        if baseline_status == "VALID":
            _validate_metrics(baseline_metrics)
            if not _is_sha256(baseline_result_hash):
                raise ValueError("ablation comparison baseline result hash is malformed")
        elif baseline_metrics is not None or baseline_result_hash is not None:
            raise ValueError("ablation comparison baseline metrics are malformed")
        if variant_status == "VALID":
            _validate_metrics(variant_metrics)
            if not _is_sha256(variant_result_hash):
                raise ValueError("ablation comparison variant result hash is malformed")
        elif variant_metrics is not None or variant_result_hash is not None:
            raise ValueError("ablation comparison variant metrics are malformed")
        if isinstance(delta, Mapping):
            if set(delta) != _METRIC_FIELDS:
                raise ValueError("ablation comparison delta dimensions differ")
            for name, value in delta.items():
                if name == "acute_return" and value is None:
                    continue
                if name == "account_orders":
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise ValueError("ablation comparison delta dimensions are malformed")
                elif (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise ValueError("ablation comparison delta dimensions are malformed")
        for side, status, replay_error, provenance, expected_carrier in (
            (
                "baseline",
                baseline_status,
                row.get("baseline_replay_error"),
                baseline_provenance,
                _BASELINE_CARRIER_SHA256,
            ),
            (
                "variant",
                variant_status,
                row.get("variant_replay_error"),
                variant_provenance,
                carrier_sha256,
            ),
        ):
            if status != "REPLAY_ERROR":
                if replay_error is not None:
                    raise ValueError(f"ablation comparison {side} replay error is malformed")
                continue
            if (
                not isinstance(replay_error, Mapping)
                or set(replay_error) != _REPLAY_ERROR_FIELDS
                or replay_error.get("contract") != item.contract
                or replay_error.get("cell_id") != item.cell_id
                or replay_error.get("binding_sha256") != binding_sha256
                or replay_error.get("carrier_sha256") != expected_carrier
                or replay_error.get("provenance_sha256") != _sha256_mapping(provenance)
                or not isinstance(replay_error.get("type"), str)
                or not replay_error.get("type")
                or not isinstance(replay_error.get("message"), str)
                or not replay_error.get("message")
            ):
                raise ValueError(f"ablation comparison {side} replay error provenance differs")
            try:
                replay_date = date.fromisoformat(str(replay_error.get("date")))
            except ValueError as exc:
                raise ValueError(f"ablation comparison {side} replay error date is malformed") from exc
            if not date.fromisoformat(item.start) <= replay_date <= date.fromisoformat(item.end):
                raise ValueError(f"ablation comparison {side} replay error date is malformed")
    if tuple(observed) != expected:
        raise ValueError("ablation comparison cell coverage differs")
    expected_contracts = {item.contract for item in schedule if item.status == "VALID"}
    if set(aggregates) != expected_contracts:
        raise ValueError("ablation comparison aggregate coverage differs")
    for contract in expected_contracts:
        aggregate = aggregates[contract]
        if not isinstance(aggregate, Mapping) or set(aggregate) != {
            "baseline",
            "variant",
            "delta",
            "coverage",
        }:
            raise ValueError("ablation comparison aggregate coverage differs")
        baseline = aggregate["baseline"]
        variant = aggregate["variant"]
        delta = aggregate["delta"]
        coverage = aggregate["coverage"]
        contract_rows = tuple(
            row for row in rows if isinstance(row, Mapping) and row.get("contract") == contract
        )
        common_valid_count = sum(
            row.get("baseline_status") == row.get("variant_status") == "VALID" for row in contract_rows
        )
        baseline_counts = Counter(str(row.get("baseline_status")) for row in contract_rows)
        variant_counts = Counter(str(row.get("variant_status")) for row in contract_rows)
        transition_counts = Counter(
            str(row.get("status_transition"))
            for row in contract_rows
            if row.get("status_transition") is not None
        )
        expected_coverage = {
            "record_count": len(contract_rows),
            "economic_count": sum(item.contract == contract and item.economic for item in schedule),
            "common_valid_count": common_valid_count,
            "baseline_status_counts": dict(sorted(baseline_counts.items())),
            "variant_status_counts": dict(sorted(variant_counts.items())),
            "status_transition_counts": dict(sorted(transition_counts.items())),
        }
        if (
            not isinstance(baseline, Mapping)
            or not isinstance(variant, Mapping)
            or not isinstance(delta, Mapping)
            or coverage != expected_coverage
            or set(baseline) != set(variant)
            or set(delta) != set(baseline)
            or baseline.get("economic_cells") != common_valid_count
            or variant.get("economic_cells") != common_valid_count
            or delta.get("economic_cells") != 0
        ):
            raise ValueError("ablation comparison aggregate coverage differs")
    expected_execution_pass = not any(
        isinstance(row, Mapping)
        and row.get("baseline_status") != "REPLAY_ERROR"
        and row.get("variant_status") == "REPLAY_ERROR"
        for row in rows
    )
    if comparison.get("execution_pass") is not expected_execution_pass:
        raise ValueError("ablation comparison execution status differs")


def _validate_experiment_checkpoints(
    registry: Any,
    checkpoints: Mapping[str, Mapping[str, Any]],
    *,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> list[dict[str, Any]]:
    """Require one distinct authenticated checkpoint for every current experiment."""
    expected_ids = tuple(item.experiment_id for item in registry.experiments)
    expected_count = 13 - len(registry.deleted_subsystems)
    if expected_count not in {12, 13} or len(expected_ids) != expected_count:
        raise ValueError("ablation aggregation registry size differs")
    if set(checkpoints) != set(expected_ids):
        raise ValueError(
            f"ablation aggregation requires exact {expected_count}/{expected_count} experiment coverage"
        )
    ordered: list[dict[str, Any]] = []
    worker_hashes: set[str] = set()
    for experiment in registry.experiments:
        raw = checkpoints[experiment.experiment_id]
        worker_hash = _validate_experiment_checkpoint(
            experiment,
            raw,
            binding_sha256=binding_sha256,
            schedule=schedule,
        )
        if worker_hash in worker_hashes:
            raise ValueError("ablation variant worker evidence was reused")
        worker_hashes.add(worker_hash)
        ordered.append(dict(raw))
    return ordered


def _evidence_coverage(
    registry: Any,
    *,
    valid: Mapping[str, Mapping[str, Any]],
    invalid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize authenticated valid and invalid artifacts without relabeling either."""
    expected = tuple(item.experiment_id for item in registry.experiments)
    if set(valid) & set(invalid):
        raise ValueError("ablation experiment has both valid and invalid artifacts")
    observed = set(valid) | set(invalid)
    if not observed <= set(expected):
        raise ValueError("ablation evidence contains an unregistered experiment")
    for experiment_id, payload in valid.items():
        if payload.get("kind") != "experiment" or payload.get("experiment_id") != experiment_id:
            raise ValueError("ablation valid experiment summary differs")
    for experiment_id, payload in invalid.items():
        if (
            payload.get("kind") != "invalid_experiment"
            or payload.get("experiment_id") != experiment_id
            or payload.get("reason") != "no_behavior_divergence"
            or payload.get("coverage_complete") is not True
        ):
            raise ValueError("ablation invalid experiment summary differs")
    missing = [item for item in expected if item not in observed]
    return {
        "complete": len(valid) == len(expected),
        "coverage_complete": not missing,
        "required_experiment_count": len(expected),
        "valid_experiment_count": len(valid),
        "invalid_experiment_count": len(invalid),
        "valid_experiment_ids": [item for item in expected if item in valid],
        "invalid_experiment_ids": [item for item in expected if item in invalid],
        "missing_experiment_ids": missing,
        **(
            {"deleted_subsystems": list(registry.deleted_subsystems)}
            if registry.deleted_subsystems
            else {}
        ),
        "invalid_experiments": {
            item: {
                "reason": invalid[item]["reason"],
                "coverage_complete": invalid[item]["coverage_complete"],
                "variant_worker_artifact": invalid[item]["variant_worker_artifact"],
            }
            for item in expected
            if item in invalid
        },
    }


def _validate_experiment_checkpoint(
    experiment: Any,
    raw: Mapping[str, Any],
    *,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> str:
    comparison = raw.get("comparison")
    divergence = comparison.get("first_divergence") if isinstance(comparison, Mapping) else None
    if (
        raw.get("schema_version") != 1
        or raw.get("kind") != "experiment"
        or raw.get("binding_sha256") != binding_sha256
        or raw.get("experiment_id") != experiment.experiment_id
        or raw.get("subsystem") != experiment.subsystem
    ):
        raise ValueError("ablation experiment checkpoint is stale")
    if raw.get("carrier_sha256") != experiment.carrier.sha256:
        raise ValueError("ablation experiment checkpoint carrier differs")
    worker_hash = raw.get("worker_payload_sha256")
    if not _is_sha256(worker_hash):
        raise ValueError("ablation experiment worker hash is malformed")
    if (
        not isinstance(comparison, Mapping)
        or not isinstance(comparison.get("cells"), list)
        or not isinstance(comparison.get("aggregates"), Mapping)
        or not isinstance(divergence, Mapping)
        or not divergence.get("cell_id")
        or not divergence.get("date")
        or not divergence.get("first_stage")
        or not isinstance(raw.get("replay_command"), list)
        or raw.get("execution_pass") is not comparison.get("execution_pass")
    ):
        raise ValueError("ablation experiment checkpoint is incomplete")
    _validate_comparison_coverage(
        comparison,
        schedule=schedule,
        binding_sha256=binding_sha256,
        carrier_sha256=experiment.carrier.sha256,
    )
    return worker_hash


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _combined_sha256(paths: Sequence[Path], *, root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _runtime() -> dict[str, str]:
    import numpy as np
    import pandas as pd

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("cannot resolve uv for ablation runtime provenance")
    try:
        uv_output = subprocess.run(  # nosec B603
            [uv, "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot inspect ablation uv runtime") from exc
    parts = uv_output.split()
    if len(parts) < 2 or parts[0] != "uv":
        raise RuntimeError("ablation uv version is malformed")
    return {
        "python_full_version": platform.python_version(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "uv_version": parts[1],
    }


def _data_provenance(data_dir: Path) -> dict[str, Any]:
    if str(_RUNNER_ROOT) not in sys.path:
        sys.path.insert(0, str(_RUNNER_ROOT))
    from uquant.validation.manifest import verify_data_manifest

    return dict(verify_data_manifest(data_dir))


_PROBE = """
import json
import platform
import sys
sys.path.insert(0, sys.argv[1])
import numpy as np
import pandas as pd
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import ProductionEngine
from uquant.types import AccountState
changes = json.loads(sys.argv[2])
config = DEFAULT_CONFIG.override(**changes)
first = AccountState.empty(config.initial_cash)
second = AccountState.empty(config.initial_cash)
if first is second or first.positions or second.positions or first.pending_orders or second.pending_orders:
    raise RuntimeError("ablation account probe is not fresh")
print(json.dumps({
    "effective_config_sha256": config_fingerprint(config),
    "fresh_account_sha256": __import__("hashlib").sha256(
        json.dumps(first.to_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest(),
    "runtime": {
        "python_full_version": platform.python_version(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
    },
    "engine_source": __import__("inspect").getfile(ProductionEngine),
}, allow_nan=False, separators=(",", ":"), sort_keys=True))
"""


def _probe_checkout(root: Path, changes: Mapping[str, bool]) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    try:
        process = subprocess.run(  # nosec B603
            [
                sys.executable,
                "-I",
                "-c",
                _PROBE,
                str(root),
                json.dumps(dict(changes), separators=(",", ":"), sort_keys=True),
            ],
            cwd=root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(process.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError("isolated ablation carrier is not importable") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("isolated ablation carrier probe is malformed")
    engine_source = Path(str(payload.get("engine_source", ""))).resolve()
    if not engine_source.is_relative_to(root):
        raise RuntimeError("isolated ablation imported production outside its checkout")
    return payload


probe_checkout: Callable[[Path, Mapping[str, bool]], dict[str, Any]] = _probe_checkout


def _contract_summary(schedule: Sequence[Any]) -> dict[str, Any]:
    names = tuple(dict.fromkeys(item.contract for item in schedule))
    return {
        name: {
            "record_count": len(selected),
            "economic_count": sum(item.economic for item in selected),
            "status_counts": dict(sorted(Counter(item.status for item in selected).items())),
        }
        for name in names
        if (selected := tuple(item for item in schedule if item.contract == name))
    }


def _schedule_rows(schedule: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "contract": item.contract,
            "cell_id": item.cell_id,
            "status": item.status,
            "economic": item.economic,
            "symbols": list(item.symbols),
            "start": item.start,
            "end": item.end,
            "acute_start": item.acute_start,
            "acute_end": item.acute_end,
            "pool_size": item.pool_size,
            "seed_index": item.seed_index,
            "derived_seed": item.derived_seed,
        }
        for item in schedule
    ]


def _git_output(root: Path, *arguments: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve git for ablation provenance")
    try:
        return subprocess.run(  # nosec B603
            [git, "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot inspect ablation git provenance") from exc


def _repository_root(source_root: Path) -> Path:
    common = Path(
        _git_output(
            source_root,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
    ).resolve()
    if common.name != ".git":
        raise ValueError("ablation git common directory is malformed")
    return common.parent


def _runner_sha256(registry_path: Path, *, source_root: Path | None = None) -> str:
    root = _RUNNER_ROOT if source_root is None else source_root
    paths = (
        root / "scripts" / "run_generalization_ablation.py",
        root / "research" / "ablation.py",
        root / "research" / "ablation_registry.py",
        registry_path,
    )
    return _combined_sha256(paths, root=root)


def _baseline_config_sha256(source_root: Path) -> str:
    """Read the effective baseline config from the source bound into evidence."""
    return str(probe_checkout(source_root, {})["effective_config_sha256"])


def _execution_binding(
    *,
    registry: Any,
    registry_path: Path,
    source_root: Path,
    data_dir: Path,
    schedule: Sequence[Any],
) -> dict[str, Any]:
    runtime = _runtime()
    runtime.update(
        {
            "platform_system": platform.system(),
            "platform_release": platform.release(),
            "platform_machine": platform.machine(),
        }
    )
    return {
        "schema_version": 1,
        "registry_sha256": registry.payload_sha256,
        "source": {
            "base_commit": registry.base_commit,
            "production_source_sha256": registry.source_sha256,
            "orchestrator_head": _git_output(source_root, "rev-parse", "HEAD"),
        },
        "fixed_contracts": [
            {
                "name": item.name,
                "path": item.path,
                "sha256": item.sha256,
                "record_count": item.record_count,
                "economic_count": item.economic_count,
            }
            for item in registry.fixed_contracts
        ],
        "schedule_sha256": hashlib.sha256(_canonical_bytes(_schedule_rows(schedule))).hexdigest(),
        "contracts": _contract_summary(schedule),
        "baseline_config_sha256": _baseline_config_sha256(source_root),
        "runner_sha256": _runner_sha256(registry_path, source_root=source_root),
        "uv_lock_sha256": _sha256(source_root / "uv.lock"),
        "runtime": runtime,
        "data": _data_provenance(data_dir),
    }


@contextmanager
def _isolated_evidence_checkout(
    repository_root: Path,
    evidence_commit: str,
) -> Iterator[Path]:
    """Materialize the exact historical evidence commit in a clean detached worktree."""
    if not re.fullmatch(r"[0-9a-f]{40}", evidence_commit):
        raise ValueError("ablation evidence commit is malformed")
    resolved = _git_output(repository_root, "rev-parse", "--verify", f"{evidence_commit}^{{commit}}")
    if resolved != evidence_commit:
        raise ValueError("ablation evidence commit differs")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve git for ablation evidence checkout")
    with tempfile.TemporaryDirectory(prefix="uquant-generalization-evidence-parent-") as temporary:
        checkout = Path(temporary) / "checkout"
        primary: BaseException | None = None
        add_attempted = False
        try:
            try:
                add_attempted = True
                subprocess.run(  # nosec B603
                    [
                        git,
                        "-C",
                        str(repository_root),
                        "worktree",
                        "add",
                        "--detach",
                        str(checkout),
                        evidence_commit,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise RuntimeError("cannot materialize ablation evidence commit") from exc
            if _git_output(checkout, "rev-parse", "HEAD") != evidence_commit or _git_output(
                checkout, "status", "--porcelain", "--untracked-files=all"
            ):
                raise ValueError("ablation evidence checkout is not exact and clean")
            yield checkout
            if _git_output(checkout, "rev-parse", "HEAD") != evidence_commit or _git_output(
                checkout, "status", "--porcelain", "--untracked-files=all"
            ):
                raise ValueError("ablation evidence checkout changed during replay")
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if add_attempted:
                try:
                    subprocess.run(  # nosec B603
                        [
                            git,
                            "-C",
                            str(repository_root),
                            "worktree",
                            "remove",
                            "--force",
                            str(checkout),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (OSError, subprocess.CalledProcessError) as exc:
                    if primary is not None:
                        primary.add_note(f"ablation evidence cleanup also failed: {exc}")
                    else:
                        raise RuntimeError("cannot remove ablation evidence checkout") from exc


def _replay_command(
    *,
    repository_root: Path,
    evidence_commit: str,
    registry_relative: Path,
    data_dir: Path,
    experiment_id: str,
    checkpoint_dir: Path | None = None,
    output: Path | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "replay",
        "--repository-root",
        str(repository_root),
        "--evidence-commit",
        evidence_commit,
        "--registry-relative",
        registry_relative.as_posix(),
        "--data-dir",
        str(data_dir),
        "--checkpoint-dir",
        str(
            checkpoint_dir
            if checkpoint_dir is not None
            else Path(tempfile.gettempdir()) / "uquant-generalization-ablation-checkpoints"
        ),
        "--output",
        str(
            output
            if output is not None
            else Path(tempfile.gettempdir()) / "uquant-phase2-ablation-progress.json"
        ),
        "--experiment",
        experiment_id,
    ]
    return command


def _first_hashed_divergence(
    baseline: Mapping[str, Sequence[Mapping[str, Any]]],
    variant: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    require: bool = False,
) -> dict[str, Any] | None:
    """Return the first fixed-cell/date causal-stage hash difference."""
    if set(baseline) != set(variant):
        raise ValueError("ablation decision trace coverage differs")
    differences: list[tuple[str, str, dict[str, Any]]] = []
    for cell_id in baseline:
        left_rows = baseline[cell_id]
        right_rows = variant[cell_id]
        left_dates = tuple(str(row.get("date", "")) for row in left_rows)
        right_dates = tuple(str(row.get("date", "")) for row in right_rows)
        common_length = min(len(left_dates), len(right_dates))
        if left_dates[:common_length] != right_dates[:common_length]:
            raise ValueError("ablation decision traces require aligned date prefixes")
        for left, right in zip(
            left_rows[:common_length],
            right_rows[:common_length],
            strict=True,
        ):
            left_stages = left.get("stages")
            right_stages = right.get("stages")
            if not isinstance(left_stages, Mapping) or not isinstance(right_stages, Mapping):
                raise ValueError("ablation decision stage hashes are malformed")
            if set(left_stages) != set(_CAUSAL_STAGES) or set(right_stages) != set(_CAUSAL_STAGES):
                raise ValueError("ablation decision stage hash coverage differs")
            changed = [stage for stage in _CAUSAL_STAGES if left_stages[stage] != right_stages[stage]]
            if changed:
                difference = {
                    "cell_id": cell_id,
                    "date": left["date"],
                    "changed_fields": changed,
                    "first_stage": changed[0],
                    "baseline_stage_sha256": dict(left_stages),
                    "variant_stage_sha256": dict(right_stages),
                }
                differences.append((str(left["date"]), cell_id, difference))
                break
    if differences:
        return min(differences, key=lambda item: (item[0], item[1]))[2]
    if require:
        raise ValueError("ablation experiment has no behavior divergence")
    return None


def _replay_cell(
    engine: Any,
    *,
    symbols: Sequence[str],
    start: str,
    end: str,
    acute_start: str | None = None,
    acute_end: str | None = None,
) -> dict[str, Any]:
    """Replay one real production cell and retain raw dimensions plus stage hashes."""
    import pandas as pd

    from research.first_divergence import CAUSAL_STAGES as TRACE_STAGES
    from research.first_divergence import (
        canonical_stages as _canonical_stages,
    )
    from research.first_divergence import (
        trace_row as _trace_row,
    )
    from research.first_divergence import (
        validate_trace_interval as _validate_trace_interval,
    )
    from uquant.validation.generalization import (
        symbol_pnl_concentration,
        symbol_pnl_from_result,
    )
    from uquant.validation.promotion import compact_promotion_payload as _compact

    if tuple(TRACE_STAGES) != _CAUSAL_STAGES:
        raise RuntimeError("ablation trace stage contract drifted")
    _validate_trace_interval(start, end)
    trace: list[dict[str, Any]] = []
    fill_cursor = 0
    failure_date = start
    original_decide = engine.decide
    original_execute_open = engine.execution.execute_open

    def observed_execute_open(*, date: Any, account: Any, panel: Any) -> Any:
        nonlocal failure_date
        failure_date = str(pd.Timestamp(date).date())
        return original_execute_open(date=date, account=account, panel=panel)

    def observed_decide(*, symbols: Any, as_of: str, account: Any) -> Any:
        nonlocal failure_date, fill_cursor
        failure_date = str(pd.Timestamp(as_of).date())
        new_fills = tuple(account.fills[fill_cursor:])
        fill_cursor = len(account.fills)
        decision = original_decide(symbols=symbols, as_of=as_of, account=account)
        trace.append(
            _trace_row(
                engine=engine,
                decision=decision,
                account=account,
                new_fills=new_fills,
            )
        )
        return decision

    replay_error: dict[str, str] | None = None
    raw: Mapping[str, Any] | None = None
    try:
        object.__setattr__(engine.execution, "execute_open", observed_execute_open)
        object.__setattr__(engine, "decide", observed_decide)
        raw = engine.backtest(symbols=tuple(symbols), start=start, end=end)
    except Exception as exc:
        replay_error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "date": failure_date,
        }
    finally:
        object.__setattr__(engine, "decide", original_decide)
        object.__setattr__(engine.execution, "execute_open", original_execute_open)
    trace_hashes = [
        {
            "date": row["date"],
            "stages": {
                stage: hashlib.sha256(_canonical_bytes(stages[stage])).hexdigest() for stage in _CAUSAL_STAGES
            },
        }
        for row in trace
        if (stages := _canonical_stages(row))
    ]
    if replay_error is not None:
        return {
            "metrics": None,
            "trace": trace_hashes,
            "replay_error": replay_error,
            "raw_result_sha256": None,
        }
    if raw is None:
        raise RuntimeError("ablation replay returned neither result nor error")
    compact = _compact(
        raw,
        acute=(acute_start, acute_end) if acute_start is not None and acute_end is not None else None,
    )
    account = raw.get("final_account")
    if not isinstance(account, Mapping):
        raise RuntimeError("ablation replay final account is missing")
    positions = account.get("positions")
    if not isinstance(positions, Mapping):
        raise RuntimeError("ablation replay final positions are malformed")
    final_date = pd.Timestamp(str(raw.get("end", end)))
    final_prices = {
        str(symbol): engine.workspace.price(str(symbol), final_date)
        for symbol, position in positions.items()
        if isinstance(position, Mapping) and int(position.get("shares", 0)) > 0
    }
    concentration = symbol_pnl_concentration(symbol_pnl_from_result(raw, final_prices))
    return {
        "metrics": {
            "final_wealth": compact["final_wealth"],
            "max_drawdown": compact["max_drawdown"],
            "account_orders": compact["account_orders"],
            "acute_return": compact["acute_return"],
            "gross_turnover": compact["gross_turnover"],
            "annual_turnover": compact["annual_turnover"],
            **concentration,
        },
        "trace": trace_hashes,
        "replay_error": None,
        "raw_result_sha256": hashlib.sha256(_canonical_bytes(raw)).hexdigest(),
    }


def _compare_worker_payloads(
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any],
    *,
    require_divergence: bool = True,
) -> dict[str, Any]:
    """Compare two complete raw runs without making a source-acceptance conclusion."""
    from research.ablation import (
        AblationCell,
        AblationMetrics,
        aggregate_dimensions,
        compare_cells,
    )

    raw_baseline_cells = baseline.get("cells")
    raw_variant_cells = variant.get("cells")
    baseline_traces = baseline.get("traces")
    variant_traces = variant.get("traces")
    if (
        not isinstance(raw_baseline_cells, list)
        or not isinstance(raw_variant_cells, list)
        or not isinstance(baseline_traces, Mapping)
        or not isinstance(variant_traces, Mapping)
    ):
        raise ValueError("ablation worker payload is incomplete")

    def by_identity(rows: Sequence[object]) -> dict[tuple[str, str], Mapping[str, Any]]:
        result: dict[tuple[str, str], Mapping[str, Any]] = {}
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise ValueError("ablation worker cell is malformed")
            contract = raw.get("contract")
            cell_id = raw.get("cell_id")
            if not isinstance(contract, str) or not contract or not isinstance(cell_id, str) or not cell_id:
                raise ValueError("ablation worker cell identity is malformed")
            identity = (contract, cell_id)
            if identity in result:
                raise ValueError("ablation worker contains duplicate cells")
            result[identity] = raw
        return result

    baseline_by_id = by_identity(raw_baseline_cells)
    variant_by_id = by_identity(raw_variant_cells)
    if set(baseline_by_id) != set(variant_by_id):
        raise ValueError("ablation worker cell coverage differs")

    def typed_cell(raw: Mapping[str, Any]) -> AblationCell:
        status = raw.get("status")
        metrics_raw = raw.get("metrics")
        if not isinstance(status, str):
            raise ValueError("ablation worker cell status is malformed")
        metrics = None
        if metrics_raw is not None:
            if not isinstance(metrics_raw, Mapping):
                raise ValueError("ablation worker metrics are malformed")
            metrics = AblationMetrics(
                final_wealth=float(metrics_raw["final_wealth"]),
                max_drawdown=float(metrics_raw["max_drawdown"]),
                account_orders=int(metrics_raw["account_orders"]),
                acute_return=(
                    None if metrics_raw["acute_return"] is None else float(metrics_raw["acute_return"])
                ),
                gross_turnover=float(metrics_raw["gross_turnover"]),
                annual_turnover=float(metrics_raw["annual_turnover"]),
                top1_concentration=float(metrics_raw["top1_concentration"]),
                top3_concentration=float(metrics_raw["top3_concentration"]),
                pnl_hhi=float(metrics_raw["pnl_hhi"]),
            )
        return AblationCell(str(raw["contract"]), str(raw["cell_id"]), status, metrics)

    compared_cells: list[dict[str, Any]] = []
    baseline_typed: list[Any] = []
    variant_typed: list[Any] = []
    common_valid_pairs: dict[tuple[str, str], tuple[Any, Any]] = {}
    for identity in baseline_by_id:
        left_raw = baseline_by_id[identity]
        right_raw = variant_by_id[identity]
        left = typed_cell(left_raw)
        right = typed_cell(right_raw)
        baseline_typed.append(left)
        variant_typed.append(right)
        delta = compare_cells(left, right).to_dict() if left.status == right.status == "VALID" else None
        if delta is not None:
            common_valid_pairs[identity] = (left, right)
            trace_key = f"{identity[0]}/{identity[1]}"
            left_dates = tuple(row.get("date") for row in baseline_traces.get(trace_key, ()))
            right_dates = tuple(row.get("date") for row in variant_traces.get(trace_key, ()))
            if left_dates != right_dates:
                raise ValueError("ablation common-valid decision traces require aligned dates")
        transition = None if left.status == right.status else f"{left.status}->{right.status}"
        compared_cells.append(
            {
                "contract": identity[0],
                "cell_id": identity[1],
                "frozen_status": left_raw.get("frozen_status"),
                "baseline_status": left.status,
                "variant_status": right.status,
                "status_transition": transition,
                "baseline_metrics": None if left.metrics is None else left.metrics.to_dict(),
                "variant_metrics": None if right.metrics is None else right.metrics.to_dict(),
                "delta": delta,
                "baseline_replay_error": left_raw.get("replay_error"),
                "variant_replay_error": right_raw.get("replay_error"),
                "baseline_raw_result_sha256": left_raw.get("raw_result_sha256"),
                "variant_raw_result_sha256": right_raw.get("raw_result_sha256"),
            }
        )

    contracts = tuple(dict.fromkeys(item.contract for item in baseline_typed))
    aggregates: dict[str, Any] = {}
    for contract in contracts:
        contract_identities = tuple(identity for identity in baseline_by_id if identity[0] == contract)
        common_identities = tuple(
            identity for identity in contract_identities if identity in common_valid_pairs
        )
        left_valid = tuple(common_valid_pairs[identity][0] for identity in common_identities)
        right_valid = tuple(common_valid_pairs[identity][1] for identity in common_identities)
        left_aggregate = aggregate_dimensions(left_valid) if left_valid else {"economic_cells": 0}
        right_aggregate = aggregate_dimensions(right_valid) if right_valid else {"economic_cells": 0}
        common = set(left_aggregate) & set(right_aggregate)
        baseline_status_counts = Counter(
            str(baseline_by_id[identity].get("status")) for identity in contract_identities
        )
        variant_status_counts = Counter(
            str(variant_by_id[identity].get("status")) for identity in contract_identities
        )
        transitions = Counter(
            f"{baseline_by_id[identity].get('status')}->{variant_by_id[identity].get('status')}"
            for identity in contract_identities
            if baseline_by_id[identity].get("status") != variant_by_id[identity].get("status")
        )
        aggregates[contract] = {
            "baseline": left_aggregate,
            "variant": right_aggregate,
            "delta": {name: right_aggregate[name] - left_aggregate[name] for name in sorted(common)},
            "coverage": {
                "record_count": len(contract_identities),
                "economic_count": sum(
                    baseline_by_id[identity].get("frozen_status") != "INSUFFICIENT_SAMPLE"
                    for identity in contract_identities
                ),
                "common_valid_count": len(common_identities),
                "baseline_status_counts": dict(sorted(baseline_status_counts.items())),
                "variant_status_counts": dict(sorted(variant_status_counts.items())),
                "status_transition_counts": dict(sorted(transitions.items())),
            },
        }
    execution_pass = not any(
        row["baseline_status"] != "REPLAY_ERROR" and row["variant_status"] == "REPLAY_ERROR"
        for row in compared_cells
    )
    return {
        "first_divergence": _first_hashed_divergence(
            baseline_traces,
            variant_traces,
            require=require_divergence,
        ),
        "cells": compared_cells,
        "aggregates": aggregates,
        "execution_pass": execution_pass,
        "baseline_provenance": baseline.get("provenance"),
        "variant_provenance": variant.get("provenance"),
    }


def _write_experiment_result(
    *,
    checkpoint_dir: Path,
    experiment: Any,
    binding_sha256: str,
    schedule: Sequence[Any],
    baseline_checkpoint: Mapping[str, Any],
    baseline_worker: Mapping[str, Any],
    variant_worker: Mapping[str, Any],
    replay_command: Sequence[str],
    expected_variant_provenance: Mapping[str, Any],
    frozen_replay_errors: Mapping[tuple[str, str], Mapping[str, str]],
) -> Path:
    """Persist either a standard divergent result or a native invalid result."""
    _validate_exact_worker(
        variant_worker,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id=experiment.experiment_id,
        carrier_sha256=experiment.carrier.sha256,
        expected_provenance=expected_variant_provenance,
        frozen_replay_errors=frozen_replay_errors,
    )
    baseline_reference = baseline_checkpoint.get("worker_artifact")
    if _read_worker_artifact(checkpoint_dir, baseline_reference) != baseline_worker:
        raise ValueError("ablation baseline raw worker reference differs")
    variant_reference = _write_worker_artifact(checkpoint_dir, variant_worker)
    comparison = _compare_worker_payloads(
        baseline_worker,
        variant_worker,
        require_divergence=False,
    )
    invalid = comparison["first_divergence"] is None
    kind = "invalid_experiment" if invalid else "experiment"
    payload = {
        "schema_version": 2,
        "kind": kind,
        "binding_sha256": binding_sha256,
        "experiment_id": experiment.experiment_id,
        "subsystem": experiment.subsystem,
        "carrier_sha256": experiment.carrier.sha256,
        "baseline_worker_artifact": baseline_reference,
        "variant_worker_artifact": variant_reference,
        "coverage_complete": True,
        "execution_pass": comparison["execution_pass"],
        "comparison": comparison,
        "replay_command": list(replay_command),
    }
    if invalid:
        payload["reason"] = "no_behavior_divergence"
        path = checkpoint_dir / "invalid" / f"{experiment.experiment_id}.json"
        standard_path = checkpoint_dir / f"{experiment.experiment_id}.json"
        if standard_path.exists():
            raise ValueError("ablation experiment has both standard and invalid artifacts")
    else:
        path = checkpoint_dir / f"{experiment.experiment_id}.json"
        if (checkpoint_dir / "invalid" / f"{experiment.experiment_id}.json").exists():
            raise ValueError("ablation experiment has both standard and invalid artifacts")
    if path.exists() and _checkpoint_payload_schema(path) == 2:
        previous = _read_checkpoint(path, binding_sha256=binding_sha256, kind=kind)
        if previous != payload:
            raise ValueError("ablation deterministic rerun differs from checkpoint")
    _write_checkpoint(path, payload)
    return path


def _read_experiment_result(
    path: Path,
    *,
    checkpoint_dir: Path,
    experiment: Any,
    binding_sha256: str,
    schedule: Sequence[Any],
    baseline_checkpoint: Mapping[str, Any],
    baseline_worker: Mapping[str, Any],
    expected_replay_command: Sequence[str],
    expected_baseline_provenance: Mapping[str, Any],
    expected_variant_provenance: Mapping[str, Any],
    frozen_replay_errors: Mapping[tuple[str, str], Mapping[str, str]],
) -> dict[str, Any]:
    """Authenticate raw workers and recompute every derived experiment claim."""
    expected_kind = "invalid_experiment" if path.parent.name == "invalid" else "experiment"
    checkpoint = _read_checkpoint(
        path,
        binding_sha256=binding_sha256,
        kind=expected_kind,
    )
    required = {
        "schema_version",
        "kind",
        "binding_sha256",
        "experiment_id",
        "subsystem",
        "carrier_sha256",
        "baseline_worker_artifact",
        "variant_worker_artifact",
        "coverage_complete",
        "execution_pass",
        "comparison",
        "replay_command",
    }
    if expected_kind == "invalid_experiment":
        required.add("reason")
    if (
        set(checkpoint) != required
        or checkpoint.get("schema_version") != 2
        or checkpoint.get("experiment_id") != experiment.experiment_id
        or checkpoint.get("subsystem") != experiment.subsystem
        or checkpoint.get("carrier_sha256") != experiment.carrier.sha256
        or checkpoint.get("coverage_complete") is not True
        or checkpoint.get("baseline_worker_artifact") != baseline_checkpoint.get("worker_artifact")
    ):
        raise ValueError("ablation experiment checkpoint is stale or incomplete")
    _validate_replay_command(checkpoint.get("replay_command"), expected=expected_replay_command)
    observed_baseline = _read_worker_artifact(
        checkpoint_dir,
        checkpoint.get("baseline_worker_artifact"),
    )
    if observed_baseline != baseline_worker:
        raise ValueError("ablation experiment baseline raw worker differs")
    _validate_exact_worker(
        observed_baseline,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=_BASELINE_CARRIER_SHA256,
        expected_provenance=expected_baseline_provenance,
        frozen_replay_errors=frozen_replay_errors,
    )
    variant_worker = _read_worker_artifact(
        checkpoint_dir,
        checkpoint.get("variant_worker_artifact"),
    )
    _validate_exact_worker(
        variant_worker,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id=experiment.experiment_id,
        carrier_sha256=experiment.carrier.sha256,
        expected_provenance=expected_variant_provenance,
        frozen_replay_errors=frozen_replay_errors,
    )
    recomputed = _compare_worker_payloads(
        observed_baseline,
        variant_worker,
        require_divergence=False,
    )
    if checkpoint.get("comparison") != recomputed:
        raise ValueError("ablation checkpoint differs from recomputed comparison")
    if checkpoint.get("execution_pass") is not recomputed.get("execution_pass"):
        raise ValueError("ablation checkpoint execution status differs")
    divergence = recomputed.get("first_divergence")
    if expected_kind == "experiment" and not isinstance(divergence, Mapping):
        raise ValueError("ablation standard checkpoint requires first divergence")
    if expected_kind == "invalid_experiment" and (
        divergence is not None or checkpoint.get("reason") != "no_behavior_divergence"
    ):
        raise ValueError("ablation invalid experiment reason differs")
    return checkpoint


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} is malformed")
    return dict(payload)


def _worker(args: argparse.Namespace) -> dict[str, Any]:
    """Replay an exact schedule using production imported from one isolated checkout."""
    source_root = Path(args.source_root).resolve()
    data_dir = Path(args.data_dir).resolve()
    schedule_checkpoint = _read_checkpoint(
        Path(args.schedule_checkpoint).resolve(),
        binding_sha256=args.binding_sha256,
        kind="schedule",
    )
    raw_schedule = schedule_checkpoint.get("cells")
    schedule_sha256 = schedule_checkpoint.get("schedule_sha256")
    if (
        not isinstance(raw_schedule, list)
        or not _is_sha256(schedule_sha256)
        or hashlib.sha256(_canonical_bytes(raw_schedule)).hexdigest() != schedule_sha256
    ):
        raise ValueError("ablation worker schedule is malformed")
    try:
        changes_payload = json.loads(args.config_json)
        checkout_payload = json.loads(args.checkout_json)
    except json.JSONDecodeError as exc:
        raise ValueError("ablation worker invocation provenance is malformed") from exc
    if (
        not isinstance(changes_payload, Mapping)
        or any(
            not isinstance(name, str) or not isinstance(value, bool)
            for name, value in changes_payload.items()
        )
        or not isinstance(checkout_payload, Mapping)
    ):
        raise ValueError("ablation worker invocation provenance is malformed")

    resolved_source = str(source_root)
    sys.path = [resolved_source, *[item for item in sys.path if item != resolved_source]]
    from uquant.config import DEFAULT_CONFIG, config_fingerprint
    from uquant.engine import ProductionEngine
    from uquant.types import AccountState
    from uquant.validation.manifest import verify_data_manifest

    engine_source = Path(sys.modules[ProductionEngine.__module__].__file__ or "").resolve()
    if not engine_source.is_relative_to(source_root):
        raise RuntimeError("ablation worker imported production outside its checkout")
    config = DEFAULT_CONFIG.override(**dict(changes_payload))
    first_account = AccountState.empty(config.initial_cash)
    second_account = AccountState.empty(config.initial_cash)
    if (
        first_account is second_account
        or first_account.positions
        or second_account.positions
        or first_account.pending_orders
        or second_account.pending_orders
    ):
        raise RuntimeError("ablation worker account factory is not fresh")
    fresh_account_sha256 = hashlib.sha256(_canonical_bytes(first_account.to_dict())).hexdigest()
    engine = ProductionEngine(data_dir, config)
    provenance = {
        "checkout": dict(checkout_payload),
        "production_engine_source": engine_source.relative_to(source_root).as_posix(),
        "effective_config_sha256": config_fingerprint(config),
        "fresh_account_sha256": fresh_account_sha256,
        "account_factory": "uquant.types.AccountState.empty/per-backtest",
        "schedule_sha256": schedule_sha256,
        "data": dict(verify_data_manifest(data_dir)),
        "runtime": _runtime(),
        "uv_lock_sha256": _sha256(source_root / "uv.lock"),
        "process_contract": {
            "isolated_python": True,
            "pythonhashseed": os.environ.get("PYTHONHASHSEED", ""),
            "single_process": True,
            "thread_limits": {
                name: os.environ.get(name, "")
                for name in (
                    "OMP_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS",
                )
            },
        },
    }
    provenance_sha256 = _sha256_mapping(provenance)
    carrier_sha256 = checkout_payload.get("carrier_sha256")
    if not _is_sha256(carrier_sha256):
        raise ValueError("ablation worker carrier provenance is malformed")
    cells: list[dict[str, Any]] = []
    traces: dict[str, Any] = {}
    economic_complete = 0
    economic_total = sum(bool(item.get("economic")) for item in raw_schedule if isinstance(item, Mapping))
    for index, raw in enumerate(raw_schedule, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("ablation worker schedule cell is malformed")
        required = {
            "contract",
            "cell_id",
            "status",
            "economic",
            "symbols",
            "start",
            "end",
            "acute_start",
            "acute_end",
            "pool_size",
            "seed_index",
            "derived_seed",
        }
        if set(raw) != required:
            raise ValueError("ablation worker schedule cell fields differ")
        contract = raw["contract"]
        cell_id = raw["cell_id"]
        status = raw["status"]
        economic = raw["economic"]
        symbols = raw["symbols"]
        if (
            not isinstance(contract, str)
            or not isinstance(cell_id, str)
            or status not in {"VALID", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}
            or not isinstance(economic, bool)
            or not isinstance(symbols, list)
            or any(not isinstance(symbol, str) for symbol in symbols)
        ):
            raise ValueError("ablation worker schedule cell is malformed")
        cell_payload: dict[str, Any] = {
            "contract": contract,
            "cell_id": cell_id,
            "frozen_status": status,
            "status": status,
            "economic": economic,
            "metrics": None,
            "replay_error": None,
            "raw_result_sha256": None,
        }
        if economic:
            economic_complete += 1
            print(
                f"[{args.experiment_id}] economic {economic_complete}/{economic_total} "
                f"record {index}/{len(raw_schedule)} {contract}/{cell_id}",
                file=sys.stderr,
                flush=True,
            )
            result = _replay_cell(
                engine,
                symbols=tuple(symbols),
                start=str(raw["start"]),
                end=str(raw["end"]),
                acute_start=(str(raw["acute_start"]) if raw["acute_start"] is not None else None),
                acute_end=(str(raw["acute_end"]) if raw["acute_end"] is not None else None),
            )
            raw_error = result["replay_error"]
            actual_status = "REPLAY_ERROR" if raw_error is not None else "VALID"
            if args.experiment_id == "baseline" and actual_status != status:
                raise RuntimeError(
                    f"frozen baseline status differs for {contract}/{cell_id}: "
                    f"expected {status}, observed {actual_status}"
                )
            cell_payload["status"] = actual_status
            traces[f"{contract}/{cell_id}"] = result["trace"]
            if isinstance(raw_error, Mapping):
                cell_payload["replay_error"] = {
                    "type": raw_error["type"],
                    "message": raw_error["message"],
                    "date": raw_error["date"],
                    "contract": contract,
                    "cell_id": cell_id,
                    "binding_sha256": args.binding_sha256,
                    "carrier_sha256": carrier_sha256,
                    "provenance_sha256": provenance_sha256,
                }
            else:
                cell_payload["metrics"] = result["metrics"]
                cell_payload["raw_result_sha256"] = result["raw_result_sha256"]
        cells.append(cell_payload)
    return {
        "schema_version": 1,
        "mode": "contract-replay",
        "binding_sha256": args.binding_sha256,
        "experiment_id": args.experiment_id,
        "cells": cells,
        "traces": traces,
        "provenance": provenance,
    }


def _invoke_worker(
    *,
    source_root: Path,
    data_dir: Path,
    schedule_checkpoint: Path,
    binding_sha256: str,
    experiment_id: str,
    config_changes: Mapping[str, bool],
    checkout: Mapping[str, Any],
    output: Path,
    worker_script: Path | None = None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-I",
        str(Path(__file__).resolve() if worker_script is None else worker_script),
        "worker",
        "--source-root",
        str(source_root),
        "--data-dir",
        str(data_dir),
        "--schedule-checkpoint",
        str(schedule_checkpoint),
        "--binding-sha256",
        binding_sha256,
        "--experiment-id",
        experiment_id,
        "--config-json",
        _canonical_bytes(dict(config_changes)).decode(),
        "--checkout-json",
        _canonical_bytes(dict(checkout)).decode(),
        "--output",
        str(output),
    ]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    try:
        subprocess.run(  # nosec B603
            command,
            cwd=source_root,
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"isolated ablation worker failed: {experiment_id}") from exc
    return _load_json_mapping(output, label="ablation worker output")


def _checkout_payload(checkout: Any) -> dict[str, Any]:
    return {
        "base_commit": checkout.base_commit,
        "experiment_commit": checkout.experiment_commit,
        "production_source_sha256": checkout.source_sha256,
        "tree_sha256": checkout.tree_sha256,
        "carrier_sha256": checkout.carrier_sha256,
        "config_changes": dict(checkout.config_changes),
        "clean": True,
    }


def _validate_worker_provenance(
    payload: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    checkout: Mapping[str, Any],
    effective_config_sha256: str,
    fresh_account_sha256: str,
) -> None:
    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("ablation worker provenance is missing")
    expected = _expected_worker_provenance(
        binding=binding,
        checkout=checkout,
        effective_config_sha256=effective_config_sha256,
        fresh_account_sha256=fresh_account_sha256,
    )
    if provenance != expected:
        raise ValueError("ablation worker provenance differs from the exact binding")


def _expected_worker_provenance(
    *,
    binding: Mapping[str, Any],
    checkout: Mapping[str, Any],
    effective_config_sha256: str,
    fresh_account_sha256: str,
) -> dict[str, Any]:
    """Construct independently expected checkout/config/data/runtime worker provenance."""
    runtime = binding.get("runtime")
    expected_runtime = (
        {
            name: runtime[name]
            for name in ("python_full_version", "numpy_version", "pandas_version", "uv_version")
        }
        if isinstance(runtime, Mapping)
        else None
    )
    if expected_runtime is None:
        raise ValueError("ablation binding runtime is malformed")
    return {
        "checkout": dict(checkout),
        "production_engine_source": "uquant/engine.py",
        "effective_config_sha256": effective_config_sha256,
        "fresh_account_sha256": fresh_account_sha256,
        "account_factory": "uquant.types.AccountState.empty/per-backtest",
        "schedule_sha256": binding.get("schedule_sha256"),
        "data": binding.get("data"),
        "runtime": expected_runtime,
        "uv_lock_sha256": binding.get("uv_lock_sha256"),
        "process_contract": {
            "isolated_python": True,
            "pythonhashseed": "0",
            "single_process": True,
            "thread_limits": {
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            },
        },
    }


def _baseline_replay_command(
    *,
    repository_root: Path,
    evidence_commit: str,
    registry_relative: Path,
    data_dir: Path,
    checkpoint_dir: Path,
    output: Path,
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "replay",
        "--repository-root",
        str(repository_root),
        "--evidence-commit",
        evidence_commit,
        "--registry-relative",
        registry_relative.as_posix(),
        "--data-dir",
        str(data_dir),
        "--checkpoint-dir",
        str(checkpoint_dir),
        "--output",
        str(output),
        "--baseline-only",
    ]


def _load_baseline_checkpoint(
    path: Path,
    *,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> dict[str, Any]:
    checkpoint = _read_checkpoint(
        path,
        binding_sha256=binding_sha256,
        kind="baseline",
    )
    worker = checkpoint.get("worker")
    worker_hash = checkpoint.get("worker_payload_sha256")
    if (
        checkpoint.get("schema_version") != 1
        or not isinstance(worker, Mapping)
        or not _is_sha256(worker_hash)
        or hashlib.sha256(_canonical_bytes(worker)).hexdigest() != worker_hash
        or not isinstance(checkpoint.get("replay_command"), list)
    ):
        raise ValueError("ablation baseline checkpoint is malformed")
    _validate_worker_payload(
        worker,
        schedule=schedule,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=_BASELINE_CARRIER_SHA256,
    )
    return checkpoint


def _load_available_experiments(
    registry: Any,
    *,
    checkpoint_dir: Path,
    binding_sha256: str,
    schedule: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    for experiment in registry.experiments:
        path = checkpoint_dir / f"{experiment.experiment_id}.json"
        if path.exists():
            checkpoint = _read_checkpoint(
                path,
                binding_sha256=binding_sha256,
                kind="experiment",
            )
            _validate_experiment_checkpoint(
                experiment,
                checkpoint,
                binding_sha256=binding_sha256,
                schedule=schedule,
            )
            available[experiment.experiment_id] = checkpoint
    return available


def _expected_baseline_provenance(
    registry: Any,
    *,
    source_root: Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    (
        _build_contract_schedule,
        isolated_baseline_checkout,
        _isolated_carrier_checkout,
        _load_ablation_registry,
        _validate_ablation_registry,
        _verify_carrier_checkout,
    ) = _project_imports()
    with (
        tempfile.TemporaryDirectory(prefix="uquant-phase2-baseline-readback-") as temporary,
        isolated_baseline_checkout(
            registry,
            source_root=source_root,
            destination=Path(temporary) / "checkout",
        ) as checkout,
    ):
        checkout_payload = _checkout_payload(checkout)
        probe = _probe_checkout(checkout.root, {})
    return _expected_worker_provenance(
        binding=binding,
        checkout=checkout_payload,
        effective_config_sha256=str(probe["effective_config_sha256"]),
        fresh_account_sha256=str(probe["fresh_account_sha256"]),
    )


def _expected_variant_provenance(
    registry: Any,
    experiment: Any,
    *,
    source_root: Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    (
        _build_contract_schedule,
        _isolated_baseline_checkout,
        isolated_carrier_checkout,
        _load_ablation_registry,
        _validate_ablation_registry,
        verify_carrier_checkout,
    ) = _project_imports()
    with (
        tempfile.TemporaryDirectory(
            prefix=f"uquant-generalization-{experiment.experiment_id}-readback-"
        ) as temporary,
        isolated_carrier_checkout(
            registry,
            experiment,
            source_root=source_root,
            destination=Path(temporary) / "checkout",
        ) as checkout,
    ):
        verify_carrier_checkout(registry, experiment, checkout)
        checkout_payload = _checkout_payload(checkout)
        probe = _probe_checkout(checkout.root, dict(checkout.config_changes))
    return _expected_worker_provenance(
        binding=binding,
        checkout=checkout_payload,
        effective_config_sha256=str(probe["effective_config_sha256"]),
        fresh_account_sha256=str(probe["fresh_account_sha256"]),
    )


def _load_available_results(
    registry: Any,
    *,
    source_root: Path,
    checkpoint_dir: Path,
    binding: Mapping[str, Any],
    binding_sha256: str,
    schedule: Sequence[Any],
    baseline_checkpoint: Mapping[str, Any],
    baseline_worker: Mapping[str, Any],
    baseline_replay_command: Sequence[str],
    baseline_provenance: Mapping[str, Any],
    repository_root: Path,
    evidence_commit: str,
    registry_relative: Path,
    data_dir: Path,
    output: Path,
    frozen_replay_errors: Mapping[tuple[str, str], Mapping[str, str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    del baseline_replay_command
    valid: dict[str, dict[str, Any]] = {}
    invalid: dict[str, dict[str, Any]] = {}
    for experiment in registry.experiments:
        path = _select_experiment_result_path(checkpoint_dir, experiment.experiment_id)
        if path is None:
            continue
        expected_variant = _expected_variant_provenance(
            registry,
            experiment,
            source_root=source_root,
            binding=binding,
        )
        expected_command = _replay_command(
            repository_root=repository_root,
            evidence_commit=evidence_commit,
            registry_relative=registry_relative,
            data_dir=data_dir,
            experiment_id=experiment.experiment_id,
            checkpoint_dir=checkpoint_dir,
            output=output,
        )
        payload = _read_experiment_result(
            path,
            checkpoint_dir=checkpoint_dir,
            experiment=experiment,
            binding_sha256=binding_sha256,
            schedule=schedule,
            baseline_checkpoint=baseline_checkpoint,
            baseline_worker=baseline_worker,
            expected_replay_command=expected_command,
            expected_baseline_provenance=baseline_provenance,
            expected_variant_provenance=expected_variant,
            frozen_replay_errors=frozen_replay_errors,
        )
        target = invalid if payload["kind"] == "invalid_experiment" else valid
        target[experiment.experiment_id] = payload
    return valid, invalid


def _select_experiment_result_path(
    checkpoint_dir: Path,
    experiment_id: str,
) -> Path | None:
    """Select one result while rejecting every standard/invalid path collision."""
    standard_path = checkpoint_dir / f"{experiment_id}.json"
    invalid_path = checkpoint_dir / "invalid" / f"{experiment_id}.json"
    if standard_path.exists() and invalid_path.exists():
        raise ValueError("ablation experiment has both standard and invalid artifacts")
    if standard_path.exists() and _checkpoint_payload_schema(standard_path) == 2:
        return standard_path
    return invalid_path if invalid_path.exists() else None


def _validate_evidence_archive(
    checkpoint_dir: Path,
    manifest: Mapping[str, Any],
) -> None:
    """Match the complete external archive to the compiled tracked trust anchor."""
    for name in ("binding", "schedule"):
        reference = manifest[f"{name}_artifact"]
        if not isinstance(reference, Mapping):
            raise ValueError("ablation evidence manifest shared artifact is malformed")
        path = checkpoint_dir / str(reference["path"])
        try:
            observed = _sha256(path)
        except OSError as exc:
            raise ValueError("ablation evidence manifest shared artifact is unreadable") from exc
        if observed != reference["file_sha256"]:
            raise ValueError("ablation evidence manifest shared artifact hash differs")
    entries = manifest["entries"]
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], Mapping):
        raise ValueError("ablation evidence manifest entry coverage differs")
    baseline_raw = entries[0]["raw"]
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("ablation evidence manifest entry is malformed")
        experiment_id = str(entry["experiment_id"])
        artifact = entry["artifact"]
        if not isinstance(artifact, Mapping):
            raise ValueError("ablation evidence manifest entry artifact is malformed")
        expected_path = checkpoint_dir / str(artifact["path"])
        if experiment_id != "baseline":
            selected = _select_experiment_result_path(checkpoint_dir, experiment_id)
            if selected != expected_path:
                raise ValueError("ablation evidence manifest experiment artifact path differs")
        _validate_evidence_manifest_entry(checkpoint_dir, entry)
        envelope = _load_json_mapping(
            expected_path,
            label="ablation evidence manifest experiment artifact",
        )
        payload = envelope.get("payload")
        if experiment_id != "baseline" and (
            not isinstance(payload, Mapping)
            or payload.get("baseline_worker_artifact")
            != {
                "path": baseline_raw["path"],
                "payload_sha256": baseline_raw["canonical_worker_sha256"],
                "file_sha256": baseline_raw["file_sha256"],
            }
        ):
            raise ValueError("ablation evidence manifest baseline raw reference differs")


def _checkpoint_payload_schema(path: Path) -> int | None:
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, Mapping) or not isinstance(envelope.get("payload"), Mapping):
        return None
    schema = envelope["payload"].get("schema_version")
    return schema if isinstance(schema, int) else None


def _progress_payload(
    *,
    registry: Any,
    binding: Mapping[str, Any],
    binding_sha256: str,
    checkpoint_dir: Path,
    baseline_path: Path,
    completed: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    expected = tuple(item.experiment_id for item in registry.experiments)
    completed_ids = tuple(item for item in expected if item in completed)
    return {
        "schema_version": 1,
        "mode": "ablation-checkpoint-progress",
        "complete": False,
        "binding_sha256": binding_sha256,
        "binding": dict(binding),
        "baseline": {
            "checkpoint": str(baseline_path),
            "file_sha256": _sha256(baseline_path),
        },
        "completed_count": len(completed_ids),
        "required_count": len(expected),
        "completed_experiment_ids": list(completed_ids),
        "missing_experiment_ids": [item for item in expected if item not in completed],
        "checkpoint_dir": str(checkpoint_dir),
    }


def _result_summary(
    *,
    registry: Any,
    binding: Mapping[str, Any],
    binding_sha256: str,
    checkpoint_dir: Path,
    baseline_path: Path,
    baseline_checkpoint: Mapping[str, Any],
    valid: Mapping[str, Mapping[str, Any]],
    invalid: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    coverage = _evidence_coverage(registry, valid=valid, invalid=invalid)

    def references(
        payloads: Mapping[str, Mapping[str, Any]],
        *,
        directory: Path,
    ) -> dict[str, Any]:
        return {
            experiment_id: {
                "artifact_path": str(directory / f"{experiment_id}.json"),
                "artifact_file_sha256": _sha256(directory / f"{experiment_id}.json"),
                "variant_worker_artifact": payload["variant_worker_artifact"],
            }
            for experiment_id, payload in payloads.items()
        }

    return {
        "schema_version": 2,
        "mode": "ablation-evidence-readback",
        **coverage,
        "binding_sha256": binding_sha256,
        "binding": dict(binding),
        "baseline": {
            "artifact_path": str(baseline_path),
            "artifact_file_sha256": _sha256(baseline_path),
            "worker_artifact": baseline_checkpoint["worker_artifact"],
            "replay_command": baseline_checkpoint["replay_command"],
        },
        "valid_experiments": references(valid, directory=checkpoint_dir),
        "invalid_experiment_artifacts": references(
            invalid,
            directory=checkpoint_dir / "invalid",
        ),
    }


def _complete_evidence(
    *,
    registry: Any,
    binding: Mapping[str, Any],
    binding_sha256: str,
    baseline_checkpoint: Mapping[str, Any],
    baseline_path: Path,
    checkpoint_dir: Path,
    checkpoints: Mapping[str, Mapping[str, Any]],
    schedule: Sequence[Any],
) -> dict[str, Any]:
    ordered = _validate_experiment_checkpoints(
        registry,
        checkpoints,
        binding_sha256=binding_sha256,
        schedule=schedule,
    )
    experiments: list[dict[str, Any]] = []
    for item in ordered:
        experiment_id = str(item["experiment_id"])
        checkpoint_path = checkpoint_dir / f"{experiment_id}.json"
        experiments.append(
            {
                **item,
                "checkpoint_file": str(checkpoint_path),
                "checkpoint_file_sha256": _sha256(checkpoint_path),
            }
        )
    return {
        "schema_version": 1,
        "mode": "complete-ablation-raw-evidence",
        "complete": True,
        "binding_sha256": binding_sha256,
        "binding": dict(binding),
        "baseline": {
            "checkpoint_file": str(baseline_path),
            "checkpoint_file_sha256": _sha256(baseline_path),
            "worker_payload_sha256": baseline_checkpoint["worker_payload_sha256"],
            "replay_command": baseline_checkpoint["replay_command"],
            "provenance": baseline_checkpoint["worker"]["provenance"],
        },
        "experiments": experiments,
        **(
            {"deleted_subsystems": list(registry.deleted_subsystems)}
            if registry.deleted_subsystems
            else {}
        ),
        "exclusions": [
            {
                "subsystem": item.subsystem,
                "reason": item.reason,
                "evidence_field": item.evidence_field,
                "frozen_value": item.frozen_value,
            }
            for item in registry.exclusions
        ],
    }


def _validate(args: argparse.Namespace) -> dict[str, Any]:
    (
        build_contract_schedule,
        _isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        _verify_carrier_checkout,
    ) = _project_imports()
    source_root = Path(args.source_root).resolve()
    registry_path = Path(args.registry).resolve()
    data_dir = Path(args.data_dir).resolve()
    registry = load_ablation_registry(registry_path)
    validate_ablation_registry(registry, source_root=source_root)
    schedule = build_contract_schedule(registry, source_root=source_root)
    repository_root = _repository_root(source_root)
    evidence_commit = _git_output(source_root, "rev-parse", "HEAD")
    registry_relative = registry_path.relative_to(source_root)
    experiments: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="uquant-generalization-ablation-") as temporary:
        temporary_root = Path(temporary)
        for index, experiment in enumerate(registry.experiments):
            destination = temporary_root / f"{index:02d}-{experiment.subsystem}"
            with isolated_carrier_checkout(
                registry,
                experiment,
                source_root=source_root,
                destination=destination,
            ) as checkout:
                probe = _probe_checkout(checkout.root, dict(checkout.config_changes))
                parent_runtime = _runtime()
                if probe.get("runtime") != {
                    name: parent_runtime[name]
                    for name in ("python_full_version", "numpy_version", "pandas_version")
                }:
                    raise RuntimeError("isolated ablation carrier runtime differs")
                experiments.append(
                    {
                        "experiment_id": experiment.experiment_id,
                        "subsystem": experiment.subsystem,
                        "carrier": {
                            "type": experiment.carrier.kind,
                            "sha256": experiment.carrier.sha256,
                            "changes": dict(experiment.carrier.changes),
                            "touched_paths": list(experiment.carrier.touched_paths),
                        },
                        "checkout": {
                            "base_commit": checkout.base_commit,
                            "experiment_commit": checkout.experiment_commit,
                            "source_sha256": checkout.source_sha256,
                            "tree_sha256": checkout.tree_sha256,
                            "clean": True,
                        },
                        "effective_config_sha256": probe["effective_config_sha256"],
                        "fresh_account_sha256": probe["fresh_account_sha256"],
                        "replay_command": _replay_command(
                            repository_root=repository_root,
                            evidence_commit=evidence_commit,
                            registry_relative=registry_relative,
                            data_dir=data_dir,
                            experiment_id=experiment.experiment_id,
                        ),
                    }
                )
    return {
        "schema_version": 1,
        "mode": "carrier-validation",
        "passed": True,
        "registry_sha256": registry.payload_sha256,
        "source": {
            "base_commit": registry.base_commit,
            "production_source_sha256": registry.source_sha256,
        },
        "contracts": _contract_summary(schedule),
        "experiments": experiments,
        **(
            {"deleted_subsystems": list(registry.deleted_subsystems)}
            if registry.deleted_subsystems
            else {}
        ),
        "exclusions": [
            {
                "subsystem": item.subsystem,
                "reason": item.reason,
                "evidence_field": item.evidence_field,
                "frozen_value": item.frozen_value,
            }
            for item in registry.exclusions
        ],
        "provenance": {
            "runner_sha256": _runner_sha256(registry_path),
            "uv_lock_sha256": _sha256(source_root / "uv.lock"),
            "runtime": _runtime(),
            "data": _data_provenance(data_dir),
        },
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Prepare/reuse baseline and execute at most one independent variant."""
    (
        build_contract_schedule,
        isolated_baseline_checkout,
        isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        verify_carrier_checkout,
    ) = _project_imports()
    source_root = Path(args.source_root).resolve()
    registry_path = Path(args.registry).resolve()
    data_dir = Path(args.data_dir).resolve()
    output = Path(args.output).resolve()
    checkpoint_dir = Path(args.checkpoint_dir).resolve()
    if _git_output(source_root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("ablation orchestration requires an exact clean source HEAD")
    evidence_commit = _git_output(source_root, "rev-parse", "HEAD")
    requested_evidence_commit = getattr(args, "evidence_commit", None)
    if requested_evidence_commit is not None and requested_evidence_commit != evidence_commit:
        raise ValueError("ablation evidence commit differs from the exact source checkout")
    repository_root = (
        Path(args.repository_root).resolve()
        if getattr(args, "repository_root", None)
        else _repository_root(source_root)
    )
    registry_relative = registry_path.relative_to(source_root)
    registry = load_ablation_registry(registry_path)
    validate_ablation_registry(registry, source_root=source_root)
    schedule = build_contract_schedule(registry, source_root=source_root)
    frozen_replay_errors = _frozen_replay_error_anchors(registry, source_root=source_root)
    binding = _execution_binding(
        registry=registry,
        registry_path=registry_path,
        source_root=source_root,
        data_dir=data_dir,
        schedule=schedule,
    )
    binding_sha256 = hashlib.sha256(_canonical_bytes(binding)).hexdigest()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    binding_path = checkpoint_dir / "binding.json"
    binding_payload = {
        "schema_version": 2,
        "kind": "binding",
        "binding_sha256": binding_sha256,
        "binding": binding,
    }
    if binding_path.exists():
        observed_binding = _read_checkpoint(
            binding_path,
            binding_sha256=binding_sha256,
            kind="binding",
        )
        if observed_binding != binding_payload:
            raise ValueError("ablation binding artifact differs")
    else:
        _write_checkpoint(binding_path, binding_payload)
    schedule_path = checkpoint_dir / "schedule.json"
    schedule_payload = {
        "schema_version": 1,
        "kind": "schedule",
        "binding_sha256": binding_sha256,
        "schedule_sha256": binding["schedule_sha256"],
        "cells": _schedule_rows(schedule),
    }
    if schedule_path.exists():
        observed_schedule = _read_checkpoint(
            schedule_path,
            binding_sha256=binding_sha256,
            kind="schedule",
        )
        if observed_schedule != schedule_payload:
            raise ValueError("ablation schedule checkpoint is stale")
    else:
        _write_checkpoint(schedule_path, schedule_payload)

    baseline_path = checkpoint_dir / "baseline.json"
    baseline_command = _baseline_replay_command(
        repository_root=repository_root,
        evidence_commit=evidence_commit,
        registry_relative=registry_relative,
        data_dir=data_dir,
        checkpoint_dir=checkpoint_dir,
        output=output,
    )
    baseline_provenance = _expected_baseline_provenance(
        registry,
        source_root=source_root,
        binding=binding,
    )
    if baseline_path.exists() and _checkpoint_payload_schema(baseline_path) == 2:
        baseline_checkpoint, baseline_worker = _read_baseline_result(
            baseline_path,
            checkpoint_dir=checkpoint_dir,
            binding_sha256=binding_sha256,
            schedule=schedule,
            expected_replay_command=baseline_command,
            expected_provenance=baseline_provenance,
            frozen_replay_errors=frozen_replay_errors,
        )
    elif baseline_path.exists():
        legacy_baseline = _load_baseline_checkpoint(
            baseline_path,
            binding_sha256=binding_sha256,
            schedule=schedule,
        )
        legacy_worker = legacy_baseline.get("worker")
        if not isinstance(legacy_worker, Mapping):
            raise ValueError("ablation legacy baseline worker is missing")
        baseline_worker = dict(legacy_worker)
        _validate_exact_worker(
            baseline_worker,
            schedule=schedule,
            binding_sha256=binding_sha256,
            experiment_id="baseline",
            carrier_sha256=_BASELINE_CARRIER_SHA256,
            expected_provenance=baseline_provenance,
            frozen_replay_errors=frozen_replay_errors,
        )
        _write_baseline_result(
            checkpoint_dir=checkpoint_dir,
            binding_sha256=binding_sha256,
            schedule=schedule,
            worker=baseline_worker,
            replay_command=baseline_command,
            expected_provenance=baseline_provenance,
            frozen_replay_errors=frozen_replay_errors,
        )
        baseline_checkpoint, baseline_worker = _read_baseline_result(
            baseline_path,
            checkpoint_dir=checkpoint_dir,
            binding_sha256=binding_sha256,
            schedule=schedule,
            expected_replay_command=baseline_command,
            expected_provenance=baseline_provenance,
            frozen_replay_errors=frozen_replay_errors,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="uquant-phase2-baseline-") as temporary:
            checkout_destination = Path(temporary) / "checkout"
            worker_output = Path(temporary) / "worker.json"
            with isolated_baseline_checkout(
                registry,
                source_root=source_root,
                destination=checkout_destination,
            ) as checkout:
                checkout_provenance = _checkout_payload(checkout)
                probe = _probe_checkout(checkout.root, {})
                worker = _invoke_worker(
                    source_root=checkout.root,
                    data_dir=data_dir,
                    schedule_checkpoint=schedule_path,
                    binding_sha256=binding_sha256,
                    experiment_id="baseline",
                    config_changes={},
                    checkout=checkout_provenance,
                    output=worker_output,
                    worker_script=source_root / "scripts" / "run_generalization_ablation.py",
                )
                if _git_output(checkout.root, "status", "--porcelain", "--untracked-files=all"):
                    raise ValueError("isolated baseline changed during replay")
                _validate_worker_payload(
                    worker,
                    schedule=schedule,
                    binding_sha256=binding_sha256,
                    experiment_id="baseline",
                    carrier_sha256=_BASELINE_CARRIER_SHA256,
                    frozen_replay_errors=frozen_replay_errors,
                )
                _validate_worker_provenance(
                    worker,
                    binding=binding,
                    checkout=checkout_provenance,
                    effective_config_sha256=str(probe["effective_config_sha256"]),
                    fresh_account_sha256=str(probe["fresh_account_sha256"]),
                )
        baseline_worker = worker
        _write_baseline_result(
            checkpoint_dir=checkpoint_dir,
            binding_sha256=binding_sha256,
            schedule=schedule,
            worker=baseline_worker,
            replay_command=baseline_command,
            expected_provenance=baseline_provenance,
            frozen_replay_errors=frozen_replay_errors,
        )
        baseline_checkpoint, baseline_worker = _read_baseline_result(
            baseline_path,
            checkpoint_dir=checkpoint_dir,
            binding_sha256=binding_sha256,
            schedule=schedule,
            expected_replay_command=baseline_command,
            expected_provenance=baseline_provenance,
            frozen_replay_errors=frozen_replay_errors,
        )

    selected = args.experiment or []
    if args.baseline_only and selected:
        raise ValueError("ablation baseline-only mode cannot select an experiment")
    if not args.baseline_only and len(selected) != 1:
        raise ValueError("ablation run requires exactly one --experiment per process")
    if selected:
        experiment_id = selected[0]
        matches = tuple(item for item in registry.experiments if item.experiment_id == experiment_id)
        if len(matches) != 1:
            raise ValueError("ablation experiment is not registered")
        experiment = matches[0]
        experiment_path = checkpoint_dir / f"{experiment.experiment_id}.json"
        invalid_path = checkpoint_dir / "invalid" / f"{experiment.experiment_id}.json"
        reusable = (
            experiment_path.exists() and _checkpoint_payload_schema(experiment_path) == 2
        ) or invalid_path.exists()
        import_worker = getattr(args, "import_worker", None)
        if not reusable or args.rerun or import_worker is not None:
            expected_variant_provenance: dict[str, Any]
            if import_worker is not None:
                variant_worker = _load_json_mapping(
                    Path(import_worker).resolve(),
                    label="imported ablation raw worker",
                )
                expected_variant_provenance = _expected_variant_provenance(
                    registry,
                    experiment,
                    source_root=source_root,
                    binding=binding,
                )
            else:
                with tempfile.TemporaryDirectory(
                    prefix=f"uquant-generalization-{experiment.experiment_id}-"
                ) as temporary:
                    checkout_destination = Path(temporary) / "checkout"
                    worker_output = Path(temporary) / "worker.json"
                    with isolated_carrier_checkout(
                        registry,
                        experiment,
                        source_root=source_root,
                        destination=checkout_destination,
                    ) as checkout:
                        checkout_provenance = _checkout_payload(checkout)
                        changes = dict(checkout.config_changes)
                        probe = _probe_checkout(checkout.root, changes)
                        variant_worker = _invoke_worker(
                            source_root=checkout.root,
                            data_dir=data_dir,
                            schedule_checkpoint=schedule_path,
                            binding_sha256=binding_sha256,
                            experiment_id=experiment.experiment_id,
                            config_changes=changes,
                            checkout=checkout_provenance,
                            output=worker_output,
                            worker_script=source_root / "scripts" / "run_generalization_ablation.py",
                        )
                        verify_carrier_checkout(registry, experiment, checkout)
                        expected_variant_provenance = _expected_worker_provenance(
                            binding=binding,
                            checkout=checkout_provenance,
                            effective_config_sha256=str(probe["effective_config_sha256"]),
                            fresh_account_sha256=str(probe["fresh_account_sha256"]),
                        )
            _validate_exact_worker(
                variant_worker,
                schedule=schedule,
                binding_sha256=binding_sha256,
                experiment_id=experiment.experiment_id,
                carrier_sha256=experiment.carrier.sha256,
                expected_provenance=expected_variant_provenance,
                frozen_replay_errors=frozen_replay_errors,
            )
            if experiment_path.exists() and _checkpoint_payload_schema(experiment_path) == 1:
                legacy_path = checkpoint_dir / "legacy" / f"{experiment.experiment_id}.v1.json"
                legacy_path.parent.mkdir(parents=True, exist_ok=True)
                if legacy_path.exists() and legacy_path.read_bytes() != experiment_path.read_bytes():
                    raise ValueError("ablation legacy experiment archive differs")
                if not legacy_path.exists():
                    os.replace(experiment_path, legacy_path)
                else:
                    experiment_path.unlink()
            experiment_command = _replay_command(
                repository_root=repository_root,
                evidence_commit=evidence_commit,
                registry_relative=registry_relative,
                data_dir=data_dir,
                checkpoint_dir=checkpoint_dir,
                output=output,
                experiment_id=experiment.experiment_id,
            )
            _write_experiment_result(
                checkpoint_dir=checkpoint_dir,
                experiment=experiment,
                binding_sha256=binding_sha256,
                schedule=schedule,
                baseline_checkpoint=baseline_checkpoint,
                baseline_worker=baseline_worker,
                variant_worker=variant_worker,
                replay_command=experiment_command,
                expected_variant_provenance=expected_variant_provenance,
                frozen_replay_errors=frozen_replay_errors,
            )

    valid, invalid = _load_available_results(
        registry=registry,
        source_root=source_root,
        checkpoint_dir=checkpoint_dir,
        binding=binding,
        binding_sha256=binding_sha256,
        schedule=schedule,
        baseline_checkpoint=baseline_checkpoint,
        baseline_worker=baseline_worker,
        baseline_replay_command=baseline_command,
        baseline_provenance=baseline_provenance,
        repository_root=repository_root,
        evidence_commit=evidence_commit,
        registry_relative=registry_relative,
        data_dir=data_dir,
        output=output,
        frozen_replay_errors=frozen_replay_errors,
    )
    return _result_summary(
        registry=registry,
        binding=binding,
        binding_sha256=binding_sha256,
        checkpoint_dir=checkpoint_dir,
        baseline_path=baseline_path,
        baseline_checkpoint=baseline_checkpoint,
        valid=valid,
        invalid=invalid,
    )


def _readback_at_checkout(args: argparse.Namespace, *, source_root: Path) -> dict[str, Any]:
    """Strictly authenticate historical evidence without executing or rewriting workers."""
    (
        build_contract_schedule,
        _isolated_baseline_checkout,
        _isolated_carrier_checkout,
        load_ablation_registry,
        validate_ablation_registry,
        _verify_carrier_checkout,
    ) = _project_imports()
    repository_root = Path(args.repository_root).resolve()
    evidence_commit = str(args.evidence_commit)
    if _git_output(source_root, "rev-parse", "HEAD") != evidence_commit or _git_output(
        source_root, "status", "--porcelain", "--untracked-files=all"
    ):
        raise ValueError("ablation evidence checkout differs")
    registry_relative = Path(args.registry_relative)
    if registry_relative.is_absolute() or ".." in registry_relative.parts:
        raise ValueError("ablation registry relative path is malformed")
    registry_path = source_root / registry_relative
    data_dir = Path(args.data_dir).resolve()
    checkpoint_dir = Path(args.checkpoint_dir).resolve()
    replay_output = Path(args.replay_output).resolve()
    registry = load_ablation_registry(registry_path)
    validate_ablation_registry(registry, source_root=source_root)
    schedule = build_contract_schedule(registry, source_root=source_root)
    frozen_replay_errors = _frozen_replay_error_anchors(registry, source_root=source_root)
    binding = _execution_binding(
        registry=registry,
        registry_path=registry_path,
        source_root=source_root,
        data_dir=data_dir,
        schedule=schedule,
    )
    binding_sha256 = hashlib.sha256(_canonical_bytes(binding)).hexdigest()
    manifest_path, manifest_digest = _evidence_manifest_anchor(registry_relative)
    evidence_manifest = _compile_evidence_manifest(
        _load_trusted_evidence_manifest(
            manifest_path,
            trusted_digest=manifest_digest,
        ),
        registry=registry,
        evidence_commit=evidence_commit,
        binding_sha256=binding_sha256,
        schedule_sha256=str(binding["schedule_sha256"]),
    )
    _validate_evidence_archive(checkpoint_dir, evidence_manifest)
    binding_payload = _read_checkpoint(
        checkpoint_dir / "binding.json",
        binding_sha256=binding_sha256,
        kind="binding",
    )
    if binding_payload != {
        "schema_version": 2,
        "kind": "binding",
        "binding_sha256": binding_sha256,
        "binding": binding,
    }:
        raise ValueError("ablation binding artifact differs")
    expected_schedule = {
        "schema_version": 1,
        "kind": "schedule",
        "binding_sha256": binding_sha256,
        "schedule_sha256": binding["schedule_sha256"],
        "cells": _schedule_rows(schedule),
    }
    if (
        _read_checkpoint(
            checkpoint_dir / "schedule.json",
            binding_sha256=binding_sha256,
            kind="schedule",
        )
        != expected_schedule
    ):
        raise ValueError("ablation schedule checkpoint is stale")
    baseline_command = _baseline_replay_command(
        repository_root=repository_root,
        evidence_commit=evidence_commit,
        registry_relative=registry_relative,
        data_dir=data_dir,
        checkpoint_dir=checkpoint_dir,
        output=replay_output,
    )
    baseline_provenance = _expected_baseline_provenance(
        registry,
        source_root=source_root,
        binding=binding,
    )
    baseline_path = checkpoint_dir / "baseline.json"
    baseline_checkpoint, baseline_worker = _read_baseline_result(
        baseline_path,
        checkpoint_dir=checkpoint_dir,
        binding_sha256=binding_sha256,
        schedule=schedule,
        expected_replay_command=baseline_command,
        expected_provenance=baseline_provenance,
        frozen_replay_errors=frozen_replay_errors,
    )
    valid, invalid = _load_available_results(
        registry,
        source_root=source_root,
        checkpoint_dir=checkpoint_dir,
        binding=binding,
        binding_sha256=binding_sha256,
        schedule=schedule,
        baseline_checkpoint=baseline_checkpoint,
        baseline_worker=baseline_worker,
        baseline_replay_command=baseline_command,
        baseline_provenance=baseline_provenance,
        repository_root=repository_root,
        evidence_commit=evidence_commit,
        registry_relative=registry_relative,
        data_dir=data_dir,
        output=replay_output,
        frozen_replay_errors=frozen_replay_errors,
    )
    return _result_summary(
        registry=registry,
        binding=binding,
        binding_sha256=binding_sha256,
        checkpoint_dir=checkpoint_dir,
        baseline_path=baseline_path,
        baseline_checkpoint=baseline_checkpoint,
        valid=valid,
        invalid=invalid,
    )


def _replay(args: argparse.Namespace) -> dict[str, Any]:
    repository_root = Path(args.repository_root).resolve()
    with _isolated_evidence_checkout(repository_root, str(args.evidence_commit)) as source_root:
        registry_relative = Path(args.registry_relative)
        if registry_relative.is_absolute() or ".." in registry_relative.parts:
            raise ValueError("ablation registry relative path is malformed")
        run_args = argparse.Namespace(
            source_root=str(source_root),
            registry=str(source_root / registry_relative),
            data_dir=args.data_dir,
            output=args.output,
            checkpoint_dir=args.checkpoint_dir,
            experiment=args.experiment,
            baseline_only=args.baseline_only,
            rerun=args.rerun,
            import_worker=args.import_worker,
            repository_root=str(repository_root),
            evidence_commit=args.evidence_commit,
        )
        return _run(run_args)


def _readback(args: argparse.Namespace) -> dict[str, Any]:
    repository_root = Path(args.repository_root).resolve()
    with _isolated_evidence_checkout(repository_root, str(args.evidence_commit)) as source_root:
        return _readback_at_checkout(args, source_root=source_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_generalization_ablation.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--source-root", required=True)
        command.add_argument("--registry", required=True)
        command.add_argument("--data-dir", default="data/frozen")
        command.add_argument("--output", required=name == "run")
        if name == "run":
            command.add_argument("--experiment", action="append", default=None)
            command.add_argument("--baseline-only", action="store_true")
            command.add_argument("--checkpoint-dir", required=True)
            command.add_argument("--rerun", action="store_true")
            command.add_argument("--import-worker")
            command.add_argument("--repository-root")
            command.add_argument("--evidence-commit")
    for name in ("replay", "readback"):
        command = subparsers.add_parser(name)
        command.add_argument("--repository-root", required=True)
        command.add_argument("--evidence-commit", required=True)
        command.add_argument("--registry-relative", required=True)
        command.add_argument("--data-dir", required=True)
        command.add_argument("--checkpoint-dir", required=True)
        command.add_argument("--output", required=True)
        if name == "replay":
            command.add_argument("--experiment", action="append", default=None)
            command.add_argument("--baseline-only", action="store_true")
            command.add_argument("--rerun", action="store_true")
            command.add_argument("--import-worker")
        else:
            command.add_argument("--replay-output", required=True)
    worker = subparsers.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("--source-root", required=True)
    worker.add_argument("--data-dir", required=True)
    worker.add_argument("--schedule-checkpoint", required=True)
    worker.add_argument("--binding-sha256", required=True)
    worker.add_argument("--experiment-id", required=True)
    worker.add_argument("--config-json", required=True)
    worker.add_argument("--checkout-json", required=True)
    worker.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate carriers or execute a sequential full-contract replay."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            payload = _validate(args)
        elif args.command == "worker":
            payload = _worker(args)
        elif args.command == "replay":
            payload = _replay(args)
        elif args.command == "readback":
            payload = _readback(args)
        else:
            payload = _run(args)
        encoded = _canonical_bytes(payload).decode()
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(encoded + "\n", encoding="utf-8")
        if args.command != "worker":
            print(encoded)
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"generalization ablation failed closed: {exc}", file=sys.stderr)
        return 1


_CLI_SEAM_LOCK = RLock()


@contextmanager
def generalization_cli_seams(
    *, probe_checkout_seam: Callable[[Path, Mapping[str, bool]], dict[str, Any]]
) -> Iterator[None]:
    """Install the one frozen import-mode test seam for a bounded call."""

    global probe_checkout
    with _CLI_SEAM_LOCK:
        original = probe_checkout
        probe_checkout = probe_checkout_seam
        try:
            yield
        finally:
            probe_checkout = original


CAUSAL_STAGES = _CAUSAL_STAGES
BASELINE_CARRIER_SHA256 = _BASELINE_CARRIER_SHA256
EVIDENCE_MANIFEST_CANONICAL_SHA256 = _EVIDENCE_MANIFEST_CANONICAL_SHA256
EVIDENCE_MANIFEST_PATH = _EVIDENCE_MANIFEST_PATH
MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256 = _MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256
MINIMAL_EVIDENCE_MANIFEST_PATH = _MINIMAL_EVIDENCE_MANIFEST_PATH
baseline_config_sha256 = _baseline_config_sha256
canonical_bytes = _canonical_bytes
checkpoint_payload_schema = _checkpoint_payload_schema
compare_worker_payloads = _compare_worker_payloads
compile_evidence_manifest = _compile_evidence_manifest
evidence_coverage = _evidence_coverage
evidence_manifest_anchor = _evidence_manifest_anchor
first_hashed_divergence = _first_hashed_divergence
frozen_replay_error_anchors = _frozen_replay_error_anchors
git_output = _git_output
isolated_evidence_checkout = _isolated_evidence_checkout
load_json_mapping = _load_json_mapping
load_trusted_evidence_manifest = _load_trusted_evidence_manifest
read_baseline_result = _read_baseline_result
read_checkpoint = _read_checkpoint
read_experiment_result = _read_experiment_result
read_worker_artifact = _read_worker_artifact
replay_cell = _replay_cell
replay_command = _replay_command
select_experiment_result_path = _select_experiment_result_path
sha256_mapping = _sha256_mapping
validate_evidence_manifest_entry = _validate_evidence_manifest_entry
validate_experiment_checkpoints = _validate_experiment_checkpoints
validate_replay_command = _validate_replay_command
validate_worker_payload = _validate_worker_payload
write_baseline_result = _write_baseline_result
write_checkpoint = _write_checkpoint
write_experiment_result = _write_experiment_result
write_worker_artifact = _write_worker_artifact


if __name__ == "__main__":
    raise SystemExit(main())
