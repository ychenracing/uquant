"""Compile-anchored champion baseline and frozen AI-era gate policy."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from .generalization_contract import (
    RANDOM_BASE_SEED,
    RANDOM_POOL_SIZES,
    RANDOM_SEED_INDEXES,
    official_windows,
)
from .universe import (
    REQUIRED_FROZEN_CHAMPION_SHA256,
    load_ai_universe,
    load_phase1_frozen_champion,
)

_ROOT = Path(__file__).resolve().parents[2]
GENERALIZATION_BASELINE_PATH: Final = _ROOT / "benchmarks" / "ai_era_generalization_baseline.json"
GENERALIZATION_POLICY_PATH: Final = _ROOT / "benchmarks" / "ai_era_generalization_policy.json"
CHAMPION_MATRIX_PATH: Final = _ROOT / "artifacts" / "phase2" / "champion-generalization-matrix.json"

REQUIRED_GENERALIZATION_BASELINE_SHA256: Final = (
    "8603c4572fbf15a3de4f89737ab078d7e61d76f9e197f210a24704b8a4aabd79"
)
REQUIRED_GENERALIZATION_POLICY_SHA256: Final = (
    "5f7df0aab80d86af973731eac7899dbce9e71b5d3b6166fe064b9e291300a086"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_METRIC_FIELDS = {
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "gross_turnover",
    "annual_turnover",
    "top1_concentration",
    "top3_concentration",
    "pnl_hhi",
}
_BASELINE_CELL_FIELDS = {
    "window",
    "scenario",
    "family",
    "status",
    "economic",
    "pool_size",
    "seed_index",
    "derived_seed",
    "evidence_sha256",
    "contract_sha256",
    "metrics",
    "replay_error",
}


@dataclass(frozen=True, slots=True)
class ReplayError:
    """Canonical engine exception evidence for one economic cell."""

    exception_type: str
    message: str


@dataclass(frozen=True, slots=True)
class BaselineCell:
    """One immutable champion cell retained for candidate comparison."""

    window: str
    scenario: str
    family: str
    status: str
    economic: bool
    pool_size: int | None
    seed_index: int | None
    derived_seed: int | None
    evidence_sha256: str
    contract_sha256: str
    metrics: Mapping[str, float | int] | None
    replay_error: ReplayError | None

    @property
    def identifier(self) -> str:
        return f"{self.window}/{self.scenario}"


@dataclass(frozen=True, slots=True)
class GeneralizationBaseline:
    """Reviewed champion evidence protected by an in-file and compiled seal."""

    sha256: str
    runner_head: str
    runner_source_sha256: str
    artifact_sha256: str
    artifact_size_bytes: int
    provenance: Mapping[str, Any]
    aggregates: Mapping[str, Any]
    cells: Mapping[str, BaselineCell]


@dataclass(frozen=True, slots=True)
class GeneralizationPolicy:
    """Literal immutable Phase 2 non-regression and intrinsic thresholds."""

    sha256: str
    baseline_sha256: str
    wealth_ratio_min: float
    drawdown_absolute_buffer: float
    orders_absolute_buffer: int
    orders_ratio_max: float
    turnover_ratio_max: float
    directional_final_wealth_strict_min: float
    directional_max_drawdown: float
    remove_one_final_wealth_min: float
    remove_one_max_drawdown: float
    positive_return_fraction_min: float
    p10_wealth_min: float
    p90_drawdown_max: float
    p90_orders_max: float
    requested_seeds_per_group: int
    random_base_seed: int
    random_seed_indexes: tuple[int, ...]
    random_pool_sizes: tuple[int, ...]
    windows: tuple[tuple[str, str, str], ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"generalization contract contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"generalization contract contains non-standard number: {value}")


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or not a regular file: {path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is missing or corrupt: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _hash_json(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("generalization contract is not finite canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return _hash_json({key: payload[key] for key in sorted(payload) if key != "canonical_sha256"})


def _require_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be SHA-256")
    return value


def _require_exact_seal(
    payload: Mapping[str, Any],
    *,
    label: str,
    required: str,
) -> str:
    seal = _require_sha256(payload.get("canonical_sha256"), label=f"{label} canonical seal")
    if seal != _canonical_sha256(payload) or seal != required:
        raise ValueError(f"{label} differs from the compiled reviewed contract")
    return seal


def _metric_payload(value: Any, *, identifier: str) -> Mapping[str, float | int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _METRIC_FIELDS:
        raise ValueError(f"generalization baseline metrics are malformed: {identifier}")
    normalized: dict[str, float | int] = {}
    for name, raw in value.items():
        if name == "account_orders":
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ValueError(f"generalization baseline orders are malformed: {identifier}")
            normalized[name] = raw
        else:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"generalization baseline metric is malformed: {identifier}/{name}")
            number = float(raw)
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"generalization baseline metric is invalid: {identifier}/{name}")
            normalized[name] = number
    return MappingProxyType(normalized)


def _replay_error(value: Any, *, identifier: str) -> ReplayError | None:
    if value is None:
        return None
    if (
        not isinstance(value, Mapping)
        or set(value) != {"exception_type", "message"}
        or not isinstance(value.get("exception_type"), str)
        or not value["exception_type"]
        or not isinstance(value.get("message"), str)
        or not value["message"]
        or " ".join(value["message"].split()) != value["message"]
    ):
        raise ValueError(f"generalization baseline replay error is malformed: {identifier}")
    return ReplayError(exception_type=value["exception_type"], message=value["message"])


def _derived_seed(size: int, seed_index: int) -> int:
    payload = f"{RANDOM_BASE_SEED}:{size}:{seed_index}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _load_baseline_cells(raw_cells: Any) -> Mapping[str, BaselineCell]:
    if not isinstance(raw_cells, list) or len(raw_cells) != 234:
        raise ValueError("generalization baseline must contain all 234 contract records")
    cells: dict[str, BaselineCell] = {}
    by_window: dict[str, list[BaselineCell]] = defaultdict(list)
    for raw in raw_cells:
        if not isinstance(raw, Mapping) or set(raw) != _BASELINE_CELL_FIELDS:
            raise ValueError("generalization baseline cell schema is malformed")
        window = raw["window"]
        scenario = raw["scenario"]
        family = raw["family"]
        status = raw["status"]
        economic = raw["economic"]
        if not all(isinstance(item, str) and item for item in (window, scenario, family, status)):
            raise ValueError("generalization baseline cell identity is malformed")
        if not isinstance(economic, bool) or status not in {"READY", "INSUFFICIENT_SAMPLE"}:
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
        cell = BaselineCell(
            window=window,
            scenario=scenario,
            family=family,
            status=status,
            economic=economic,
            pool_size=pool_size,
            seed_index=seed_index,
            derived_seed=derived_seed,
            evidence_sha256=_require_sha256(
                raw["evidence_sha256"], label=f"generalization baseline evidence {identifier}"
            ),
            contract_sha256=_require_sha256(
                raw["contract_sha256"], label=f"generalization baseline contract {identifier}"
            ),
            metrics=metrics,
            replay_error=replay_error,
        )
        cells[identifier] = cell
        by_window[window].append(cell)
    expected_windows = {window.name for window in official_windows()}
    if set(by_window) != expected_windows:
        raise ValueError("generalization baseline windows differ from the official contract")
    for name, window_cells in by_window.items():
        if len(window_cells) != 39 or sum(cell.economic for cell in window_cells) != 32:
            raise ValueError(f"generalization baseline coverage differs: {name}")
        random_pairs = {
            (cell.pool_size, cell.seed_index) for cell in window_cells if cell.family == "random"
        }
        if random_pairs != {
            (size, index) for size in RANDOM_POOL_SIZES for index in RANDOM_SEED_INDEXES
        }:
            raise ValueError(f"generalization baseline random matrix differs: {name}")
    return MappingProxyType(cells)


def load_generalization_baseline(
    path: str | Path | None = None,
    *,
    artifact_path: str | Path | None = None,
) -> GeneralizationBaseline:
    """Load the reviewed champion matrix summary and verify its bound raw artifact."""
    source = GENERALIZATION_BASELINE_PATH if path is None else Path(path)
    payload = _read_json(source, label="generalization baseline")
    if set(payload) != {
        "schema_version",
        "baseline_id",
        "champion",
        "matrix_runner",
        "aggregates",
        "cells",
        "canonical_sha256",
    } or payload.get("schema_version") != 1 or payload.get("baseline_id") != (
        "ai-era-generalization-champion-v1"
    ):
        raise ValueError("generalization baseline schema is malformed")
    seal = _require_exact_seal(
        payload,
        label="generalization baseline",
        required=REQUIRED_GENERALIZATION_BASELINE_SHA256,
    )
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
    if not isinstance(runner.get("head"), str) or not _COMMIT.fullmatch(runner["head"]):
        raise ValueError("generalization baseline runner HEAD is malformed")
    runner_source = _require_sha256(
        runner.get("source_sha256"), label="generalization baseline runner source"
    )
    artifact_sha256 = _require_sha256(
        runner.get("artifact_sha256"), label="generalization baseline artifact"
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
    cells = _load_baseline_cells(payload["cells"])
    artifact_source = CHAMPION_MATRIX_PATH if artifact_path is None else Path(artifact_path)
    if artifact_source.is_symlink() or not artifact_source.is_file():
        raise ValueError(f"generalization champion artifact is missing: {artifact_source}")
    artifact_bytes = artifact_source.read_bytes()
    if len(artifact_bytes) != artifact_size or hashlib.sha256(artifact_bytes).hexdigest() != artifact_sha256:
        raise ValueError("generalization champion artifact differs from the reviewed baseline")
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
        provenance=MappingProxyType(provenance),
        aggregates=MappingProxyType(dict(payload["aggregates"])),
        cells=cells,
    )


def load_generalization_policy(path: str | Path | None = None) -> GeneralizationPolicy:
    """Load the one reviewed policy, rejecting edited and locally resealed thresholds."""
    source = GENERALIZATION_POLICY_PATH if path is None else Path(path)
    payload = _read_json(source, label="generalization policy")
    if set(payload) != {
        "schema_version",
        "policy_id",
        "baseline_sha256",
        "relative_per_cell",
        "intrinsic",
        "random_tails",
        "scenario_contract",
        "canonical_sha256",
    } or payload.get("schema_version") != 1 or payload.get("policy_id") != (
        "ai-era-generalization-policy-v1"
    ):
        raise ValueError("generalization policy schema is malformed")
    seal = _require_exact_seal(
        payload,
        label="generalization policy",
        required=REQUIRED_GENERALIZATION_POLICY_SHA256,
    )
    if payload["baseline_sha256"] != REQUIRED_GENERALIZATION_BASELINE_SHA256:
        raise ValueError("generalization policy baseline differs from the reviewed reference")
    relative = payload["relative_per_cell"]
    intrinsic = payload["intrinsic"]
    tails = payload["random_tails"]
    contract = payload["scenario_contract"]
    if not all(isinstance(item, Mapping) for item in (relative, intrinsic, tails, contract)):
        raise ValueError("generalization policy sections are malformed")
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
        "replay_errors_excluded_from_quantiles_and_force_failure": True,
    }
    if dict(relative) != expected_relative or dict(intrinsic) != expected_intrinsic:
        raise ValueError("generalization policy thresholds differ from the reviewed contract")
    if dict(tails) != expected_tails:
        raise ValueError("generalization random-tail policy differs from the reviewed contract")
    windows = tuple((window.name, window.start, window.end) for window in official_windows())
    expected_contract = {
        "windows": [
            {"name": name, "start": start, "end": end} for name, start, end in windows
        ],
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
    for name in ("window_fingerprint", "scenario_fingerprint", "evidence_fingerprint"):
        _require_sha256(contract.get(name), label=f"generalization policy {name}")
    if dict(contract) != expected_contract:
        raise ValueError("generalization scenario policy differs from the reviewed contract")
    return GeneralizationPolicy(
        sha256=seal,
        baseline_sha256=payload["baseline_sha256"],
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


def evaluate_cell_non_regression(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    policy: GeneralizationPolicy,
) -> tuple[str, ...]:
    """Apply the frozen relative per-cell wealth, risk, order, and turnover gates."""
    failures: list[str] = []
    candidate_wealth = float(candidate["final_wealth"])
    reference_wealth = float(reference["final_wealth"])
    wealth_limit = reference_wealth * policy.wealth_ratio_min
    if candidate_wealth < wealth_limit:
        failures.append(
            f"final_wealth {candidate_wealth} is below 95% reference {wealth_limit:g}"
        )
    candidate_drawdown = float(candidate["max_drawdown"])
    drawdown_limit = float(reference["max_drawdown"]) + policy.drawdown_absolute_buffer
    if candidate_drawdown > drawdown_limit:
        failures.append(
            f"max_drawdown {candidate_drawdown} exceeds reference-plus-buffer {drawdown_limit:g}"
        )
    candidate_orders = int(candidate["account_orders"])
    reference_orders = int(reference["account_orders"])
    order_limit = max(
        reference_orders + policy.orders_absolute_buffer,
        math.ceil(reference_orders * policy.orders_ratio_max),
    )
    if candidate_orders > order_limit:
        failures.append(
            f"account_orders {candidate_orders} exceeds reference activity limit {order_limit}"
        )
    for name in ("gross_turnover", "annual_turnover"):
        candidate_turnover = float(candidate[name])
        reference_turnover = float(reference[name])
        if reference_turnover == 0.0:
            if candidate_turnover != 0.0:
                failures.append(
                    f"{name} {candidate_turnover} must remain zero because reference is zero"
                )
        else:
            turnover_limit = reference_turnover * policy.turnover_ratio_max
            if candidate_turnover > turnover_limit:
                failures.append(
                    f"{name} {candidate_turnover} exceeds 110% reference {turnover_limit:g}"
                )
    return tuple(failures)


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("generalization tail quantile requires valid economic cells")
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _candidate_contract_sha256(cell: Mapping[str, Any]) -> str:
    return _hash_json(
        {
            key: value
            for key, value in cell.items()
            if key not in {"raw", "metrics", "replay_error"}
        }
    )


def evaluate_generalization_policy_artifact(
    artifact: Mapping[str, Any],
    *,
    baseline: GeneralizationBaseline,
    policy: GeneralizationPolicy,
    require_exact_equality: bool = False,
) -> dict[str, Any]:
    """Recompute frozen relative, intrinsic, and random-tail results from raw cells."""
    if policy.baseline_sha256 != baseline.sha256:
        raise ValueError("generalization policy and baseline identities differ")
    raw_cells = artifact.get("cells")
    provenance = artifact.get("provenance")
    if not isinstance(raw_cells, list) or not isinstance(provenance, Mapping):
        raise ValueError("generalization candidate artifact is malformed")
    failures: list[str] = []
    provenance_fields = (
        "effective_config_sha256",
        "data",
        "runtime",
        "universe_sha256",
        "industry_sha256",
        "window_fingerprint",
        "scenario_fingerprint",
        "evidence_fingerprint",
        "lookback_sessions",
    )
    failures.extend(
        f"candidate provenance differs from champion inputs: {name}"
        for name in provenance_fields
        if provenance.get(name) != baseline.provenance.get(name)
    )
    observed: dict[str, Mapping[str, Any]] = {}
    for raw in raw_cells:
        if not isinstance(raw, Mapping):
            failures.append("candidate cell is malformed")
            continue
        window = raw.get("window")
        scenario = raw.get("scenario")
        if not isinstance(window, str) or not isinstance(scenario, str):
            failures.append("candidate cell identity is malformed")
            continue
        identifier = f"{window}/{scenario}"
        if identifier in observed:
            failures.append(f"candidate contains duplicate cell: {identifier}")
        observed[identifier] = raw
    missing = sorted(set(baseline.cells) - set(observed))
    unexpected = sorted(set(observed) - set(baseline.cells))
    if missing:
        failures.append(f"candidate missing baseline cells: {missing}")
    if unexpected:
        failures.append(f"candidate has unexpected cells: {unexpected}")

    exact_equality_passed = True
    economic_valid = 0
    replay_errors = 0
    intrinsic_results: list[dict[str, Any]] = []
    random_groups: dict[tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]] = (
        defaultdict(list)
    )
    for identifier in sorted(set(baseline.cells) & set(observed)):
        reference = baseline.cells[identifier]
        candidate = observed[identifier]
        if _candidate_contract_sha256(candidate) != reference.contract_sha256:
            failures.append(f"candidate cell contract differs from baseline: {identifier}")
            continue
        metrics = candidate.get("metrics")
        error_raw = candidate.get("replay_error")
        error = _replay_error(error_raw, identifier=identifier)
        if not reference.economic:
            if metrics is not None or error is not None or candidate.get("raw") is not None:
                failures.append(f"candidate insufficient sample has economic evidence: {identifier}")
            continue
        if error is not None:
            replay_errors += 1
            failures.append(
                f"cell replay failed: {identifier}: {error.exception_type}: {error.message}"
            )
            if metrics is not None or candidate.get("raw") is not None:
                failures.append(f"candidate replay error contains fabricated metrics: {identifier}")
            if reference.replay_error != error:
                exact_equality_passed = False
                if require_exact_equality:
                    failures.append(f"exact equality differs: {identifier}: replay error")
            if reference.metrics is not None:
                failures.append(f"candidate lacks finite metrics required by reference: {identifier}")
        else:
            candidate_metrics = _metric_payload(metrics, identifier=identifier)
            if candidate_metrics is None or candidate.get("raw") is None:
                failures.append(f"candidate economic metrics are missing: {identifier}")
                continue
            economic_valid += 1
            if reference.metrics is not None:
                failures.extend(
                    f"cell non-regression failed: {identifier}: {reason}"
                    for reason in evaluate_cell_non_regression(
                        candidate_metrics, reference.metrics, policy=policy
                    )
                )
                if dict(candidate_metrics) != dict(reference.metrics):
                    exact_equality_passed = False
                    if require_exact_equality:
                        failures.append(f"exact equality differs: {identifier}: metrics")
            else:
                exact_equality_passed = False
                if require_exact_equality:
                    failures.append(f"exact equality differs: {identifier}: replay recovered")
            wealth = float(candidate_metrics["final_wealth"])
            drawdown = float(candidate_metrics["max_drawdown"])
            intrinsic_reasons: list[str] = []
            if reference.family in {"remove_all_core", "tradable_no_optical"}:
                if wealth <= policy.directional_final_wealth_strict_min:
                    intrinsic_reasons.append(
                        f"final_wealth {wealth:g} must be greater than 1.0"
                    )
                if drawdown > policy.directional_max_drawdown:
                    intrinsic_reasons.append(f"max_drawdown {drawdown:g} exceeds 0.3")
            elif reference.family == "remove_one":
                if wealth < policy.remove_one_final_wealth_min:
                    intrinsic_reasons.append(f"final_wealth {wealth:g} is below 0.8")
                if drawdown > policy.remove_one_max_drawdown:
                    intrinsic_reasons.append(f"max_drawdown {drawdown:g} exceeds 0.3")
            if reference.family in {"remove_all_core", "tradable_no_optical", "remove_one"}:
                intrinsic_results.append(
                    {
                        "identifier": identifier,
                        "family": reference.family,
                        "final_wealth": wealth,
                        "max_drawdown": drawdown,
                        "passed": not intrinsic_reasons,
                        "failures": intrinsic_reasons,
                    }
                )
                failures.extend(
                    f"intrinsic directional failed: {identifier}: {reason}"
                    for reason in intrinsic_reasons
                )
        if reference.family == "random" and reference.pool_size is not None:
            random_groups[(reference.window, reference.pool_size)].append(
                (identifier, metrics if isinstance(metrics, Mapping) else None, error is not None)
            )

    tail_results: list[dict[str, Any]] = []
    for (window, pool_size), group in sorted(random_groups.items()):
        valid = [metrics for _, metrics, has_error in group if metrics is not None and not has_error]
        error_count = sum(has_error for _, _, has_error in group)
        requested = policy.requested_seeds_per_group
        if len(group) != requested:
            failures.append(
                f"random tail coverage failed: {window}/size-{pool_size}: "
                f"requested {requested}, observed {len(group)}"
            )
        wealth_values = [float(item["final_wealth"]) for item in valid]
        drawdown_values = [float(item["max_drawdown"]) for item in valid]
        order_values = [float(item["account_orders"]) for item in valid]
        positive_fraction = sum(value > 1.0 for value in wealth_values) / requested
        p10_wealth = _quantile(wealth_values, 0.10) if wealth_values else None
        p90_drawdown = _quantile(drawdown_values, 0.90) if drawdown_values else None
        p90_orders = _quantile(order_values, 0.90) if order_values else None
        reasons: list[str] = []
        if error_count:
            reasons.append(f"{error_count} replay error cells")
        if positive_fraction < policy.positive_return_fraction_min:
            reasons.append(f"positive-return fraction {positive_fraction:g} is below 0.6")
        if p10_wealth is None or p10_wealth < policy.p10_wealth_min:
            reasons.append(f"p10 wealth {p10_wealth} is below 0.8")
        if p90_drawdown is None or p90_drawdown > policy.p90_drawdown_max:
            reasons.append(f"p90 drawdown {p90_drawdown} exceeds 0.3")
        if p90_orders is None or p90_orders > policy.p90_orders_max:
            reasons.append(f"p90 orders {p90_orders} exceeds 20")
        tail_results.append(
            {
                "window": window,
                "pool_size": pool_size,
                "requested_cells": requested,
                "valid_cells": len(valid),
                "replay_error_cells": error_count,
                "positive_return_fraction": positive_fraction,
                "p10_wealth": p10_wealth,
                "p90_drawdown": p90_drawdown,
                "p90_orders": p90_orders,
                "passed": not reasons,
                "failures": reasons,
            }
        )
        failures.extend(
            f"random tail failed: {window}/size-{pool_size}: {reason}" for reason in reasons
        )

    expected_economic = sum(cell.economic for cell in baseline.cells.values())
    if economic_valid + replay_errors != expected_economic:
        failures.append(
            "candidate economic coverage is incomplete: "
            f"expected {expected_economic}, valid {economic_valid}, errors {replay_errors}"
        )
    return {
        "passed": not failures,
        "exact_equality_required": require_exact_equality,
        "exact_equality_passed": exact_equality_passed,
        "economic_cells_expected": expected_economic,
        "economic_cells_valid": economic_valid,
        "replay_error_cells": replay_errors,
        "intrinsic_results": intrinsic_results,
        "random_tail_results": tail_results,
        "failures": failures,
    }
