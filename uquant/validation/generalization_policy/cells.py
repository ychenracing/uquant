"""Compile-anchored champion baseline and frozen AI-era gate policy."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from ..generalization_contract import (
    RANDOM_BASE_SEED,
    RANDOM_POOL_SIZES,
    RANDOM_SEED_INDEXES,
    official_windows,
)
from ..universe import (
    REQUIRED_FROZEN_CHAMPION_SHA256,
    load_ai_universe,
    load_phase1_frozen_champion,
)
from .projection import (
    attribution_neutral_equality_sha256 as _attribution_neutral_equality_sha256,
)
from .schema import (
    ATTRIBUTION_DEFINITION as _ATTRIBUTION_DEFINITION,
)
from .schema import (
    BASELINE_CELL_FIELDS as _BASELINE_CELL_FIELDS,
)
from .schema import (
    CHAMPION_MATRIX_PATH,
    GENERALIZATION_BASELINE_PATH,
    GENERALIZATION_POLICY_PATH,
    REQUIRED_GENERALIZATION_BASELINE_SHA256,
    REQUIRED_GENERALIZATION_POLICY_SHA256,
    BaselineCell,
    GeneralizationBaseline,
    GeneralizationPolicy,
)
from .schema import (
    COMMIT_PATTERN as _COMMIT,
)
from .schema import (
    artifact_equality_sha256 as _artifact_equality_sha256,
)
from .schema import (
    derived_seed as _derived_seed,
)
from .schema import (
    metric_payload as _metric_payload,
)
from .schema import (
    read_json as _read_json,
)
from .schema import (
    reject_duplicate_keys as _reject_duplicate_keys,
)
from .schema import (
    reject_nonstandard_constant as _reject_nonstandard_constant,
)
from .schema import (
    replay_error as _replay_error,
)
from .schema import (
    require_exact_seal as _require_exact_seal,
)
from .schema import (
    require_sha256 as _require_sha256,
)


def _validate_baseline_seed(
    *,
    family: str,
    pool_size: Any,
    seed_index: Any,
    derived_seed: Any,
    identifier: str,
) -> None:
    random_fields = (pool_size, seed_index, derived_seed)
    if family == "random":
        if (
            isinstance(pool_size, bool)
            or not isinstance(pool_size, int)
            or isinstance(seed_index, bool)
            or not isinstance(seed_index, int)
            or isinstance(derived_seed, bool)
            or not isinstance(derived_seed, int)
            or pool_size not in RANDOM_POOL_SIZES
            or seed_index not in RANDOM_SEED_INDEXES
            or derived_seed != _derived_seed(pool_size, seed_index)
        ):
            raise ValueError(f"generalization baseline random seed differs: {identifier}")
    elif any(item is not None for item in random_fields):
        raise ValueError(f"generalization baseline non-random seed is present: {identifier}")


def _validated_baseline_cell(
    raw: Any,
    *,
    cells: Mapping[str, BaselineCell],
) -> BaselineCell:
    if not isinstance(raw, Mapping) or set(raw) != _BASELINE_CELL_FIELDS:
        raise ValueError("generalization baseline cell schema is malformed")
    window = raw["window"]
    scenario = raw["scenario"]
    family = raw["family"]
    status = raw["status"]
    economic = raw["economic"]
    if not all(isinstance(item, str) and item for item in (window, scenario, family, status)):
        raise ValueError("generalization baseline cell identity is malformed")
    if not isinstance(economic, bool) or status not in {
        "READY",
        "INSUFFICIENT_SAMPLE",
    }:
        raise ValueError("generalization baseline cell status is malformed")
    identifier = f"{window}/{scenario}"
    if identifier in cells:
        raise ValueError(f"generalization baseline contains duplicate cell: {identifier}")
    metrics = _metric_payload(raw["metrics"], identifier=identifier)
    replay_error = _replay_error(raw["replay_error"], identifier=identifier)
    if economic != (status == "READY"):
        raise ValueError(f"generalization baseline economic status differs: {identifier}")
    if economic and (metrics is None) == (replay_error is None):
        raise ValueError(f"generalization baseline economic evidence is incomplete: {identifier}")
    if not economic and (metrics is not None or replay_error is not None):
        raise ValueError(f"generalization insufficient sample has economic evidence: {identifier}")
    pool_size = raw["pool_size"]
    seed_index = raw["seed_index"]
    derived_seed = raw["derived_seed"]
    _validate_baseline_seed(
        family=family,
        pool_size=pool_size,
        seed_index=seed_index,
        derived_seed=derived_seed,
        identifier=identifier,
    )
    return BaselineCell(
        window=window,
        scenario=scenario,
        family=family,
        status=status,
        economic=economic,
        pool_size=pool_size,
        seed_index=seed_index,
        derived_seed=derived_seed,
        evidence_sha256=_require_sha256(
            raw["evidence_sha256"],
            label=f"generalization baseline evidence {identifier}",
        ),
        contract_sha256=_require_sha256(
            raw["contract_sha256"],
            label=f"generalization baseline contract {identifier}",
        ),
        metrics=metrics,
        replay_error=replay_error,
    )


def _validate_baseline_coverage(
    by_window: Mapping[str, list[BaselineCell]],
) -> None:
    expected_windows = {window.name for window in official_windows()}
    if set(by_window) != expected_windows:
        raise ValueError("generalization baseline windows differ from the official contract")
    for name, window_cells in by_window.items():
        if len(window_cells) != 39 or sum(cell.economic for cell in window_cells) != 32:
            raise ValueError(f"generalization baseline coverage differs: {name}")
        random_pairs = {(cell.pool_size, cell.seed_index) for cell in window_cells if cell.family == "random"}
        if random_pairs != {(size, index) for size in RANDOM_POOL_SIZES for index in RANDOM_SEED_INDEXES}:
            raise ValueError(f"generalization baseline random matrix differs: {name}")


def _load_baseline_cells(raw_cells: Any) -> Mapping[str, BaselineCell]:
    if not isinstance(raw_cells, list) or len(raw_cells) != 234:
        raise ValueError("generalization baseline must contain all 234 contract records")
    cells: dict[str, BaselineCell] = {}
    by_window: dict[str, list[BaselineCell]] = defaultdict(list)
    for raw in raw_cells:
        cell = _validated_baseline_cell(raw, cells=cells)
        cells[cell.identifier] = cell
        by_window[cell.window].append(cell)
    _validate_baseline_coverage(by_window)
    return MappingProxyType(cells)


def _baseline_champion_and_runner(
    payload: Mapping[str, Any],
) -> tuple[Any, Mapping[str, Any], dict[str, Any]]:
    champion_payload = payload["champion"]
    runner = payload["matrix_runner"]
    if not isinstance(champion_payload, Mapping) or not isinstance(runner, Mapping):
        raise ValueError("generalization baseline provenance is malformed")
    champion = load_phase1_frozen_champion()
    expected_champion = {
        "phase1_contract_sha256": REQUIRED_FROZEN_CHAMPION_SHA256,
        "production_commit": champion.production_commit,
        "production_source_sha256": champion.production_source_sha256,
        "effective_config_sha256": champion.effective_config_sha256,
        "data": {
            "snapshot_id": champion.data_snapshot_id,
            "files_verified": champion.data_files_verified,
            "manifest_sha256": champion.data_manifest_sha256,
            "checksums_sha256": champion.data_checksums_sha256,
        },
        "environment": {
            "python_full_version": champion.python_full_version,
            "numpy_version": champion.numpy_version,
            "pandas_version": champion.pandas_version,
            "uv_version": champion.uv_version,
            "uv_lock_sha256": champion.uv_lock_sha256,
        },
        "github_phase1_artifact_sha256": champion.github_artifact_sha256,
    }
    if dict(champion_payload) != expected_champion:
        raise ValueError("generalization baseline differs from the accepted Phase 1 champion")
    return champion, runner, expected_champion


def _validated_baseline_runner(
    runner: Mapping[str, Any],
    *,
    champion: Any,
    expected_champion: Mapping[str, Any],
) -> tuple[str, str, int]:
    if not isinstance(runner.get("head"), str) or not _COMMIT.fullmatch(runner["head"]):
        raise ValueError("generalization baseline runner HEAD is malformed")
    runner_source = _require_sha256(
        runner.get("source_sha256"),
        label="generalization baseline runner source",
    )
    artifact_sha256 = _require_sha256(
        runner.get("artifact_sha256"),
        label="generalization baseline artifact",
    )
    artifact_size = runner.get("artifact_size_bytes")
    if isinstance(artifact_size, bool) or not isinstance(artifact_size, int) or artifact_size < 1:
        raise ValueError("generalization baseline artifact size is malformed")
    if runner.get("effective_config_sha256") != champion.effective_config_sha256:
        raise ValueError("generalization baseline config differs from the champion")
    if runner.get("data") != expected_champion["data"]:
        raise ValueError("generalization baseline data differs from the champion")
    if runner.get("universe_sha256") != load_ai_universe().sha256:
        raise ValueError("generalization baseline universe differs from the champion")
    return runner_source, artifact_sha256, artifact_size


def _reviewed_baseline_artifact(
    *,
    artifact_path: str | Path | None,
    artifact_size: int,
    artifact_sha256: str,
) -> tuple[str, str]:
    artifact_source = CHAMPION_MATRIX_PATH if artifact_path is None else Path(artifact_path)
    if artifact_source.is_symlink() or not artifact_source.is_file():
        raise ValueError(f"generalization champion artifact is missing: {artifact_source}")
    artifact_bytes = artifact_source.read_bytes()
    if len(artifact_bytes) != artifact_size or (
        hashlib.sha256(artifact_bytes).hexdigest() != artifact_sha256
    ):
        raise ValueError("generalization champion artifact differs from the reviewed baseline")
    try:
        reviewed_artifact = json.loads(
            artifact_bytes,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (UnicodeError, ValueError) as exc:
        raise ValueError("generalization champion artifact is corrupt") from exc
    if not isinstance(reviewed_artifact, dict):
        raise ValueError("generalization champion artifact must be an object")
    return (
        _artifact_equality_sha256(reviewed_artifact),
        _attribution_neutral_equality_sha256(
            {
                **reviewed_artifact,
                "attribution_definition": dict(_ATTRIBUTION_DEFINITION),
            }
        ),
    )


def load_generalization_baseline(
    path: str | Path | None = None,
    *,
    artifact_path: str | Path | None = None,
) -> GeneralizationBaseline:
    """Load the reviewed champion matrix summary and verify its bound raw artifact."""
    source = GENERALIZATION_BASELINE_PATH if path is None else Path(path)
    payload = _read_json(source, label="generalization baseline")
    if (
        set(payload)
        != {
            "schema_version",
            "baseline_id",
            "champion",
            "matrix_runner",
            "aggregates",
            "cells",
            "canonical_sha256",
        }
        or payload.get("schema_version") != 1
        or payload.get("baseline_id") != "ai-era-generalization-champion-v1"
    ):
        raise ValueError("generalization baseline schema is malformed")
    seal = _require_exact_seal(
        payload,
        label="generalization baseline",
        required=REQUIRED_GENERALIZATION_BASELINE_SHA256,
    )
    champion, runner, expected_champion = _baseline_champion_and_runner(payload)
    runner_source, artifact_sha256, artifact_size = _validated_baseline_runner(
        runner,
        champion=champion,
        expected_champion=expected_champion,
    )
    cells = _load_baseline_cells(payload["cells"])
    artifact_equality, attribution_neutral_equality = _reviewed_baseline_artifact(
        artifact_path=artifact_path,
        artifact_size=artifact_size,
        artifact_sha256=artifact_sha256,
    )
    if not isinstance(payload["aggregates"], Mapping):
        raise ValueError("generalization baseline aggregates are malformed")
    provenance = {
        key: value for key, value in runner.items() if key not in {"artifact_sha256", "artifact_size_bytes"}
    }
    return GeneralizationBaseline(
        sha256=seal,
        runner_head=runner["head"],
        runner_source_sha256=runner_source,
        artifact_sha256=artifact_sha256,
        artifact_size_bytes=artifact_size,
        artifact_equality_sha256=artifact_equality,
        attribution_neutral_equality_sha256=attribution_neutral_equality,
        provenance=MappingProxyType(provenance),
        aggregates=MappingProxyType(dict(payload["aggregates"])),
        cells=cells,
    )


def _validate_baseline_policy(value: Mapping[str, Any]) -> None:
    expected = {
        "empty_authenticated_support_requires_literal_policy": True,
        "exact_reviewed_evidence_passes": True,
        "floor_and_ceiling_bounds_use_authenticated_baseline": True,
        "identical_baseline_replay_error_passes": True,
        "recovered_cell_uses_authenticated_group_envelope": True,
        "recovered_cell_uses_relative_per_cell_tolerances": True,
        "recovered_cell_is_excluded_from_tail_rank_non_regression": True,
    }
    if dict(value) != expected:
        raise ValueError("generalization baseline non-regression differs from the reviewed contract")


def _validate_policy_thresholds(
    *,
    relative: Mapping[str, Any],
    intrinsic: Mapping[str, Any],
    tails: Mapping[str, Any],
) -> None:
    expected_relative = {
        "wealth_ratio_min": 0.95,
        "drawdown_absolute_buffer": 0.02,
        "orders_absolute_buffer": 1,
        "orders_ratio_max": 1.10,
        "turnover_ratio_max": 1.10,
        "zero_reference_turnover_requires_zero": True,
    }
    expected_intrinsic = {
        "directional_families": ["remove_all_core", "tradable_no_optical"],
        "directional_final_wealth_strict_min": 1.0,
        "directional_max_drawdown": 0.30,
        "remove_one_final_wealth_min": 0.80,
        "remove_one_max_drawdown": 0.30,
    }
    expected_tails = {
        "group_by": ["window", "pool_size"],
        "requested_seeds_per_group": 5,
        "positive_return_definition": "final_wealth > 1.0",
        "positive_return_fraction_min": 0.60,
        "p10_wealth_min": 0.80,
        "p90_drawdown_max": 0.30,
        "p90_orders_max": 20.0,
        "quantile_method": "linear interpolation at (n - 1) * probability",
        "replay_errors_excluded_from_quantiles": True,
        "replay_error_failure_handling": "baseline_non_regression",
    }
    if dict(relative) != expected_relative or dict(intrinsic) != expected_intrinsic:
        raise ValueError("generalization policy thresholds differ from the reviewed contract")
    if dict(tails) != expected_tails:
        raise ValueError("generalization random-tail policy differs from the reviewed contract")


def _validated_policy_windows(
    contract: Mapping[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    windows = tuple((window.name, window.start, window.end) for window in official_windows())
    expected_contract = {
        "windows": [{"name": name, "start": start, "end": end} for name, start, end in windows],
        "records_per_window": 39,
        "economic_cells_per_window": 32,
        "insufficient_sample_records_per_window": 7,
        "random_base_seed": RANDOM_BASE_SEED,
        "random_seed_indexes": list(RANDOM_SEED_INDEXES),
        "random_pool_sizes": list(RANDOM_POOL_SIZES),
        "window_fingerprint": contract.get("window_fingerprint"),
        "scenario_fingerprint": contract.get("scenario_fingerprint"),
        "evidence_fingerprint": contract.get("evidence_fingerprint"),
        "lookback_sessions": 120,
    }
    for name in (
        "window_fingerprint",
        "scenario_fingerprint",
        "evidence_fingerprint",
    ):
        _require_sha256(
            contract.get(name),
            label=f"generalization policy {name}",
        )
    if dict(contract) != expected_contract:
        raise ValueError("generalization scenario policy differs from the reviewed contract")
    return windows


def _loaded_generalization_policy(
    *,
    seal: str,
    baseline_sha256: str,
    windows: tuple[tuple[str, str, str], ...],
) -> GeneralizationPolicy:
    return GeneralizationPolicy(
        schema_version=2,
        policy_id="ai-era-generalization-policy-v2",
        sha256=seal,
        baseline_sha256=baseline_sha256,
        champion_equality_passes=True,
        baseline_grandfathering=True,
        empty_support_requires_literal_policy=True,
        identical_baseline_replay_error_passes=True,
        recovered_replay_envelope=True,
        wealth_ratio_min=0.95,
        drawdown_absolute_buffer=0.02,
        orders_absolute_buffer=1,
        orders_ratio_max=1.10,
        turnover_ratio_max=1.10,
        directional_final_wealth_strict_min=1.0,
        directional_max_drawdown=0.30,
        remove_one_final_wealth_min=0.80,
        remove_one_max_drawdown=0.30,
        positive_return_fraction_min=0.60,
        p10_wealth_min=0.80,
        p90_drawdown_max=0.30,
        p90_orders_max=20.0,
        requested_seeds_per_group=5,
        random_base_seed=RANDOM_BASE_SEED,
        random_seed_indexes=RANDOM_SEED_INDEXES,
        random_pool_sizes=RANDOM_POOL_SIZES,
        windows=windows,
    )


def load_generalization_policy(
    path: str | Path | None = None,
) -> GeneralizationPolicy:
    """Load the one reviewed policy, rejecting edited and locally resealed thresholds."""
    source = GENERALIZATION_POLICY_PATH if path is None else Path(path)
    payload = _read_json(source, label="generalization policy")
    if (
        set(payload)
        != {
            "schema_version",
            "policy_id",
            "baseline_sha256",
            "baseline_non_regression",
            "relative_per_cell",
            "intrinsic",
            "random_tails",
            "scenario_contract",
            "canonical_sha256",
        }
        or payload.get("schema_version") != 2
        or payload.get("policy_id") != "ai-era-generalization-policy-v2"
    ):
        raise ValueError("generalization policy schema is malformed")
    seal = _require_exact_seal(
        payload,
        label="generalization policy",
        required=REQUIRED_GENERALIZATION_POLICY_SHA256,
    )
    if payload["baseline_sha256"] != REQUIRED_GENERALIZATION_BASELINE_SHA256:
        raise ValueError("generalization policy baseline differs from the reviewed reference")
    sections = (
        payload["baseline_non_regression"],
        payload["relative_per_cell"],
        payload["intrinsic"],
        payload["random_tails"],
        payload["scenario_contract"],
    )
    if not all(isinstance(item, Mapping) for item in sections):
        raise ValueError("generalization policy sections are malformed")
    baseline_non_regression, relative, intrinsic, tails, contract = (
        cast(Mapping[str, Any], item) for item in sections
    )
    _validate_baseline_policy(baseline_non_regression)
    _validate_policy_thresholds(
        relative=relative,
        intrinsic=intrinsic,
        tails=tails,
    )
    windows = _validated_policy_windows(contract)
    return _loaded_generalization_policy(
        seal=seal,
        baseline_sha256=payload["baseline_sha256"],
        windows=windows,
    )


load_baseline_cells = _load_baseline_cells
