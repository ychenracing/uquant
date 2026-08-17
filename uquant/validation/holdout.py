"""Immutable future-holdout boundary and exact post-checkout evidence."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess  # nosec B404
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast

from ..atomic_io import atomic_write_text
from ..config import DEFAULT_CONFIG, config_fingerprint
from ..data import DataStore
from . import ai_era as ai_era_module
from .ai_era import AI_ERA_WINDOWS, runtime_environment_provenance
from .universe import AIUniverse, load_ai_universe

LAST_IN_SAMPLE_DATE: Final = "2026-08-05"
HOLDOUT_START: Final = "2026-08-06"
HOLDOUT_DATA_DIRECTORY: Final = "data/holdout/phase2-future-v1"
REVIEW_MILESTONES: Final = (20, 40, 60)
REVIEW_CALENDAR_SOURCE: Final = (
    "https://www.sse.com.cn/disclosure/announcement/general/c/"
    "c_20251222_10802507.shtml"
)
REVIEW_SESSIONS: Final = (
    "2026-08-06",
    "2026-08-07",
    "2026-08-10",
    "2026-08-11",
    "2026-08-12",
    "2026-08-13",
    "2026-08-14",
    "2026-08-17",
    "2026-08-18",
    "2026-08-19",
    "2026-08-20",
    "2026-08-21",
    "2026-08-24",
    "2026-08-25",
    "2026-08-26",
    "2026-08-27",
    "2026-08-28",
    "2026-08-31",
    "2026-09-01",
    "2026-09-02",
    "2026-09-03",
    "2026-09-04",
    "2026-09-07",
    "2026-09-08",
    "2026-09-09",
    "2026-09-10",
    "2026-09-11",
    "2026-09-14",
    "2026-09-15",
    "2026-09-16",
    "2026-09-17",
    "2026-09-18",
    "2026-09-21",
    "2026-09-22",
    "2026-09-23",
    "2026-09-24",
    "2026-09-28",
    "2026-09-29",
    "2026-09-30",
    "2026-10-08",
    "2026-10-09",
    "2026-10-12",
    "2026-10-13",
    "2026-10-14",
    "2026-10-15",
    "2026-10-16",
    "2026-10-19",
    "2026-10-20",
    "2026-10-21",
    "2026-10-22",
    "2026-10-23",
    "2026-10-26",
    "2026-10-27",
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
    "2026-11-02",
    "2026-11-03",
    "2026-11-04",
    "2026-11-05",
)
REVIEWED_PHASE1_WINDOWS: Final = MappingProxyType(
    {
        "h1_2023": ("2023-01-03", "2023-06-30"),
        "h2_2023": ("2023-07-03", "2023-12-29"),
        "h1_2024": ("2024-01-02", "2024-07-01"),
        "h2_2024": ("2024-07-01", "2024-12-31"),
        "bull_crash_2025_2026": ("2025-01-02", "2026-07-31"),
        "continuous_ai_era": ("2023-01-03", "2026-08-05"),
    }
)
STRATEGY_ANCHOR_COMMIT: Final = "c47367bba64c827fe18f788c9a3650e13ece306f"
STRATEGY_SOURCE_SHA256: Final = (
    "f9c78557e38342c5a994f19fde63352f635ac37c5d2d7a187ba410b98caa1aed"
)
STRATEGY_CONFIG_SHA256: Final = (
    "ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13"
)
STRATEGY_CLI_SHA256: Final = (
    "fb3da89b7bb8ec745e2249d10173855edc5976a6d1d5f4fd952552d7a2e7e427"
)
STRATEGY_ACCOUNT_CODE_SHA256: Final = (
    "de361ef93a218449df927f5aab14e5013110cc3141a89f94686156bed37a66fc"
)
PRIOR_CLOSE_ACCOUNT_SHA256: Final = (
    "251c90cef356821547c633c69595371aa857a704d8ea21e5119be16136ac0fc8"
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
    "f1555d2f5527b83899ade8f934f67de8df6050aa2ebc7453d0d4245c618e2aeb"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_CONTRACT_FIELDS = {
    "schema_version",
    "contract_id",
    "canonical_sha256",
    "dates",
    "phase1_windows",
    "data_directory",
    "review_calendar",
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
_STRATEGY_FIXED_RELATIVES: Final = {
    "benchmarks/config_parameter_governance.json",
    "benchmarks/reference_registry.json",
}
_STRATEGY_OPERATIONAL_RELATIVES: Final = {
    "uquant/atomic_io.py",
    "uquant/cli.py",
    "uquant/execution_journal.py",
    "uquant/report.py",
    "uquant/validation/ci_artifacts.py",
    "uquant/validation/equivalence.py",
    "uquant/validation/holdout.py",
    "uquant/validation/holdout_runtime.py",
}
_CLI_OPERATIONAL_COMMANDS: Final = {
    "execution-journal",
    "holdout-append",
    "holdout-manifest",
    "holdout-replay",
}


@dataclass(frozen=True, slots=True)
class FutureHoldoutContract:
    """Reviewed dates, storage boundary, milestones, and observation policy."""

    sha256: str
    last_in_sample_date: str
    first_holdout_date: str
    data_directory: str
    review_sessions: tuple[str, ...]
    review_milestones: tuple[int, ...]
    score_fields: tuple[str, ...]
    parameter_changes_from_observation: bool
    phase1_windows: Mapping[str, tuple[str, str]]
    strategy_anchor_commit: str
    strategy_source_sha256: str
    strategy_config_sha256: str
    strategy_cli_sha256: str
    strategy_account_code_sha256: str
    prior_close_account_sha256: str


@dataclass(frozen=True, slots=True)
class HoldoutBinding:
    """Exact candidate and locked runtime identities bound by a manifest."""

    production_commit: str
    production_source_sha256: str
    strategy_source_sha256: str
    strategy_cli_sha256: str
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
            "strategy_cli_sha256",
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


def _read_json_snapshot(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is missing or not a regular file: {path}")
    try:
        content = path.read_bytes()
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is corrupt") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value, content


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    return _read_json_snapshot(path, label=label)[0]


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve git executable for holdout provenance")
    return executable


def load_future_holdout_contract(path: str | Path | None = None) -> FutureHoldoutContract:
    """Load the reviewed contract and reject edits even when locally resealed."""

    source = _repository_root() / "benchmarks/future_holdout_contract.json" if path is None else Path(path)
    raw = _read_json(source, label="future holdout contract")
    if set(raw) != _CONTRACT_FIELDS:
        raise ValueError("future holdout contract schema is malformed")
    if raw["schema_version"] != 3 or raw["contract_id"] != "phase2-future-holdout-v1":
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
    phase1_windows = raw["phase1_windows"]
    policy = raw["observation_policy"]
    review_calendar = raw["review_calendar"]
    strategy_anchor = raw["strategy_anchor"]
    if not isinstance(dates, dict) or set(dates) != {"last_in_sample", "first_holdout"}:
        raise ValueError("future holdout date contract is malformed")
    sealed_windows = {name: list(bounds) for name, bounds in REVIEWED_PHASE1_WINDOWS.items()}
    if not isinstance(phase1_windows, dict) or phase1_windows != sealed_windows:
        raise ValueError("future holdout Phase 1 windows are malformed")
    if not isinstance(policy, dict) or set(policy) != {
        "parameter_changes_from_observation",
        "empty_observation_scores",
        "decision_at_last_in_sample_executes_in_holdout",
    }:
        raise ValueError("future holdout observation policy is malformed")
    if review_calendar != {
        "exchange": "SSE",
        "source": REVIEW_CALENDAR_SOURCE,
        "sessions": list(REVIEW_SESSIONS),
    }:
        raise ValueError("future holdout review calendar is malformed")
    if not isinstance(strategy_anchor, dict) or strategy_anchor != {
        "candidate_commit": STRATEGY_ANCHOR_COMMIT,
        "decision_source_sha256": STRATEGY_SOURCE_SHA256,
        "cli_decision_sha256": STRATEGY_CLI_SHA256,
        "account_code_sha256": STRATEGY_ACCOUNT_CODE_SHA256,
        "prior_close_account_sha256": PRIOR_CLOSE_ACCOUNT_SHA256,
        "effective_config_sha256": STRATEGY_CONFIG_SHA256,
    }:
        raise ValueError("future holdout strategy anchor is malformed")
    if (
        dates != {"last_in_sample": LAST_IN_SAMPLE_DATE, "first_holdout": HOLDOUT_START}
        or raw["data_directory"] != HOLDOUT_DATA_DIRECTORY
        or len(REVIEW_SESSIONS) != REVIEW_MILESTONES[-1]
        or REVIEW_SESSIONS[0] != HOLDOUT_START
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
        review_sessions=REVIEW_SESSIONS,
        review_milestones=REVIEW_MILESTONES,
        score_fields=SCORE_FIELDS,
        parameter_changes_from_observation=False,
        phase1_windows=REVIEWED_PHASE1_WINDOWS,
        strategy_anchor_commit=STRATEGY_ANCHOR_COMMIT,
        strategy_source_sha256=STRATEGY_SOURCE_SHA256,
        strategy_config_sha256=STRATEGY_CONFIG_SHA256,
        strategy_cli_sha256=STRATEGY_CLI_SHA256,
        strategy_account_code_sha256=STRATEGY_ACCOUNT_CODE_SHA256,
        prior_close_account_sha256=PRIOR_CLOSE_ACCOUNT_SHA256,
    )


def _csv_dates_from_text(text: str, *, path: Path) -> tuple[str, ...]:
    try:
        reader = csv.DictReader(text.splitlines())
        if reader.fieldnames is None or "date" not in reader.fieldnames:
            raise RuntimeError(f"market data lacks date column: {path}")
        values = tuple(row["date"] for row in reader)
    except csv.Error as exc:
        raise RuntimeError(f"cannot inspect market data: {path}") from exc
    for value in values:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise RuntimeError(f"market data contains invalid date: {path}") from exc
    return values


def _csv_dates(path: Path) -> tuple[str, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"cannot inspect market data: {path}") from exc
    return _csv_dates_from_text(text, path=path)


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
    phase1_windows: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[tuple[str, ...], str]:
    """Validate isolation and return the one-read future-data identity."""

    supplied_root = Path(repository_root)
    if supplied_root.is_symlink():
        raise RuntimeError("holdout repository root must not be a symlink")
    root = supplied_root.resolve()
    reviewed = load_future_holdout_contract()
    if contract is not None and contract != reviewed:
        raise ValueError("holdout layout requires the reviewed sealed contract")
    expected = reviewed
    sealed_windows = dict(expected.phase1_windows)
    if (
        dict(AI_ERA_WINDOWS) != sealed_windows
        or dict(ai_era_module.AI_ERA_WINDOWS) != sealed_windows
    ):
        raise RuntimeError("live AI-era schedule differs from the sealed Phase 1 windows")
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
    supplied_windows = sealed_windows if phase1_windows is None else dict(phase1_windows)
    if supplied_windows != sealed_windows:
        expanded = any(
            name in sealed_windows
            and (bounds[0] < sealed_windows[name][0] or bounds[1] > sealed_windows[name][1])
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
    try:
        sessions, data_sha256 = holdout_data_identity(holdout)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        _session_dates(sessions, contract=expected)
    except ValueError as exc:
        if "predates the frozen boundary" in str(exc):
            raise RuntimeError("holdout directory contains an in-sample market row") from exc
        raise RuntimeError(str(exc)) from exc
    return sessions, data_sha256


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
    """Require the unchanged frozen-candidate state and frozen data prefix."""

    if account_payload.get("code_hash") != STRATEGY_ACCOUNT_CODE_SHA256:
        raise ValueError("holdout account is not from the exact frozen candidate")
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
    if _canonical_sha256(dict(account_payload)) != PRIOR_CLOSE_ACCOUNT_SHA256:
        raise ValueError(
            "holdout account differs from the authenticated continuous replay"
        )


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
    if sessions != contract.review_sessions[: len(sessions)]:
        raise ValueError("holdout sessions must be the contracted exchange session prefix")
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
        "strategy_cli_sha256": raw.pop("strategy_cli_sha256"),
        "effective_config_sha256": raw.pop("effective_config_sha256"),
        "universe_sha256": raw.pop("universe_sha256"),
        "industry_sha256": raw.pop("industry_sha256"),
        "environment": raw,
    }


def _assemble_future_holdout_manifest(
    *,
    contract: FutureHoldoutContract,
    binding: HoldoutBinding,
    account_payload: Mapping[str, Any],
    holdout_sessions: Iterable[str],
    scores: Mapping[str, float | int | None] | None = None,
    holdout_data_sha256: str,
    metrics_sha256: str | None = None,
) -> dict[str, Any]:
    """Assemble already-read authoritative inputs into the sealed schema."""

    sessions = _session_dates(holdout_sessions, contract=contract)
    if (
        binding.strategy_source_sha256 != contract.strategy_source_sha256
        or binding.strategy_cli_sha256 != contract.strategy_cli_sha256
        or binding.effective_config_sha256 != contract.strategy_config_sha256
    ):
        raise ValueError(
            "current strategy decision path or config drifted from the observation anchor"
        )
    normalized_scores = _normalized_scores(scores, sessions=sessions, contract=contract)
    if not _SHA256.fullmatch(holdout_data_sha256):
        raise ValueError("holdout data identity must be SHA-256")
    if metrics_sha256 is not None and not _SHA256.fullmatch(metrics_sha256):
        raise ValueError("holdout metrics identity must be SHA-256")
    if bool(sessions) != (metrics_sha256 is not None):
        raise ValueError("observed sessions and independent metrics evidence must agree")
    binding_payload = _binding_payload(binding)
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "manifest_id": "phase2-future-holdout-manifest-v2",
        "contract_sha256": contract.sha256,
        "production": binding_payload["production"],
        "strategy_anchor": {
            "candidate_commit": contract.strategy_anchor_commit,
            "decision_source_sha256": contract.strategy_source_sha256,
            "cli_decision_sha256": contract.strategy_cli_sha256,
            "account_code_sha256": contract.strategy_account_code_sha256,
            "prior_close_account_sha256": contract.prior_close_account_sha256,
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
            "sha256": holdout_data_sha256,
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
            "metrics_sha256": metrics_sha256,
            "parameter_changes_from_observation": False,
        },
        "scores": normalized_scores,
    }
    manifest["canonical_sha256"] = _canonical_sha256(manifest, omit_seal=True)
    return manifest


def _validate_future_holdout_manifest_payload(
    manifest: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
) -> None:
    """Reject a stale, weakened, resealed, or inconsistent readback payload."""

    raw = dict(manifest)
    observation = raw.get("observation")
    if isinstance(observation, Mapping) and observation.get("parameter_changes_from_observation") is not False:
        raise ValueError("holdout parameter changes from observation are prohibited")
    if set(raw) != _MANIFEST_FIELDS:
        raise ValueError("future holdout manifest schema is malformed")
    seal = raw.get("canonical_sha256")
    if not isinstance(seal, str) or seal != _canonical_sha256(raw, omit_seal=True):
        raise ValueError("future holdout manifest hash is invalid")
    if raw != dict(expected):
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


def _is_strategy_relative(relative: str) -> bool:
    if relative in _STRATEGY_OPERATIONAL_RELATIVES:
        return False
    if relative in _STRATEGY_FIXED_RELATIVES:
        return True
    path = Path(relative)
    return (
        relative.startswith("uquant/")
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )


def _strategy_source_paths(root: Path) -> tuple[Path, ...]:
    package_paths = tuple(
        path
        for path in (root / "uquant").rglob("*")
        if path.is_file() and _is_strategy_relative(path.relative_to(root).as_posix())
    )
    fixed_paths = tuple(root / relative for relative in _STRATEGY_FIXED_RELATIVES)
    paths = tuple(sorted({*package_paths, *fixed_paths}))
    resources = tuple(
        path for path in paths if path.is_relative_to(root / "uquant/validation/resources")
    )
    if (
        not paths
        or not resources
        or any(path.is_symlink() or not path.is_file() for path in paths)
    ):
        raise RuntimeError("cannot resolve complete anchored strategy source")
    return paths


def _strategy_source_sha256(root: Path) -> str:
    """Hash the complete current decision/state source and resource inventory."""

    base = Path(root).resolve()
    return _source_sha256(_strategy_source_paths(base), root=base)


def _assigned_names(statement: ast.stmt) -> set[str]:
    if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
        return set()
    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
    return {
        node.id
        for target in targets
        for node in ast.walk(target)
        if isinstance(node, ast.Name)
    }


def _loaded_names(statement: ast.stmt) -> set[str]:
    return {
        node.id
        for node in ast.walk(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _adds_operational_parser(statement: ast.stmt) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in _CLI_OPERATIONAL_COMMANDS
        for node in ast.walk(statement)
    )


def _safe_parser_value(value: ast.expr) -> bool:
    if isinstance(value, ast.Constant):
        return True
    if isinstance(value, (ast.List, ast.Tuple)):
        return all(_safe_parser_value(item) for item in value.elts)
    return isinstance(value, ast.Name) and value.id in {"float", "int", "str"}


def _safe_operational_parser_statement(
    statement: ast.stmt,
    *,
    operational_names: set[str],
) -> bool:
    value: ast.expr | None = None
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            return False
        value = statement.value
    elif isinstance(statement, ast.Expr):
        value = statement.value
    if not isinstance(value, ast.Call):
        return False
    calls = [node for node in ast.walk(value) if isinstance(node, ast.Call)]
    if len(calls) != 1 or not isinstance(value.func, ast.Attribute):
        return False
    receiver = value.func.value
    if (
        not isinstance(receiver, ast.Name)
        or receiver.id not in {"sub", *operational_names}
        or value.func.attr not in {"add_argument", "add_parser", "add_subparsers"}
    ):
        return False
    return all(_safe_parser_value(item) for item in value.args) and all(
        item.arg is not None and _safe_parser_value(item.value)
        for item in value.keywords
    )


def _parser_strategy_body(body: list[ast.stmt]) -> list[ast.stmt]:
    operational_names: set[str] = set()
    retained: list[ast.stmt] = []
    for statement in body:
        assigned = _assigned_names(statement)
        if _adds_operational_parser(statement) or _loaded_names(statement) & operational_names:
            operational_names.update(assigned)
            if _safe_operational_parser_statement(
                statement,
                operational_names=operational_names,
            ):
                continue
        retained.append(statement)
    return retained


def _command_guard(statement: ast.stmt) -> str | None:
    if (
        not isinstance(statement, ast.If)
        or statement.orelse
        or not isinstance(statement.test, ast.Compare)
    ):
        return None
    comparison = statement.test
    if (
        len(comparison.ops) != 1
        or not isinstance(comparison.ops[0], ast.Eq)
        or len(comparison.comparators) != 1
        or not isinstance(comparison.left, ast.Attribute)
        or comparison.left.attr != "command"
        or not isinstance(comparison.left.value, ast.Name)
        or comparison.left.value.id != "args"
        or not isinstance(comparison.comparators[0], ast.Constant)
        or not isinstance(comparison.comparators[0].value, str)
    ):
        return None
    return comparison.comparators[0].value


def _cli_strategy_ast(source: bytes) -> bytes:
    """Compile the production CLI decision/config/persistence path to canonical AST."""

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise RuntimeError("cannot compile the anchored production CLI") from exc
    retained: list[ast.stmt] = []
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name == "_parser":
                statement.body = _parser_strategy_body(statement.body)
            elif statement.name == "main":
                statement.body = [
                    item
                    for item in statement.body
                    if _command_guard(item) not in _CLI_OPERATIONAL_COMMANDS
                ]
        retained.append(statement)
    tree.body = retained
    return ast.dump(
        tree,
        annotate_fields=True,
        include_attributes=False,
    ).encode("utf-8")


def _strategy_cli_sha256(root: Path, *, from_git: str | None = None) -> str:
    """Hash compiled CLI semantics that can affect decisions or persisted state."""

    base = Path(root).resolve()
    path = base / "uquant/cli.py"
    source = (
        path.read_bytes()
        if from_git is None
        else subprocess.run(
            [_git_executable(), "-C", str(base), "show", f"{from_git}:uquant/cli.py"],
            check=True,
            capture_output=True,
        ).stdout  # nosec B603
    )
    return hashlib.sha256(_cli_strategy_ast(source)).hexdigest()


def _git_strategy_relatives(root: Path, *, commit: str) -> tuple[str, ...]:
    completed = subprocess.run(
        [
            _git_executable(),
            "-C",
            str(root),
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            "uquant",
            *_STRATEGY_FIXED_RELATIVES,
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    return tuple(
        sorted(
            relative
            for relative in completed.stdout.splitlines()
            if _is_strategy_relative(relative)
        )
    )


def _validated_strategy_source_sha256(root: Path) -> str:
    paths = _strategy_source_paths(root)
    current_relatives = tuple(path.relative_to(root).as_posix() for path in paths)
    anchored_relatives = _git_strategy_relatives(root, commit=STRATEGY_ANCHOR_COMMIT)
    if current_relatives != anchored_relatives:
        raise RuntimeError("strategy source inventory drifted from the Task 8 anchor")
    anchored_sha256 = _source_sha256(
        paths,
        root=root,
        from_git=STRATEGY_ANCHOR_COMMIT,
    )
    current_sha256 = _source_sha256(paths, root=root)
    if anchored_sha256 != STRATEGY_SOURCE_SHA256 or current_sha256 != anchored_sha256:
        raise RuntimeError("strategy source bytes drifted from the Task 8 anchor")
    return current_sha256


def _validated_strategy_cli_sha256(root: Path) -> str:
    anchored = _strategy_cli_sha256(root, from_git=STRATEGY_ANCHOR_COMMIT)
    current = _strategy_cli_sha256(root)
    if anchored != STRATEGY_CLI_SHA256 or current != anchored:
        raise RuntimeError("production CLI decision path drifted from the Task 8 anchor")
    return current


def _strategy_account_code_sha256(root: Path) -> str:
    """Reconstruct the exact code fingerprint written by the frozen candidate."""

    completed = subprocess.run(
        [
            _git_executable(),
            "-C",
            str(root),
            "ls-tree",
            "-r",
            "--name-only",
            STRATEGY_ANCHOR_COMMIT,
            "--",
            "uquant",
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    package_sources = tuple(
        sorted(
            relative
            for relative in completed.stdout.splitlines()
            if Path(relative).parent == Path("uquant")
            and Path(relative).suffix == ".py"
        )
    )
    if not package_sources:
        raise RuntimeError("cannot resolve the frozen account code inventory")
    digest = hashlib.sha256()
    for relative in (
        *package_sources,
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
    ):
        content = subprocess.run(
            [
                _git_executable(),
                "-C",
                str(root),
                "show",
                f"{STRATEGY_ANCHOR_COMMIT}:{relative}",
            ],
            check=True,
            capture_output=True,
        ).stdout  # nosec B603
        digest.update(Path(relative).name.encode())
        digest.update(content)
    value = digest.hexdigest()
    if value != STRATEGY_ACCOUNT_CODE_SHA256:
        raise RuntimeError("frozen account code anchor differs from the exact candidate")
    return value


def _source_sha256(paths: Sequence[Path], *, root: Path, from_git: str | None = None) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = (
            path.read_bytes()
            if from_git is None
            else subprocess.run(
                [_git_executable(), "-C", str(root), "show", f"{from_git}:{relative}"],
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
        [
            _git_executable(),
            "-C",
            str(root),
            "status",
            "--porcelain",
            "--",
            *relative_paths,
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # nosec B603
    if status.stdout.strip():
        raise RuntimeError("holdout provenance requires committed production source")
    completed = subprocess.run(
        [_git_executable(), "-C", str(root), "rev-parse", "HEAD"],
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
    _strategy_account_code_sha256(root)
    return HoldoutBinding(
        production_commit=head,
        production_source_sha256=source,
        strategy_source_sha256=_validated_strategy_source_sha256(root),
        strategy_cli_sha256=_validated_strategy_cli_sha256(root),
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
        try:
            decoded = content.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"cannot inspect market data: {path}") from exc
        sessions.update(_csv_dates_from_text(decoded, path=path))
    if not paths:
        digest.update(b"uquant.empty-future-holdout.v1")
    return tuple(sorted(sessions)), digest.hexdigest()


def _manifest_repository_root(repository_root: str | Path | None) -> Path:
    owning_root = _repository_root().resolve()
    root = owning_root if repository_root is None else Path(repository_root).resolve()
    if root != owning_root:
        raise ValueError("holdout manifest requires the owning repository root")
    return root


def _observation_metrics(
    metrics_path: str | Path | None,
    *,
    sessions: tuple[str, ...],
    holdout_data_sha256: str,
    contract: FutureHoldoutContract,
    account_path: str | Path | None = None,
    repository_root: str | Path | None = None,
    journal_path: str | Path | None = None,
) -> tuple[dict[str, float | int | None], str | None]:
    if not sessions:
        if metrics_path is not None:
            raise ValueError("holdout metrics must be omitted before observations exist")
        return _normalized_scores(None, sessions=sessions, contract=contract), None
    if metrics_path is None:
        raise RuntimeError(
            "observed sessions require a deterministic holdout replay; "
            "detached score files are prohibited"
        )
    from .holdout_runtime import (
        read_future_holdout_replay,
        replay_future_holdout,
    )

    source = Path(metrics_path)
    try:
        before = source.read_bytes()
        observed = read_future_holdout_replay(
            source,
            contract=contract,
            sessions=sessions,
            holdout_data_sha256=holdout_data_sha256,
        )
        after = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "observed sessions require a deterministic holdout replay; "
            "detached score files are prohibited"
        ) from exc
    if before != after:
        raise RuntimeError("future holdout replay changed during readback")
    if account_path is None or repository_root is None:
        raise RuntimeError(
            "observed sessions require deterministic holdout re-execution"
        )
    expected = replay_future_holdout(
        repository_root=repository_root,
        account_path=account_path,
        journal_path=journal_path,
        contract=contract,
    )
    if observed != expected:
        raise RuntimeError(
            "future holdout replay differs from deterministic re-execution"
        )
    scores = cast(Mapping[str, float | int | None], observed["scores"])
    return dict(scores), hashlib.sha256(before).hexdigest()


def build_future_holdout_manifest(
    *,
    account_path: str | Path,
    metrics_path: str | Path | None = None,
    journal_path: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build evidence only from authoritative repository and file inputs."""

    root = _manifest_repository_root(repository_root)
    contract = load_future_holdout_contract(
        root / "benchmarks/future_holdout_contract.json"
    )
    sessions, data_sha256 = validate_holdout_layout(root, contract=contract)
    from ..account import load_account

    account = load_account(account_path).to_dict()
    validate_prior_close_account(account, frozen_data_dir=root / "data/frozen")
    scores, metrics_sha256 = _observation_metrics(
        metrics_path,
        sessions=sessions,
        holdout_data_sha256=data_sha256,
        contract=contract,
        account_path=account_path,
        repository_root=root,
        journal_path=journal_path,
    )
    binding = current_holdout_binding(root)
    return _assemble_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=sessions,
        scores=scores,
        holdout_data_sha256=data_sha256,
        metrics_sha256=metrics_sha256,
    )


def validate_future_holdout_manifest(
    *,
    manifest_path: str | Path,
    account_path: str | Path,
    metrics_path: str | Path | None = None,
    journal_path: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> None:
    """Re-read every authoritative input and reject stale or forged evidence."""

    manifest = _read_json(Path(manifest_path), label="future holdout manifest")
    expected = build_future_holdout_manifest(
        account_path=account_path,
        metrics_path=metrics_path,
        journal_path=journal_path,
        repository_root=repository_root,
    )
    _validate_future_holdout_manifest_payload(manifest, expected=expected)


def generate_future_holdout_manifest(
    *,
    account_path: str | Path,
    output_path: str | Path,
    metrics_path: str | Path | None = None,
    journal_path: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Generate the ignored exact-HEAD manifest used by the final acceptance gate."""

    root = _manifest_repository_root(repository_root)
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    manifest = build_future_holdout_manifest(
        account_path=account_path,
        metrics_path=metrics_path,
        journal_path=journal_path,
        repository_root=root,
    )
    tracked = subprocess.run(
        [_git_executable(), "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")  # nosec B603
    protected_paths = [Path(account_path)]
    if metrics_path is not None:
        protected_paths.append(Path(metrics_path))
    if journal_path is not None:
        protected_paths.append(Path(journal_path))
    protected_paths.extend(
        [
            *(root / value.decode("utf-8") for value in tracked if value),
            *(path for path in (root / "data/frozen").rglob("*") if path.is_file()),
            *_closed_csv_files(
                root / contract.data_directory,
                label="future holdout",
                missing_ok=True,
            ),
        ]
    )
    destination = Path(output_path)
    atomic_write_text(
        destination,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        protected_paths=protected_paths,
    )
    validate_future_holdout_manifest(
        manifest_path=destination,
        account_path=account_path,
        metrics_path=metrics_path,
        journal_path=journal_path,
        repository_root=root,
    )
    return manifest
