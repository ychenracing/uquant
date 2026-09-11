"""Independent Absolute Generalization policy and current checkout binding."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import stat
import subprocess  # nosec B404
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, cast

from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.runtime_identity import runtime_environment_provenance
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
from uquant.provenance.surfaces import load_source_surface_registry
from uquant.validation.manifest import verify_data_manifest

ABSOLUTE_GENERALIZATION_CONTRACT_SHA256: Final = (
    "c0942cf1a1be3f8c8f91be10f2b6ed29621838090c27d543cf4e6329dade33b0"
)

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONTRACT_PATH = _ROOT / "benchmarks/absolute_generalization_acceptance_contract.json"
_POLICY_SHA256 = "c0942cf1a1be3f8c8f91be10f2b6ed29621838090c27d543cf4e6329dade33b0"
_EFFECTIVE_CONFIG_SHA256 = "adf8c123de75f1df13e16e20793f46f631e35606d1bff20d84ebc3a43dff8e51"
_OWNERSHIP_CONTRACT_PATH = _ROOT / "benchmarks/strategic_ownership_acceptance_contract.json"
_REGISTRY_SHA256 = "7c8675ed23a7c9cc2df35a1ebac8a0f82f2cc274090f5206af149f6428aa9bbf"
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
    """Independent sealed policy with a verified current candidate."""

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
    if load_source_surface_registry(_ROOT).canonical_sha256 != _REGISTRY_SHA256:
        raise ValueError("absolute generalization source registry identity differs")
    if config_fingerprint(DEFAULT_CONFIG) != _EFFECTIVE_CONFIG_SHA256:
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


def _candidate_identity() -> _CandidateIdentity:
    """Bind current physical economic bytes to the actual checked-out commit."""
    source = source_surface_fingerprint(_ROOT, "economic_decision_v1")
    if source != git_source_surface_fingerprint(_ROOT, "HEAD", "economic_decision_v1"):
        raise ValueError("absolute generalization candidate source identity differs")
    return _CandidateIdentity(source, "economic_decision_v1", _REGISTRY_SHA256)


def verify_run_checkout() -> dict[str, object]:
    """Preflight trusted CI checkout, runner bytes and the frozen runtime."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve current checkout identity")
    values = subprocess.run(
        [git, "-C", str(_ROOT), "rev-parse", "HEAD", "HEAD^{tree}"],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()  # nosec B603
    head, tree = values
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if head != os.environ.get("GITHUB_SHA"):
            raise ValueError("absolute generalization CI checkout differs from event SHA")
        event = strict_json_loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
        name = os.environ.get("GITHUB_EVENT_NAME", "")
        if not isinstance(event, dict):
            raise ValueError("absolute generalization CI event is malformed")
        expected: dict[str, Callable[[], object]] = {
            "push": lambda: event["after"],
            "pull_request": lambda: event["pull_request"]["merge_commit_sha"],
            "merge_group": lambda: event["merge_group"]["head_sha"],
            "workflow_dispatch": lambda: os.environ["GITHUB_SHA"],
        }
        if name not in expected or expected[name]() != head:
            raise ValueError("absolute generalization CI event target differs")
    for surface in ("economic_decision_v1", "full_package_v1", "validation_runner_v1"):
        if source_surface_fingerprint(_ROOT, surface) != git_source_surface_fingerprint(_ROOT, head, surface):
            raise ValueError(f"absolute generalization checkout surface differs: {surface}")
    return {"head": head, "tree": tree, "runtime": runtime_identity()}


def runtime_identity() -> dict[str, str]:
    """Require the reviewed interpreter and numerical execution environment."""
    runtime = runtime_environment_provenance(_ROOT)
    if runtime != {
        "python_full_version": "3.12.13", "numpy_version": "2.5.1",
        "pandas_version": "3.0.5", "uv_version": "0.11.33",
        "uv_lock_sha256": "4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61",
    }:
        raise ValueError("absolute generalization execution runtime differs")
    return runtime


def _validate_raw(raw: dict[str, object]) -> None:
    expected_fields = {
        "baseline_can_relax_absolute_limits", "canonical_sha256",
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
    if canonical_json_sha256(unsealed) != _POLICY_SHA256:
        raise ValueError("absolute generalization independent policy identity differs")
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
    inputs = cast(Mapping[str, object], raw["inputs"])
    frozen_data = cast(Mapping[str, object], inputs["frozen_data"])
    thresholds = cast(Mapping[str, object], raw["thresholds"])
    repairs = cast(Sequence[Mapping[str, int]], thresholds["repair_bounds"])
    window = cast(Mapping[str, str], raw["window"])
    return AbsoluteGeneralizationContract(
        schema_version=1,
        contract_id="absolute-generalization-acceptance",
        baseline_can_relax_absolute_limits=False,
        candidate=_candidate_identity(),
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
    """Load the sole policy and bind the current checkout without changing its gates."""

    raw = _read_strict_contract(Path(path))
    _validate_raw(raw)
    _verify_independent_authorities()
    return _build_contract(raw)


__all__ = (
    "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256",
    "AbsoluteGeneralizationContract",
    "load_absolute_generalization_contract",
)
