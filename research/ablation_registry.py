"""Immutable registry and carrier validation for Phase 2 ablations."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess  # nosec B404
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

DEFAULT_ABLATION_REGISTRY_PATH: Final = (
    Path(__file__).resolve().parents[1] / "artifacts" / "phase2" / "ablations" / "registry.json"
)
MINIMAL_ABLATION_REGISTRY_PATH: Final = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "phase2"
    / "ablations"
    / "minimal_registry.json"
)
POST_TASK8_SOURCE_CONTRACT_PATH: Final = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "phase2"
    / "ablations"
    / "post_task8_source_contract.json"
)
BASE_SOURCE_COMMIT: Final = "7f80436373b6da03536e15ff1908c010bfb92eb3"
MINIMAL_BASE_SOURCE_COMMIT: Final = "e5e0fa903c9a9b26701063ae01f352af3e246a7d"
_POST_TASK8_SOURCE_CONTRACT_SHA256: Final = (
    "16b0979fb6c69850449bd75ed40228ed3f254d0d3f3a9d563aaac8933792295f"
)
REQUIRED_SUBSYSTEMS: Final = (
    "sector_guard",
    "chronic_overlay",
    "transition_overlay",
    "capital_budget_ladder",
    "challenger_scout",
    "conviction_weighting",
    "recovery_conviction_weighting",
    "tactical_rebound_probe",
    "strategic_trailing",
    "restoration_special_handling",
    "add_tranche",
    "replacement_rotation",
    "dynamic_risk_anchors",
    "hierarchical_industry_shrinkage",
    "group_balanced_reference",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_FIXED_CONTRACT_HASHES: Final = {
    "benchmarks/promotion_baseline.json": (
        "b3067ae1bde683d832f9593d80eeea2616d1c934f41291e85ab36d9a6a695bc2"
    ),
    "benchmarks/ai_era_generalization_baseline.json": (
        "2a463f9f7ea63fc01564089af96399f1bdf3ff2023414c9c9a9935e09e2e9c10"
    ),
    "benchmarks/ai_era_generalization_policy.json": (
        "15d0ed3746fd7c223aa89edbff26a97b7de7c0a7f9763f168e8fa93a97f5dda3"
    ),
    "artifacts/phase2/champion-generalization-matrix.json": (
        "926ea8419ab8aad7a05577eee56aeefa90c33cc7faa4e1ee1d2bbbaac77439cc"
    ),
}
_CONFIG_CARRIERS: Final = {
    "sector_guard": "sector_guard_enabled",
    "chronic_overlay": "chronic_overlay_enabled",
    "transition_overlay": "transition_overlay_enabled",
    "capital_budget_ladder": "capital_budget_ladder_enabled",
    "challenger_scout": "challenger_scout_enabled",
    "conviction_weighting": "conviction_weighting_enabled",
    "recovery_conviction_weighting": "recovery_conviction_weighting_enabled",
    "dynamic_risk_anchors": "dynamic_risk_anchors_enabled",
}
_PATCH_CARRIERS: Final = {
    "tactical_rebound_probe": "uquant/portfolio.py",
    "strategic_trailing": "uquant/portfolio_strategic.py",
    "restoration_special_handling": "uquant/portfolio_strategic.py",
    "add_tranche": "uquant/portfolio_leaders.py",
    "replacement_rotation": "uquant/portfolio_leaders.py",
}
_REVIEWED_CARRIER_HASHES: Final = {
    "sector_guard": "618f5b6a2163307d454e3b4d22eb9e0e16157524b488b023417b0c6f3da57886",
    "chronic_overlay": "eb148771589c2caaac98c153d5145302e0ae343eef9251a38bec99382ac7a42d",
    "transition_overlay": "c10391f7105895f540858734ed5813c13de0ea2242b8207c9b2c66e466ac2a7a",
    "capital_budget_ladder": "ec612c0e18ddfaf94fbe2a4153b6319d9901e2d420a5b84e67735b28ff4b95e5",
    "challenger_scout": "76ab5fbc8d734e489f484cfebba28a66f39f5b3c6836408976eda348a67c6f24",
    "conviction_weighting": "50d0eeed080060b4b370bf2e7cda87060f7ffcaf9175cb6e436af54a5eded253",
    "recovery_conviction_weighting": ("d7090c7ead3cb8f0c1472c45e9184a712df1a72b3bcd1d6b6fde8608249d0ea2"),
    "tactical_rebound_probe": "a30417ed0d9afd9d6dc99b24729971c258d4f744b8e276c0dfa7291c5e324a1b",
    "strategic_trailing": "87ef953f34f36f62cec49500e25ef0dd64eb6469adda6e2074b672ddf7619cb8",
    "restoration_special_handling": ("9eb03bdd4d39493f00eea578d90fdf4816b8716e16be85c185c56306e9508b74"),
    "add_tranche": "601ddb6ddfd7fdd358012e71e2d181de4a5b2abe15104a36c9d94c9fec7e3986",
    "replacement_rotation": "a2c0262f63bd8de2f5a11515bfeb8afbf3a34e03a503eeb72371d24f33c6160d",
    "dynamic_risk_anchors": "17a7273e35ca7a3e36300536f592e85ce3575147291e45f04ad253efd96ec7b2",
}
_TOP_LEVEL_FIELDS: Final = {
    "schema_version",
    "registry_id",
    "source_contract",
    "fixed_contracts",
    "invariants",
    "experiments",
    "exclusions",
}
_DERIVED_TOP_LEVEL_FIELDS: Final = {
    "schema_version",
    "registry_id",
    "parent_registry_path",
    "parent_registry_sha256",
    "source_contract",
    "deleted_subsystems",
}
_PRODUCTION_FIXED_PATHS: Final = (
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "benchmarks/reference_registry.json",
    "benchmarks/config_parameter_governance.json",
)


def canonical_sha256(value: object) -> str:
    """Hash finite canonical JSON without accepting serializer-dependent bytes."""
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _production_paths(root: Path) -> tuple[str, ...]:
    discovered = {
        *(_PRODUCTION_FIXED_PATHS),
        *(path.relative_to(root).as_posix() for path in (root / "uquant").rglob("*.py")),
        *(
            path.relative_to(root).as_posix()
            for path in (root / "uquant" / "validation" / "resources").glob("*.json")
        ),
    }
    return tuple(sorted(discovered))


def source_fingerprint(
    root: str | Path,
    paths: Sequence[str] | None = None,
) -> str:
    """Hash exact relative path identities and bytes for a production source tree."""
    base = Path(root).resolve()
    relatives = _production_paths(base) if paths is None else tuple(sorted(paths))
    if not relatives or len(relatives) != len(set(relatives)):
        raise ValueError("ablation source paths must be non-empty and unique")
    digest = hashlib.sha256()
    for relative_text in relatives:
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("ablation source path escapes its root")
        path = base / relative
        if not path.is_file():
            raise ValueError(f"ablation source path is missing: {relative_text}")
        encoded_path = relative.as_posix().encode()
        content = path.read_bytes()
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _git_bytes(root: Path, arguments: Sequence[str]) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve git for ablation source validation")
    try:
        completed = subprocess.run(  # nosec B603
            [git, "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot read reviewed ablation source from Git objects") from exc
    return completed.stdout


def _production_paths_at_commit(root: Path, commit: str) -> tuple[str, ...]:
    if not _COMMIT.fullmatch(commit):
        raise ValueError("post-Task8 source commit is invalid")
    names = _git_bytes(root, ("ls-tree", "-r", "--name-only", commit, "--", "uquant"))
    discovered = set(_PRODUCTION_FIXED_PATHS)
    for name in names.decode("utf-8").splitlines():
        path = Path(name)
        if path.suffix == ".py" or (
            path.suffix == ".json"
            and path.parent.as_posix() == "uquant/validation/resources"
        ):
            discovered.add(path.as_posix())
    return tuple(sorted(discovered))


def _git_blob(root: Path, commit: str, relative: str) -> bytes:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("post-Task8 source path escapes its root")
    return _git_bytes(root, ("show", f"{commit}:{path.as_posix()}"))


def _source_fingerprint_at_commit(
    root: Path,
    commit: str,
    paths: Sequence[str],
) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        encoded_path = relative.encode()
        content = _git_blob(root, commit, relative)
        digest.update(len(encoded_path).to_bytes(4, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _source_delta(
    root: Path,
    base_commit: str,
    reviewed_commit: str,
    base_paths: Sequence[str],
    reviewed_paths: Sequence[str],
) -> list[dict[str, str | None]]:
    base_set = set(base_paths)
    reviewed_set = set(reviewed_paths)
    deltas: list[dict[str, str | None]] = []
    for relative in sorted(base_set | reviewed_set):
        before = _git_blob(root, base_commit, relative) if relative in base_set else None
        after = _git_blob(root, reviewed_commit, relative) if relative in reviewed_set else None
        if before == after:
            continue
        deltas.append(
            {
                "path": relative,
                "status": (
                    "added" if before is None else "deleted" if after is None else "modified"
                ),
                "before_sha256": hashlib.sha256(before).hexdigest() if before is not None else None,
                "after_sha256": hashlib.sha256(after).hexdigest() if after is not None else None,
            }
        )
    return deltas


@dataclass(frozen=True, slots=True)
class AblationWindow:
    name: str
    start: str
    end: str


@dataclass(frozen=True, slots=True)
class FixedContract:
    name: str
    path: str
    sha256: str
    record_count: int
    economic_count: int
    valid_count: int
    replay_error_count: int
    insufficient_count: int
    minimum_date: str
    lookback_sessions: int | None
    random_base_seed: int | None
    random_seed_indexes: tuple[int, ...]
    random_pool_sizes: tuple[int, ...]
    windows: tuple[AblationWindow, ...]


@dataclass(frozen=True, slots=True)
class Carrier:
    kind: str
    subsystem: str
    changes: tuple[tuple[str, bool], ...]
    patch: str
    touched_paths: tuple[str, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class Experiment:
    experiment_id: str
    subsystem: str
    carrier: Carrier


@dataclass(frozen=True, slots=True)
class Exclusion:
    subsystem: str
    reason: str
    evidence_field: str
    frozen_value: bool


@dataclass(frozen=True, slots=True)
class Invariants:
    rules: tuple[str, ...]
    protected_config_fields: tuple[str, ...]
    protected_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AblationRegistry:
    schema_version: int
    registry_id: str
    base_commit: str
    source_sha256: str
    fixed_contracts: tuple[FixedContract, ...]
    invariants: Invariants
    experiments: tuple[Experiment, ...]
    exclusions: tuple[Exclusion, ...]
    deleted_subsystems: tuple[str, ...]
    payload_sha256: str

    def contract(self, name: str) -> FixedContract:
        matches = tuple(item for item in self.fixed_contracts if item.name == name)
        if len(matches) != 1:
            raise ValueError(f"ablation registry requires one fixed contract: {name}")
        return matches[0]


@dataclass(frozen=True, slots=True)
class ContractCell:
    """One immutable Phase 1 or Generalization schedule record."""

    contract: str
    cell_id: str
    status: str
    economic: bool
    symbols: tuple[str, ...]
    start: str
    end: str
    acute_start: str | None = None
    acute_end: str | None = None
    pool_size: int | None = None
    seed_index: int | None = None
    derived_seed: int | None = None


@dataclass(frozen=True, slots=True)
class CarrierCheckout:
    """Provenance for one isolated, clean, content-addressed carrier tree."""

    root: Path
    base_commit: str
    experiment_commit: str
    source_sha256: str
    tree_sha256: str
    carrier_sha256: str
    config_changes: tuple[tuple[str, bool], ...]


def _require_mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, Any], value)


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty text")
    return value


def _require_nonnegative_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _parse_windows(value: object, *, label: str) -> tuple[AblationWindow, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    windows: list[AblationWindow] = []
    for raw in value:
        item = _require_mapping(raw, label=f"{label} item")
        if set(item) != {"name", "start", "end"}:
            raise ValueError(f"{label} item fields are invalid")
        windows.append(
            AblationWindow(
                _require_text(item["name"], label=f"{label} name"),
                _require_text(item["start"], label=f"{label} start"),
                _require_text(item["end"], label=f"{label} end"),
            )
        )
    if len({item.name for item in windows}) != len(windows):
        raise ValueError(f"{label} contains duplicate names")
    return tuple(windows)


def _parse_contract(raw: object) -> FixedContract:
    item = _require_mapping(raw, label="fixed contract")
    required = {
        "name",
        "path",
        "sha256",
        "record_count",
        "economic_count",
        "valid_count",
        "replay_error_count",
        "insufficient_count",
        "minimum_date",
        "lookback_sessions",
        "random_base_seed",
        "random_seed_indexes",
        "random_pool_sizes",
        "windows",
    }
    if set(item) != required:
        raise ValueError("fixed contract fields are incomplete or unexpected")
    path = _require_text(item["path"], label="fixed contract path")
    sha256 = _require_text(item["sha256"], label="fixed contract hash")
    if _FIXED_CONTRACT_HASHES.get(path) != sha256:
        raise ValueError("fixed contract hash differs from the reviewed anchor")
    lookback = item["lookback_sessions"]
    random_seed = item["random_base_seed"]
    if lookback is not None:
        lookback = _require_nonnegative_integer(lookback, label="fixed contract lookback")
    if random_seed is not None:
        random_seed = _require_nonnegative_integer(random_seed, label="fixed contract seed")
    seed_indexes = item["random_seed_indexes"]
    pool_sizes = item["random_pool_sizes"]
    if not isinstance(seed_indexes, list) or not isinstance(pool_sizes, list):
        raise ValueError("fixed contract random fields must be lists")
    return FixedContract(
        name=_require_text(item["name"], label="fixed contract name"),
        path=path,
        sha256=sha256,
        record_count=_require_nonnegative_integer(item["record_count"], label="record count"),
        economic_count=_require_nonnegative_integer(item["economic_count"], label="economic count"),
        valid_count=_require_nonnegative_integer(item["valid_count"], label="valid count"),
        replay_error_count=_require_nonnegative_integer(
            item["replay_error_count"], label="replay error count"
        ),
        insufficient_count=_require_nonnegative_integer(
            item["insufficient_count"], label="insufficient count"
        ),
        minimum_date=_require_text(item["minimum_date"], label="minimum date"),
        lookback_sessions=cast(int | None, lookback),
        random_base_seed=cast(int | None, random_seed),
        random_seed_indexes=tuple(
            _require_nonnegative_integer(value, label="seed index") for value in seed_indexes
        ),
        random_pool_sizes=tuple(
            _require_nonnegative_integer(value, label="pool size") for value in pool_sizes
        ),
        windows=_parse_windows(item["windows"], label="fixed contract windows"),
    )


def _parse_carrier(raw: object, *, subsystem: str) -> Carrier:
    item = _require_mapping(raw, label="ablation carrier")
    if set(item) != {"type", "subsystem", "changes", "patch", "touched_paths", "sha256"}:
        raise ValueError("ablation carrier fields are incomplete or unexpected")
    kind = _require_text(item["type"], label="ablation carrier type")
    if item["subsystem"] != subsystem:
        raise ValueError("ablation carrier subsystem differs from experiment")
    changes_raw = _require_mapping(item["changes"], label="ablation config changes")
    if any(not isinstance(value, bool) for value in changes_raw.values()):
        raise ValueError("ablation config changes must be boolean")
    changes = tuple(sorted((str(name), bool(value)) for name, value in changes_raw.items()))
    patch = item["patch"]
    if not isinstance(patch, str):
        raise ValueError("ablation patch must be text")
    paths_raw = item["touched_paths"]
    if not isinstance(paths_raw, list) or any(not isinstance(path, str) for path in paths_raw):
        raise ValueError("ablation touched paths must be a text list")
    touched_paths = tuple(cast(list[str], paths_raw))
    sha256 = _require_text(item["sha256"], label="ablation carrier hash")
    if not _SHA256.fullmatch(sha256):
        raise ValueError("ablation carrier hash must be SHA-256")
    expected_hash = canonical_sha256({"changes": dict(changes)} if kind == "config" else {"patch": patch})
    if expected_hash != sha256:
        raise ValueError("ablation carrier hash differs from its exact content")
    if _REVIEWED_CARRIER_HASHES.get(subsystem) != sha256:
        raise ValueError("ablation carrier differs from the reviewed carrier hash")
    return Carrier(kind, subsystem, changes, patch, touched_paths, sha256)


def _parse_registry(payload: Mapping[str, Any]) -> AblationRegistry:
    if set(payload) != _TOP_LEVEL_FIELDS or payload.get("schema_version") != 1:
        raise ValueError("ablation registry schema is incomplete or unexpected")
    source = _require_mapping(payload["source_contract"], label="source contract")
    if set(source) != {"base_commit", "production_source_sha256"}:
        raise ValueError("ablation source contract fields are invalid")
    base_commit = _require_text(source["base_commit"], label="source commit")
    source_sha256 = _require_text(source["production_source_sha256"], label="source hash")
    if base_commit != BASE_SOURCE_COMMIT or not _COMMIT.fullmatch(base_commit):
        raise ValueError("ablation source commit differs from the reviewed task base")
    if not _SHA256.fullmatch(source_sha256):
        raise ValueError("ablation source hash must be SHA-256")
    contracts_raw = payload["fixed_contracts"]
    if not isinstance(contracts_raw, list):
        raise ValueError("ablation fixed contracts must be a list")
    contracts = tuple(_parse_contract(item) for item in contracts_raw)
    invariants_raw = _require_mapping(payload["invariants"], label="ablation invariants")
    if set(invariants_raw) != {"rules", "protected_config_fields", "protected_paths"}:
        raise ValueError("ablation invariant fields are invalid")

    def text_tuple(name: str) -> tuple[str, ...]:
        value = invariants_raw[name]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"ablation invariant {name} must be a text list")
        result = tuple(cast(list[str], value))
        if result != tuple(sorted(set(result))):
            raise ValueError(f"ablation invariant {name} must be canonical")
        return result

    experiments_raw = payload["experiments"]
    if not isinstance(experiments_raw, list):
        raise ValueError("ablation experiments must be a list")
    experiments: list[Experiment] = []
    for raw in experiments_raw:
        item = _require_mapping(raw, label="ablation experiment")
        if set(item) != {"experiment_id", "subsystem", "carrier"}:
            raise ValueError("ablation experiment fields are invalid")
        subsystem = _require_text(item["subsystem"], label="ablation subsystem")
        experiments.append(
            Experiment(
                _require_text(item["experiment_id"], label="ablation experiment id"),
                subsystem,
                _parse_carrier(item["carrier"], subsystem=subsystem),
            )
        )
    exclusions_raw = payload["exclusions"]
    if not isinstance(exclusions_raw, list):
        raise ValueError("ablation exclusions must be a list")
    exclusions: list[Exclusion] = []
    for raw in exclusions_raw:
        item = _require_mapping(raw, label="ablation exclusion")
        if set(item) != {"subsystem", "reason", "evidence_field", "frozen_value"}:
            raise ValueError("ablation exclusion fields are invalid")
        if not isinstance(item["frozen_value"], bool):
            raise ValueError("ablation exclusion frozen value must be boolean")
        exclusions.append(
            Exclusion(
                _require_text(item["subsystem"], label="excluded subsystem"),
                _require_text(item["reason"], label="exclusion reason"),
                _require_text(item["evidence_field"], label="exclusion evidence field"),
                item["frozen_value"],
            )
        )
    return AblationRegistry(
        schema_version=1,
        registry_id=_require_text(payload["registry_id"], label="registry id"),
        base_commit=base_commit,
        source_sha256=source_sha256,
        fixed_contracts=contracts,
        invariants=Invariants(
            text_tuple("rules"),
            text_tuple("protected_config_fields"),
            text_tuple("protected_paths"),
        ),
        experiments=tuple(experiments),
        exclusions=tuple(exclusions),
        deleted_subsystems=(),
        payload_sha256=canonical_sha256(payload),
    )


def load_ablation_registry(
    path: str | Path = DEFAULT_ABLATION_REGISTRY_PATH,
) -> AblationRegistry:
    """Load and structurally validate the immutable registry artifact."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load ablation registry: {source}") from exc
    raw = _require_mapping(payload, label="ablation registry")
    if set(raw) == _TOP_LEVEL_FIELDS:
        return _parse_registry(raw)
    if set(raw) != _DERIVED_TOP_LEVEL_FIELDS or raw.get("schema_version") != 1:
        raise ValueError("ablation registry schema is incomplete or unexpected")
    if raw.get("registry_id") != "phase2-post-transition-deletion-ablation-v1":
        raise ValueError("derived ablation registry identity differs")
    if raw.get("parent_registry_path") != "registry.json":
        raise ValueError("derived ablation parent path differs")
    parent = load_ablation_registry(source.parent / "registry.json")
    if raw.get("parent_registry_sha256") != parent.payload_sha256:
        raise ValueError("derived ablation parent registry hash differs")
    source_contract = _require_mapping(raw.get("source_contract"), label="source contract")
    if set(source_contract) != {"base_commit", "production_source_sha256"}:
        raise ValueError("ablation source contract fields are invalid")
    base_commit = _require_text(source_contract.get("base_commit"), label="source commit")
    source_sha256 = _require_text(
        source_contract.get("production_source_sha256"), label="source hash"
    )
    if base_commit != MINIMAL_BASE_SOURCE_COMMIT or not _SHA256.fullmatch(source_sha256):
        raise ValueError("derived ablation source contract differs")
    deleted = raw.get("deleted_subsystems")
    if deleted != ["transition_overlay"]:
        raise ValueError("derived ablation deletion ledger differs")
    experiments = tuple(
        item for item in parent.experiments if item.subsystem not in set(deleted)
    )
    if len(experiments) != len(parent.experiments) - 1:
        raise ValueError("derived ablation deletion did not remove exactly one carrier")
    return AblationRegistry(
        schema_version=1,
        registry_id="phase2-post-transition-deletion-ablation-v1",
        base_commit=base_commit,
        source_sha256=source_sha256,
        fixed_contracts=parent.fixed_contracts,
        invariants=parent.invariants,
        experiments=experiments,
        exclusions=parent.exclusions,
        deleted_subsystems=("transition_overlay",),
        payload_sha256=canonical_sha256(raw),
    )


def _patch_paths(patch: str) -> tuple[str, ...]:
    paths = re.findall(r"^diff --git a/(\S+) b/(\S+)$", patch, flags=re.MULTILINE)
    if not paths or any(left != right for left, right in paths):
        raise ValueError("ablation patch paths are missing or rename production files")
    return tuple(left for left, _ in paths)


def _is_protected(path: str, protected_paths: Sequence[str]) -> bool:
    return any(path == protected or path.startswith(f"{protected}/") for protected in protected_paths)


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate post-Task8 source contract key: {key}")
        result[key] = value
    return result


def _validate_post_task8_source(
    registry: AblationRegistry,
    *,
    root: Path,
    observed_source_sha256: str,
) -> None:
    """Accept only the exact, sealed Git-object delta reviewed after Task 8."""
    if (
        registry.registry_id != "phase2-post-transition-deletion-ablation-v1"
        or registry.base_commit != MINIMAL_BASE_SOURCE_COMMIT
    ):
        raise ValueError("ablation production source differs from the reviewed source hash")
    try:
        payload = json.loads(
            POST_TASK8_SOURCE_CONTRACT_PATH.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cannot load post-Task8 source contract") from exc
    contract = _require_mapping(payload, label="post-Task8 source contract")
    if set(contract) != {
        "schema_version",
        "contract_id",
        "canonical_sha256",
        "base",
        "reviewed",
        "deltas",
    }:
        raise ValueError("post-Task8 source contract fields are invalid")
    if contract.get("schema_version") != 1 or contract.get("contract_id") != (
        "phase2-post-task8-source-v1"
    ):
        raise ValueError("post-Task8 source contract identity differs")
    seal = _require_text(contract.get("canonical_sha256"), label="post-Task8 source seal")
    unsealed = {key: value for key, value in contract.items() if key != "canonical_sha256"}
    if seal != _POST_TASK8_SOURCE_CONTRACT_SHA256 or canonical_sha256(unsealed) != seal:
        raise ValueError("post-Task8 source contract seal differs")

    base = _require_mapping(contract.get("base"), label="post-Task8 base source")
    reviewed = _require_mapping(contract.get("reviewed"), label="post-Task8 reviewed source")
    source_fields = {"commit", "production_source_sha256", "path_count"}
    if set(base) != source_fields or set(reviewed) != source_fields:
        raise ValueError("post-Task8 source endpoint fields are invalid")
    base_commit = _require_text(base.get("commit"), label="post-Task8 base commit")
    reviewed_commit = _require_text(
        reviewed.get("commit"), label="post-Task8 reviewed commit"
    )
    if (
        base_commit != registry.base_commit
        or base.get("production_source_sha256") != registry.source_sha256
        or not _COMMIT.fullmatch(reviewed_commit)
    ):
        raise ValueError("post-Task8 source endpoints differ from the registry")

    base_paths = _production_paths_at_commit(root, base_commit)
    reviewed_paths = _production_paths_at_commit(root, reviewed_commit)
    if base != {
        "commit": base_commit,
        "production_source_sha256": _source_fingerprint_at_commit(
            root, base_commit, base_paths
        ),
        "path_count": len(base_paths),
    }:
        raise ValueError("post-Task8 base Git source differs from its reviewed endpoint")
    reviewed_source_sha256 = _source_fingerprint_at_commit(
        root, reviewed_commit, reviewed_paths
    )
    if reviewed != {
        "commit": reviewed_commit,
        "production_source_sha256": reviewed_source_sha256,
        "path_count": len(reviewed_paths),
    }:
        raise ValueError("post-Task8 reviewed Git source differs from its endpoint")
    deltas = contract.get("deltas")
    if not isinstance(deltas, list) or deltas != _source_delta(
        root,
        base_commit,
        reviewed_commit,
        base_paths,
        reviewed_paths,
    ):
        raise ValueError("post-Task8 exact source delta differs from the reviewed contract")
    if observed_source_sha256 != reviewed_source_sha256:
        raise ValueError("ablation production source differs from the reviewed source hash")


def validate_ablation_registry(
    registry: AblationRegistry,
    *,
    source_root: str | Path,
    check_patch_application: bool = True,
) -> None:
    """Fail closed on drift, bundled carriers, safety edits, or invalid patches."""
    root = Path(source_root).resolve()
    subsystems = tuple(item.subsystem for item in registry.experiments)
    excluded = tuple(item.subsystem for item in registry.exclusions)
    deleted = registry.deleted_subsystems
    if (
        len(subsystems) != len(set(subsystems))
        or len(excluded) != len(set(excluded))
        or len(deleted) != len(set(deleted))
    ):
        raise ValueError("ablation registry contains duplicate subsystems")
    if (
        set(subsystems) | set(excluded) | set(deleted) != set(REQUIRED_SUBSYSTEMS)
        or set(subsystems) & set(excluded)
        or set(subsystems) & set(deleted)
        or set(excluded) & set(deleted)
    ):
        raise ValueError("ablation registry coverage differs from mandated subsystems")
    if bool(deleted) != (registry.registry_id == "phase2-post-transition-deletion-ablation-v1"):
        raise ValueError("ablation deletion ledger differs from registry identity")
    if {item.experiment_id for item in registry.experiments} != {
        f"without_{item.subsystem}" for item in registry.experiments
    }:
        raise ValueError("ablation experiment identity is not canonical")
    hashes = tuple(item.carrier.sha256 for item in registry.experiments)
    if len(hashes) != len(set(hashes)):
        raise ValueError("ablation carrier identities must be unique")
    observed_source_sha256 = source_fingerprint(root)
    if observed_source_sha256 != registry.source_sha256:
        _validate_post_task8_source(
            registry,
            root=root,
            observed_source_sha256=observed_source_sha256,
        )
    for contract in registry.fixed_contracts:
        sealed_contract = _git_blob(root, registry.base_commit, contract.path)
        if hashlib.sha256(sealed_contract).hexdigest() != contract.sha256:
            raise ValueError(f"fixed contract hash is stale: {contract.path}")
        if contract.minimum_date < "2023-01-01":
            raise ValueError("fixed contract includes pre-2023 economics")
    for experiment in registry.experiments:
        carrier = experiment.carrier
        expected_config = _CONFIG_CARRIERS.get(experiment.subsystem)
        expected_patch = _PATCH_CARRIERS.get(experiment.subsystem)
        if carrier.kind == "config":
            if expected_config is None or dict(carrier.changes) != {expected_config: False}:
                raise ValueError("ablation config carrier is not a one-subsystem disable")
            if carrier.patch or carrier.touched_paths:
                raise ValueError("ablation config carrier cannot contain a patch")
            if expected_config in registry.invariants.protected_config_fields:
                raise ValueError("ablation config carrier changes a protected market/safety field")
        elif carrier.kind == "patch":
            if expected_patch is None or carrier.changes:
                raise ValueError("ablation patch carrier is not a one-subsystem disable")
            observed_paths = _patch_paths(carrier.patch)
            if observed_paths != carrier.touched_paths or observed_paths != (expected_patch,):
                raise ValueError("ablation patch touches another subsystem")
            if any(_is_protected(path, registry.invariants.protected_paths) for path in observed_paths):
                raise ValueError("ablation patch touches protected market/safety code")
            if check_patch_application:
                git = shutil.which("git")
                if git is None:
                    raise RuntimeError("cannot resolve git for ablation patch validation")
                try:
                    subprocess.run(  # nosec B603
                        [git, "-C", str(root), "apply", "--check", "--whitespace=error-all", "-"],
                        input=carrier.patch,
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise ValueError(f"ablation patch does not apply: {experiment.subsystem}") from exc
        else:
            raise ValueError("ablation carrier type must be config or patch")
    expected_exclusions = {
        "hierarchical_industry_shrinkage": "hierarchical_industry_shrinkage_enabled",
        "group_balanced_reference": "group_balanced_reference_enabled",
    }
    if {item.subsystem: item.evidence_field for item in registry.exclusions} != expected_exclusions or any(
        item.reason != "inactive_in_frozen_config" or item.frozen_value for item in registry.exclusions
    ):
        raise ValueError("ablation inactive compatibility exclusions are malformed")


def _verified_json(
    root: Path,
    contract: FixedContract,
    *,
    base_commit: str,
) -> Mapping[str, Any]:
    source = _git_blob(root, base_commit, contract.path)
    if hashlib.sha256(source).hexdigest() != contract.sha256:
        raise ValueError(f"fixed contract hash is stale: {contract.path}")
    try:
        payload = json.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load fixed ablation contract: {contract.path}") from exc
    return _require_mapping(payload, label=f"fixed contract {contract.name}")


def _phase1_schedule(
    root: Path,
    contract: FixedContract,
    *,
    base_commit: str,
) -> tuple[ContractCell, ...]:
    payload = _verified_json(root, contract, base_commit=base_commit)
    pools = _require_mapping(payload.get("pools"), label="phase1 pools")
    contract_payload = _require_mapping(payload.get("contract"), label="phase1 contract")
    windows = _require_mapping(contract_payload.get("windows"), label="phase1 windows")
    acute = _require_mapping(contract_payload.get("acute_windows"), label="phase1 acute windows")
    protected = _require_mapping(
        contract_payload.get("protected_intervals"), label="phase1 protected intervals"
    )
    cells: list[ContractCell] = []
    for pool_name, raw_symbols in pools.items():
        if not isinstance(pool_name, str) or not isinstance(raw_symbols, list) or not raw_symbols:
            raise ValueError("phase1 pool is malformed")
        if any(not isinstance(symbol, str) for symbol in raw_symbols):
            raise ValueError("phase1 pool symbols are malformed")
        symbols = tuple(sorted(set(cast(list[str], raw_symbols))))
        if len(symbols) != len(raw_symbols):
            raise ValueError("phase1 pool symbols are not canonical")
        for window_name, raw_bounds in windows.items():
            if not isinstance(window_name, str):
                raise ValueError("phase1 window name is malformed")
            bounds = _require_mapping(raw_bounds, label="phase1 window")
            acute_bounds = _require_mapping(acute.get(window_name), label="phase1 acute window")
            cells.append(
                ContractCell(
                    contract=contract.name,
                    cell_id=f"{pool_name}/{window_name}",
                    status="VALID",
                    economic=True,
                    symbols=symbols,
                    start=_require_text(bounds.get("start"), label="phase1 window start"),
                    end=_require_text(bounds.get("end"), label="phase1 window end"),
                    acute_start=_require_text(acute_bounds.get("start"), label="phase1 acute start"),
                    acute_end=_require_text(acute_bounds.get("end"), label="phase1 acute end"),
                )
            )
        for interval_name, raw_bounds in protected.items():
            if not isinstance(interval_name, str):
                raise ValueError("phase1 protected interval name is malformed")
            bounds = _require_mapping(raw_bounds, label="phase1 protected interval")
            cells.append(
                ContractCell(
                    contract=contract.name,
                    cell_id=f"{pool_name}/{interval_name}",
                    status="VALID",
                    economic=True,
                    symbols=symbols,
                    start=_require_text(bounds.get("start"), label="phase1 protected start"),
                    end=_require_text(bounds.get("end"), label="phase1 protected end"),
                )
            )
    return tuple(cells)


def _generalization_schedule(
    root: Path,
    contract: FixedContract,
    evidence_contract: FixedContract,
    *,
    base_commit: str,
) -> tuple[ContractCell, ...]:
    _verified_json(root, contract, base_commit=base_commit)
    payload = _verified_json(root, evidence_contract, base_commit=base_commit)
    raw_cells = payload.get("cells")
    if not isinstance(raw_cells, list):
        raise ValueError("frozen generalization cells are missing")
    cells: list[ContractCell] = []
    for raw in raw_cells:
        item = _require_mapping(raw, label="frozen generalization cell")
        window = _require_text(item.get("window"), label="generalization window")
        scenario = _require_text(item.get("scenario"), label="generalization scenario")
        symbols_raw = item.get("symbols")
        if not isinstance(symbols_raw, list) or any(not isinstance(symbol, str) for symbol in symbols_raw):
            raise ValueError("generalization symbols are malformed")
        symbols = tuple(cast(list[str], symbols_raw))
        if symbols != tuple(sorted(set(symbols))):
            raise ValueError("generalization symbols are not canonical")
        economic = item.get("economic")
        if not isinstance(economic, bool):
            raise ValueError("generalization economic flag is malformed")
        if not economic:
            status = "INSUFFICIENT_SAMPLE"
        elif item.get("replay_error") is not None or item.get("metrics") is None:
            status = "REPLAY_ERROR"
        else:
            status = "VALID"

        pool_size_raw = item.get("pool_size")
        seed_index_raw = item.get("seed_index")
        derived_seed_raw = item.get("derived_seed")

        cells.append(
            ContractCell(
                contract=contract.name,
                cell_id=f"{window}/{scenario}",
                status=status,
                economic=economic,
                symbols=symbols,
                start=_require_text(item.get("start"), label="generalization start"),
                end=_require_text(item.get("end"), label="generalization end"),
                pool_size=(
                    None
                    if pool_size_raw is None
                    else _require_nonnegative_integer(pool_size_raw, label="generalization pool_size")
                ),
                seed_index=(
                    None
                    if seed_index_raw is None
                    else _require_nonnegative_integer(seed_index_raw, label="generalization seed_index")
                ),
                derived_seed=(
                    None
                    if derived_seed_raw is None
                    else _require_nonnegative_integer(derived_seed_raw, label="generalization derived_seed")
                ),
            )
        )
    return tuple(cells)


def build_contract_schedule(
    registry: AblationRegistry,
    *,
    source_root: str | Path,
) -> tuple[ContractCell, ...]:
    """Build the complete fixed 45+234 record schedule without resampling."""
    root = Path(source_root).resolve()
    phase1 = registry.contract("phase1_performance")
    generalization = registry.contract("ai_era_generalization")
    evidence = registry.contract("frozen_generalization_status")
    cells = (
        *_phase1_schedule(root, phase1, base_commit=registry.base_commit),
        *_generalization_schedule(
            root,
            generalization,
            evidence,
            base_commit=registry.base_commit,
        ),
    )
    identities = tuple((cell.contract, cell.cell_id) for cell in cells)
    if len(identities) != len(set(identities)):
        raise ValueError("ablation fixed schedule contains duplicate cells")
    by_contract = {
        phase1.name: tuple(cell for cell in cells if cell.contract == phase1.name),
        generalization.name: tuple(cell for cell in cells if cell.contract == generalization.name),
    }
    for contract in (phase1, generalization):
        selected = by_contract[contract.name]
        if len(selected) != contract.record_count:
            raise ValueError(f"ablation fixed schedule coverage differs: {contract.name}")
        if sum(cell.economic for cell in selected) != contract.economic_count:
            raise ValueError(f"ablation economic schedule coverage differs: {contract.name}")
        if sum(cell.status == "VALID" for cell in selected) != contract.valid_count:
            raise ValueError(f"ablation valid schedule coverage differs: {contract.name}")
        if sum(cell.status == "REPLAY_ERROR" for cell in selected) != contract.replay_error_count:
            raise ValueError(f"ablation replay-error coverage differs: {contract.name}")
        if sum(cell.status == "INSUFFICIENT_SAMPLE" for cell in selected) != contract.insufficient_count:
            raise ValueError(f"ablation insufficient-sample coverage differs: {contract.name}")
        if any(cell.start < "2023-01-01" or cell.start > cell.end for cell in selected):
            raise ValueError(f"ablation schedule contains an invalid economic date: {contract.name}")
    return tuple(cells)


def _git(
    root: Path,
    arguments: Sequence[str],
    *,
    input_text: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve git for isolated ablation execution")
    try:
        completed = subprocess.run(  # nosec B603
            [git, "-C", str(root), *arguments],
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
            env=None if environment is None else dict(environment),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("isolated ablation git operation failed") from exc
    return completed.stdout.strip()


def verify_carrier_checkout(
    registry: AblationRegistry,
    experiment: Experiment,
    checkout: CarrierCheckout,
) -> None:
    """Re-read exact checkout state and reject any post-materialization mutation."""
    if not checkout.root.is_dir() or checkout.carrier_sha256 != experiment.carrier.sha256:
        raise ValueError("ablation carrier checkout identity is stale")
    if _git(checkout.root, ("status", "--porcelain", "--untracked-files=all")):
        raise ValueError("ablation carrier checkout changed after materialization")
    if _git(checkout.root, ("rev-parse", "HEAD")) != checkout.experiment_commit:
        raise ValueError("ablation carrier checkout HEAD changed after materialization")
    if _git(checkout.root, ("rev-parse", "HEAD^{tree}")) != checkout.tree_sha256:
        raise ValueError("ablation carrier checkout tree changed after materialization")
    observed_source = source_fingerprint(checkout.root)
    if observed_source != checkout.source_sha256:
        raise ValueError("ablation carrier checkout source changed after materialization")
    if experiment.carrier.kind == "config":
        if checkout.experiment_commit != registry.base_commit or observed_source != registry.source_sha256:
            raise ValueError("ablation config carrier source differs from the exact baseline")
    else:
        changed = tuple(
            sorted(
                _git(
                    checkout.root,
                    ("diff", "--name-only", registry.base_commit, checkout.experiment_commit),
                ).splitlines()
            )
        )
        if changed != experiment.carrier.touched_paths:
            raise ValueError("ablation patch checkout changes another subsystem")


@contextmanager
def isolated_carrier_checkout(
    registry: AblationRegistry,
    experiment: Experiment,
    *,
    source_root: str | Path,
    destination: str | Path,
) -> Iterator[CarrierCheckout]:
    """Materialize one carrier in a detached clean worktree and remove it afterward."""
    root = Path(source_root).resolve()
    target = Path(destination).resolve()
    validate_ablation_registry(registry, source_root=root)
    if target.exists():
        raise ValueError("isolated ablation checkout destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(root, ("worktree", "add", "--detach", str(target), registry.base_commit))
    try:
        if source_fingerprint(target) != registry.source_sha256:
            raise ValueError("isolated ablation checkout differs from registry source")
        if experiment.carrier.kind == "patch":
            _git(
                target,
                ("apply", "--whitespace=error-all", "-"),
                input_text=experiment.carrier.patch,
            )
            changed = tuple(sorted(_git(target, ("diff", "--name-only", "HEAD")).splitlines()))
            if changed != experiment.carrier.touched_paths:
                raise ValueError("isolated ablation patch changes another subsystem")
            _git(target, ("diff", "--check"))
            _git(target, ("add", "--", *experiment.carrier.touched_paths))
            fixed_environment = {
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
                "GIT_AUTHOR_EMAIL": "ablation@invalid",
                "GIT_AUTHOR_NAME": "uquant ablation",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
                "GIT_COMMITTER_EMAIL": "ablation@invalid",
                "GIT_COMMITTER_NAME": "uquant ablation",
            }
            _git(
                target,
                ("commit", "--no-gpg-sign", "-m", f"Ablate {experiment.subsystem}"),
                environment=fixed_environment,
            )
        head = _git(target, ("rev-parse", "HEAD"))
        checkout = CarrierCheckout(
            root=target,
            base_commit=registry.base_commit,
            experiment_commit=head,
            source_sha256=source_fingerprint(target),
            tree_sha256=_git(target, ("rev-parse", "HEAD^{tree}")),
            carrier_sha256=experiment.carrier.sha256,
            config_changes=experiment.carrier.changes,
        )
        verify_carrier_checkout(registry, experiment, checkout)
        yield checkout
    finally:
        _git(root, ("worktree", "remove", "--force", str(target)))


@contextmanager
def isolated_baseline_checkout(
    registry: AblationRegistry,
    *,
    source_root: str | Path,
    destination: str | Path,
) -> Iterator[CarrierCheckout]:
    """Materialize the exact clean registry baseline as a detached worktree."""
    root = Path(source_root).resolve()
    target = Path(destination).resolve()
    validate_ablation_registry(registry, source_root=root)
    if target.exists():
        raise ValueError("isolated ablation checkout destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(root, ("worktree", "add", "--detach", str(target), registry.base_commit))
    try:
        observed_source = source_fingerprint(target)
        if observed_source != registry.source_sha256:
            raise ValueError("isolated ablation baseline differs from registry source")
        checkout = CarrierCheckout(
            root=target,
            base_commit=registry.base_commit,
            experiment_commit=_git(target, ("rev-parse", "HEAD")),
            source_sha256=observed_source,
            tree_sha256=_git(target, ("rev-parse", "HEAD^{tree}")),
            carrier_sha256=canonical_sha256({"changes": {}}),
            config_changes=(),
        )
        if checkout.experiment_commit != registry.base_commit or _git(
            target, ("status", "--porcelain", "--untracked-files=all")
        ):
            raise ValueError("isolated ablation baseline is not exact and clean")
        yield checkout
    finally:
        _git(root, ("worktree", "remove", "--force", str(target)))
