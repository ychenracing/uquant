"""Authenticate raw Absolute LOO evidence for the generalization tradeoff gate."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from uquant.provenance.fingerprints import (
    git_source_surface_fingerprint,
    source_surface_fingerprint,
)
from uquant.validation.absolute_generalization.artifacts import validate_cell_artifact
from uquant.validation.absolute_generalization.contract import (
    AbsoluteGeneralizationContract,
    load_absolute_generalization_contract,
)

_SURFACE = "economic_decision_v1"
_TRUSTED_RUNNER_PATHS = (
    "scripts/run_absolute_generalization_acceptance.py",
    "uquant/validation/absolute_generalization",
)
_LOO_SHARDS = frozenset(f"loo-{letter}" for letter in "abcdef")
_PUBLIC_MANIFEST_VALIDATOR = """
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from uquant.validation.absolute_generalization import (
    load_absolute_generalization_contract,
    validate_shard_manifest,
)

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
source = sys.argv[2]
manifest_path = Path(sys.argv[3])
expected_sha256 = sys.argv[4]
encoded = manifest_path.read_bytes()
if hashlib.sha256(encoded).hexdigest() != expected_sha256:
    raise ValueError("manifest bytes changed after parent read")
contract = load_absolute_generalization_contract(
    root / "benchmarks/absolute_generalization_acceptance_contract.json"
)
contract = replace(
    contract,
    candidate=replace(contract.candidate, production_source_sha256=source),
)
validate_shard_manifest(json.loads(encoded), contract)
"""


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _evaluate_cells(cells: Iterable[Any], universe: Sequence[str], policy: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(cells)
    symbols = [row.removed_symbol for row in rows]
    if len(rows) != len(universe) or len(set(symbols)) != len(symbols) or set(symbols) != set(universe):
        return {"status": "INCOMPLETE", "passed": False, "failures": ["native 34/34 coverage differs"]}
    if any(row.status != "COMPLETE" or row.metrics is None for row in rows):
        return {"status": "INCOMPLETE", "passed": False, "failures": ["native raw replay is incomplete"]}
    wealth = [float(row.metrics.final_wealth) for row in rows]
    drawdown = [float(row.metrics.max_drawdown) for row in rows]
    if any(not math.isfinite(value) for value in (*wealth, *drawdown)):
        return {"status": "INCOMPLETE", "passed": False, "failures": ["native metrics are non-finite"]}
    metrics = {
        "positive_fraction": sum(value > 1.0 for value in wealth) / len(wealth),
        "p10_wealth": _percentile(wealth, 0.10),
        "p90_drawdown": _percentile(drawdown, 0.90),
        "worst_drawdown": max(drawdown),
    }
    limits = (
        ("positive_fraction", "minimum_positive_fraction", ">="),
        ("p10_wealth", "minimum_p10_wealth", ">="),
        ("p90_drawdown", "maximum_p90_drawdown", "<="),
        ("worst_drawdown", "maximum_worst_drawdown", "<="),
    )
    failures = [
        {"metric": metric, "actual": metrics[metric], "operator": operator, "required": float(policy[key])}
        for metric, key, operator in limits
        if (metrics[metric] < float(policy[key]) if operator == ">=" else metrics[metric] > float(policy[key]))
    ]
    diagnostics = {
        "epoch_owner_counts": [row.metrics.distinct_owner_count for row in rows],
        "passive_wealth_ratio": "unavailable in raw Absolute CellMetrics; diagnostic only",
        "cash_drag": [row.metrics.cash_drag for row in rows],
    }
    return {"status": "FAIL" if failures else "PASS", "passed": not failures,
            "metrics": metrics, "failures": failures, "diagnostics": diagnostics}


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(root), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()  # nosec B603


def _trusted_contract(
    root: Path, candidate: Path, producer_head: str, producer_source: str,
) -> AbsoluteGeneralizationContract:
    base = load_absolute_generalization_contract(root / "benchmarks/absolute_generalization_acceptance_contract.json")
    candidate_source = source_surface_fingerprint(candidate, _SURFACE)
    candidate_head = _git(candidate, "rev-parse", "HEAD")
    if candidate_source != git_source_surface_fingerprint(candidate, candidate_head, _SURFACE):
        raise ValueError("candidate economic source is not its committed HEAD")
    if producer_source != git_source_surface_fingerprint(root, producer_head, _SURFACE):
        raise ValueError("native producer Git economic source differs from manifest")
    if producer_source != candidate_source:
        raise ValueError("native producer economic source differs from evaluated candidate")
    return replace(base, candidate=replace(base.candidate, production_source_sha256=producer_source))


def _validate_contract_scope(
    tradeoff: Mapping[str, Any], absolute: AbsoluteGeneralizationContract,
) -> None:
    if tradeoff.get("universe") != list(absolute.canonical_universe):
        raise ValueError("tradeoff universe differs from native Absolute universe")
    if tradeoff.get("window") != {
        "start": absolute.window_start.isoformat(), "end": absolute.window_end.isoformat(),
    }:
        raise ValueError("tradeoff window differs from native Absolute window")


def _verify_producer(root: Path, candidate: Path, head: str, tree: str) -> None:
    if _git(root, "rev-parse", f"{head}^{{commit}}") != head or _git(root, "rev-parse", f"{head}^{{tree}}") != tree:
        raise ValueError("native producer Git identity differs")
    if _git(candidate, "status", "--porcelain", "--", *_TRUSTED_RUNNER_PATHS):
        raise ValueError("candidate validation runner has uncommitted bytes")
    for relative in _TRUSTED_RUNNER_PATHS:
        producer = _git(root, "rev-parse", f"{head}:{relative}")
        candidate_object = _git(candidate, "hash-object", str(candidate / relative)) if (candidate / relative).is_file() else _git(candidate, "rev-parse", f"HEAD:{relative}")
        if producer != candidate_object:
            raise ValueError(f"native validation runner differs: {relative}")


def _verify_producer_tree(root: Path, head: str, candidate_source_tree: str) -> None:
    if _git(root, "rev-parse", f"{head}:uquant") != candidate_source_tree:
        raise ValueError("native producer uquant tree differs from evaluated candidate")


def _validate_manifest_at_producer(
    root: Path,
    head: str,
    producer_source: str,
    manifest_path: Path,
    manifest_sha256: str,
) -> None:
    """Run the public manifest validator in an exact temporary producer checkout."""

    with tempfile.TemporaryDirectory(prefix="uquant-native-manifest-") as folder:
        checkout = Path(folder) / "producer"
        _git(root, "worktree", "add", "--detach", str(checkout), head)
        try:
            completed = subprocess.run(  # nosec B603
                [
                    sys.executable,
                    "-c",
                    _PUBLIC_MANIFEST_VALIDATOR,
                    str(checkout),
                    producer_source,
                    str(manifest_path),
                    manifest_sha256,
                ],
                text=True,
                capture_output=True,
                cwd=checkout,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or "failed"
                raise ValueError(f"public shard manifest validation failed: {detail}")
        finally:
            _git(root, "worktree", "remove", "--force", str(checkout))


def evaluate_native(
    root: str | Path,
    candidate_root: str | Path,
    candidate_source_tree: str,
    sharddir: str | Path,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Return PASS only for 34 authenticated, fully reconciled raw LOO cells."""
    repository, candidate, shards = map(lambda value: Path(value).resolve(), (root, candidate_root, sharddir))
    try:
        if _git(candidate, "rev-parse", "HEAD:uquant") != candidate_source_tree:
            raise ValueError("candidate_root HEAD:uquant differs from evaluator candidate_source_tree")
        paths = sorted(path for path in shards.rglob("manifest.json") if path.parent.name in _LOO_SHARDS)
        if not paths:
            raise ValueError("native shard manifests are missing")
        producer: tuple[str, str, str] | None = None
        absolute: AbsoluteGeneralizationContract | None = None
        cells: list[Any] = []
        seen_shards: set[str] = set()
        run_identities: set[str] = set()
        for path in paths:
            encoded = path.read_bytes()
            raw = json.loads(encoded)
            identity = (raw.get("head"), raw.get("tree"), raw.get("production_source_sha256"))
            if not all(isinstance(value, str) for value in identity):
                raise ValueError("native shard producer identity is malformed")
            typed_identity = cast(tuple[str, str, str], identity)
            if producer is None:
                producer = typed_identity
                head, tree, producer_source = producer
                _verify_producer(repository, candidate, head, tree)
                _verify_producer_tree(repository, head, candidate_source_tree)
                absolute = _trusted_contract(repository, candidate, head, producer_source)
                _validate_contract_scope(contract, absolute)
            elif typed_identity != producer:
                raise ValueError("native shard producer identity is mixed")
            assert absolute is not None and producer is not None
            head, tree, producer_source = producer
            _validate_manifest_at_producer(
                repository, head, producer_source, path, hashlib.sha256(encoded).hexdigest()
            )
            document = raw
            shard = str(document["shard"])
            if shard in seen_shards or shard not in {name for name, _ in absolute.shards}:
                raise ValueError("native shard identity is duplicate or unexpected")
            seen_shards.add(shard)
            for item in cast(Sequence[Mapping[str, object]], document["cells"]):
                cell = validate_cell_artifact(item, absolute)
                if cell.identities.head != head or cell.identities.tree != tree:
                    raise ValueError("native cell checkout differs from its original CI manifest")
                cells.append(replace(cell, replay_evidence=None))
            run_identities.add(f"{document['run_id']}:{document['run_attempt']}")
            del raw, document
        if absolute is None or producer is None:
            raise ValueError("native LOO shard manifests are missing")
        result = _evaluate_cells(cells, absolute.canonical_universe, contract["native_absolute"])
        result["evidence"] = {"head": head, "tree": tree, "production_source_sha256": producer_source,
                              "run_identities": sorted(run_identities),
                              "manifest_count": len(paths), "cell_count": len(cells)}
        return result
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {"status": "INCOMPLETE", "passed": False,
                "failures": [f"{type(exc).__name__}: {exc}"]}


__all__ = ("evaluate_native",)
