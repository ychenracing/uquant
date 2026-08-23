"""Deterministic universe-generalization diagnostics and frozen references.

The production engine remains the only strategy implementation.  This module
only constructs causal universe perturbations, replays them through a supplied
runner, and aggregates dependency evidence.  It deliberately has no API that
writes a baseline file: reference updates must remain an explicit, reviewed
repository change.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pandas as pd

from ..ai_era import require_ai_era_interval
from ..manifest import verify_data_manifest
from .models import (
    _COMMIT,
    _COMPETITOR_BEST_FIELDS,
    _COMPETITOR_PROVENANCE_FIELDS,
    _EXECUTION_CONTRACT,
    _FIXED_PRODUCTION_PATHS,
    _PROVENANCE_SECTIONS,
    _SHA256,
)
from .scenarios import _canonical_symbols, _validate_industry_coverage


def compatibility_value[Value](name: str, fallback: Value) -> Value:
    facade = sys.modules.get("uquant.validation.generalization")
    return cast(Value, getattr(facade, name, fallback)) if facade is not None else fallback

def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validation_fingerprint(
    *,
    case_fingerprint: str,
    provenance: Mapping[str, Any],
    competitor_best: Mapping[str, Any],
) -> str:
    """Bind the case design, replay inputs, and reviewed external objective."""
    return _fingerprint(
        {
            "case_fingerprint": case_fingerprint,
            "provenance": provenance,
            "competitor_best": competitor_best,
        }
    )


def _exact_fields(value: Any, expected: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"generalization {label} must be an object")
    observed = set(value)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise RuntimeError(f"generalization {label} is missing fields: {missing}")
    if unexpected:
        raise RuntimeError(f"generalization {label} has unexpected fields: {unexpected}")
    return value


def _nonempty_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"generalization {label} must be a non-empty string")
    return value


def _validated_provenance(value: Any) -> dict[str, Any]:
    """Validate and normalize every input needed to reproduce the replay."""
    root = _exact_fields(value, _PROVENANCE_SECTIONS, label="provenance")
    data = _exact_fields(
        root["data"],
        {"snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"},
        label="provenance.data",
    )
    snapshot_id = _nonempty_text(data["snapshot_id"], label="provenance.data.snapshot_id")
    files_verified = data["files_verified"]
    if isinstance(files_verified, bool) or not isinstance(files_verified, int) or files_verified < 1:
        raise RuntimeError("generalization provenance.data.files_verified must be a positive integer")
    data_hashes: dict[str, str] = {}
    for name in ("manifest_sha256", "checksums_sha256"):
        digest = _nonempty_text(data[name], label=f"provenance.data.{name}")
        if not _SHA256.fullmatch(digest):
            raise RuntimeError(f"generalization provenance.data.{name} must be SHA-256")
        data_hashes[name] = digest

    dataset = _exact_fields(
        root["dataset"],
        {"universe", "industries", "prior_symbols", "start", "end"},
        label="provenance.dataset",
    )
    universe_value = dataset["universe"]
    prior_value = dataset["prior_symbols"]
    if not isinstance(universe_value, list) or not isinstance(prior_value, list):
        raise RuntimeError("generalization provenance dataset memberships must be lists")
    try:
        universe = _canonical_symbols(universe_value, label="provenance dataset universe")
        priors = _canonical_symbols(prior_value, label="provenance dataset prior symbols")
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if list(universe) != universe_value or list(priors) != prior_value:
        raise RuntimeError("generalization provenance dataset memberships must be canonical")
    if not set(priors) <= set(universe):
        raise RuntimeError("generalization provenance prior symbols are outside the universe")
    industries = dataset["industries"]
    if not isinstance(industries, Mapping):
        raise RuntimeError("generalization provenance.dataset.industries must be an object")
    normalized_industries = dict(sorted(industries.items(), key=lambda item: str(item[0])))
    try:
        _validate_industry_coverage(universe, normalized_industries)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    start = _nonempty_text(dataset["start"], label="provenance.dataset.start")
    end = _nonempty_text(dataset["end"], label="provenance.dataset.end")
    try:
        start_date = pd.Timestamp(start).normalize()
        end_date = pd.Timestamp(end).normalize()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("generalization provenance dataset window is invalid") from exc
    if start_date > end_date or str(start_date.date()) != start or str(end_date.date()) != end:
        raise RuntimeError("generalization provenance dataset window must be canonical and ordered")
    require_ai_era_interval(start, end)

    execution = _exact_fields(
        root["execution"],
        set(_EXECUTION_CONTRACT) | {"initial_cash"},
        label="provenance.execution",
    )
    for name, expected in _EXECUTION_CONTRACT.items():
        if execution[name] != expected:
            raise RuntimeError(f"generalization execution contract mismatch: {name}")
    initial_cash = execution["initial_cash"]
    if (
        isinstance(initial_cash, bool)
        or not isinstance(initial_cash, (int, float))
        or not math.isfinite(float(initial_cash))
        or float(initial_cash) <= 0
    ):
        raise RuntimeError("generalization execution initial_cash must be positive and finite")

    production = _exact_fields(
        root["production"],
        {"repository", "commit", "source_sha256"},
        label="provenance.production",
    )
    repository = _nonempty_text(production["repository"], label="provenance.production.repository")
    commit = _nonempty_text(production["commit"], label="provenance.production.commit")
    source_sha256 = _nonempty_text(production["source_sha256"], label="provenance.production.source_sha256")
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("generalization production commit must be immutable")
    if not _SHA256.fullmatch(source_sha256):
        raise RuntimeError("generalization production source_sha256 must be SHA-256")

    return {
        "data": {
            "snapshot_id": snapshot_id,
            "files_verified": files_verified,
            **data_hashes,
        },
        "dataset": {
            "universe": list(universe),
            "industries": normalized_industries,
            "prior_symbols": list(priors),
            "start": start,
            "end": end,
        },
        "execution": {
            **_EXECUTION_CONTRACT,
            "initial_cash": float(initial_cash),
        },
        "production": {
            "repository": repository,
            "commit": commit,
            "source_sha256": source_sha256,
        },
    }


def _validated_competitor_best(value: Any) -> dict[str, Any]:
    root = _exact_fields(value, _COMPETITOR_BEST_FIELDS, label="competitor_best")
    if root["metric"] != "final_wealth" or root["scenario"] != "remove_all_priors":
        raise RuntimeError("generalization competitor_best must be final_wealth for remove_all_priors")
    metric_value = root["value"]
    if (
        isinstance(metric_value, bool)
        or not isinstance(metric_value, (int, float))
        or not math.isfinite(float(metric_value))
        or float(metric_value) <= 0
    ):
        raise RuntimeError("generalization competitor_best.value must be positive and finite")
    provenance = _exact_fields(
        root["provenance"],
        _COMPETITOR_PROVENANCE_FIELDS,
        label="competitor_best.provenance",
    )
    normalized_provenance = {
        name: _nonempty_text(provenance[name], label=f"competitor_best.provenance.{name}")
        for name in sorted(_COMPETITOR_PROVENANCE_FIELDS)
    }
    if not _COMMIT.fullmatch(normalized_provenance["reference_commit"]):
        raise RuntimeError("generalization competitor reference_commit must be immutable")
    if not _SHA256.fullmatch(normalized_provenance["reference_sha256"]):
        raise RuntimeError("generalization competitor reference_sha256 must be SHA-256")
    return {
        "metric": "final_wealth",
        "scenario": "remove_all_priors",
        "value": float(metric_value),
        "provenance": normalized_provenance,
    }


def _production_source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [
        *(root / relative for relative in _FIXED_PRODUCTION_PATHS),
        *sorted((root / "uquant").rglob("*.py")),
    ]
    if any(not path.is_file() for path in paths):
        raise RuntimeError("cannot fingerprint generalization production source")
    for path in paths:
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve git executable for generalization provenance")
    return executable


def _git_stdout(root: Path, arguments: list[str], *, label: str) -> str:
    try:
        # Git is resolved explicitly; every caller supplies a fixed argument list.
        completed = subprocess.run(
            [_git_executable(), "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(label) from exc
    return completed.stdout


def _production_commit(root: Path) -> str:
    status = compatibility_value("_git_stdout", _git_stdout)(
        root,
        [
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "uquant",
            *_FIXED_PRODUCTION_PATHS,
        ],
        label="cannot inspect generalization production source",
    )
    if status.strip():
        raise RuntimeError("generalization production provenance requires committed source")
    commit = compatibility_value("_git_stdout", _git_stdout)(
        root,
        ["log", "-1", "--format=%H", "--", "uquant", *_FIXED_PRODUCTION_PATHS],
        label="cannot resolve immutable production commit",
    ).strip()
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("cannot resolve immutable production commit")
    return commit


@contextmanager
def _immutable_validation_inputs(
    *,
    baseline_path: Path,
    baseline_sha256: str,
    data_dir: str | Path,
    repository_root: Path,
    data_before: Mapping[str, Any],
    source_before: str,
) -> Iterator[None]:
    """Reject baseline, candidate-source, or frozen-data mutation during replay."""
    try:
        yield
    finally:
        try:
            current_baseline = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            data_after = compatibility_value(
                "verify_data_manifest", verify_data_manifest
            )(data_dir)
            source_after = compatibility_value(
                "_production_source_fingerprint", _production_source_fingerprint
            )(repository_root)
        except Exception as exc:
            raise RuntimeError("generalization source or data changed during validation") from exc
        if current_baseline != baseline_sha256:
            raise RuntimeError("generalization baseline changed during validation")
        if data_after != data_before or source_after != source_before:
            raise RuntimeError("generalization source or data changed during validation")


def build_generalization_provenance(
    *,
    data: Mapping[str, Any],
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    start: str,
    end: str,
    production_commit: str,
    production_source_sha256: str,
    repository: str = "ychenracing/uquant",
    initial_cash: float = 2_000_000.0,
) -> dict[str, Any]:
    """Build the exact reviewed provenance envelope for baseline evidence."""
    symbols = _canonical_symbols(universe, label="generalization universe")
    priors = _canonical_symbols(prior_symbols, label="prior symbols")
    return _validated_provenance(
        {
            "data": dict(data),
            "dataset": {
                "universe": list(symbols),
                "industries": dict(sorted(industries.items())),
                "prior_symbols": list(priors),
                "start": start,
                "end": end,
            },
            "execution": {
                **_EXECUTION_CONTRACT,
                "initial_cash": initial_cash,
            },
            "production": {
                "repository": repository,
                "commit": production_commit,
                "source_sha256": production_source_sha256,
            },
        }
    )
