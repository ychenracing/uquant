"""Compile-anchored champion baseline and frozen AI-era gate policy."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Set
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Never

from ..generalization import symbol_pnl_concentration
from ..generalization_contract import (
    RANDOM_BASE_SEED,
)

_ROOT = Path(__file__).resolve().parents[3]
GENERALIZATION_BASELINE_PATH: Final = _ROOT / "benchmarks" / "ai_era_generalization_baseline.json"
GENERALIZATION_POLICY_PATH: Final = _ROOT / "benchmarks" / "ai_era_generalization_policy.json"
CHAMPION_MATRIX_PATH: Final = _ROOT / "artifacts" / "phase2" / "champion-generalization-matrix.json"

REQUIRED_GENERALIZATION_BASELINE_SHA256: Final = (
    "8603c4572fbf15a3de4f89737ab078d7e61d76f9e197f210a24704b8a4aabd79"
)
REQUIRED_GENERALIZATION_POLICY_SHA256: Final = (
    "46cf95d26d04186824f181266da68e5a2d98814b65371c0b358c7cacfa8ef8fc"
)
_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256: Final = (
    "f43e1efe07b3f18c7931bc27a527886f1da5a8bc95026b02ab0a0116bec94545"
)
_DEPRECATED_V1_ATTRIBUTION_TOKEN: Final = MappingProxyType(
    {
        "status": "DEPRECATED_NON_CAUSAL_V1_ATTRIBUTION",
        "frozen_collection_sha256": _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256,
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")


class ImmutableGeneralizationDefinition(dict[str, str]):
    """JSON-compatible immutable policy definition."""

    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("policy definitions are immutable")

    def __setitem__(self, key: str, value: str, /) -> None:
        del key, value
        self._reject_mutation()

    def __delitem__(self, key: str, /) -> None:
        del key
        self._reject_mutation()

    def clear(self) -> None:
        self._reject_mutation()

    def pop(self, key: str, default: object = None, /) -> Never:
        del key, default
        self._reject_mutation()

    def popitem(self) -> Never:
        self._reject_mutation()

    def setdefault(self, key: str, default: str | None = None, /) -> Never:
        del key, default
        self._reject_mutation()

    def update(self, other: object = (), /, **kwargs: str) -> None:
        del other, kwargs
        self._reject_mutation()

    def __ior__(self, value: object, /) -> Never:
        del value
        self._reject_mutation()

    def __or__(self, value: object, /) -> Any:
        if not isinstance(value, dict):
            return NotImplemented
        result = dict(self)
        result.update(value)
        return result

    def __deepcopy__(self, memo: dict[int, object]) -> dict[str, str]:
        del memo
        return dict(self)


_ARTIFACT_FIELDS_V1 = frozenset(
    {
        "schema_version",
        "gate",
        "passed",
        "failures",
        "provenance",
        "concentration_definition",
        "aggregates",
        "cells",
    }
)
_ARTIFACT_FIELDS_V2 = frozenset({*_ARTIFACT_FIELDS_V1, "attribution_definition"})
_PROVENANCE_FIELDS = frozenset(
    {
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
)
_DATA_FIELDS = frozenset({"snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"})
_RUNTIME_FIELDS = frozenset(
    {
        "python_full_version",
        "numpy_version",
        "pandas_version",
        "uv_version",
        "uv_lock_sha256",
    }
)
_CELL_FIELDS_V1 = frozenset(
    {
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
)
_CELL_FIELDS_V2 = frozenset(
    {
        *_CELL_FIELDS_V1,
        "attribution_status",
        "attribution",
        "concentration",
    }
)
_ATTRIBUTION_DEFINITION = ImmutableGeneralizationDefinition(
    {
        "schema": "uquant.economic-attribution.v1",
        "interval": "cell start/end inclusive; no pre-window warmup or post-end data",
        "accounting_identity": "realized_pnl + open_pnl = final_equity - initial_cash",
        "lot_identity": "originating BUY event plus per-SELL sold_tranches",
        "concentration": "positive, signed-net, and absolute PnL denominators",
        "diagnostics": "cash drag and paired risk avoidance are not accounting PnL",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "as_of",
        "scores",
        "eligible_symbols",
        "ineligible_symbols",
        "lookback_sessions",
        "sha256",
    }
)
_METRIC_FIELDS = frozenset(
    {
        "final_wealth",
        "max_drawdown",
        "account_orders",
        "gross_turnover",
        "annual_turnover",
        "top1_concentration",
        "top3_concentration",
        "pnl_hhi",
    }
)
_BASELINE_CELL_FIELDS = frozenset(
    {
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
)
_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS = frozenset(
    {
        "event_id",
        "origin_subsystem",
        "mechanism",
        "origin_lifecycle",
        "replaces_symbol",
        "industry_at_entry",
        "industry_manifest_sha256",
    }
)


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
    """Literal immutable generalization non-regression and intrinsic thresholds."""

    schema_version: int
    policy_id: str
    sha256: str
    baseline_sha256: str
    champion_equality_passes: bool
    baseline_grandfathering: bool
    empty_support_requires_literal_policy: bool
    identical_baseline_replay_error_passes: bool
    recovered_replay_envelope: bool
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


def _reject_duplicate_policy_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"generalization contract contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_policy_constant(value: str) -> None:
    raise ValueError(f"generalization contract contains non-standard number: {value}")


def _read_policy_json(path: Path, *, label: str) -> dict[str, Any]:
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


def _hash_policy_json(value: Any) -> str:
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
    expected_fields: Set[str],
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


def _provenance_hash_failures(value: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
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
    return failures


def _data_provenance_failures(data: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(data, Mapping):
        return failures
    if not isinstance(data.get("snapshot_id"), str) or not data["snapshot_id"]:
        failures.append("candidate provenance data snapshot_id is malformed")
    files_verified = data.get("files_verified")
    if isinstance(files_verified, bool) or not isinstance(files_verified, int) or files_verified < 1:
        failures.append("candidate provenance data files_verified is malformed")
    for name in ("manifest_sha256", "checksums_sha256"):
        digest = data.get(name)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            failures.append(f"candidate provenance data {name} is malformed")
    return failures


def _runtime_provenance_failures(runtime: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(runtime, Mapping):
        return failures
    for name in _RUNTIME_FIELDS - {"uv_lock_sha256"}:
        version = runtime.get(name)
        if not isinstance(version, str) or not version:
            failures.append(f"candidate provenance runtime {name} is malformed")
    lock_digest = runtime.get("uv_lock_sha256")
    if not isinstance(lock_digest, str) or not _SHA256.fullmatch(lock_digest):
        failures.append("candidate provenance runtime uv_lock_sha256 is malformed")
    return failures


def _provenance_schema_failures(value: Any) -> tuple[str, ...]:
    failures = list(_schema_failures(value, _PROVENANCE_FIELDS, label="candidate provenance"))
    if not isinstance(value, Mapping):
        return tuple(failures)
    data = value.get("data")
    runtime = value.get("runtime")
    failures.extend(_schema_failures(data, _DATA_FIELDS, label="candidate provenance data"))
    failures.extend(_schema_failures(runtime, _RUNTIME_FIELDS, label="candidate provenance runtime"))
    head = value.get("head")
    if not isinstance(head, str) or not _COMMIT.fullmatch(head):
        failures.append("candidate provenance HEAD is malformed")
    failures.extend(_provenance_hash_failures(value))
    lookback = value.get("lookback_sessions")
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
        failures.append("candidate provenance lookback_sessions is malformed")
    failures.extend(_data_provenance_failures(data))
    failures.extend(_runtime_provenance_failures(runtime))
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


def _generalization_policy_sha256(payload: Mapping[str, Any]) -> str:
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


def _policy_derived_seed(size: int, seed_index: int) -> int:
    payload = f"{RANDOM_BASE_SEED}:{size}:{seed_index}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


_canonical_sha256 = _generalization_policy_sha256
_derived_seed = _policy_derived_seed
_hash_json = _hash_policy_json
_read_json = _read_policy_json
_reject_duplicate_keys = _reject_duplicate_policy_keys
_reject_nonstandard_constant = _reject_nonstandard_policy_constant

# Stable package-owner surface consumed by the ordered evaluation stages.  The
# aliases retain the exact immutable function and value identities while making
# the ownership edge explicit instead of importing another module's privates.
ARTIFACT_FIELDS_V1 = _ARTIFACT_FIELDS_V1
ARTIFACT_FIELDS_V2 = _ARTIFACT_FIELDS_V2
ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS = _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS
ATTRIBUTION_DEFINITION = _ATTRIBUTION_DEFINITION
BASELINE_CELL_FIELDS = _BASELINE_CELL_FIELDS
CELL_FIELDS_V1 = _CELL_FIELDS_V1
CELL_FIELDS_V2 = _CELL_FIELDS_V2
COMMIT_PATTERN = _COMMIT
DATA_FIELDS = _DATA_FIELDS
DEPRECATED_V1_ATTRIBUTION_TOKEN = _DEPRECATED_V1_ATTRIBUTION_TOKEN
EVIDENCE_FIELDS = _EVIDENCE_FIELDS
METRIC_FIELDS = _METRIC_FIELDS
PROVENANCE_FIELDS = _PROVENANCE_FIELDS
REPOSITORY_ROOT = _ROOT
ROOT = _ROOT
REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256 = _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256
RUNTIME_FIELDS = _RUNTIME_FIELDS
SHA256_PATTERN = _SHA256
artifact_equality_sha256 = _artifact_equality_sha256
canonical_sha256 = _canonical_sha256
derived_seed = _derived_seed
hash_json = _hash_json
metric_payload = _metric_payload
metrics_reconciled_from_raw = _metrics_reconciled_from_raw
provenance_schema_failures = _provenance_schema_failures
read_json = _read_json
reject_duplicate_keys = _reject_duplicate_keys
reject_nonstandard_constant = _reject_nonstandard_constant
replay_error = _replay_error
require_exact_seal = _require_exact_seal
require_sha256 = _require_sha256
schema_failures = _schema_failures
