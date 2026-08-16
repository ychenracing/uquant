"""Immutable future-holdout boundary and exact post-checkout evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess  # nosec B404
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, cast

from ..atomic_io import atomic_write_text
from ..config import DEFAULT_CONFIG, config_fingerprint
from ..data import DataStore
from ..engine import code_fingerprint
from .ai_era import AI_ERA_WINDOWS, runtime_environment_provenance
from .universe import AIUniverse, load_ai_universe

LAST_IN_SAMPLE_DATE: Final = "2026-08-05"
HOLDOUT_START: Final = "2026-08-06"
HOLDOUT_DATA_DIRECTORY: Final = "data/holdout/phase2-future-v1"
REVIEW_MILESTONES: Final = (40, 60)
STRATEGY_ANCHOR_COMMIT: Final = "fbbacefe0cb082778e57a84909f344475f556a57"
STRATEGY_SOURCE_SHA256: Final = (
    "1cbc76ede178659e32d64ed864c162e7f0e4b3e172153c8ba2997374e62435a8"
)
STRATEGY_CONFIG_SHA256: Final = (
    "ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13"
)
SCORE_FIELDS: Final = (
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "gross_turnover",
    "top1_concentration",
    "top3_concentration",
    "pnl_hhi",
)
REQUIRED_FUTURE_HOLDOUT_SHA256: Final = (
    "5594511f08761906d78b3b2542b841dbef31dd3ecc86d4fb586a9748de86626a"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_CONTRACT_FIELDS = {
    "schema_version",
    "contract_id",
    "canonical_sha256",
    "dates",
    "data_directory",
    "review_milestones",
    "score_fields",
    "observation_policy",
    "strategy_anchor",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "manifest_id",
    "contract_sha256",
    "canonical_sha256",
    "production",
    "strategy_anchor",
    "effective_config_sha256",
    "universe_sha256",
    "industry_sha256",
    "environment",
    "dates",
    "data",
    "prior_close_state",
    "review_milestones",
    "observation",
    "scores",
}
_ACCOUNT_EXECUTION_FIELDS = {
    "initial_cash",
    "cash",
    "positions",
    "pending_orders",
    "order_ledger",
    "fills",
}


@dataclass(frozen=True, slots=True)
class FutureHoldoutContract:
    """Reviewed dates, storage boundary, milestones, and observation policy."""

    sha256: str
    last_in_sample_date: str
    first_holdout_date: str
    data_directory: str
    review_milestones: tuple[int, int]
    score_fields: tuple[str, ...]
    parameter_changes_from_observation: bool
    strategy_anchor_commit: str
    strategy_source_sha256: str
    strategy_config_sha256: str


@dataclass(frozen=True, slots=True)
class HoldoutBinding:
    """Exact candidate and locked runtime identities bound by a manifest."""

    production_commit: str
    production_source_sha256: str
    strategy_source_sha256: str
    effective_config_sha256: str
    universe_sha256: str
    industry_sha256: str
    python_full_version: str
    numpy_version: str
    pandas_version: str
    uv_version: str
    uv_lock_sha256: str

    def __post_init__(self) -> None:
        if not _COMMIT.fullmatch(self.production_commit):
            raise ValueError("holdout production commit must be a full Git SHA")
        for field in (
            "production_source_sha256",
            "strategy_source_sha256",
            "effective_config_sha256",
            "universe_sha256",
            "industry_sha256",
            "uv_lock_sha256",
        ):
            if not _SHA256.fullmatch(getattr(self, field)):
                raise ValueError(f"holdout {field} must be SHA-256")
        for field in (
            "python_full_version",
            "numpy_version",
            "pandas_version",
            "uv_version",
        ):
            if not getattr(self, field):
                raise ValueError(f"holdout {field} must be non-empty")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"holdout JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"holdout JSON contains non-standard number: {value}")


def _canonical_bytes(value: object, *, omit_seal: bool = False) -> bytes:
    payload = value
    if omit_seal:
        if not isinstance(value, Mapping):
            raise TypeError("sealed holdout payload must be a mapping")
        payload = {key: item for key, item in value.items() if key != "canonical_sha256"}
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_sha256(value: object, *, omit_seal: bool = False) -> str:
    return hashlib.sha256(_canonical_bytes(value, omit_seal=omit_seal)).hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or not a regular file: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is corrupt") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_future_holdout_contract(path: str | Path | None = None) -> FutureHoldoutContract:
    """Load the reviewed contract and reject edits even when locally resealed."""

    source = _repository_root() / "benchmarks/future_holdout_contract.json" if path is None else Path(path)
    raw = _read_json(source, label="future holdout contract")
    if set(raw) != _CONTRACT_FIELDS:
        raise ValueError("future holdout contract schema is malformed")
    if raw["schema_version"] != 1 or raw["contract_id"] != "phase2-future-holdout-v1":
        raise ValueError("future holdout contract identity is malformed")
    seal = raw["canonical_sha256"]
    if (
        not isinstance(seal, str)
        or not _SHA256.fullmatch(seal)
        or seal != _canonical_sha256(raw, omit_seal=True)
        or seal != REQUIRED_FUTURE_HOLDOUT_SHA256
    ):
        raise ValueError("future holdout contract differs from the reviewed contract")
    dates = raw["dates"]
    policy = raw["observation_policy"]
    strategy_anchor = raw["strategy_anchor"]
    if not isinstance(dates, dict) or set(dates) != {"last_in_sample", "first_holdout"}:
        raise ValueError("future holdout date contract is malformed")
    if not isinstance(policy, dict) or set(policy) != {
        "parameter_changes_from_observation",
        "empty_observation_scores",
        "decision_at_last_in_sample_executes_in_holdout",
    }:
        raise ValueError("future holdout observation policy is malformed")
    if not isinstance(strategy_anchor, dict) or strategy_anchor != {
        "candidate_commit": STRATEGY_ANCHOR_COMMIT,
        "decision_source_sha256": STRATEGY_SOURCE_SHA256,
        "effective_config_sha256": STRATEGY_CONFIG_SHA256,
    }:
        raise ValueError("future holdout strategy anchor is malformed")
    if (
        dates != {"last_in_sample": LAST_IN_SAMPLE_DATE, "first_holdout": HOLDOUT_START}
        or raw["data_directory"] != HOLDOUT_DATA_DIRECTORY
        or raw["review_milestones"] != list(REVIEW_MILESTONES)
        or raw["score_fields"] != list(SCORE_FIELDS)
        or policy
        != {
            "parameter_changes_from_observation": False,
            "empty_observation_scores": None,
            "decision_at_last_in_sample_executes_in_holdout": True,
        }
    ):
        raise ValueError("future holdout contract weakens the reviewed boundary")
    if date.fromisoformat(HOLDOUT_START) <= date.fromisoformat(LAST_IN_SAMPLE_DATE):
        raise ValueError("future holdout must begin after the in-sample boundary")
    return FutureHoldoutContract(
        sha256=seal,
        last_in_sample_date=LAST_IN_SAMPLE_DATE,
        first_holdout_date=HOLDOUT_START,
        data_directory=HOLDOUT_DATA_DIRECTORY,
        review_milestones=REVIEW_MILESTONES,
        score_fields=SCORE_FIELDS,
        parameter_changes_from_observation=False,
        strategy_anchor_commit=STRATEGY_ANCHOR_COMMIT,
        strategy_source_sha256=STRATEGY_SOURCE_SHA256,
        strategy_config_sha256=STRATEGY_CONFIG_SHA256,
    )


def _csv_dates(path: Path) -> tuple[str, ...]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "date" not in reader.fieldnames:
                raise RuntimeError(f"market data lacks date column: {path}")
            values = tuple(row["date"] for row in reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"cannot inspect market data: {path}") from exc
    for value in values:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError(f"market data contains invalid date: {path}") from exc
    return values


def maximum_observed_market_date(data_dir: str | Path) -> str:
    """Find the maximum actual market row date without trusting a manifest label."""

    root = Path(data_dir)
    paths = tuple(sorted(root.rglob("*.csv"))) if root.is_dir() else ()
    dates = [value for path in paths for value in _csv_dates(path)]
    if not dates:
        raise RuntimeError(f"no observed market sessions in {root}")
    return max(dates)


def _closed_csv_files(root: Path, *, label: str, missing_ok: bool) -> tuple[Path, ...]:
    current = root.absolute()
    symlinked = False
    while True:
        symlinked = symlinked or current.is_symlink()
        if current == current.parent:
            break
        current = current.parent
    if symlinked:
        raise RuntimeError(f"{label} must not be a symlink")
    if not root.exists():
        if missing_ok:
            return ()
        raise RuntimeError(f"{label} is missing")
    if not root.is_dir():
        raise RuntimeError(f"{label} must be a directory")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"{label} contains a symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix.lower() != ".csv":
            raise RuntimeError(f"{label} contains an unsupported file: {path}")
        files.append(path)
    return tuple(files)


def validate_holdout_layout(
    repository_root: str | Path,
    *,
    contract: FutureHoldoutContract | None = None,
    phase1_windows: Mapping[str, tuple[str, str]] = AI_ERA_WINDOWS,
) -> None:
    """Prove frozen data and official windows cannot consume future sessions."""

    supplied_root = Path(repository_root)
    if supplied_root.is_symlink():
        raise RuntimeError("holdout repository root must not be a symlink")
    root = supplied_root.resolve()
    expected = load_future_holdout_contract() if contract is None else contract
    frozen = root / "data/frozen"
    holdout = root / expected.data_directory
    holdout_root = root / "data/holdout"
    if frozen.is_symlink() or not frozen.is_dir():
        raise RuntimeError("data/frozen is missing or not a physical directory")
    if any(path.is_symlink() for path in frozen.rglob("*")):
        raise RuntimeError("data/frozen contains a symlink")
    observed_maximum = maximum_observed_market_date(frozen)
    if observed_maximum > expected.last_in_sample_date:
        raise RuntimeError("holdout data entered data/frozen")
    if observed_maximum != expected.last_in_sample_date:
        raise RuntimeError("maximum observed economic market date differs from the frozen boundary")
    supplied_windows = dict(phase1_windows)
    official_windows = dict(AI_ERA_WINDOWS)
    if supplied_windows != official_windows:
        expanded = any(
            name in official_windows
            and (bounds[0] < official_windows[name][0] or bounds[1] > official_windows[name][1])
            for name, bounds in supplied_windows.items()
        )
        if expanded:
            raise RuntimeError("Phase 1 window expanded beyond the immutable official bounds")
        raise RuntimeError("official Phase 1 windows differ from the immutable contract")
    if holdout_root.is_symlink():
        raise RuntimeError("data/holdout must be a physical directory")
    if holdout_root.exists():
        if not holdout_root.is_dir():
            raise RuntimeError("data/holdout must be a physical directory")
        unexpected = []
        for path in holdout_root.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"future holdout contains a symlink: {path}")
            if path != holdout and not path.is_relative_to(holdout):
                unexpected.append(path)
        if unexpected:
            raise RuntimeError("future data is outside the isolated holdout directory")
    for path in _closed_csv_files(holdout, label="future holdout", missing_ok=True):
        if any(value < expected.first_holdout_date for value in _csv_dates(path)):
            raise RuntimeError("holdout directory contains an in-sample market row")


def _state_hashes(account_payload: Mapping[str, Any], *, as_of: str) -> dict[str, str]:
    if account_payload.get("last_successful_run") != as_of or account_payload.get("data_hash_as_of") != as_of:
        raise ValueError("holdout account is not the exact prior-close state")
    positions = account_payload.get("positions")
    pending = account_payload.get("pending_orders")
    if not isinstance(positions, Mapping) or not isinstance(pending, list):
        raise ValueError("holdout account positions or pending orders are malformed")
    tranches = {
        str(symbol): value.get("tranches", [])
        for symbol, value in positions.items()
        if isinstance(value, Mapping)
    }
    strategy = {
        key: value
        for key, value in account_payload.items()
        if key not in _ACCOUNT_EXECUTION_FIELDS
    }
    return {
        "as_of": as_of,
        "account_sha256": _canonical_sha256(dict(account_payload)),
        "positions_sha256": _canonical_sha256(dict(positions)),
        "tranches_sha256": _canonical_sha256(tranches),
        "pending_orders_sha256": _canonical_sha256(pending),
        "strategy_state_sha256": _canonical_sha256(strategy),
    }


def validate_prior_close_account(
    account_payload: Mapping[str, Any],
    *,
    frozen_data_dir: str | Path,
) -> None:
    """Require the prior-close state to match current code and frozen data bytes."""

    if account_payload.get("code_hash") != code_fingerprint():
        raise ValueError("holdout account code fingerprint is stale")
    if account_payload.get("data_hash_as_of") != LAST_IN_SAMPLE_DATE:
        raise ValueError("holdout account data hash is not bound to the prior close")
    symbols = account_payload.get("data_hash_symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(symbol, str) for symbol in symbols)
    ):
        raise ValueError("holdout account data hash symbols are malformed")
    expected = DataStore(frozen_data_dir).manifest(
        symbols,
        as_of=LAST_IN_SAMPLE_DATE,
    ).digest
    if account_payload.get("data_hash") != expected:
        raise ValueError("holdout account data hash does not match the frozen prefix")


def _session_dates(values: Iterable[str], *, contract: FutureHoldoutContract) -> tuple[str, ...]:
    sessions = tuple(values)
    if sessions != tuple(sorted(set(sessions))):
        raise ValueError("holdout sessions must be unique and increasing")
    for value in sessions:
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("holdout session must be an ISO date") from exc
        if parsed < date.fromisoformat(contract.first_holdout_date):
            raise ValueError("holdout session predates the frozen boundary")
    return sessions


def _normalized_scores(
    scores: Mapping[str, float | int | None] | None,
    *,
    sessions: Sequence[str],
    contract: FutureHoldoutContract,
) -> dict[str, float | int | None]:
    supplied = {} if scores is None else dict(scores)
    unknown = set(supplied) - set(contract.score_fields)
    if unknown:
        raise ValueError(f"unknown holdout scores: {sorted(unknown)}")
    normalized = {name: supplied.get(name) for name in contract.score_fields}
    if not sessions:
        if any(value is not None for value in normalized.values()):
            raise ValueError("scores must be null when no holdout sessions exist")
        return normalized
    if any(value is None for value in normalized.values()):
        raise ValueError("observed holdout sessions require every score")
    for name, value in normalized.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"holdout score must be finite: {name}")
    if not isinstance(normalized["account_orders"], int):
        raise ValueError("holdout account_orders must be an integer")
    final_wealth = cast(float | int, normalized["final_wealth"])
    gross_turnover = cast(float | int, normalized["gross_turnover"])
    if float(final_wealth) <= 0:
        raise ValueError("holdout final_wealth must be positive")
    if int(normalized["account_orders"]) < 0:
        raise ValueError("holdout account_orders must be nonnegative")
    if float(gross_turnover) < 0:
        raise ValueError("holdout gross_turnover must be nonnegative")
    for name in ("max_drawdown", "top1_concentration", "top3_concentration", "pnl_hhi"):
        value = cast(float | int, normalized[name])
        if not 0 <= float(value) <= 1:
            raise ValueError(f"holdout {name} must be between zero and one")
    top1 = cast(float | int, normalized["top1_concentration"])
    top3 = cast(float | int, normalized["top3_concentration"])
    if float(top3) < float(top1):
        raise ValueError("holdout top3_concentration must not be below top1_concentration")
    return normalized


def _binding_payload(binding: HoldoutBinding) -> dict[str, Any]:
    raw = asdict(binding)
    return {
        "production": {
            "commit": raw.pop("production_commit"),
            "source_sha256": raw.pop("production_source_sha256"),
        },
        "strategy_source_sha256": raw.pop("strategy_source_sha256"),
        "effective_config_sha256": raw.pop("effective_config_sha256"),
        "universe_sha256": raw.pop("universe_sha256"),
        "industry_sha256": raw.pop("industry_sha256"),
        "environment": raw,
    }


def build_future_holdout_manifest(
    *,
    contract: FutureHoldoutContract,
    binding: HoldoutBinding,
    account_payload: Mapping[str, Any],
    holdout_sessions: Iterable[str],
    scores: Mapping[str, float | int | None] | None = None,
    holdout_data_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a self-verifying post-checkout snapshot from independently supplied inputs."""

    sessions = _session_dates(holdout_sessions, contract=contract)
    if (
        binding.strategy_source_sha256 != contract.strategy_source_sha256
        or binding.effective_config_sha256 != contract.strategy_config_sha256
    ):
        raise ValueError("current strategy source or config drifted from the observation anchor")
    normalized_scores = _normalized_scores(scores, sessions=sessions, contract=contract)
    data_sha256 = holdout_data_sha256 or _canonical_sha256(list(sessions))
    if not _SHA256.fullmatch(data_sha256):
        raise ValueError("holdout data identity must be SHA-256")
    binding_payload = _binding_payload(binding)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": "phase2-future-holdout-manifest-v1",
        "contract_sha256": contract.sha256,
        "production": binding_payload["production"],
        "strategy_anchor": {
            "candidate_commit": contract.strategy_anchor_commit,
            "decision_source_sha256": binding_payload["strategy_source_sha256"],
            "effective_config_sha256": contract.strategy_config_sha256,
        },
        "effective_config_sha256": binding_payload["effective_config_sha256"],
        "universe_sha256": binding_payload["universe_sha256"],
        "industry_sha256": binding_payload["industry_sha256"],
        "environment": binding_payload["environment"],
        "dates": {
            "last_in_sample": contract.last_in_sample_date,
            "first_holdout": contract.first_holdout_date,
        },
        "data": {
            "directory": contract.data_directory,
            "sha256": data_sha256,
        },
        "prior_close_state": _state_hashes(
            account_payload,
            as_of=contract.last_in_sample_date,
        ),
        "review_milestones": list(contract.review_milestones),
        "observation": {
            "session_count": len(sessions),
            "first_session": sessions[0] if sessions else None,
            "last_session": sessions[-1] if sessions else None,
            "parameter_changes_from_observation": False,
        },
        "scores": normalized_scores,
    }
    manifest["canonical_sha256"] = _canonical_sha256(manifest, omit_seal=True)
    return manifest


def validate_future_holdout_manifest(
    manifest: Mapping[str, Any],
    *,
    contract: FutureHoldoutContract,
    binding: HoldoutBinding,
    account_payload: Mapping[str, Any],
    holdout_sessions: Iterable[str],
    holdout_data_sha256: str | None = None,
) -> None:
    """Reject a stale, weakened, resealed, or internally inconsistent manifest."""

    raw = dict(manifest)
    observation = raw.get("observation")
    if isinstance(observation, Mapping) and observation.get("parameter_changes_from_observation") is not False:
        raise ValueError("holdout parameter changes from observation are prohibited")
    if set(raw) != _MANIFEST_FIELDS:
        raise ValueError("future holdout manifest schema is malformed")
    seal = raw.get("canonical_sha256")
    if not isinstance(seal, str) or seal != _canonical_sha256(raw, omit_seal=True):
        raise ValueError("future holdout manifest hash is invalid")
    scores = raw.get("scores")
    if not isinstance(scores, Mapping):
        raise ValueError("future holdout scores are malformed")
    expected = build_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account_payload,
        holdout_sessions=holdout_sessions,
        scores=dict(scores),
        holdout_data_sha256=holdout_data_sha256,
    )
    if raw != expected:
        raise ValueError("future holdout manifest is stale")


def _industry_sha256(universe: AIUniverse) -> str:
    payload = [
        {
            "symbol": member.symbol,
            "industry": member.industry,
            "effective_from": member.effective_from.isoformat(),
            "effective_to": member.effective_to.isoformat() if member.effective_to else None,
        }
        for member in universe.members
    ]
    return _canonical_sha256(payload)


def _source_paths(root: Path) -> tuple[Path, ...]:
    fixed = (
        root / "benchmarks/reference_registry.json",
        root / "benchmarks/config_parameter_governance.json",
        root / "benchmarks/future_holdout_contract.json",
        root / "pyproject.toml",
        root / "requirements.txt",
        root / "uv.lock",
    )
    python_sources = tuple((root / "uquant").rglob("*.py"))
    resources = tuple((root / "uquant/validation/resources").glob("*.json"))
    paths = tuple(sorted({*fixed, *python_sources, *resources}))
    if any(not path.is_file() for path in paths) or not python_sources or not resources:
        raise RuntimeError("cannot resolve exact holdout production source")
    return paths


def _strategy_source_sha256(root: Path) -> str:
    operational_names = {
        "__init__.py",
        "__main__.py",
        "atomic_io.py",
        "cli.py",
        "execution_journal.py",
        "report.py",
    }
    paths = tuple(
        sorted(
            path
            for path in (root / "uquant").glob("*.py")
            if path.name not in operational_names
        )
    )
    if not paths:
        raise RuntimeError("cannot resolve anchored strategy source")
    return _source_sha256(paths, root=root)


def _source_sha256(paths: Sequence[Path], *, root: Path, from_git: str | None = None) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = (
            path.read_bytes()
            if from_git is None
            else subprocess.run(
                ["git", "-C", str(root), "show", f"{from_git}:{relative}"],
                check=True,
                capture_output=True,
            ).stdout  # nosec B603
        )
        relative_bytes = relative.encode()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def holdout_source_sha256(repository_root: str | Path) -> str:
    """Hash every production, validation, contract, environment, and lock input."""

    root = Path(repository_root).resolve()
    return _source_sha256(_source_paths(root), root=root)


def current_holdout_binding(repository_root: str | Path | None = None) -> HoldoutBinding:
    """Resolve a clean exact-HEAD production/runtime binding for post-checkout evidence."""

    owning_root = _repository_root().resolve()
    root = owning_root if repository_root is None else Path(repository_root).resolve()
    if root != owning_root:
        raise ValueError("holdout binding requires the owning repository root")
    paths = _source_paths(root)
    relative_paths = [
        "uquant",
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
        "benchmarks/future_holdout_contract.json",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
    ]
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", *relative_paths],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    if status.stdout.strip():
        raise RuntimeError("holdout provenance requires committed production source")
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    head = completed.stdout.strip()
    source = _source_sha256(paths, root=root)
    if not _COMMIT.fullmatch(head) or _source_sha256(paths, root=root, from_git=head) != source:
        raise RuntimeError("holdout production source does not match exact HEAD")
    universe = load_ai_universe()
    runtime = runtime_environment_provenance(root)
    return HoldoutBinding(
        production_commit=head,
        production_source_sha256=source,
        strategy_source_sha256=_strategy_source_sha256(root),
        effective_config_sha256=config_fingerprint(DEFAULT_CONFIG),
        universe_sha256=universe.sha256,
        industry_sha256=_industry_sha256(universe),
        python_full_version=runtime["python_full_version"],
        numpy_version=runtime["numpy_version"],
        pandas_version=runtime["pandas_version"],
        uv_version=runtime["uv_version"],
        uv_lock_sha256=runtime["uv_lock_sha256"],
    )


def holdout_data_identity(data_dir: str | Path) -> tuple[tuple[str, ...], str]:
    """Bind every isolated future-data byte and return its distinct sessions."""

    root = Path(data_dir)
    try:
        paths = _closed_csv_files(root, label="future holdout", missing_ok=True)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    digest = hashlib.sha256()
    sessions: set[str] = set()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        sessions.update(_csv_dates(path))
    if not paths:
        digest.update(b"uquant.empty-future-holdout.v1")
    return tuple(sorted(sessions)), digest.hexdigest()


def generate_future_holdout_manifest(
    *,
    account_path: str | Path,
    output_path: str | Path,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Generate the ignored exact-HEAD manifest used by the final acceptance gate."""

    owning_root = _repository_root().resolve()
    root = owning_root if repository_root is None else Path(repository_root).resolve()
    if root != owning_root:
        raise ValueError("holdout manifest requires the owning repository root")
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    validate_holdout_layout(root, contract=contract)
    from ..account import load_account

    account = load_account(account_path).to_dict()
    validate_prior_close_account(account, frozen_data_dir=root / "data/frozen")
    sessions, data_sha256 = holdout_data_identity(root / contract.data_directory)
    if sessions:
        raise RuntimeError("automatic holdout generation requires reviewed metrics for observed sessions")
    binding = current_holdout_binding(root)
    manifest = build_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=sessions,
        holdout_data_sha256=data_sha256,
    )
    validate_future_holdout_manifest(
        manifest,
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=sessions,
        holdout_data_sha256=data_sha256,
    )
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")  # nosec B603
    protected_paths = [
        Path(account_path),
        *(root / value.decode("utf-8") for value in tracked if value),
        *(path for path in (root / "data/frozen").rglob("*") if path.is_file()),
        *_closed_csv_files(
            root / contract.data_directory,
            label="future holdout",
            missing_ok=True,
        ),
    ]
    destination = Path(output_path)
    atomic_write_text(
        destination,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        protected_paths=protected_paths,
    )
    validate_future_holdout_manifest(
        _read_json(destination, label="future holdout manifest"),
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=sessions,
        holdout_data_sha256=data_sha256,
    )
    return manifest
