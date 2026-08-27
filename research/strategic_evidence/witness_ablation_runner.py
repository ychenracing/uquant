"""Resumable, streaming Task-4 witness-ablation execution and verification."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess  # nosec B404
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from uquant.atomic_io import atomic_write_text
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.universe import default_ai_universe
from uquant.engine import ProductionEngine
from uquant.validation.generalization.scenarios import compute_pre_window_evidence
from uquant.validation.generalization_contract import INDUSTRY_MIN_SAMPLE

from .contract import StrategicEvidenceContract, load_contract
from .forced_owner_runner import verify_frozen_inputs
from .models import canonical_sha256, require_sha256
from .provenance import (
    build_provenance,
    canonical_json_bytes,
    seal_payload,
    validate_provenance,
    verify_sealed_payload,
)
from .replay import ReplayRequest, ReplayResult, run_replay
from .trace import RouteTraceRow
from .witness_ablation import (
    BASELINE,
    DIAGNOSTIC_ONLY,
    ECONOMIC,
    EVIDENCE_REMOVAL,
    FULL_REMOVAL,
    TRADABLE_REMOVAL,
    AblationCell,
    AblationSpec,
    DiagnosticProjectionRow,
    FirstDivergences,
    ablation_cell_from_compact,
    cell_from_replay,
    derive_first_divergences,
    derive_symbol_roles,
    diagnostic_projection,
    diagnostic_projection_from_compact,
    enumerate_initial_specs,
    is_decisive,
    minimal_decisive_witness_sets,
    necessary_triple_support,
    rank_critical_symbols,
    select_bounded_search,
)

_STREAM_SCHEMA_VERSION = 1
_ROUTE_METADATA_FIELDS = frozenset(
    {
        "path",
        "byte_size",
        "bytes_sha256",
        "row_count",
        "rows_sha256",
        "linkage_sha256",
        "header_payload_sha256",
        "resume_identity",
    }
)
_LOGICAL_ROUTE_PATH = (
    "artifacts/strategic_evidence_closure/external/"
    "checkpoint4_witness_ablation_full_routes.jsonl.gz"
)
_DEFAULT_SUMMARY = Path("artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_full.json")
_DEFAULT_MANIFEST = Path("artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_manifest.json")
_TASK4_TEMP_ROOT = Path(tempfile.gettempdir()) / "uquant-strategic-evidence" / "task4"
_DEFAULT_TRACE_SHARD = _TASK4_TEMP_ROOT / "witness_ablation_full_routes.jsonl.gz"
_DEFAULT_RESUME_DIR = _TASK4_TEMP_ROOT / "resume"
_SENTINEL_END = "2023-01-10"


class _BinaryWriter(Protocol):
    def write(self, data: bytes, /) -> int: ...


def _source_file_manifest(repository: Path, paths: Sequence[Path]) -> dict[str, str]:
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Task 4 source manifest paths are empty or duplicated")
    result: dict[str, str] = {}
    for path in sorted(paths):
        resolved = path.resolve()
        if not resolved.is_relative_to(repository) or not resolved.is_file():
            raise ValueError("Task 4 source manifest path is missing or escapes the repository")
        result[resolved.relative_to(repository).as_posix()] = _sha256_file(resolved)
    return result


def build_executable_source_manifest(
    root: str | Path,
    *,
    require_clean: bool = True,
) -> dict[str, Any]:
    """Seal every research file on the actual replay/ablation execution path."""

    repository = Path(root).resolve()
    paths = [repository / "research" / "candidate_runner.py"]
    paths.extend(sorted((repository / "research" / "strategic_evidence").glob("*.py")))
    files = _source_file_manifest(repository, paths)
    if require_clean:
        status = subprocess.check_output(  # nosec B603, B607
            ["git", "status", "--porcelain", "--", *files],
            cwd=repository,
            text=True,
        )
        if status.strip():
            raise ValueError("Task 4 executable research source is dirty")
        head = _git_commit(repository)
        for relative, digest in files.items():
            committed = subprocess.check_output(  # nosec B603, B607
                ["git", "show", f"{head}:{relative}"],
                cwd=repository,
            )
            if hashlib.sha256(committed).hexdigest() != digest:
                raise ValueError("Task 4 executable source differs from exact HEAD")
    return {"files": files, "manifest_sha256": canonical_sha256({"files": files})}


def recompute_task4_identities(
    root: str | Path,
    *,
    contract: StrategicEvidenceContract,
) -> dict[str, str]:
    """Compute runtime input identities from bytes and live contracts, never copied strings."""

    repository = Path(root).resolve()
    fixed = (
        repository / "pyproject.toml",
        repository / "requirements.txt",
        repository / "uv.lock",
        repository / "benchmarks" / "reference_registry.json",
        repository / "benchmarks" / "config_parameter_governance.json",
    )
    production_paths = [*fixed, *sorted((repository / "uquant").rglob("*.py"))]
    production_files = _source_file_manifest(repository, production_paths)
    research = build_executable_source_manifest(repository, require_clean=False)
    industries = dict(_contract_industries(contract))
    return {
        "production_source_sha256": canonical_sha256({"files": production_files}),
        "research_source_sha256": str(research["manifest_sha256"]),
        "config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "data_manifest_sha256": _sha256_file(repository / "data" / "frozen" / "DATA_MANIFEST.json"),
        "universe_sha256": canonical_sha256({"symbols": list(contract.canonical_universe)}),
        "industry_mapping_sha256": canonical_sha256(
            {"as_of": contract.window["end"], "industries": industries}
        ),
        "window_sha256": canonical_sha256(dict(contract.window)),
        "uv_lock_sha256": _sha256_file(repository / "uv.lock"),
    }


def capture_runtime_metadata(root: str | Path) -> dict[str, str]:
    """Capture truthful first-run runtime values for sealing and resume reuse."""

    del root
    import numpy as np
    import pandas as pd

    uv = subprocess.check_output(  # nosec B603, B607
        ["uv", "--version"], text=True
    ).strip()
    if not uv:
        raise ValueError("Task 4 uv runtime version is empty")
    return {
        "python": subprocess.check_output(  # nosec B603, B607
            ["python", "-c", "import platform; print(platform.python_version())"],
            text=True,
        ).strip(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "uv": uv,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _checkpoint_provenance(path: Path) -> dict[str, Any]:
    """Read a checkpoint provenance only after its complete stream verifies."""

    verify_streaming_shard(path)
    try:
        with gzip.open(path, "rb") as stream:
            first = stream.readline()
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ValueError("witness ablation gzip shard is unreadable") from exc
    if not first:
        raise ValueError("witness ablation gzip shard is empty")
    header = _verify_header(
        _decode_record(first),
        expected_provenance=None,
        expected_resume_identity=None,
    )
    provenance = header["provenance"]
    if not isinstance(provenance, Mapping):
        raise ValueError("witness ablation checkpoint provenance is malformed")
    return validate_provenance(provenance)


def resolve_resume_runtime_metadata(
    resume_dir: str | Path,
    *,
    resume: bool,
    current: Mapping[str, str],
) -> dict[str, str]:
    """Reuse the first run time while requiring the same actual runtime on resume."""

    required = {"python", "numpy", "pandas", "uv", "generated_at"}
    if set(current) != required or not all(
        isinstance(value, str) and value for value in current.values()
    ):
        raise ValueError("Task 4 current runtime metadata differs")
    result = dict(current)
    checkpoints = sorted(Path(resume_dir).glob("*.jsonl.gz")) if resume else []
    if not checkpoints:
        return result
    first_provenance = _checkpoint_provenance(checkpoints[0])
    historical = {field: str(first_provenance[field]) for field in required}
    for field in required - {"generated_at"}:
        if historical[field] != result[field]:
            raise ValueError(f"Task 4 resume runtime differs: {field}")
    for checkpoint in checkpoints[1:]:
        observed = _checkpoint_provenance(checkpoint)
        if any(observed[field] != first_provenance[field] for field in required):
            raise ValueError("Task 4 resume checkpoints bind different runtimes")
    result["generated_at"] = historical["generated_at"]
    return result


def _contract_industries(contract: StrategicEvidenceContract) -> tuple[tuple[str, str], ...]:
    universe = default_ai_universe()
    result = tuple(
        (symbol, universe.industry_of(symbol, contract.window["end"]))
        for symbol in contract.canonical_universe
    )
    if any(industry == "unknown" for _, industry in result):
        raise ValueError("Task 4 canonical industry mapping is incomplete")
    return result


@dataclass(frozen=True, slots=True)
class BalancedIndustryUniverse:
    """Concrete causal/PIT retained universe for the special sixth industry cell."""

    evidence_as_of: str
    per_industry: int
    symbols: tuple[str, ...]
    removed_symbols: tuple[str, ...]
    industries: tuple[tuple[str, str], ...]
    industry_mapping_sha256: str
    evidence_sha256: str
    symbols_sha256: str

    def validate(self, *, contract: StrategicEvidenceContract) -> None:
        canonical = tuple(contract.canonical_universe)
        try:
            evidence_date = date.fromisoformat(self.evidence_as_of)
            window_start = date.fromisoformat(contract.window["start"])
        except ValueError as exc:
            raise ValueError("balanced industry universe evidence date is malformed") from exc
        if evidence_date >= window_start:
            raise ValueError("balanced industry universe evidence is not causal")
        if self.per_industry != INDUSTRY_MIN_SAMPLE:
            raise ValueError("balanced industry universe sample size differs")
        if self.symbols != tuple(sorted(set(self.symbols))) or not self.symbols:
            raise ValueError("balanced industry universe symbols are malformed")
        if self.removed_symbols != tuple(sorted(set(self.removed_symbols))):
            raise ValueError("balanced industry universe removals are malformed")
        if set(self.symbols) & set(self.removed_symbols) or set(self.symbols) | set(
            self.removed_symbols
        ) != set(canonical):
            raise ValueError("balanced industry universe does not partition canonical symbols")
        if self.industries != tuple(sorted(self.industries)):
            raise ValueError("balanced industry universe industries are not canonical")
        if tuple(symbol for symbol, _ in self.industries) != tuple(
            symbol for symbol in canonical if symbol not in set(self.removed_symbols)
        ):
            raise ValueError("balanced industry universe industry coverage differs")
        if any(not industry or industry == "unknown" for _, industry in self.industries):
            raise ValueError("balanced industry universe industry mapping is incomplete")
        expected_industry_hash = canonical_sha256(
            {
                "as_of": self.evidence_as_of,
                "industries": {symbol: industry for symbol, industry in self.industries},
            }
        )
        expected_symbols_hash = canonical_sha256({"symbols": list(self.symbols)})
        if self.industry_mapping_sha256 != expected_industry_hash:
            raise ValueError("balanced industry universe mapping seal differs")
        if self.symbols_sha256 != expected_symbols_hash:
            raise ValueError("balanced industry universe symbol seal differs")
        require_sha256(self.evidence_sha256, field="balanced industry evidence sha256")

    def compact(self) -> dict[str, Any]:
        return {
            "evidence_as_of": self.evidence_as_of,
            "per_industry": self.per_industry,
            "symbols": list(self.symbols),
            "removed_symbols": list(self.removed_symbols),
            "industries": {symbol: industry for symbol, industry in self.industries},
            "industry_mapping_sha256": self.industry_mapping_sha256,
            "evidence_sha256": self.evidence_sha256,
            "symbols_sha256": self.symbols_sha256,
        }


def derive_balanced_industry_universe(
    data_dir: str | Path,
    *,
    contract: StrategicEvidenceContract,
) -> BalancedIndustryUniverse:
    """Derive the production-generalization balanced universe from pre-window bytes."""

    universe = default_ai_universe()
    day_before = date.fromisoformat(contract.window["start"]) - timedelta(days=1)
    pit_symbols = universe.symbols_as_of(day_before)
    if not pit_symbols or not set(pit_symbols) <= set(contract.canonical_universe):
        raise ValueError("balanced industry PIT universe differs from the Task 4 contract")
    engine = ProductionEngine(data_dir)
    engine.workspace.load(pit_symbols)
    histories = {symbol: engine.workspace.raw_frame(symbol)["close"] for symbol in pit_symbols}
    evidence = compute_pre_window_evidence(
        histories,
        pit_symbols,
        window_start=contract.window["start"],
    )
    if universe.symbols_as_of(evidence.as_of) != pit_symbols:
        raise ValueError("balanced industry evidence date changes PIT membership")
    industries = {symbol: universe.industry_of(symbol, evidence.as_of) for symbol in pit_symbols}
    if any(industry == "unknown" for industry in industries.values()):
        raise ValueError("balanced industry PIT mapping is incomplete")
    scores = evidence.score_map()
    grouped: dict[str, list[str]] = {}
    for symbol in pit_symbols:
        grouped.setdefault(industries[symbol], []).append(symbol)
    balanced = tuple(
        sorted(
            symbol
            for industry in sorted(grouped)
            for symbol in sorted(
                grouped[industry],
                key=lambda item: (item not in scores, -scores.get(item, 0.0), item),
            )[:INDUSTRY_MIN_SAMPLE]
        )
    )
    removed = tuple(sorted(set(contract.canonical_universe) - set(balanced)))
    retained_industries = tuple((symbol, industries[symbol]) for symbol in balanced)
    evidence_hash = canonical_sha256(
        {
            "as_of": evidence.as_of,
            "scores": [[symbol, score] for symbol, score in evidence.scores],
            "ineligible_symbols": list(evidence.ineligible_symbols),
        }
    )
    result = BalancedIndustryUniverse(
        evidence_as_of=evidence.as_of,
        per_industry=INDUSTRY_MIN_SAMPLE,
        symbols=balanced,
        removed_symbols=removed,
        industries=retained_industries,
        industry_mapping_sha256=canonical_sha256(
            {"as_of": evidence.as_of, "industries": dict(retained_industries)}
        ),
        evidence_sha256=evidence_hash,
        symbols_sha256=canonical_sha256({"symbols": list(balanced)}),
    )
    result.validate(contract=contract)
    return result


def task4_sentinel_specs(initial_specs: Sequence[AblationSpec]) -> tuple[AblationSpec, ...]:
    """Select the preregistered critical symbol across all three removal axes."""

    desired = (FULL_REMOVAL, EVIDENCE_REMOVAL, TRADABLE_REMOVAL)
    by_axis = {
        spec.axis: spec
        for spec in initial_specs
        if spec.scope == "CANONICAL_LEAVE_ONE_OUT" and spec.subject == "sz300308"
    }
    if set(by_axis) != set(desired):
        raise ValueError("Task 4 representative sentinel coverage differs")
    return tuple(by_axis[axis] for axis in desired)


def comparison_baseline_scope(spec: AblationSpec) -> str:
    """Name the exact baseline universe against which one cell is attributable."""

    return (
        "REPORT_UNIVERSE_13"
        if spec.scope == "REPORT_UNIVERSE_LEAVE_ONE_OUT"
        else "CANONICAL_34"
    )


def build_task4_scenario(
    *,
    contract: StrategicEvidenceContract,
    initial_specs: Sequence[AblationSpec],
    balanced: BalancedIndustryUniverse,
    source_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the exact 117-cell initial matrix and bounded-search ceiling."""

    specs = tuple(initial_specs)
    cell_ids = [spec.cell_id for spec in specs]
    economic = sum(spec.evidence_class == ECONOMIC for spec in specs)
    diagnostic = sum(spec.evidence_class == DIAGNOSTIC_ONLY for spec in specs)
    if len(specs) != 117 or len(set(cell_ids)) != 117 or economic != 49 or diagnostic != 68:
        raise ValueError("Task 4 scenario requires exact 117/49/68 initial coverage")
    matrix = contract.raw.get("matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("Task 4 scenario matrix contract is missing")
    critical = matrix.get("critical_symbols")
    if not isinstance(critical, list) or critical != ["sz300308", "sz300502", "sz300394"]:
        raise ValueError("Task 4 preregistered critical symbols differ")
    balanced.validate(contract=contract)
    if set(source_manifest) != {"files", "manifest_sha256"} or not isinstance(
        source_manifest.get("files"), Mapping
    ):
        raise ValueError("Task 4 executable source manifest differs")
    source_files = dict(source_manifest["files"])
    if source_manifest["manifest_sha256"] != canonical_sha256({"files": source_files}):
        raise ValueError("Task 4 executable source manifest seal differs")
    for required_path in (
        "research/candidate_runner.py",
        "research/strategic_evidence/replay.py",
        "research/strategic_evidence/witness_ablation.py",
        "research/strategic_evidence/witness_ablation_runner.py",
    ):
        if required_path not in source_files:
            raise ValueError("Task 4 executable source manifest coverage differs")
    industries = _contract_industries(contract)
    sentinels = task4_sentinel_specs(specs)
    report13 = matrix.get("report_universe_13")
    if not isinstance(report13, list) or not all(isinstance(symbol, str) for symbol in report13):
        raise ValueError("Task 4 report comparison baseline differs")
    return {
        "kind": "task4-witness-ablation-and-first-divergence",
        "contract_payload_sha256": contract.payload_sha256,
        "base_commit": contract.base_commit,
        "window": dict(contract.window),
        "future_holdout_boundary": contract.future_holdout_boundary,
        "random_seed": contract.random_seed,
        "required_initial_cell_ids": cell_ids,
        "required_initial_cell_count": 117,
        "economic_initial_cell_count": 49,
        "diagnostic_initial_cell_count": 68,
        "preregistered_critical_symbols": list(critical),
        "top_symbol_limit": 8,
        "required_pair_count": 28,
        "triple_rule": "only data-supported necessary triples",
        "replacement_reason": (
            "prior Task 4 runner materialized all gzip route rows during assembly and verification"
        ),
        "executable_source_manifest": {
            "files": source_files,
            "manifest_sha256": source_manifest["manifest_sha256"],
        },
        "sentinel_window": {"start": contract.window["start"], "end": _SENTINEL_END},
        "sentinel_cell_ids": [spec.cell_id for spec in sentinels],
        "comparison_baselines": {
            "CANONICAL_34": {
                "symbols": list(contract.canonical_universe),
                "symbols_sha256": canonical_sha256(
                    {"symbols": list(contract.canonical_universe)}
                ),
            },
            "REPORT_UNIVERSE_13": {
                "symbols": list(report13),
                "symbols_sha256": canonical_sha256({"symbols": list(report13)}),
            },
        },
        "industries": {symbol: industry for symbol, industry in industries},
        "point_in_time_industries": {symbol: industry for symbol, industry in balanced.industries},
        "balanced_industry_universe": balanced.compact(),
    }


def build_resume_identity(provenance: Mapping[str, Any], spec: AblationSpec) -> str:
    """Bind resume to every exact provenance field and the complete cell scenario."""

    validated = validate_provenance(provenance)
    return canonical_sha256(
        {
            "kind": "task4-witness-ablation-cell",
            "provenance": validated,
            "spec": spec.compact(),
        }
    )


def _stream_header(
    provenance: Mapping[str, Any],
    *,
    resume_identity: str,
) -> dict[str, Any]:
    require_sha256(resume_identity, field="witness ablation resume identity")
    return seal_payload(
        {
            "record_type": "HEADER",
            "schema_version": _STREAM_SCHEMA_VERSION,
            "provenance": validate_provenance(provenance),
            "resume_identity": resume_identity,
        }
    )


def _stream_row(index: int, payload: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "record_type": "ROW",
        "index": index,
        "payload": dict(payload),
    }
    row["row_sha256"] = canonical_sha256({"task4_stream_row": row})
    return row


def _stream_footer(
    *,
    header: Mapping[str, Any],
    row_count: int,
    rows_sha256: str,
) -> dict[str, Any]:
    require_sha256(rows_sha256, field="witness ablation rows sha256")
    return seal_payload(
        {
            "record_type": "FOOTER",
            "row_count": row_count,
            "rows_sha256": rows_sha256,
            "linkage_sha256": canonical_sha256(
                {
                    "header_payload_sha256": header["payload_sha256"],
                    "row_count": row_count,
                    "rows_sha256": rows_sha256,
                }
            ),
        }
    )


def _write_line(stream: _BinaryWriter, value: Mapping[str, Any]) -> bytes:
    encoded = canonical_json_bytes(value) + b"\n"
    stream.write(encoded)
    return encoded


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_streaming_shard(
    path: str | Path,
    *,
    rows: Iterable[Mapping[str, Any]],
    provenance: Mapping[str, Any],
    resume_identity: str,
) -> dict[str, Any]:
    """Atomically write and immediately verify one deterministic one-pass shard."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = _stream_header(provenance, resume_identity=resume_identity)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    row_digest = hashlib.sha256()
    row_count = 0
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw,
                mtime=0,
            ) as compressed:
                _write_line(compressed, header)
                for row_count, raw_row in enumerate(rows, start=1):
                    sealed_row = _stream_row(row_count - 1, raw_row)
                    encoded = _write_line(compressed, sealed_row)
                    row_digest.update(encoded)
                footer = _stream_footer(
                    header=header,
                    row_count=row_count,
                    rows_sha256=row_digest.hexdigest(),
                )
                _write_line(compressed, footer)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    metadata = verify_streaming_shard(
        target,
        expected_provenance=provenance,
        expected_resume_identity=resume_identity,
    )
    return metadata


def _decode_record(raw_line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw_line)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("witness ablation shard contains malformed JSONL") from exc
    if not isinstance(value, dict):
        raise ValueError("witness ablation shard record must be an object")
    return value


def _verify_header(
    value: object,
    *,
    expected_provenance: Mapping[str, Any] | None,
    expected_resume_identity: str | None,
) -> dict[str, Any]:
    header = verify_sealed_payload(value, label="witness ablation stream header")
    if (
        set(header)
        != {
            "record_type",
            "schema_version",
            "provenance",
            "resume_identity",
            "payload_sha256",
        }
        or header["record_type"] != "HEADER"
        or header["schema_version"] != _STREAM_SCHEMA_VERSION
    ):
        raise ValueError("witness ablation shard header differs")
    provenance = header["provenance"]
    if not isinstance(provenance, Mapping):
        raise ValueError("witness ablation shard provenance is malformed")
    validated = validate_provenance(provenance)
    if expected_provenance is not None and validated != validate_provenance(expected_provenance):
        raise ValueError("witness ablation shard provenance differs")
    identity = require_sha256(header["resume_identity"], field="witness ablation resume identity")
    if expected_resume_identity is not None and identity != require_sha256(
        expected_resume_identity, field="expected witness ablation resume identity"
    ):
        raise ValueError("witness ablation shard resume identity differs")
    return header


def _verify_row(value: object, *, expected_index: int) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, Mapping):
        raise ValueError("witness ablation stream row is malformed")
    raw = dict(value)
    if set(raw) != {"record_type", "index", "payload", "row_sha256"}:
        raise ValueError("witness ablation stream row fields differ")
    if raw["record_type"] != "ROW" or raw["index"] != expected_index:
        raise ValueError("witness ablation stream row ordering differs")
    payload = raw["payload"]
    if not isinstance(payload, Mapping):
        raise ValueError("witness ablation stream row payload is malformed")
    seal = require_sha256(raw.pop("row_sha256"), field="witness ablation row sha256")
    if seal != canonical_sha256({"task4_stream_row": raw}):
        raise ValueError("witness ablation stream row seal differs")
    sealed = {**raw, "row_sha256": seal}
    return dict(payload), canonical_json_bytes(sealed) + b"\n"


def _verify_footer(
    value: object,
    *,
    header: Mapping[str, Any],
    row_count: int,
    rows_sha256: str,
) -> dict[str, Any]:
    footer = verify_sealed_payload(value, label="witness ablation stream footer")
    expected = _stream_footer(
        header=header,
        row_count=row_count,
        rows_sha256=rows_sha256,
    )
    if footer != expected:
        raise ValueError("witness ablation shard footer or linkage differs")
    return footer


def _iter_verified_rows(
    path: Path,
    *,
    expected_provenance: Mapping[str, Any] | None,
    expected_resume_identity: str | None,
) -> Iterator[dict[str, Any]]:
    """Yield payloads while verifying all seals when the iterator is exhausted."""

    try:
        with gzip.open(path, "rb") as stream:
            first = stream.readline()
            if not first:
                raise ValueError("witness ablation gzip shard is empty")
            header = _verify_header(
                _decode_record(first),
                expected_provenance=expected_provenance,
                expected_resume_identity=expected_resume_identity,
            )
            digest = hashlib.sha256()
            count = 0
            footer: dict[str, Any] | None = None
            for raw_line in stream:
                record = _decode_record(raw_line)
                if record.get("record_type") == "FOOTER":
                    if footer is not None:
                        raise ValueError("witness ablation shard contains duplicate footers")
                    footer = _verify_footer(
                        record,
                        header=header,
                        row_count=count,
                        rows_sha256=digest.hexdigest(),
                    )
                    continue
                if footer is not None:
                    raise ValueError("witness ablation shard has rows after its footer")
                payload, encoded = _verify_row(record, expected_index=count)
                digest.update(encoded)
                count += 1
                yield payload
            if footer is None:
                raise ValueError("witness ablation shard footer is absent")
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ValueError("witness ablation gzip shard is unreadable") from exc


def iter_streaming_rows(
    path: str | Path,
    *,
    expected_provenance: Mapping[str, Any] | None = None,
    expected_resume_identity: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream verified payloads; callers must exhaust the iterator."""

    yield from _iter_verified_rows(
        Path(path),
        expected_provenance=expected_provenance,
        expected_resume_identity=expected_resume_identity,
    )


def verify_streaming_shard(
    path: str | Path,
    *,
    expected_provenance: Mapping[str, Any] | None = None,
    expected_resume_identity: str | None = None,
) -> dict[str, Any]:
    """Verify a shard in O(one row) memory and return portable-linkage metadata."""

    target = Path(path).resolve()
    try:
        with gzip.open(target, "rb") as stream:
            first = stream.readline()
            if not first:
                raise ValueError("witness ablation gzip shard is empty")
            header = _verify_header(
                _decode_record(first),
                expected_provenance=expected_provenance,
                expected_resume_identity=expected_resume_identity,
            )
            digest = hashlib.sha256()
            count = 0
            footer: dict[str, Any] | None = None
            for raw_line in stream:
                record = _decode_record(raw_line)
                if record.get("record_type") == "FOOTER":
                    if footer is not None:
                        raise ValueError("witness ablation shard contains duplicate footers")
                    footer = _verify_footer(
                        record,
                        header=header,
                        row_count=count,
                        rows_sha256=digest.hexdigest(),
                    )
                    continue
                if footer is not None:
                    raise ValueError("witness ablation shard has rows after its footer")
                _, encoded = _verify_row(record, expected_index=count)
                digest.update(encoded)
                count += 1
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise ValueError("witness ablation gzip shard is unreadable") from exc
    if footer is None:
        raise ValueError("witness ablation shard footer is absent")
    return {
        "path": str(target),
        "byte_size": target.stat().st_size,
        "bytes_sha256": _sha256_file(target),
        "row_count": count,
        "rows_sha256": footer["rows_sha256"],
        "linkage_sha256": footer["linkage_sha256"],
        "header_payload_sha256": header["payload_sha256"],
        "resume_identity": header["resume_identity"],
    }


def assemble_full_route_shard(
    path: str | Path,
    *,
    cell_shards: Iterable[str | Path],
    provenance: Mapping[str, Any],
    resume_identity: str,
) -> dict[str, Any]:
    """Assemble verified cell rows without retaining routes or accounts in memory."""

    def rows() -> Iterator[Mapping[str, Any]]:
        for raw_source in cell_shards:
            source = Path(raw_source)
            yield from iter_streaming_rows(
                source,
                expected_provenance=provenance,
            )

    return write_streaming_shard(
        path,
        rows=rows(),
        provenance=provenance,
        resume_identity=resume_identity,
    )


def write_cell_shard(
    path: str | Path,
    *,
    cell: AblationCell,
    result: ReplayResult,
    divergences: FirstDivergences,
    diagnostic_projection: Sequence[DiagnosticProjectionRow] = (),
    provenance: Mapping[str, Any],
    resume_identity: str,
) -> dict[str, Any]:
    """Checkpoint one compact cell followed by every retained route row."""

    trace = result.trace
    if not isinstance(trace, tuple) or not all(isinstance(row, RouteTraceRow) for row in trace):
        raise ValueError("witness ablation cell result trace is malformed")
    if len(trace) != cell.partial_trace_row_count:
        raise ValueError("witness ablation cell trace count differs")
    projections = tuple(diagnostic_projection)
    if len(projections) != cell.diagnostic_projection_row_count:
        raise ValueError("witness ablation cell diagnostic projection count differs")
    projection_sha = (
        None
        if not projections
        else canonical_sha256(
            {"diagnostic_projection": [row.compact() for row in projections]}
        )
    )
    if projection_sha != cell.diagnostic_projection_sha256:
        raise ValueError("witness ablation cell diagnostic projection seal differs")

    def rows() -> Iterator[Mapping[str, Any]]:
        yield {
            "record_type": "CELL",
            "cell": cell.compact(),
            "divergences": divergences.compact(),
            "route_row_count": len(trace),
            "diagnostic_projection_row_count": len(projections),
        }
        for index, row in enumerate(trace):
            yield {
                "record_type": "ROUTE",
                "cell_id": cell.cell_id,
                "route_index": index,
                "trace": asdict(row),
            }
        for index, projection in enumerate(projections):
            yield {
                "record_type": "DIAGNOSTIC_PROJECTION",
                "cell_id": cell.cell_id,
                "projection_index": index,
                "projection": projection.compact(),
            }

    return write_streaming_shard(
        path,
        rows=rows(),
        provenance=provenance,
        resume_identity=resume_identity,
    )


def _route_from_mapping(value: object) -> RouteTraceRow:
    if not isinstance(value, Mapping):
        raise ValueError("witness ablation route row is malformed")
    raw = dict(value)
    try:
        return RouteTraceRow(
            date=str(raw["date"]),
            reference_context=dict(raw["reference_context"]),
            leaders=tuple(dict(item) for item in raw["leaders"]),
            risk=dict(raw["risk"]),
            opportunity=str(raw["opportunity"]),
            targets=tuple(dict(item) for item in raw["targets"]),
            orders=tuple(dict(item) for item in raw["orders"]),
            fills=tuple(dict(item) for item in raw["fills"]),
            account_sha256=str(raw["account_sha256"]),
            equity=raw["equity"],
            target_gross=raw["target_gross"],
            intervention_provenance=(
                None if raw["intervention_provenance"] is None else dict(raw["intervention_provenance"])
            ),
            cash=raw["cash"],
            position_shares={str(symbol): int(shares) for symbol, shares in raw["position_shares"].items()},
            close_marks={str(symbol): mark for symbol, mark in raw["close_marks"].items()},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("witness ablation route row is malformed") from exc


def _divergences_from_compact(value: object) -> FirstDivergences:
    if not isinstance(value, Mapping) or set(value) != {
        "route",
        "state",
        "economic",
        "comparable",
        "uncompared_reason",
    }:
        raise ValueError("witness ablation compact divergences are malformed")
    raw = dict(value)
    layers: dict[str, Mapping[str, str] | None] = {}
    for name in ("route", "state", "economic"):
        item = raw[name]
        if item is not None and (
            not isinstance(item, Mapping)
            or set(item) != {"date", "layer"}
            or not all(isinstance(field, str) and field for field in item.values())
        ):
            raise ValueError("witness ablation compact divergence layer is malformed")
        layers[name] = None if item is None else {str(key): str(field) for key, field in item.items()}
    comparable = raw["comparable"]
    reason = raw["uncompared_reason"]
    if not isinstance(comparable, bool) or (reason is not None and not isinstance(reason, str)):
        raise ValueError("witness ablation compact comparability is malformed")
    return FirstDivergences(
        route=layers["route"],
        state=layers["state"],
        economic=layers["economic"],
        comparable=comparable,
        uncompared_reason=reason,
    )


def read_cell_shard(
    path: str | Path,
    *,
    expected_provenance: Mapping[str, Any],
    expected_resume_identity: str,
) -> tuple[
    AblationCell,
    tuple[RouteTraceRow, ...],
    FirstDivergences,
    tuple[DiagnosticProjectionRow, ...],
]:
    """Read one resumable cell, retaining a partial terminal route literally."""

    cell: AblationCell | None = None
    divergences: FirstDivergences | None = None
    expected_count: int | None = None
    expected_projection_count: int | None = None
    routes: list[RouteTraceRow] = []
    projections: list[DiagnosticProjectionRow] = []
    for payload in iter_streaming_rows(
        path,
        expected_provenance=expected_provenance,
        expected_resume_identity=expected_resume_identity,
    ):
        record_type = payload.get("record_type")
        if record_type == "CELL":
            if cell is not None or set(payload) != {
                "record_type",
                "cell",
                "divergences",
                "route_row_count",
                "diagnostic_projection_row_count",
            }:
                raise ValueError("witness ablation cell header differs")
            cell = ablation_cell_from_compact(payload["cell"])
            divergences = _divergences_from_compact(payload["divergences"])
            count = payload["route_row_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("witness ablation cell route count is malformed")
            expected_count = count
            projection_count = payload["diagnostic_projection_row_count"]
            if (
                isinstance(projection_count, bool)
                or not isinstance(projection_count, int)
                or projection_count < 0
            ):
                raise ValueError("witness ablation cell diagnostic projection count is malformed")
            expected_projection_count = projection_count
        elif record_type == "ROUTE":
            if cell is None or set(payload) != {
                "record_type",
                "cell_id",
                "route_index",
                "trace",
            }:
                raise ValueError("witness ablation cell route linkage differs")
            if payload["cell_id"] != cell.cell_id or payload["route_index"] != len(routes):
                raise ValueError("witness ablation cell route ordering differs")
            routes.append(_route_from_mapping(payload["trace"]))
        elif record_type == "DIAGNOSTIC_PROJECTION":
            if cell is None or set(payload) != {
                "record_type",
                "cell_id",
                "projection_index",
                "projection",
            }:
                raise ValueError("witness ablation diagnostic projection linkage differs")
            if payload["cell_id"] != cell.cell_id or payload["projection_index"] != len(projections):
                raise ValueError("witness ablation diagnostic projection ordering differs")
            projections.append(diagnostic_projection_from_compact(payload["projection"]))
        else:
            raise ValueError("witness ablation cell record type differs")
    if (
        cell is None
        or divergences is None
        or expected_count is None
        or expected_projection_count is None
    ):
        raise ValueError("witness ablation cell header is absent")
    trace = tuple(routes)
    if len(trace) != expected_count or len(trace) != cell.partial_trace_row_count:
        raise ValueError("witness ablation cell retained trace count differs")
    observed_trace_sha = None if not trace else canonical_sha256({"trace": [asdict(row) for row in trace]})
    if observed_trace_sha != cell.trace_sha256:
        raise ValueError("witness ablation cell retained trace seal differs")
    projection = tuple(projections)
    if len(projection) != expected_projection_count or len(projection) != cell.diagnostic_projection_row_count:
        raise ValueError("witness ablation cell diagnostic projection count differs")
    observed_projection_sha = (
        None
        if not projection
        else canonical_sha256(
            {"diagnostic_projection": [row.compact() for row in projection]}
        )
    )
    if observed_projection_sha != cell.diagnostic_projection_sha256:
        raise ValueError("witness ablation cell diagnostic projection seal differs")
    return cell, trace, divergences, projection


def _portable_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if set(metadata) != _ROUTE_METADATA_FIELDS:
        raise ValueError("Task 4 route metadata fields differ")
    byte_size = metadata["byte_size"]
    row_count = metadata["row_count"]
    if (
        isinstance(byte_size, bool)
        or not isinstance(byte_size, int)
        or byte_size < 0
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 0
    ):
        raise ValueError("Task 4 route metadata counts are malformed")
    for field in (
        "bytes_sha256",
        "rows_sha256",
        "linkage_sha256",
        "header_payload_sha256",
        "resume_identity",
    ):
        require_sha256(metadata[field], field=f"Task 4 route {field}")
    return {
        "logical_path": _LOGICAL_ROUTE_PATH,
        **{key: metadata[key] for key in sorted(_ROUTE_METADATA_FIELDS - {"path"})},
    }


def _relative_identity(repository: Path, artifact: Path, *, label: str) -> str:
    try:
        relative = artifact.resolve().relative_to(repository.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must be inside the repository") from exc
    identity = relative.as_posix()
    if not identity or identity == ".":
        raise ValueError(f"{label} identity is malformed")
    return identity


def _resolve_identity(repository: Path, value: object, *, label: str) -> Path:
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


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n",
    )


def write_compact_and_manifest(
    *,
    repository: str | Path,
    summary_path: str | Path,
    manifest_path: str | Path,
    summary_payload: Mapping[str, Any],
    route_metadata: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write portable compact evidence and its repository-relative manifest."""

    if summary_payload.get("completion_status") != "FINAL":
        raise ValueError("Task 4 compact outputs require FINAL completion")
    root = Path(repository).resolve()
    summary_target = Path(summary_path).resolve()
    manifest_target = Path(manifest_path).resolve()
    portable_route = _portable_metadata(route_metadata)
    summary = seal_payload(
        {
            **summary_payload,
            "schema_version": 1,
            "checkpoint": "TASK4_WITNESS_ABLATION",
            "route_shard": portable_route,
        }
    )
    _write_json(summary_target, summary)
    observed_summary = verify_sealed_payload(
        json.loads(summary_target.read_text(encoding="utf-8")),
        label="checkpoint4 witness ablation summary",
    )
    if canonical_json_bytes(observed_summary) != canonical_json_bytes(summary):
        raise ValueError("checkpoint4 witness ablation summary readback differs")
    manifest = seal_payload(
        {
            "schema_version": 1,
            "checkpoint": "TASK4_WITNESS_ABLATION",
            "summary": {
                "path": _relative_identity(root, summary_target, label="Task 4 summary"),
                "byte_size": summary_target.stat().st_size,
                "bytes_sha256": _sha256_file(summary_target),
                "payload_sha256": summary["payload_sha256"],
            },
            "route_shard": portable_route,
        }
    )
    _write_json(manifest_target, manifest)
    observed_manifest = verify_sealed_payload(
        json.loads(manifest_target.read_text(encoding="utf-8")),
        label="checkpoint4 witness ablation manifest",
    )
    if canonical_json_bytes(observed_manifest) != canonical_json_bytes(manifest):
        raise ValueError("checkpoint4 witness ablation manifest readback differs")
    return summary, manifest


def verify_task4_manifest(
    repository: str | Path,
    *,
    summary_path: str | Path,
    manifest_path: str | Path,
    route_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify compact linkage without admitting runtime paths into its identity."""

    root = Path(repository).resolve()
    summary_target = Path(summary_path)
    if not summary_target.is_absolute():
        summary_target = root / summary_target
    manifest_target = Path(manifest_path)
    if not manifest_target.is_absolute():
        manifest_target = root / manifest_target
    summary = verify_sealed_payload(
        json.loads(summary_target.read_text(encoding="utf-8")),
        label="checkpoint4 witness ablation summary",
    )
    manifest = verify_sealed_payload(
        json.loads(manifest_target.read_text(encoding="utf-8")),
        label="checkpoint4 witness ablation manifest",
    )
    if (
        set(manifest)
        != {
            "schema_version",
            "checkpoint",
            "summary",
            "route_shard",
            "payload_sha256",
        }
        or manifest["schema_version"] != 1
        or manifest["checkpoint"] != "TASK4_WITNESS_ABLATION"
        or summary.get("schema_version") != 1
        or summary.get("checkpoint") != "TASK4_WITNESS_ABLATION"
        or summary.get("completion_status") != "FINAL"
    ):
        raise ValueError("checkpoint4 witness ablation compact schema differs")
    manifest_summary = manifest.get("summary")
    if not isinstance(manifest_summary, Mapping):
        raise ValueError("checkpoint4 witness ablation summary manifest is malformed")
    resolved = _resolve_identity(root, manifest_summary.get("path"), label="Task 4 summary")
    if resolved != summary_target.resolve():
        raise ValueError("checkpoint4 witness ablation summary path differs")
    expected_summary = {
        "path": _relative_identity(root, summary_target, label="Task 4 summary"),
        "byte_size": summary_target.stat().st_size,
        "bytes_sha256": _sha256_file(summary_target),
        "payload_sha256": summary["payload_sha256"],
    }
    portable_route = _portable_metadata(route_metadata)
    if manifest_summary != expected_summary:
        raise ValueError("checkpoint4 witness ablation summary linkage differs")
    if summary.get("route_shard") != portable_route or manifest.get("route_shard") != portable_route:
        raise ValueError("checkpoint4 witness ablation route linkage differs")
    return {
        "summary_payload_sha256": summary["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "route_bytes_sha256": portable_route["bytes_sha256"],
        "route_row_count": portable_route["row_count"],
    }


def validate_initial_coverage(
    cells: Sequence[AblationCell],
    *,
    specs: Sequence[AblationSpec],
) -> None:
    """Count every terminal failure while enforcing exact 117/49/68 coverage."""

    expected = {spec.cell_id: spec for spec in specs}
    observed = {cell.cell_id: cell for cell in cells}
    if len(expected) != 117 or len(observed) != len(cells) or set(observed) != set(expected):
        raise ValueError("Task 4 initial cell coverage differs")
    for cell_id, cell in observed.items():
        if cell.spec != expected[cell_id]:
            raise ValueError("Task 4 initial cell specification differs")
        if cell.status not in {"SUCCESS", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}:
            raise ValueError("Task 4 initial cell status is not terminal")
        if cell.status == "SUCCESS" and cell.spec.evidence_class == ECONOMIC:
            if cell.metrics is None or cell.final_account_sha256 is None or cell.trace_sha256 is None:
                raise ValueError("successful Task 4 economic cell lacks evidence")
        elif cell.metrics is not None:
            raise ValueError("Task 4 terminal/diagnostic cell carries economic metrics")


def _git_commit(repository: Path) -> str:
    commit = subprocess.check_output(  # nosec B603, B607
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("Task 4 experiment commit is malformed")
    return commit


def _research_source_sha256(repository: Path) -> str:
    files = sorted((repository / "research" / "strategic_evidence").glob("*.py"))
    if not files:
        raise ValueError("Task 4 research source is missing")
    return canonical_sha256({path.relative_to(repository).as_posix(): _sha256_file(path) for path in files})


def _checkpoint_path(resume_dir: Path, *, index: int, cell_id: str) -> Path:
    suffix = hashlib.sha256(cell_id.encode("utf-8")).hexdigest()[:16]
    return resume_dir / f"{index:04d}-{suffix}.jsonl.gz"


def _intervention_audit(spec: AblationSpec, *, resolved_removed: Sequence[str]) -> dict[str, Any]:
    return {
        "axis": spec.axis,
        "scope": spec.scope,
        "subject": spec.subject,
        "removed_symbols": list(resolved_removed),
        "evidence_class": spec.evidence_class,
    }


def resolve_ablation_universe(
    spec: AblationSpec,
    *,
    contract: StrategicEvidenceContract,
    balanced: BalancedIndustryUniverse,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return production decision universe and concrete symbols to remove."""

    matrix = contract.raw["matrix"]
    canonical = tuple(contract.canonical_universe)
    if spec.scope == "REPORT_UNIVERSE_LEAVE_ONE_OUT":
        raw_report = matrix["report_universe_13"]
        if not isinstance(raw_report, list):
            raise ValueError("Task 4 report universe is malformed")
        source = tuple(str(symbol) for symbol in raw_report)
        removed = spec.removed_symbols
    elif spec.scope == "INDUSTRY_REMOVAL":
        source = canonical
        if spec.subject == "industry-balanced":
            balanced.validate(contract=contract)
            removed = balanced.removed_symbols
        else:
            industries = dict(_contract_industries(contract))
            removed = tuple(
                symbol
                for symbol in canonical
                if industries[symbol] == spec.subject
            )
            if not removed:
                raise ValueError(f"Task 4 industry removal has no symbols: {spec.subject}")
    else:
        source = canonical
        removed = spec.removed_symbols
    concrete = tuple(symbol for symbol in removed if not symbol.startswith("industry:"))
    if spec.axis != BASELINE and not concrete:
        raise ValueError("Task 4 concrete removal is empty")
    if not set(concrete) <= set(source):
        raise ValueError("Task 4 removal lies outside its production universe")
    return source, tuple(sorted(concrete))


def _result_for_spec(
    *,
    data_dir: Path,
    contract: StrategicEvidenceContract,
    spec: AblationSpec,
    baseline: ReplayResult,
    balanced: BalancedIndustryUniverse,
    window: Mapping[str, str] | None = None,
) -> tuple[ReplayResult, tuple[DiagnosticProjectionRow, ...]]:
    replay_window = contract.window if window is None else window
    if spec.axis == BASELINE:
        return baseline, ()
    if spec.evidence_class == DIAGNOSTIC_ONLY:
        projection = diagnostic_projection(
            baseline.trace,
            removed_symbols=spec.removed_symbols,
            source_symbols=baseline.request.symbols,
            axis=spec.axis,
        )
        return (
            ReplayResult(
                request=ReplayRequest(
                    symbols=baseline.request.symbols,
                    start=baseline.request.start,
                    end=baseline.request.end,
                    scenario=f"witness-ablation:{spec.cell_id}",
                ),
                metrics={},
                trace=(),
                final_account={},
                intervention_provenance=_intervention_audit(
                    spec, resolved_removed=spec.removed_symbols
                ),
                status="SUCCESS",
                error=None,
            ),
            projection,
        )
    source, removed = resolve_ablation_universe(spec, contract=contract, balanced=balanced)
    symbols = tuple(symbol for symbol in source if symbol not in set(removed))
    if not symbols:
        return (
            ReplayResult(
                request=ReplayRequest(
                    symbols=source,
                    start=replay_window["start"],
                    end=replay_window["end"],
                    scenario=f"witness-ablation:{spec.cell_id}",
                ),
                metrics={},
                trace=(),
                final_account={},
                intervention_provenance=_intervention_audit(spec, resolved_removed=removed),
                status="INSUFFICIENT_SAMPLE",
                error="full removal leaves no production symbols",
            ),
            (),
        )
    result = run_replay(
        data_dir,
        ReplayRequest(
            symbols=symbols,
            start=replay_window["start"],
            end=replay_window["end"],
            scenario=f"witness-ablation:{spec.cell_id}",
        ),
    )
    audit = _intervention_audit(spec, resolved_removed=removed)
    if result.intervention_provenance is not None:
        audit["replay_intervention"] = dict(result.intervention_provenance)
    return replace(result, intervention_provenance=audit), ()


def _run_task4_sentinels(
    *,
    data_dir: Path,
    contract: StrategicEvidenceContract,
    specs: Sequence[AblationSpec],
    balanced: BalancedIndustryUniverse,
) -> dict[str, Any]:
    """Exercise production full removal and both diagnostic axes before the matrix."""

    window = {"start": contract.window["start"], "end": _SENTINEL_END}
    baseline = run_replay(
        data_dir,
        ReplayRequest(
            symbols=contract.canonical_universe,
            start=window["start"],
            end=window["end"],
            scenario="witness-ablation:sentinel-baseline",
        ),
    )
    if baseline.status != "SUCCESS":
        raise ValueError(f"Task 4 sentinel baseline failed: {baseline.status}: {baseline.error}")
    observations: list[dict[str, Any]] = []
    for spec in task4_sentinel_specs(specs):
        result, projection = _result_for_spec(
            data_dir=data_dir,
            contract=contract,
            spec=spec,
            baseline=baseline,
            balanced=balanced,
            window=window,
        )
        if result.status != "SUCCESS":
            raise ValueError(f"Task 4 sentinel failed: {spec.cell_id}: {result.status}: {result.error}")
        cell = cell_from_replay(spec, result, diagnostic_projection=projection)
        divergence = (
            FirstDivergences(None, None, None, False, "single-layer diagnostic projection")
            if projection
            else derive_first_divergences(baseline.trace, result.trace, status=result.status)
        )
        observations.append(
            {
                "cell_id": spec.cell_id,
                "evidence_class": spec.evidence_class,
                "status": cell.status,
                "trace_row_count": cell.partial_trace_row_count,
                "comparable": divergence.comparable,
            }
        )
    return {"window": window, "observations": observations}


def _execute_or_resume(
    *,
    data_dir: Path,
    contract: StrategicEvidenceContract,
    spec: AblationSpec,
    baseline: ReplayResult,
    baseline_trace: Sequence[RouteTraceRow],
    balanced: BalancedIndustryUniverse,
    provenance: Mapping[str, Any],
    checkpoint_path: Path,
    resume: bool,
) -> tuple[AblationCell, tuple[RouteTraceRow, ...], FirstDivergences, bool]:
    identity = build_resume_identity(provenance, spec)
    if checkpoint_path.exists():
        if not resume:
            raise ValueError(
                f"Task 4 checkpoint exists; pass --resume or use a fresh directory: {checkpoint_path}"
            )
        cell, trace, divergences, _ = read_cell_shard(
            checkpoint_path,
            expected_provenance=provenance,
            expected_resume_identity=identity,
        )
        if cell.spec != spec:
            raise ValueError("Task 4 resumed cell scenario differs")
        return cell, trace, divergences, True
    result, projection = _result_for_spec(
        data_dir=data_dir,
        contract=contract,
        spec=spec,
        baseline=baseline,
        balanced=balanced,
    )
    cell = cell_from_replay(spec, result, diagnostic_projection=projection)
    divergences = (
        FirstDivergences(None, None, None, True, None)
        if spec.axis == BASELINE
        else FirstDivergences(None, None, None, False, "single-layer diagnostic projection")
        if projection
        else derive_first_divergences(baseline_trace, result.trace, status=result.status)
    )
    write_cell_shard(
        checkpoint_path,
        cell=cell,
        result=result,
        divergences=divergences,
        diagnostic_projection=projection,
        provenance=provenance,
        resume_identity=identity,
    )
    return cell, result.trace, divergences, False


def verify_full_route_linkage(
    path: str | Path,
    *,
    expected_cell_ids: Sequence[str],
    expected_cells: Sequence[AblationCell] | None = None,
    expected_provenance: Mapping[str, Any],
    expected_resume_identity: str,
) -> dict[str, Any]:
    """Stream-verify exact cell/header/route linkage without retaining routes."""

    expected = tuple(expected_cell_ids)
    expected_payloads = None if expected_cells is None else tuple(expected_cells)
    if expected_payloads is not None and tuple(cell.cell_id for cell in expected_payloads) != expected:
        raise ValueError("Task 4 expected full-route cell identities differ")
    observed: list[str] = []
    current_cell: str | None = None
    expected_routes = 0
    observed_routes = 0
    expected_projections = 0
    observed_projections = 0
    for payload in iter_streaming_rows(
        path,
        expected_provenance=expected_provenance,
        expected_resume_identity=expected_resume_identity,
    ):
        record_type = payload.get("record_type")
        if record_type == "CELL":
            if current_cell is not None and (
                observed_routes != expected_routes or observed_projections != expected_projections
            ):
                raise ValueError("Task 4 full route/projection cell count differs")
            raw_cell = payload.get("cell")
            if not isinstance(raw_cell, Mapping):
                raise ValueError("Task 4 full route cell header is malformed")
            cell = ablation_cell_from_compact(raw_cell)
            if expected_payloads is not None:
                index = len(observed)
                if index >= len(expected_payloads) or cell != expected_payloads[index]:
                    raise ValueError("Task 4 full route cell payload differs")
            cell_id = raw_cell.get("cell_id")
            count = payload.get("route_row_count")
            projection_count = payload.get("diagnostic_projection_row_count")
            if (
                not isinstance(cell_id, str)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or isinstance(projection_count, bool)
                or not isinstance(projection_count, int)
                or projection_count < 0
            ):
                raise ValueError("Task 4 full route cell linkage is malformed")
            observed.append(cell_id)
            current_cell = cell_id
            expected_routes = count
            observed_routes = 0
            expected_projections = projection_count
            observed_projections = 0
        elif record_type == "ROUTE":
            if current_cell is None or payload.get("cell_id") != current_cell:
                raise ValueError("Task 4 full route row linkage differs")
            if payload.get("route_index") != observed_routes:
                raise ValueError("Task 4 full route row ordering differs")
            observed_routes += 1
        elif record_type == "DIAGNOSTIC_PROJECTION":
            if current_cell is None or payload.get("cell_id") != current_cell:
                raise ValueError("Task 4 full diagnostic projection linkage differs")
            if payload.get("projection_index") != observed_projections:
                raise ValueError("Task 4 full diagnostic projection ordering differs")
            observed_projections += 1
        else:
            raise ValueError("Task 4 full route record type differs")
    if current_cell is not None and (
        observed_routes != expected_routes or observed_projections != expected_projections
    ):
        raise ValueError("Task 4 full route/projection final cell count differs")
    if tuple(observed) != expected or len(set(observed)) != len(observed):
        raise ValueError("Task 4 full route exact cell coverage differs")
    return verify_streaming_shard(
        path,
        expected_provenance=expected_provenance,
        expected_resume_identity=expected_resume_identity,
    )


def _causal_scores(
    specs: Sequence[AblationSpec],
    divergences: Mapping[str, FirstDivergences],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for spec in specs:
        if spec.scope != "CANONICAL_LEAVE_ONE_OUT" or spec.axis != FULL_REMOVAL:
            continue
        item = divergences[spec.cell_id]
        score = 0.0
        if item.comparable:
            score += 4.0 * int(item.route is not None)
            score += 2.0 * int(item.state is not None)
            score += float(item.economic is not None)
        scores[spec.subject] = score
    return scores


def _search_spec(symbols: Sequence[str], *, scope: str) -> AblationSpec:
    removed = tuple(sorted(symbols))
    return AblationSpec(
        scope=scope,
        subject="+".join(removed),
        removed_symbols=removed,
        axis=FULL_REMOVAL,
        evidence_class=ECONOMIC,
    )


def execute_task4_matrix(
    root: str | Path,
    *,
    summary_path: str | Path = _DEFAULT_SUMMARY,
    manifest_path: str | Path = _DEFAULT_MANIFEST,
    trace_shard_path: str | Path = _DEFAULT_TRACE_SHARD,
    resume_dir: str | Path = _DEFAULT_RESUME_DIR,
    resume: bool = False,
    include_bounded_search: bool = True,
) -> dict[str, Any]:
    """Execute/resume exact initial coverage, pairs, supported triples, and seals."""

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
    if trace_target.is_relative_to(repository) or resume_target.is_relative_to(repository):
        raise ValueError("large Task 4 routes and resume shards must remain outside Git")
    contract = load_contract(repository / "benchmarks" / "strategic_evidence_closure_contract.json")
    input_verification = verify_frozen_inputs(repository, contract)
    specs = enumerate_initial_specs(contract)
    data_dir = repository / "data" / "frozen"
    balanced = derive_balanced_industry_universe(data_dir, contract=contract)
    source_manifest = build_executable_source_manifest(repository, require_clean=True)
    observed_identities = recompute_task4_identities(repository, contract=contract)
    runtime_metadata = resolve_resume_runtime_metadata(
        resume_target,
        resume=resume,
        current=capture_runtime_metadata(repository),
    )
    scenario = build_task4_scenario(
        contract=contract,
        initial_specs=specs,
        balanced=balanced,
        source_manifest=source_manifest,
    )
    provenance = build_provenance(
        contract,
        experiment_commit=_git_commit(repository),
        research_source_sha256=observed_identities["research_source_sha256"],
        scenario=scenario,
        generated_at=runtime_metadata["generated_at"],
        observed_identities=observed_identities,
        runtime_metadata=runtime_metadata,
    )
    sentinel_verification = _run_task4_sentinels(
        data_dir=data_dir,
        contract=contract,
        specs=specs,
        balanced=balanced,
    )
    baseline = run_replay(
        data_dir,
        ReplayRequest(
            symbols=contract.canonical_universe,
            start=contract.window["start"],
            end=contract.window["end"],
            scenario="witness-ablation:baseline",
        ),
    )
    if baseline.status != "SUCCESS":
        raise ValueError(f"Task 4 baseline failed: {baseline.status}: {baseline.error}")
    report_symbols = tuple(str(symbol) for symbol in contract.raw["matrix"]["report_universe_13"])
    report_baseline = run_replay(
        data_dir,
        ReplayRequest(
            symbols=report_symbols,
            start=contract.window["start"],
            end=contract.window["end"],
            scenario="witness-ablation:report-universe-13-baseline",
        ),
    )
    if report_baseline.status != "SUCCESS":
        raise ValueError(
            f"Task 4 report baseline failed: {report_baseline.status}: {report_baseline.error}"
        )
    resume_target.mkdir(parents=True, exist_ok=True)
    trace_target.parent.mkdir(parents=True, exist_ok=True)
    cells: list[AblationCell] = []
    search_cells: list[AblationCell] = []
    divergences: dict[str, FirstDivergences] = {}
    shard_paths: list[Path] = []
    reused = 0

    def run_specs(items: Sequence[AblationSpec]) -> None:
        nonlocal reused
        for spec in items:
            index = len(shard_paths)
            checkpoint = _checkpoint_path(resume_target, index=index, cell_id=spec.cell_id)
            comparison_baseline = (
                report_baseline
                if comparison_baseline_scope(spec) == "REPORT_UNIVERSE_13"
                else baseline
            )
            cell, _, difference, was_reused = _execute_or_resume(
                data_dir=data_dir,
                contract=contract,
                spec=spec,
                baseline=comparison_baseline,
                baseline_trace=comparison_baseline.trace,
                balanced=balanced,
                provenance=provenance,
                checkpoint_path=checkpoint,
                resume=resume,
            )
            (cells if spec in specs else search_cells).append(cell)
            divergences[spec.cell_id] = difference
            shard_paths.append(checkpoint)
            reused += int(was_reused)
            print(
                json.dumps(
                    {
                        "cell": spec.cell_id,
                        "index": index + 1,
                        "resumed": was_reused,
                        "status": cell.status,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    run_specs(specs)
    validate_initial_coverage(cells, specs=specs)
    if not include_bounded_search:
        return {
            "completion_status": "NON_FINAL",
            "checkpoint": "TASK4_WITNESS_ABLATION_INITIAL_CHECKPOINT",
            "initial_cell_count": len(cells),
            "status_counts": dict(sorted(Counter(cell.status for cell in cells).items())),
            "resume": {
                "reused_cell_count": reused,
                "executed_cell_count": len(cells) - reused,
                "checkpoint_count": len(shard_paths),
            },
        }
    matrix = contract.raw["matrix"]
    critical_raw = matrix["critical_symbols"]
    if not isinstance(critical_raw, list):
        raise ValueError("Task 4 critical symbols must be a list")
    ranked = rank_critical_symbols(
        _causal_scores(specs, divergences),
        preregistered=tuple(str(symbol) for symbol in critical_raw),
    )
    pair_specs: tuple[AblationSpec, ...] = ()
    triple_specs: tuple[AblationSpec, ...] = ()
    outcome_by_removal = {
        frozenset((spec.subject,)): divergences[spec.cell_id]
        for spec in specs
        if spec.scope == "CANONICAL_LEAVE_ONE_OUT" and spec.axis == FULL_REMOVAL
    }
    if include_bounded_search:
        pairs, _ = select_bounded_search(ranked, {})
        pair_specs = tuple(_search_spec(pair, scope="CRITICAL_PAIR") for pair in pairs)
        run_specs(pair_specs)
        pair_differences = {frozenset(spec.removed_symbols): divergences[spec.cell_id] for spec in pair_specs}
        outcome_by_removal.update(pair_differences)
        support: dict[tuple[str, str, str], bool] = {}
        for triple in combinations(ranked, 3):
            support[triple] = necessary_triple_support(triple, outcome_by_removal)
        _, triples = select_bounded_search(ranked, support)
        triple_specs = tuple(_search_spec(triple, scope="NECESSARY_TRIPLE") for triple in triples)
        run_specs(triple_specs)
        outcome_by_removal.update(
            {frozenset(spec.removed_symbols): divergences[spec.cell_id] for spec in triple_specs}
        )
    minimal_sets = minimal_decisive_witness_sets(outcome_by_removal)
    decisive_pairs = tuple(symbols for symbols in minimal_sets if len(symbols) == 2)
    single_divergences = {
        spec.subject: divergences[spec.cell_id]
        for spec in specs
        if spec.scope == "CANONICAL_LEAVE_ONE_OUT" and spec.axis == FULL_REMOVAL
    }
    roles = derive_symbol_roles(
        baseline.trace,
        single_divergences,
        decisive_pairs=decisive_pairs,
    )
    necessary_triple_specs = tuple(
        spec for spec in triple_specs if is_decisive(divergences[spec.cell_id])
    )
    all_cells = (*cells, *search_cells)
    final_identity = canonical_sha256(
        {
            "kind": "task4-witness-ablation-full-route",
            "provenance": provenance,
            "cell_ids": [cell.cell_id for cell in all_cells],
        }
    )
    route_metadata = assemble_full_route_shard(
        trace_target,
        cell_shards=shard_paths,
        provenance=provenance,
        resume_identity=final_identity,
    )
    verified_route = verify_full_route_linkage(
        trace_target,
        expected_cell_ids=[cell.cell_id for cell in all_cells],
        expected_cells=all_cells,
        expected_provenance=provenance,
        expected_resume_identity=final_identity,
    )
    if verified_route != route_metadata:
        raise ValueError("Task 4 final route verification metadata differs")
    summary_payload = {
        "completion_status": "FINAL",
        "schema_version": 1,
        "checkpoint": "TASK4_WITNESS_ABLATION",
        "contract_payload_sha256": contract.payload_sha256,
        "provenance": provenance,
        "scenario": scenario,
        "sentinel_verification": sentinel_verification,
        "input_verification": input_verification,
        "window": {
            **contract.window,
            "future_holdout_boundary": contract.future_holdout_boundary,
        },
        "comparison_baseline_seals": {
            "CANONICAL_34": {
                "trace_sha256": canonical_sha256(
                    {"trace": [asdict(row) for row in baseline.trace]}
                ),
                "final_account_payload_sha256": canonical_sha256(dict(baseline.final_account)),
            },
            "REPORT_UNIVERSE_13": {
                "trace_sha256": canonical_sha256(
                    {"trace": [asdict(row) for row in report_baseline.trace]}
                ),
                "final_account_payload_sha256": canonical_sha256(
                    dict(report_baseline.final_account)
                ),
            },
        },
        "required_initial_cell_ids": [spec.cell_id for spec in specs],
        "initial_status_counts": dict(sorted(Counter(cell.status for cell in cells).items())),
        "initial_cells": [cell.compact() for cell in cells],
        "first_divergences": {
            cell_id: difference.compact() for cell_id, difference in sorted(divergences.items())
        },
        "symbol_roles": {symbol: list(values) for symbol, values in roles.items()},
        "critical_ranking": list(ranked),
        "critical_pair_cell_ids": [spec.cell_id for spec in pair_specs],
        "supported_triple_cell_ids": [spec.cell_id for spec in triple_specs],
        "necessary_triple_cell_ids": [spec.cell_id for spec in necessary_triple_specs],
        "minimal_witness_sets": [list(symbols) for symbols in minimal_sets],
        "search_cells": [cell.compact() for cell in search_cells],
        "resume": {
            "reused_cell_count": reused,
            "executed_cell_count": len(all_cells) - reused,
            "checkpoint_count": len(shard_paths),
        },
        "large_traces_committed": False,
        "execution_entrypoint": (
            "python -m research.strategic_evidence.witness_ablation_runner run --resume"
        ),
    }
    summary, manifest = write_compact_and_manifest(
        repository=repository,
        summary_path=summary_target,
        manifest_path=manifest_target,
        summary_payload=summary_payload,
        route_metadata=route_metadata,
    )
    verification = verify_task4_outputs(
        repository,
        summary_path=summary_target,
        manifest_path=manifest_target,
        trace_shard_path=trace_target,
    )
    return {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "summary_payload_sha256": summary["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "status_counts": summary["initial_status_counts"],
        "verification": verification,
    }


_FINAL_SUMMARY_FIELDS = frozenset(
    {
        "completion_status",
        "schema_version",
        "checkpoint",
        "contract_payload_sha256",
        "provenance",
        "scenario",
        "sentinel_verification",
        "input_verification",
        "window",
        "comparison_baseline_seals",
        "required_initial_cell_ids",
        "initial_status_counts",
        "initial_cells",
        "first_divergences",
        "symbol_roles",
        "critical_ranking",
        "critical_pair_cell_ids",
        "supported_triple_cell_ids",
        "necessary_triple_cell_ids",
        "minimal_witness_sets",
        "search_cells",
        "resume",
        "large_traces_committed",
        "execution_entrypoint",
        "route_shard",
        "payload_sha256",
    }
)


def _exact_string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"Task 4 {label} is malformed")
    if len(value) != len(set(value)):
        raise ValueError(f"Task 4 {label} contains duplicates")
    return list(value)


def validate_final_summary_contract(
    root: str | Path,
    summary: Mapping[str, Any],
) -> tuple[tuple[AblationCell, ...], tuple[AblationCell, ...], dict[str, Any]]:
    """Recompute every preregistered final-search and provenance obligation."""

    if set(summary) != _FINAL_SUMMARY_FIELDS:
        raise ValueError("Task 4 final summary fields differ")
    if (
        summary["completion_status"] != "FINAL"
        or summary["schema_version"] != 1
        or summary["checkpoint"] != "TASK4_WITNESS_ABLATION"
        or summary["large_traces_committed"] is not False
    ):
        raise ValueError("Task 4 final summary completion differs")

    repository = Path(root).resolve()
    contract = load_contract(repository / "benchmarks" / "strategic_evidence_closure_contract.json")
    if summary["contract_payload_sha256"] != contract.payload_sha256:
        raise ValueError("Task 4 final summary contract linkage differs")
    specs = enumerate_initial_specs(contract)
    balanced = derive_balanced_industry_universe(repository / "data" / "frozen", contract=contract)
    source_manifest = build_executable_source_manifest(repository, require_clean=True)
    expected_scenario = build_task4_scenario(
        contract=contract,
        initial_specs=specs,
        balanced=balanced,
        source_manifest=source_manifest,
    )
    if summary["scenario"] != expected_scenario:
        raise ValueError("Task 4 final summary scenario linkage differs")
    if summary["input_verification"] != verify_frozen_inputs(repository, contract):
        raise ValueError("Task 4 final frozen input verification differs")
    expected_window = {
        **contract.window,
        "future_holdout_boundary": contract.future_holdout_boundary,
    }
    if summary["window"] != expected_window:
        raise ValueError("Task 4 final window linkage differs")

    raw_provenance = summary["provenance"]
    if not isinstance(raw_provenance, Mapping):
        raise ValueError("Task 4 final summary provenance is malformed")
    provenance = validate_provenance(raw_provenance)
    observed_identities = recompute_task4_identities(repository, contract=contract)
    current_runtime = capture_runtime_metadata(repository)
    for field in ("python", "numpy", "pandas", "uv"):
        if provenance[field] != current_runtime[field]:
            raise ValueError(f"Task 4 final runtime differs: {field}")
    expected_provenance = build_provenance(
        contract,
        experiment_commit=str(provenance["experiment_commit"]),
        research_source_sha256=observed_identities["research_source_sha256"],
        scenario=expected_scenario,
        generated_at=str(provenance["generated_at"]),
        observed_identities=observed_identities,
        runtime_metadata={
            **{field: current_runtime[field] for field in ("python", "numpy", "pandas", "uv")},
            "generated_at": str(provenance["generated_at"]),
        },
    )
    if provenance != expected_provenance:
        raise ValueError("Task 4 final summary provenance linkage differs")

    raw_ids = _exact_string_list(
        summary["required_initial_cell_ids"], label="required initial cell identities"
    )
    if raw_ids != [spec.cell_id for spec in specs]:
        raise ValueError("Task 4 final required initial identities differ")
    raw_cells = summary["initial_cells"]
    if not isinstance(raw_cells, list):
        raise ValueError("Task 4 final initial cells are malformed")
    cells = tuple(ablation_cell_from_compact(item) for item in raw_cells)
    validate_initial_coverage(cells, specs=specs)
    expected_status_counts = dict(sorted(Counter(cell.status for cell in cells).items()))
    if summary["initial_status_counts"] != expected_status_counts:
        raise ValueError("Task 4 final initial status counts differ")

    raw_search = summary["search_cells"]
    if not isinstance(raw_search, list):
        raise ValueError("Task 4 final search cells are malformed")
    search_cells = tuple(ablation_cell_from_compact(item) for item in raw_search)
    all_ids = [cell.cell_id for cell in (*cells, *search_cells)]
    raw_divergences = summary["first_divergences"]
    if not isinstance(raw_divergences, Mapping) or set(raw_divergences) != set(all_ids):
        raise ValueError("Task 4 final divergence coverage differs")
    divergences = {
        str(cell_id): _divergences_from_compact(value)
        for cell_id, value in raw_divergences.items()
    }

    matrix = contract.raw["matrix"]
    critical_raw = matrix["critical_symbols"]
    if not isinstance(critical_raw, list):
        raise ValueError("Task 4 final preregistered critical symbols differ")
    ranked = rank_critical_symbols(
        _causal_scores(specs, divergences),
        preregistered=tuple(str(symbol) for symbol in critical_raw),
    )
    if _exact_string_list(summary["critical_ranking"], label="critical ranking") != list(ranked):
        raise ValueError("Task 4 final critical ranking differs")
    pairs, _ = select_bounded_search(ranked, {})
    pair_specs = tuple(_search_spec(pair, scope="CRITICAL_PAIR") for pair in pairs)
    if _exact_string_list(
        summary["critical_pair_cell_ids"], label="critical pair identities"
    ) != [spec.cell_id for spec in pair_specs]:
        raise ValueError("Task 4 final 28-pair search differs")
    if len(search_cells) < len(pair_specs) or any(
        cell.spec != spec
        for cell, spec in zip(search_cells[: len(pair_specs)], pair_specs, strict=True)
    ):
        raise ValueError("Task 4 final pair cell specifications differ")

    outcomes: dict[frozenset[str], FirstDivergences] = {
        frozenset((spec.subject,)): divergences[spec.cell_id]
        for spec in specs
        if spec.scope == "CANONICAL_LEAVE_ONE_OUT" and spec.axis == FULL_REMOVAL
    }
    for spec in pair_specs:
        difference = divergences.get(spec.cell_id)
        if difference is None:
            raise ValueError("Task 4 final pair divergence is absent")
        outcomes[frozenset(spec.removed_symbols)] = difference
    support = {
        triple: necessary_triple_support(triple, outcomes)
        for triple in combinations(ranked, 3)
    }
    _, triples = select_bounded_search(ranked, support)
    triple_specs = tuple(_search_spec(triple, scope="NECESSARY_TRIPLE") for triple in triples)
    if _exact_string_list(
        summary["supported_triple_cell_ids"], label="supported triple identities"
    ) != [spec.cell_id for spec in triple_specs]:
        raise ValueError("Task 4 final supported triple search differs")
    expected_search_specs = (*pair_specs, *triple_specs)
    if len(search_cells) != len(expected_search_specs) or any(
        cell.spec != spec for cell, spec in zip(search_cells, expected_search_specs, strict=True)
    ):
        raise ValueError("Task 4 final search cell specifications differ")
    for spec in triple_specs:
        outcomes[frozenset(spec.removed_symbols)] = divergences[spec.cell_id]
    necessary_ids = [
        spec.cell_id for spec in triple_specs if is_decisive(divergences[spec.cell_id])
    ]
    if _exact_string_list(
        summary["necessary_triple_cell_ids"], label="necessary triple identities"
    ) != necessary_ids:
        raise ValueError("Task 4 final necessary triples differ")
    minimal_sets = minimal_decisive_witness_sets(outcomes)
    if summary["minimal_witness_sets"] != [list(symbols) for symbols in minimal_sets]:
        raise ValueError("Task 4 final minimal witness sets differ")

    roles = summary["symbol_roles"]
    allowed_roles = {
        "owner",
        "qualification witness",
        "ghost witness",
        "decisive-pair member",
        "risk anchor",
    }
    if not isinstance(roles, Mapping) or not all(
        isinstance(symbol, str)
        and symbol
        and isinstance(values, list)
        and all(isinstance(value, str) and value for value in values)
        and set(values) <= allowed_roles
        and len(values) == len(set(values))
        for symbol, values in roles.items()
    ):
        raise ValueError("Task 4 final symbol roles are malformed")
    expected_pair_members = {
        symbol for symbols in minimal_sets if len(symbols) == 2 for symbol in symbols
    }
    observed_pair_members = {
        str(symbol)
        for symbol, values in roles.items()
        if "decisive-pair member" in values
    }
    if observed_pair_members != expected_pair_members:
        raise ValueError("Task 4 final decisive-pair roles are non-minimal")

    sentinels = task4_sentinel_specs(specs)
    sentinel = summary["sentinel_verification"]
    if not isinstance(sentinel, Mapping) or set(sentinel) != {"window", "observations"}:
        raise ValueError("Task 4 final sentinel verification is malformed")
    if sentinel["window"] != expected_scenario["sentinel_window"]:
        raise ValueError("Task 4 final sentinel window differs")
    observations = sentinel["observations"]
    if not isinstance(observations, list) or len(observations) != len(sentinels):
        raise ValueError("Task 4 final sentinel observations differ")
    for spec, observation in zip(sentinels, observations, strict=True):
        expected_keys = {
            "cell_id",
            "evidence_class",
            "status",
            "trace_row_count",
            "comparable",
        }
        if not isinstance(observation, Mapping) or set(observation) != expected_keys:
            raise ValueError("Task 4 final sentinel observation fields differ")
        count = observation["trace_row_count"]
        expected_comparable = spec.evidence_class == ECONOMIC
        if (
            observation["cell_id"] != spec.cell_id
            or observation["evidence_class"] != spec.evidence_class
            or observation["status"] != "SUCCESS"
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < int(expected_comparable)
            or observation["comparable"] is not expected_comparable
            or (not expected_comparable and count != 0)
        ):
            raise ValueError("Task 4 final sentinel observation differs")

    baselines = summary["comparison_baseline_seals"]
    if not isinstance(baselines, Mapping) or set(baselines) != {
        "CANONICAL_34",
        "REPORT_UNIVERSE_13",
    }:
        raise ValueError("Task 4 final comparison baseline seals differ")
    for value in baselines.values():
        if not isinstance(value, Mapping) or set(value) != {
            "trace_sha256",
            "final_account_payload_sha256",
        }:
            raise ValueError("Task 4 final comparison baseline seal fields differ")
        require_sha256(value["trace_sha256"], field="Task 4 comparison baseline trace")
        require_sha256(
            value["final_account_payload_sha256"],
            field="Task 4 comparison baseline account",
        )
    resume_summary = summary["resume"]
    if not isinstance(resume_summary, Mapping) or set(resume_summary) != {
        "reused_cell_count",
        "executed_cell_count",
        "checkpoint_count",
    }:
        raise ValueError("Task 4 final resume summary differs")
    counts = tuple(resume_summary[field] for field in sorted(resume_summary))
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts):
        raise ValueError("Task 4 final resume counts are malformed")
    if (
        resume_summary["checkpoint_count"] != len(cells) + len(search_cells)
        or resume_summary["reused_cell_count"] + resume_summary["executed_cell_count"]
        != resume_summary["checkpoint_count"]
        or summary["execution_entrypoint"]
        != "python -m research.strategic_evidence.witness_ablation_runner run --resume"
    ):
        raise ValueError("Task 4 final execution summary differs")
    return cells, search_cells, provenance


def verify_task4_outputs(
    root: str | Path,
    *,
    summary_path: str | Path = _DEFAULT_SUMMARY,
    manifest_path: str | Path = _DEFAULT_MANIFEST,
    trace_shard_path: str | Path = _DEFAULT_TRACE_SHARD,
) -> dict[str, Any]:
    """Stream-verify compact seals, exact initial coverage, and full-route linkage."""

    repository = Path(root).resolve()
    summary_target = Path(summary_path)
    if not summary_target.is_absolute():
        summary_target = repository / summary_target
    manifest_target = Path(manifest_path)
    if not manifest_target.is_absolute():
        manifest_target = repository / manifest_target
    summary = verify_sealed_payload(
        json.loads(summary_target.read_text(encoding="utf-8")),
        label="checkpoint4 witness ablation summary",
    )
    cells, search_cells, provenance = validate_final_summary_contract(repository, summary)
    all_cells = (*cells, *search_cells)
    final_identity = canonical_sha256(
        {
            "kind": "task4-witness-ablation-full-route",
            "provenance": dict(provenance),
            "cell_ids": [cell.cell_id for cell in all_cells],
        }
    )
    route_metadata = verify_full_route_linkage(
        trace_shard_path,
        expected_cell_ids=[cell.cell_id for cell in all_cells],
        expected_cells=all_cells,
        expected_provenance=provenance,
        expected_resume_identity=final_identity,
    )
    manifest_verification = verify_task4_manifest(
        repository,
        summary_path=summary_target,
        manifest_path=manifest_target,
        route_metadata=route_metadata,
    )
    return {
        **manifest_verification,
        "initial_cell_count": len(cells),
        "search_cell_count": len(search_cells),
        "status_counts": dict(sorted(Counter(cell.status for cell in cells).items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="execute/resume Task-4 initial and bounded search")
    verify_outputs = subparsers.add_parser(
        "verify", help="verify compact Task-4 outputs and external full routes"
    )
    verify = subparsers.add_parser("verify-shard", help="stream-verify one Task-4 route shard")
    verify.add_argument("path")
    for item in (run, verify_outputs):
        item.add_argument("--root", default=".")
        item.add_argument("--summary", default=str(_DEFAULT_SUMMARY))
        item.add_argument("--manifest", default=str(_DEFAULT_MANIFEST))
        item.add_argument("--trace-shard", default=str(_DEFAULT_TRACE_SHARD))
    run.add_argument("--resume-dir", default=str(_DEFAULT_RESUME_DIR))
    run.add_argument("--resume", action="store_true")
    run.add_argument("--initial-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a bounded Task-4 verification command without loading full routes."""

    args = _parser().parse_args(argv)
    if args.command == "run":
        result = execute_task4_matrix(
            args.root,
            summary_path=args.summary,
            manifest_path=args.manifest,
            trace_shard_path=args.trace_shard,
            resume_dir=args.resume_dir,
            resume=args.resume,
            include_bounded_search=not args.initial_only,
        )
    elif args.command == "verify":
        result = verify_task4_outputs(
            args.root,
            summary_path=args.summary,
            manifest_path=args.manifest,
            trace_shard_path=args.trace_shard,
        )
    else:
        result = verify_streaming_shard(args.path)
    print(json.dumps(result, allow_nan=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "BalancedIndustryUniverse",
    "assemble_full_route_shard",
    "build_executable_source_manifest",
    "build_resume_identity",
    "build_task4_scenario",
    "capture_runtime_metadata",
    "comparison_baseline_scope",
    "derive_balanced_industry_universe",
    "execute_task4_matrix",
    "iter_streaming_rows",
    "main",
    "read_cell_shard",
    "recompute_task4_identities",
    "resolve_ablation_universe",
    "resolve_resume_runtime_metadata",
    "task4_sentinel_specs",
    "validate_final_summary_contract",
    "validate_initial_coverage",
    "verify_full_route_linkage",
    "verify_streaming_shard",
    "verify_task4_manifest",
    "verify_task4_outputs",
    "write_cell_shard",
    "write_compact_and_manifest",
    "write_streaming_shard",
)
