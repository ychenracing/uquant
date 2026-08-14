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

from ..attribution import validate_attribution_against_engine_result
from ..config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from ..engine import code_fingerprint
from .control_plane import validate_engine_control_plane
from .generalization import symbol_pnl_concentration
from .generalization_contract import (
    RANDOM_BASE_SEED,
    RANDOM_POOL_SIZES,
    RANDOM_SEED_INDEXES,
    official_windows,
)
from .generalization_matrix import _head_and_source
from .replay_evidence import VerifiedMarketData
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
_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256: Final = (
    "f43e1efe07b3f18c7931bc27a527886f1da5a8bc95026b02ab0a0116bec94545"
)
_DEPRECATED_V1_ATTRIBUTION_TOKEN: Final = {
    "status": "DEPRECATED_NON_CAUSAL_V1_ATTRIBUTION",
    "frozen_collection_sha256": _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256,
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_ARTIFACT_FIELDS_V1 = {
    "schema_version",
    "gate",
    "passed",
    "failures",
    "provenance",
    "concentration_definition",
    "aggregates",
    "cells",
}
_ARTIFACT_FIELDS_V2 = {*_ARTIFACT_FIELDS_V1, "attribution_definition"}
_PROVENANCE_FIELDS = {
    "head",
    "source_sha256",
    "effective_config_sha256",
    "data",
    "runtime",
    "universe_sha256",
    "industry_sha256",
    "window_fingerprint",
    "scenario_fingerprint",
    "evidence_fingerprint",
    "lookback_sessions",
}
_DATA_FIELDS = {"snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"}
_RUNTIME_FIELDS = {
    "python_full_version",
    "numpy_version",
    "pandas_version",
    "uv_version",
    "uv_lock_sha256",
}
_CELL_FIELDS_V1 = {
    "window",
    "start",
    "end",
    "scenario",
    "family",
    "status",
    "economic",
    "symbols",
    "reference_symbols",
    "removed_symbols",
    "industry",
    "pool_size",
    "seed_index",
    "derived_seed",
    "evidence",
    "raw",
    "metrics",
    "replay_error",
}
_CELL_FIELDS_V2 = {
    *_CELL_FIELDS_V1,
    "attribution_status",
    "attribution",
    "concentration",
}
_ATTRIBUTION_DEFINITION = {
    "schema": "uquant.economic-attribution.v1",
    "interval": "cell start/end inclusive; no pre-window warmup or post-end data",
    "accounting_identity": "realized_pnl + open_pnl = final_equity - initial_cash",
    "lot_identity": "originating BUY event plus per-SELL sold_tranches",
    "concentration": "positive, signed-net, and absolute PnL denominators",
    "diagnostics": "cash drag and paired risk avoidance are not accounting PnL",
}
_EVIDENCE_FIELDS = {
    "as_of",
    "scores",
    "eligible_symbols",
    "ineligible_symbols",
    "lookback_sessions",
    "sha256",
}
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
_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS = {
    "event_id",
    "origin_subsystem",
    "mechanism",
    "origin_lifecycle",
    "replaces_symbol",
    "industry_at_entry",
    "industry_manifest_sha256",
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
    artifact_equality_sha256: str
    attribution_neutral_equality_sha256: str
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


def _artifact_equality_sha256(artifact: Mapping[str, Any]) -> str:
    """Hash exact artifact evidence while allowing candidate runner identity to differ."""
    provenance = artifact.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("generalization artifact provenance is malformed")
    normalized = dict(artifact)
    normalized["provenance"] = {
        key: value for key, value in provenance.items() if key not in {"head", "source_sha256"}
    }
    return _hash_json(normalized)


def _schema_failures(
    value: Any,
    expected_fields: set[str],
    *,
    label: str,
) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return (f"{label} is malformed",)
    missing = sorted(expected_fields - set(value))
    unexpected = sorted(set(value) - expected_fields)
    failures: list[str] = []
    if missing:
        failures.append(f"{label} is missing fields: {missing}")
    if unexpected:
        failures.append(f"{label} has unexpected fields: {unexpected}")
    return tuple(failures)


def _provenance_schema_failures(value: Any) -> tuple[str, ...]:
    failures = list(_schema_failures(value, _PROVENANCE_FIELDS, label="candidate provenance"))
    if not isinstance(value, Mapping):
        return tuple(failures)
    failures.extend(
        _schema_failures(value.get("data"), _DATA_FIELDS, label="candidate provenance data")
    )
    failures.extend(
        _schema_failures(
            value.get("runtime"), _RUNTIME_FIELDS, label="candidate provenance runtime"
        )
    )
    head = value.get("head")
    if not isinstance(head, str) or not _COMMIT.fullmatch(head):
        failures.append("candidate provenance HEAD is malformed")
    for name in (
        "source_sha256",
        "effective_config_sha256",
        "universe_sha256",
        "industry_sha256",
        "window_fingerprint",
        "scenario_fingerprint",
        "evidence_fingerprint",
    ):
        digest = value.get(name)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            failures.append(f"candidate provenance {name} is malformed")
    lookback = value.get("lookback_sessions")
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
        failures.append("candidate provenance lookback_sessions is malformed")
    data = value.get("data")
    if isinstance(data, Mapping):
        if not isinstance(data.get("snapshot_id"), str) or not data["snapshot_id"]:
            failures.append("candidate provenance data snapshot_id is malformed")
        files_verified = data.get("files_verified")
        if (
            isinstance(files_verified, bool)
            or not isinstance(files_verified, int)
            or files_verified < 1
        ):
            failures.append("candidate provenance data files_verified is malformed")
        for name in ("manifest_sha256", "checksums_sha256"):
            digest = data.get(name)
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                failures.append(f"candidate provenance data {name} is malformed")
    runtime = value.get("runtime")
    if isinstance(runtime, Mapping):
        for name in _RUNTIME_FIELDS - {"uv_lock_sha256"}:
            version = runtime.get(name)
            if not isinstance(version, str) or not version:
                failures.append(f"candidate provenance runtime {name} is malformed")
        lock_digest = runtime.get("uv_lock_sha256")
        if not isinstance(lock_digest, str) or not _SHA256.fullmatch(lock_digest):
            failures.append("candidate provenance runtime uv_lock_sha256 is malformed")
    return tuple(failures)


def _metrics_reconciled_from_raw(
    raw: Mapping[str, Any],
    *,
    identifier: str,
) -> Mapping[str, float | int]:
    pnl = raw.get("symbol_pnl")
    if not isinstance(pnl, Mapping):
        raise ValueError(f"candidate raw symbol PnL is malformed: {identifier}")
    normalized_pnl: dict[str, float] = {}
    for symbol, value in pnl.items():
        if (
            not isinstance(symbol, str)
            or not symbol
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"candidate raw symbol PnL is malformed: {identifier}")
        normalized_pnl[symbol] = float(value)
    reconciled = _metric_payload(
        {
            "final_wealth": raw.get("final_wealth"),
            "max_drawdown": raw.get("max_drawdown"),
            "account_orders": raw.get("account_orders"),
            "gross_turnover": raw.get("gross_turnover"),
            "annual_turnover": raw.get("annual_turnover"),
            **symbol_pnl_concentration(normalized_pnl),
        },
        identifier=identifier,
    )
    if reconciled is None:
        raise ValueError(f"candidate raw metrics are missing: {identifier}")
    return reconciled


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
    artifact_equality_sha256 = _artifact_equality_sha256(reviewed_artifact)
    attribution_neutral_equality_sha256 = _attribution_neutral_equality_sha256(
        {**reviewed_artifact, "attribution_definition": _ATTRIBUTION_DEFINITION}
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
        artifact_equality_sha256=artifact_equality_sha256,
        attribution_neutral_equality_sha256=attribution_neutral_equality_sha256,
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
            if key
            not in {
                "raw",
                "metrics",
                "replay_error",
                "attribution_status",
                "attribution",
                "concentration",
            }
        }
    )


def _project_raw_evidence_for_frozen_v1(
    raw: Mapping[str, Any],
    *,
    source_schema: int,
    frozen_v1_attribution_verified: bool = False,
) -> dict[str, Any]:
    """Apply the same closed raw-evidence migration used by exact equality."""

    try:
        projected = json.loads(
            json.dumps(raw, allow_nan=False, separators=(",", ":"), sort_keys=True)
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("generalization raw evidence is not finite canonical JSON") from exc
    if not isinstance(projected, dict) or source_schema not in {1, 2}:
        raise ValueError("generalization raw evidence schema is malformed")
    if source_schema == 1:
        if not frozen_v1_attribution_verified or not isinstance(
            projected.get("attribution"), dict
        ):
            raise ValueError(
                "deprecated v1 attribution lacks its compiled collection validation"
            )
    elif "attribution" in projected or "legacy_attribution" in projected:
        raise ValueError("candidate v2 raw evidence injects deprecated v1 attribution")
    projected["attribution"] = dict(_DEPRECATED_V1_ATTRIBUTION_TOKEN)
    legacy_decision_digests = projected.pop("legacy_decision_digests", None)
    if legacy_decision_digests is not None:
        projected["decision_digests"] = legacy_decision_digests
    projected.pop("decision_trace", None)
    projected.pop("daily_replay_evidence", None)
    account = projected.get("final_account")
    if not isinstance(account, dict):
        return projected
    # The evaluator verifies both values against the current compiled
    # schema/source before this cross-version projection.  Fixed tokens let
    # immutable v1 and current v2 bindings compare without pretending their
    # schema versions and source hashes are equal.
    account["schema_version"] = "VALIDATED_ACCOUNT_SCHEMA_BINDING"
    account["code_hash"] = "VALIDATED_PRODUCTION_SOURCE_BINDING"
    for collection_name in ("pending_orders", "order_ledger", "fills"):
        collection = account.get(collection_name)
        if not isinstance(collection, list):
            continue
        for record in collection:
            if not isinstance(record, dict):
                continue
            for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                record.pop(name, None)
            sold_tranches = record.get("sold_tranches")
            if isinstance(sold_tranches, list):
                for sold_lot in sold_tranches:
                    if not isinstance(sold_lot, dict):
                        continue
                    for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                        sold_lot.pop(name, None)
    positions = account.get("positions")
    if isinstance(positions, dict):
        for position in positions.values():
            if not isinstance(position, dict):
                continue
            tranches = position.get("tranches")
            if not isinstance(tranches, list):
                continue
            for tranche in tranches:
                if not isinstance(tranche, dict):
                    continue
                for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                    tranche.pop(name, None)
    return projected


def _v2_economic_projection(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Project validated v2 additions while retaining the frozen v1 control plane."""

    try:
        projected = json.loads(
            json.dumps(artifact, allow_nan=False, separators=(",", ":"), sort_keys=True)
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("generalization candidate is not finite canonical JSON") from exc
    if not isinstance(projected, dict):
        raise ValueError("generalization candidate artifact is malformed")
    source_schema = projected.get("schema_version")
    if source_schema not in {1, 2}:
        raise ValueError("generalization candidate schema version is malformed")
    projected["schema_version"] = 1
    projected.pop("attribution_definition", None)
    cells = projected.get("cells")
    if not isinstance(cells, list):
        raise ValueError("generalization candidate cell collection is malformed")
    frozen_v1_attribution: dict[str, Any] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("generalization candidate cell is malformed")
        raw = cell.get("raw")
        if isinstance(raw, dict) and source_schema == 1:
            identifier = f"{cell.get('window')}/{cell.get('scenario')}"
            legacy_attribution = raw.get("attribution")
            if not isinstance(legacy_attribution, dict) or identifier in frozen_v1_attribution:
                raise ValueError(
                    "deprecated v1 attribution payload collection is malformed"
                )
            frozen_v1_attribution[identifier] = legacy_attribution
    if source_schema == 1 and _hash_json(frozen_v1_attribution) != (
        _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256
    ):
        raise ValueError(
            "deprecated v1 attribution differs from the compiled frozen collection"
        )
    for cell in cells:
        cell.pop("attribution_status", None)
        cell.pop("attribution", None)
        cell.pop("concentration", None)
        raw = cell.get("raw")
        if isinstance(raw, dict):
            cell["raw"] = _project_raw_evidence_for_frozen_v1(
                raw,
                source_schema=source_schema,
                frozen_v1_attribution_verified=source_schema == 1,
            )
    return projected


def _attribution_neutral_equality_sha256(artifact: Mapping[str, Any]) -> str:
    projected = _v2_economic_projection(artifact)
    return _artifact_equality_sha256(projected)


def evaluate_generalization_policy_artifact(
    artifact: Mapping[str, Any],
    *,
    baseline: GeneralizationBaseline,
    policy: GeneralizationPolicy,
    require_exact_equality: bool = False,
    data_dir: str | Path | None = None,
    expected_config: SystemConfig | None = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Recompute frozen relative, intrinsic, and random-tail results from raw cells."""
    if policy.baseline_sha256 != baseline.sha256:
        raise ValueError("generalization policy and baseline identities differ")
    failures: list[str] = []
    equality_differences: list[str] = []
    schema_version = artifact.get("schema_version")
    if schema_version == 2 and data_dir is None:
        raise ValueError(
            "schema-v2 evaluation requires an explicit frozen data directory"
        )
    v2_projection_valid = schema_version == 2
    expected_artifact_fields = (
        _ARTIFACT_FIELDS_V2 if schema_version == 2 else _ARTIFACT_FIELDS_V1
    )
    expected_cell_fields = _CELL_FIELDS_V2 if schema_version == 2 else _CELL_FIELDS_V1
    artifact_schema_failures = _schema_failures(
        artifact, expected_artifact_fields, label="generalization candidate artifact"
    )
    failures.extend(artifact_schema_failures)
    equality_differences.extend(artifact_schema_failures)
    if artifact_schema_failures:
        v2_projection_valid = False
    if schema_version not in {1, 2}:
        failures.append("generalization candidate schema version is malformed")
        equality_differences.append("schema version")
    if schema_version == 2 and artifact.get("attribution_definition") != _ATTRIBUTION_DEFINITION:
        failures.append("generalization candidate attribution definition is malformed")
        equality_differences.append("attribution definition")
    if artifact.get("gate") != "ai-era-generalization":
        failures.append("generalization candidate gate identity is malformed")
        equality_differences.append("gate identity")
    if not isinstance(artifact.get("passed"), bool):
        failures.append("generalization candidate passed state is malformed")
        equality_differences.append("passed state")
    advertised_failures = artifact.get("failures")
    if not isinstance(advertised_failures, list) or any(
        not isinstance(item, str) for item in advertised_failures
    ):
        failures.append("generalization candidate failure state is malformed")
        equality_differences.append("failure state")
    if not isinstance(artifact.get("concentration_definition"), Mapping):
        failures.append("generalization candidate concentration definition is malformed")
        equality_differences.append("concentration definition")
    if not isinstance(artifact.get("aggregates"), Mapping):
        failures.append("generalization candidate aggregates are malformed")
        equality_differences.append("aggregate schema")
    raw_cells_value = artifact.get("cells")
    provenance_value = artifact.get("provenance")
    if not isinstance(raw_cells_value, list):
        failures.append("generalization candidate cell collection is malformed")
        equality_differences.append("cell collection is malformed")
        raw_cells: list[Any] = []
    else:
        raw_cells = raw_cells_value
    provenance_schema_failures = _provenance_schema_failures(provenance_value)
    failures.extend(provenance_schema_failures)
    equality_differences.extend(provenance_schema_failures)
    if provenance_schema_failures:
        v2_projection_valid = False
    if not isinstance(provenance_value, Mapping):
        provenance: Mapping[str, Any] = {}
    else:
        provenance = provenance_value
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
    provenance_mismatches = tuple(
        name
        for name in provenance_fields
        if provenance.get(name) != baseline.provenance.get(name)
    )
    failures.extend(
        f"candidate provenance differs from champion inputs: {name}"
        for name in provenance_mismatches
    )
    equality_differences.extend(f"provenance {name}" for name in provenance_mismatches)
    if provenance_mismatches:
        v2_projection_valid = False
    market: VerifiedMarketData | None = None
    trusted_config: SystemConfig | None = None
    if schema_version == 2:
        if not isinstance(expected_config, SystemConfig):
            raise ValueError("schema-v2 evaluation requires a trusted effective config")
        trusted_config = expected_config
        current_config_sha256 = config_fingerprint(expected_config)
        if provenance.get("effective_config_sha256") != current_config_sha256:
            message = "candidate effective config differs from compiled production config"
            failures.append(message)
            equality_differences.append(message)
            v2_projection_valid = False
        try:
            current_head, current_source = _head_and_source(_ROOT)
        except RuntimeError as exc:
            message = f"candidate source binding cannot be verified: {exc}"
            failures.append(message)
            equality_differences.append(message)
            v2_projection_valid = False
        else:
            if (
                provenance.get("head") != current_head
                or provenance.get("source_sha256") != current_source
            ):
                message = "candidate source binding differs from exact current HEAD"
                failures.append(message)
                equality_differences.append(message)
                v2_projection_valid = False
        if data_dir is None:
            message = "candidate v2 replay validation requires an explicit frozen data directory"
            failures.append(message)
            equality_differences.append(message)
            v2_projection_valid = False
        else:
            data_binding = provenance.get("data")
            if not isinstance(data_binding, Mapping):
                message = "candidate frozen data binding is malformed"
                failures.append(message)
                equality_differences.append(message)
                v2_projection_valid = False
            else:
                try:
                    market = VerifiedMarketData(
                        data_dir,
                        expected_manifest=data_binding,
                    )
                except (RuntimeError, ValueError) as exc:
                    message = f"candidate frozen data binding cannot be verified: {exc}"
                    failures.append(message)
                    equality_differences.append(message)
                    v2_projection_valid = False
    if artifact.get("aggregates") != baseline.aggregates:
        equality_differences.append("aggregate evidence")
    observed: dict[str, Mapping[str, Any]] = {}
    invalid_cells: set[str] = set()
    for raw in raw_cells:
        if not isinstance(raw, Mapping):
            failures.append("candidate cell is malformed")
            equality_differences.append("malformed cell record")
            continue
        window = raw.get("window")
        scenario = raw.get("scenario")
        if not isinstance(window, str) or not isinstance(scenario, str):
            failures.append("candidate cell identity is malformed")
            equality_differences.append("malformed cell identity")
            continue
        identifier = f"{window}/{scenario}"
        cell_schema_failures = _schema_failures(
            raw, expected_cell_fields, label=f"candidate cell {identifier}"
        )
        evidence_schema_failures = _schema_failures(
            raw.get("evidence"),
            _EVIDENCE_FIELDS,
            label=f"candidate cell evidence {identifier}",
        )
        if cell_schema_failures or evidence_schema_failures:
            failures.extend(cell_schema_failures)
            failures.extend(evidence_schema_failures)
            equality_differences.extend(cell_schema_failures)
            equality_differences.extend(evidence_schema_failures)
            invalid_cells.add(identifier)
            if schema_version == 2:
                v2_projection_valid = False
        if identifier in observed:
            failures.append(f"candidate contains duplicate cell: {identifier}")
            equality_differences.append(f"duplicate cell {identifier}")
        observed[identifier] = raw
    missing = sorted(set(baseline.cells) - set(observed))
    unexpected = sorted(set(observed) - set(baseline.cells))
    if missing:
        failures.append(f"candidate missing baseline cells: {missing}")
        equality_differences.extend(f"missing cell {identifier}" for identifier in missing)
    if unexpected:
        failures.append(f"candidate has unexpected cells: {unexpected}")
        equality_differences.extend(
            f"unexpected cell {identifier}" for identifier in unexpected
        )

    economic_valid = 0
    replay_errors = 0
    intrinsic_results: list[dict[str, Any]] = []
    random_groups: dict[tuple[str, int], list[tuple[str, Mapping[str, Any] | None, bool]]] = (
        defaultdict(list)
    )
    for identifier in sorted(set(baseline.cells) & set(observed)):
        reference = baseline.cells[identifier]
        candidate = observed[identifier]
        if identifier in invalid_cells:
            continue
        try:
            candidate_contract_sha256 = _candidate_contract_sha256(candidate)
        except ValueError as exc:
            failures.append(f"candidate cell contract is malformed: {identifier}: {exc}")
            equality_differences.append(f"malformed cell contract {identifier}")
            continue
        if candidate_contract_sha256 != reference.contract_sha256:
            failures.append(f"candidate cell contract differs from baseline: {identifier}")
            equality_differences.append(f"cell contract {identifier}")
            continue
        metrics = candidate.get("metrics")
        error_raw = candidate.get("replay_error")
        attribution_status = candidate.get("attribution_status")
        attribution = candidate.get("attribution")
        concentration = candidate.get("concentration")
        try:
            error = _replay_error(error_raw, identifier=identifier)
        except ValueError as exc:
            failures.append(f"candidate replay error is malformed: {identifier}: {exc}")
            equality_differences.append(f"malformed replay error {identifier}")
            continue
        if not reference.economic:
            if metrics is not None or error is not None or candidate.get("raw") is not None:
                failures.append(f"candidate insufficient sample has economic evidence: {identifier}")
                equality_differences.append(f"insufficient-sample evidence {identifier}")
            if schema_version == 2 and (
                attribution_status != "INSUFFICIENT_SAMPLE"
                or attribution is not None
                or concentration is not None
            ):
                failures.append(f"candidate insufficient sample attribution state differs: {identifier}")
                equality_differences.append(f"insufficient-sample attribution {identifier}")
            continue
        if error is not None:
            replay_errors += 1
            failures.append(
                f"cell replay failed: {identifier}: {error.exception_type}: {error.message}"
            )
            if metrics is not None or candidate.get("raw") is not None:
                failures.append(f"candidate replay error contains fabricated metrics: {identifier}")
                equality_differences.append(f"fabricated replay-error evidence {identifier}")
            if schema_version == 2 and (
                attribution_status != "ERROR"
                or attribution is not None
                or concentration is not None
            ):
                failures.append(f"candidate replay error attribution state differs: {identifier}")
                equality_differences.append(f"replay-error attribution {identifier}")
            if reference.replay_error != error:
                equality_differences.append(f"replay error {identifier}")
            if reference.metrics is not None:
                failures.append(f"candidate lacks finite metrics required by reference: {identifier}")
        else:
            try:
                candidate_metrics = _metric_payload(metrics, identifier=identifier)
            except ValueError as exc:
                failures.append(f"candidate economic metrics are malformed: {identifier}: {exc}")
                equality_differences.append(f"malformed metrics {identifier}")
                continue
            candidate_raw = candidate.get("raw")
            if candidate_metrics is None or not isinstance(candidate_raw, Mapping):
                failures.append(f"candidate economic metrics are missing: {identifier}")
                equality_differences.append(f"economic evidence {identifier}")
                continue
            if schema_version == 2:
                if attribution_status != "VALID" or not isinstance(attribution, Mapping):
                    failures.append(f"candidate economic attribution is missing: {identifier}")
                    equality_differences.append(f"economic attribution {identifier}")
                    v2_projection_valid = False
                    continue
                try:
                    start = str(candidate.get("start"))
                    end = str(candidate.get("end"))
                    trusted_sessions = (
                        None if market is None else market.sessions(start, end)
                    )
                    if market is not None:
                        if trusted_config is None:
                            raise ValueError(
                                "schema-v2 evaluation requires a trusted effective config"
                            )
                        validate_engine_control_plane(
                            candidate_raw,
                            economic_start=start,
                            economic_end=end,
                            expected_sessions=trusted_sessions or (),
                            expected_config=trusted_config,
                            expected_code_sha256=code_fingerprint(),
                            attribution=attribution,
                        )
                    canonical_attribution = validate_attribution_against_engine_result(
                        candidate_raw,
                        economic_start=start,
                        economic_end=end,
                        attribution=attribution,
                        trusted_sessions=trusted_sessions,
                        trusted_close=None if market is None else market.close,
                        require_daily_replay_evidence=True,
                    )
                except (TypeError, ValueError) as exc:
                    failures.append(
                        f"candidate economic attribution is malformed: {identifier}: {exc}"
                    )
                    equality_differences.append(f"malformed attribution {identifier}")
                    v2_projection_valid = False
                    continue
                if concentration != canonical_attribution["symbol_concentration"]:
                    failures.append(
                        f"candidate concentration differs from economic attribution: {identifier}"
                    )
                    equality_differences.append(f"detached concentration {identifier}")
                    v2_projection_valid = False
                    continue
            try:
                reconciled_metrics = _metrics_reconciled_from_raw(
                    candidate_raw, identifier=identifier
                )
            except ValueError as exc:
                failures.append(f"candidate raw economic evidence is malformed: {identifier}: {exc}")
                equality_differences.append(f"malformed raw evidence {identifier}")
                continue
            if dict(candidate_metrics) != dict(reconciled_metrics):
                failures.append(f"candidate metrics do not reconcile to raw evidence: {identifier}")
                equality_differences.append(f"raw evidence reconciliation {identifier}")
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
                    equality_differences.append(f"metrics {identifier}")
            else:
                equality_differences.append(f"replay recovered {identifier}")
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
        equality_differences.append("economic coverage")
    if schema_version == 2 and not v2_projection_valid:
        equality_differences.append("validated v2 control-plane evidence")
    else:
        try:
            artifact_equality_sha256 = (
                _attribution_neutral_equality_sha256(artifact)
                if schema_version == 2
                else _artifact_equality_sha256(artifact)
            )
        except ValueError as exc:
            failures.append(f"generalization candidate evidence is malformed: {exc}")
            equality_differences.append("malformed artifact evidence")
        else:
            expected_equality_sha256 = (
                baseline.attribution_neutral_equality_sha256
                if schema_version == 2
                else baseline.artifact_equality_sha256
            )
            if artifact_equality_sha256 != expected_equality_sha256:
                equality_differences.append("artifact evidence payload")
    exact_equality_passed = not equality_differences
    if require_exact_equality:
        failures.extend(
            f"exact equality differs: {reason}" for reason in equality_differences
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
