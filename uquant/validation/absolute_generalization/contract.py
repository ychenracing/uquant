"""Strict immutable Absolute Generalization Acceptance contract."""

from __future__ import annotations

import hashlib
import math
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, cast

from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.strict_json import (
    canonical_json_bytes,
    canonical_json_sha256,
    strict_json_loads,
)
from uquant.contracts.universe import default_ai_universe
from uquant.provenance.fingerprints import (
    git_source_surface_fingerprint,
    source_surface_fingerprint,
)
from uquant.validation.manifest import verify_data_manifest

ABSOLUTE_GENERALIZATION_CONTRACT_SHA256: Final = (
    "625a6142ef74c29adf59051af4c88e8b3faa02237d75c62d9d561ff0a07a76ad"
)

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONTRACT_PATH = _ROOT / "benchmarks/absolute_generalization_acceptance_contract.json"
_OWNERSHIP_CONTRACT_PATH = _ROOT / "benchmarks/strategic_ownership_acceptance_contract.json"
_BASELINE_COMMIT = "d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5"
_BASELINE_SOURCE = "d1ef7977ae482e46a920381e6af58791199ec8e1a02586dbe8df451e7d4696c9"
_CANDIDATE_SOURCE = "c00763ef58671de613b18668babf7c07e4f2d928ece1b4b75b80c967203c306f"
_REGISTRY_SHA256 = "da0418442020762272b3b5008c17b515794688270b4940313ccfdfd0b13877cb"
_OWNERSHIP_SHA256 = "72e6b510c3bcf44ac77d2c13613f4d72a14ae8dab0d60a19e5947055ae7cbf08"

_UNIVERSE = (
    "sh600487", "sh601869", "sh603688", "sh603986", "sh688008", "sh688012",
    "sh688019", "sh688037", "sh688041", "sh688072", "sh688082", "sh688110",
    "sh688120", "sh688146", "sh688200", "sh688233", "sh688256", "sh688268",
    "sh688300", "sh688347", "sh688361", "sh688498", "sh688766", "sz000636",
    "sz002281", "sz002371", "sz002409", "sz300054", "sz300223", "sz300308",
    "sz300394", "sz300502", "sz300604", "sz300666",
)
_COMPONENTS = (
    "champion_non_regression",
    "absolute_strategic_robustness",
    "failed_grant_recovery",
    "witness_resilience",
    "repeated_crowning",
    "bounded_healthy_cash_vacancy",
    "complete_literal_metrics",
)
_CRITICAL = ("sz300308", "sz300502", "sz300394")
_WITNESSES = ("sh603688", "sh688008", "sh688082", "sz002409", "sz300666")
_SHARDS = (
    ("loo-a", ("sh600487", "sh688019", "sh688120", "sh688300", "sz002281", "sz300394")),
    ("loo-b", ("sh601869", "sh688037", "sh688146", "sh688347", "sz002371", "sz300502")),
    ("loo-c", ("sh603688", "sh688041", "sh688200", "sh688361", "sz002409", "sz300604")),
    ("loo-d", ("sh603986", "sh688072", "sh688233", "sh688498", "sz300054", "sz300666")),
    ("loo-e", ("sh688008", "sh688082", "sh688256", "sh688766", "sz300223")),
    ("loo-f", ("sh688012", "sh688110", "sh688268", "sz000636", "sz300308")),
)


@dataclass(frozen=True, slots=True)
class _FrozenBaseline:
    champion_final_wealth: float
    champion_minimum_final_wealth: float
    champion_maximum_drawdown: float
    production_source_sha256: str
    strategic_ownership_contract_sha256: str


@dataclass(frozen=True, slots=True)
class _CandidateIdentity:
    baseline_commit: str
    baseline_source_sha256: str
    production_source_sha256: str
    source_surface_id: str
    source_surface_registry_sha256: str


@dataclass(frozen=True, slots=True)
class _FrozenDataIdentity:
    snapshot_id: str
    files_verified: int
    manifest_sha256: str
    checksums_sha256: str


@dataclass(frozen=True, slots=True)
class _InputIdentities:
    ai_universe_sha256: str
    effective_config_sha256: str
    frozen_data: _FrozenDataIdentity
    uv_lock_sha256: str


@dataclass(frozen=True, slots=True)
class _RepairBound:
    persisted_damage_level: int
    target_budget_level: int
    maximum_healthy_sessions: int


@dataclass(frozen=True, slots=True)
class _Thresholds:
    maximum_failed_grant_retry_healthy_sessions: int
    maximum_p90_drawdown: float
    maximum_p90_healthy_zero_total_target_streak: int
    maximum_terminal_zero_strategic_target_scc_sessions: int
    maximum_worst_healthy_zero_total_target_streak: int
    minimum_p10_final_wealth: float
    minimum_positive_return_fraction: float
    minimum_repeated_crowning_actual_epochs: int
    minimum_repeated_crowning_distinct_owners: int
    minimum_witness_fraction: float
    positive_return_final_wealth_exclusive_minimum: float
    repair_bounds: tuple[_RepairBound, ...]


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationContract:
    """One compile-sealed absolute acceptance contract."""

    schema_version: int
    contract_id: str
    baseline_can_relax_absolute_limits: bool
    candidate: _CandidateIdentity
    canonical_universe: tuple[str, ...]
    components: tuple[str, ...]
    critical_removals: tuple[str, ...]
    frozen_baseline: _FrozenBaseline
    inputs: _InputIdentities
    percentile_method: str
    required_witnesses: tuple[str, ...]
    shards: tuple[tuple[str, tuple[str, ...]], ...]
    thresholds: _Thresholds
    window_start: date
    window_end: date
    canonical_sha256: str


def _finite_json(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("absolute generalization contract must contain only finite numbers")
    if isinstance(value, Mapping):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _finite_json(item)


def _exact(value: object, expected: object, *, label: str) -> None:
    if canonical_json_bytes(value) != canonical_json_bytes(expected):
        raise ValueError(f"absolute generalization {label} differs from the frozen contract")


def _read_physical_regular_file(path: Path, *, label: str) -> bytes:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"{label} is missing or unsafe")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError(f"{label} is missing or unsafe")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ValueError(f"{label} is missing or unsafe") from exc


def _read_strict_contract(path: Path) -> dict[str, object]:
    document = _read_physical_regular_file(
        path, label="absolute generalization contract"
    )
    decoded = strict_json_loads(document)
    if not isinstance(decoded, dict):
        raise ValueError("absolute generalization contract must be a JSON object")
    raw = cast(dict[str, object], decoded)
    _finite_json(raw)
    if document != canonical_json_bytes(raw) + b"\n":
        raise ValueError("absolute generalization contract is not canonical JSON")
    return raw


def _read_ownership_contract(path: Path) -> Mapping[str, object]:
    document = _read_physical_regular_file(
        path, label="absolute generalization ownership contract"
    )
    decoded = strict_json_loads(document)
    if not isinstance(decoded, Mapping):
        raise ValueError("absolute generalization ownership contract must be an object")
    return cast(Mapping[str, object], decoded)


def _verify_independent_authorities() -> None:
    if (
        git_source_surface_fingerprint(_ROOT, _BASELINE_COMMIT, "economic_decision_v1")
        != _BASELINE_SOURCE
        or source_surface_fingerprint(_ROOT, "economic_decision_v1") != _CANDIDATE_SOURCE
    ):
        raise ValueError("absolute generalization candidate source identity differs")
    if config_fingerprint(DEFAULT_CONFIG) != "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5":
        raise ValueError("absolute generalization effective config identity differs")
    if hashlib.sha256((_ROOT / "uv.lock").read_bytes()).hexdigest() != "4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61":
        raise ValueError("absolute generalization uv.lock identity differs")
    universe = default_ai_universe()
    if universe.sha256 != "03f42c5066fb8e1c7b2f8e1b7dd38d508d8053f548ebb5596317ce587d7cffd0" or universe.symbols != _UNIVERSE:
        raise ValueError("absolute generalization production AI universe differs")
    ownership = _read_ownership_contract(_OWNERSHIP_CONTRACT_PATH)
    if canonical_json_sha256(ownership) != _OWNERSHIP_SHA256:
        raise ValueError("absolute generalization ownership contract identity differs")
    if tuple(cast(Sequence[str], ownership.get("canonical_universe"))) != _UNIVERSE:
        raise ValueError("absolute generalization ownership universe differs")
    expected_data = {
        "snapshot_id": "20260809T094222Z-causal-tech-index-rebase",
        "files_verified": 36,
        "manifest_sha256": "343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d",
        "checksums_sha256": "ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29",
    }
    if verify_data_manifest(_ROOT / "data/frozen") != expected_data:
        raise ValueError("absolute generalization frozen data identity differs")


def _validate_raw(raw: dict[str, object]) -> None:
    expected_fields = {
        "baseline_can_relax_absolute_limits", "candidate", "canonical_sha256",
        "canonical_universe", "components", "contract_id", "critical_removals",
        "frozen_baseline", "inputs", "percentile_method", "required_witnesses",
        "schema_version", "shards", "thresholds", "window",
    }
    if set(raw) != expected_fields:
        raise ValueError("absolute generalization contract schema differs")
    seal = raw["canonical_sha256"]
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    if not isinstance(seal, str) or canonical_json_sha256(unsealed) != seal:
        raise ValueError("absolute generalization contract seal is invalid")
    if seal != ABSOLUTE_GENERALIZATION_CONTRACT_SHA256:
        raise ValueError("absolute generalization compiled contract identity differs")
    _exact(raw["candidate"], {
        "baseline_commit": _BASELINE_COMMIT, "baseline_source_sha256": _BASELINE_SOURCE,
        "production_source_sha256": _CANDIDATE_SOURCE,
        "source_surface_id": "economic_decision_v1", "source_surface_registry_sha256": _REGISTRY_SHA256,
    }, label="candidate identity")
    _exact(raw["canonical_universe"], list(_UNIVERSE), label="canonical universe")
    _exact(raw["components"], list(_COMPONENTS), label="capability components")
    _exact(raw["critical_removals"], list(_CRITICAL), label="critical removals")
    _exact(raw["required_witnesses"], list(_WITNESSES), label="witness removals")
    _exact(raw["shards"], {name: list(symbols) for name, symbols in _SHARDS}, label="static shards")
    if raw["schema_version"] != 1 or raw["contract_id"] != "absolute-generalization-acceptance":
        raise ValueError("absolute generalization contract identity differs")
    if raw["baseline_can_relax_absolute_limits"] is not False:
        raise ValueError("absolute generalization baseline relaxation must be false")


def _build_contract(raw: dict[str, object]) -> AbsoluteGeneralizationContract:
    baseline = cast(Mapping[str, object], raw["frozen_baseline"])
    candidate = cast(Mapping[str, object], raw["candidate"])
    inputs = cast(Mapping[str, object], raw["inputs"])
    frozen_data = cast(Mapping[str, object], inputs["frozen_data"])
    thresholds = cast(Mapping[str, object], raw["thresholds"])
    repairs = cast(Sequence[Mapping[str, int]], thresholds["repair_bounds"])
    window = cast(Mapping[str, str], raw["window"])
    return AbsoluteGeneralizationContract(
        schema_version=1,
        contract_id="absolute-generalization-acceptance",
        baseline_can_relax_absolute_limits=False,
        candidate=_CandidateIdentity(
            baseline_commit=cast(str, candidate["baseline_commit"]),
            baseline_source_sha256=cast(
                str, candidate["baseline_source_sha256"]
            ),
            production_source_sha256=cast(str, candidate["production_source_sha256"]),
            source_surface_id=cast(str, candidate["source_surface_id"]),
            source_surface_registry_sha256=cast(
                str, candidate["source_surface_registry_sha256"]
            ),
        ),
        canonical_universe=_UNIVERSE,
        components=_COMPONENTS,
        critical_removals=_CRITICAL,
        frozen_baseline=_FrozenBaseline(
            champion_final_wealth=cast(float, baseline["champion_final_wealth"]),
            champion_minimum_final_wealth=cast(
                float, baseline["champion_minimum_final_wealth"]
            ),
            champion_maximum_drawdown=cast(
                float, baseline["champion_maximum_drawdown"]
            ),
            production_source_sha256=cast(
                str, baseline["production_source_sha256"]
            ),
            strategic_ownership_contract_sha256=cast(
                str, baseline["strategic_ownership_contract_sha256"]
            ),
        ),
        inputs=_InputIdentities(
            ai_universe_sha256=cast(str, inputs["ai_universe_sha256"]),
            effective_config_sha256=cast(str, inputs["effective_config_sha256"]),
            frozen_data=_FrozenDataIdentity(
                snapshot_id=cast(str, frozen_data["snapshot_id"]),
                files_verified=cast(int, frozen_data["files_verified"]),
                manifest_sha256=cast(str, frozen_data["manifest_sha256"]),
                checksums_sha256=cast(str, frozen_data["checksums_sha256"]),
            ),
            uv_lock_sha256=cast(str, inputs["uv_lock_sha256"]),
        ),
        percentile_method="linear_interpolation_at_(n-1)*probability",
        required_witnesses=_WITNESSES,
        shards=_SHARDS,
        thresholds=_Thresholds(
            maximum_failed_grant_retry_healthy_sessions=cast(
                int, thresholds["maximum_failed_grant_retry_healthy_sessions"]
            ),
            maximum_p90_drawdown=cast(float, thresholds["maximum_p90_drawdown"]),
            maximum_p90_healthy_zero_total_target_streak=cast(
                int, thresholds["maximum_p90_healthy_zero_total_target_streak"]
            ),
            maximum_terminal_zero_strategic_target_scc_sessions=cast(
                int,
                thresholds["maximum_terminal_zero_strategic_target_scc_sessions"],
            ),
            maximum_worst_healthy_zero_total_target_streak=cast(
                int, thresholds["maximum_worst_healthy_zero_total_target_streak"]
            ),
            minimum_p10_final_wealth=cast(
                float, thresholds["minimum_p10_final_wealth"]
            ),
            minimum_positive_return_fraction=cast(
                float, thresholds["minimum_positive_return_fraction"]
            ),
            minimum_repeated_crowning_actual_epochs=cast(
                int, thresholds["minimum_repeated_crowning_actual_epochs"]
            ),
            minimum_repeated_crowning_distinct_owners=cast(
                int, thresholds["minimum_repeated_crowning_distinct_owners"]
            ),
            minimum_witness_fraction=cast(
                float, thresholds["minimum_witness_fraction"]
            ),
            positive_return_final_wealth_exclusive_minimum=cast(
                float, thresholds["positive_return_final_wealth_exclusive_minimum"]
            ),
            repair_bounds=tuple(
                _RepairBound(
                    persisted_damage_level=value["persisted_damage_level"],
                    target_budget_level=value["target_budget_level"],
                    maximum_healthy_sessions=value["maximum_healthy_sessions"],
                )
                for value in repairs
            ),
        ),
        window_start=date.fromisoformat(window["start"]),
        window_end=date.fromisoformat(window["end"]),
        canonical_sha256=ABSOLUTE_GENERALIZATION_CONTRACT_SHA256,
    )


def load_absolute_generalization_contract(
    path: str | Path = _DEFAULT_CONTRACT_PATH,
) -> AbsoluteGeneralizationContract:
    """Load the frozen contract and revalidate every independent identity."""

    raw = _read_strict_contract(Path(path))
    _validate_raw(raw)
    _verify_independent_authorities()
    return _build_contract(raw)


__all__ = (
    "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256",
    "AbsoluteGeneralizationContract",
    "load_absolute_generalization_contract",
)
