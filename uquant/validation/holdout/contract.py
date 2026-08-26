"""Immutable future-holdout contract, calendar, and data-layout boundary."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from .. import ai_era as ai_era_module
from ..ai_era import AI_ERA_WINDOWS
from .capabilities import holdout_facade_capabilities

_CHECKPOINT_RELATIVE = Path("artifacts/future_holdout_checkpoint.json")


LAST_IN_SAMPLE_DATE: Final = "2026-08-05"
HOLDOUT_START: Final = "2026-08-06"
HOLDOUT_DATA_DIRECTORY: Final = "data/holdout/phase2-future-v1"
REVIEW_MILESTONES: Final = (20, 40, 60)
REVIEW_CALENDAR_SOURCE: Final = (
    "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml"
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
REVIEWED_PERFORMANCE_WINDOWS: Final = MappingProxyType(
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
STRATEGY_SOURCE_SHA256: Final = "f9c78557e38342c5a994f19fde63352f635ac37c5d2d7a187ba410b98caa1aed"
STRATEGY_CONFIG_SHA256: Final = "ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13"
STRATEGY_CLI_SHA256: Final = "fb3da89b7bb8ec745e2249d10173855edc5976a6d1d5f4fd952552d7a2e7e427"
STRATEGY_ACCOUNT_CODE_SHA256: Final = "de361ef93a218449df927f5aab14e5013110cc3141a89f94686156bed37a66fc"
PRIOR_CLOSE_ACCOUNT_SHA256: Final = "251c90cef356821547c633c69595371aa857a704d8ea21e5119be16136ac0fc8"
SCORE_FIELDS: Final = (
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "gross_turnover",
    "top1_concentration",
    "top3_concentration",
    "pnl_hhi",
)
REQUIRED_FUTURE_HOLDOUT_SHA256: Final = "f1555d2f5527b83899ade8f934f67de8df6050aa2ebc7453d0d4245c618e2aeb"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_CONTRACT_FIELDS = frozenset(
    {
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
)
_MANIFEST_FIELDS = frozenset(
    {
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
)
_ACCOUNT_EXECUTION_FIELDS = frozenset(
    {
        "initial_cash",
        "cash",
        "positions",
        "pending_orders",
        "order_ledger",
        "fills",
    }
)
_STRATEGY_FIXED_RELATIVES: Final = frozenset(
    {
        "benchmarks/config_parameter_governance.json",
        "benchmarks/reference_registry.json",
    }
)
_STRATEGY_OPERATIONAL_RELATIVES: Final = frozenset(
    {
        "uquant/atomic_io.py",
        "uquant/cli.py",
        "uquant/execution_journal.py",
        "uquant/report.py",
        "uquant/validation/ci_artifacts.py",
        "uquant/validation/equivalence.py",
        "uquant/validation/holdout.py",
        "uquant/validation/holdout_lanes.py",
        "uquant/validation/holdout_runtime.py",
        "uquant/validation/execution_journal.py",
        "uquant/risk_sentinel/__main__.py",
        "uquant/risk_sentinel/calibration.py",
        "uquant/risk_sentinel/cli.py",
        "uquant/risk_sentinel/validation.py",
    }
)
_CLI_OPERATIONAL_COMMANDS: Final = frozenset(
    {
        "execution-journal",
        "holdout-append",
        "holdout-manifest",
        "holdout-replay",
    }
)


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
    performance_windows: Mapping[str, tuple[str, str]]
    strategy_anchor_commit: str
    strategy_source_sha256: str
    strategy_config_sha256: str
    strategy_cli_sha256: str
    strategy_account_code_sha256: str
    prior_close_account_sha256: str


def _reject_duplicate_holdout_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"holdout JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_holdout_constant(value: str) -> None:
    raise ValueError(f"holdout JSON contains non-standard number: {value}")


def _holdout_contract_canonical_bytes(value: object, *, omit_seal: bool = False) -> bytes:
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


_canonical_bytes = _holdout_contract_canonical_bytes


def _holdout_contract_sha256(value: object, *, omit_seal: bool = False) -> str:
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


def _read_holdout_json(path: Path, *, label: str) -> dict[str, Any]:
    return _read_json_snapshot(path, label=label)[0]


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _holdout_git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve git executable for holdout provenance")
    return executable


def _validate_contract_identity(raw: Mapping[str, Any]) -> str:
    if set(raw) != _CONTRACT_FIELDS:
        raise ValueError("future holdout contract schema is malformed")
    if raw["schema_version"] != 3 or raw["contract_id"] != "phase2-future-holdout-v1":
        raise ValueError("future holdout contract identity is malformed")
    seal = raw["canonical_sha256"]
    capabilities = holdout_facade_capabilities()
    required_seal = (
        REQUIRED_FUTURE_HOLDOUT_SHA256
        if capabilities is None
        else capabilities.required_future_holdout_sha256
    )
    if (
        not isinstance(seal, str)
        or not _SHA256.fullmatch(seal)
        or seal != _canonical_sha256(raw, omit_seal=True)
        or seal != required_seal
    ):
        raise ValueError("future holdout contract differs from the reviewed contract")
    return seal


def _validate_contract_sections(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    dates = raw["dates"]
    performance_windows = raw["phase1_windows"]
    policy = raw["observation_policy"]
    review_calendar = raw["review_calendar"]
    strategy_anchor = raw["strategy_anchor"]
    if not isinstance(dates, dict) or set(dates) != {"last_in_sample", "first_holdout"}:
        raise ValueError("future holdout date contract is malformed")
    sealed_windows = {name: list(bounds) for name, bounds in REVIEWED_PERFORMANCE_WINDOWS.items()}
    if not isinstance(performance_windows, dict) or performance_windows != sealed_windows:
        raise ValueError("future holdout performance windows are malformed")
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
    return dates, policy


def _validate_contract_boundaries(
    raw: Mapping[str, Any],
    *,
    dates: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
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


def _reviewed_holdout_contract(seal: str) -> FutureHoldoutContract:
    return FutureHoldoutContract(
        sha256=seal,
        last_in_sample_date=LAST_IN_SAMPLE_DATE,
        first_holdout_date=HOLDOUT_START,
        data_directory=HOLDOUT_DATA_DIRECTORY,
        review_sessions=REVIEW_SESSIONS,
        review_milestones=REVIEW_MILESTONES,
        score_fields=SCORE_FIELDS,
        parameter_changes_from_observation=False,
        performance_windows=REVIEWED_PERFORMANCE_WINDOWS,
        strategy_anchor_commit=STRATEGY_ANCHOR_COMMIT,
        strategy_source_sha256=STRATEGY_SOURCE_SHA256,
        strategy_config_sha256=STRATEGY_CONFIG_SHA256,
        strategy_cli_sha256=STRATEGY_CLI_SHA256,
        strategy_account_code_sha256=STRATEGY_ACCOUNT_CODE_SHA256,
        prior_close_account_sha256=PRIOR_CLOSE_ACCOUNT_SHA256,
    )


def load_future_holdout_contract(path: str | Path | None = None) -> FutureHoldoutContract:
    """Load the reviewed contract and reject edits even when locally resealed."""

    source = _repository_root() / "benchmarks/future_holdout_contract.json" if path is None else Path(path)
    raw = _read_json(source, label="future holdout contract")
    seal = _validate_contract_identity(raw)
    dates, policy = _validate_contract_sections(raw)
    _validate_contract_boundaries(raw, dates=dates, policy=policy)
    return _reviewed_holdout_contract(seal)


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


def _validate_frozen_boundary(root: Path, contract: FutureHoldoutContract) -> None:
    frozen = root / "data/frozen"
    if frozen.is_symlink() or not frozen.is_dir():
        raise RuntimeError("data/frozen is missing or not a physical directory")
    if any(path.is_symlink() for path in frozen.rglob("*")):
        raise RuntimeError("data/frozen contains a symlink")
    observed_maximum = maximum_observed_market_date(frozen)
    if observed_maximum > contract.last_in_sample_date:
        raise RuntimeError("holdout data entered data/frozen")
    if observed_maximum != contract.last_in_sample_date:
        raise RuntimeError("maximum observed economic market date differs from the frozen boundary")


def _validate_live_schedule(contract: FutureHoldoutContract) -> None:
    sealed_windows = dict(contract.performance_windows)
    capabilities = holdout_facade_capabilities()
    live_windows = AI_ERA_WINDOWS if capabilities is None else capabilities.ai_era_windows
    if (
        dict(live_windows) != sealed_windows
        or dict(ai_era_module.AI_ERA_WINDOWS) != sealed_windows
    ):
        raise RuntimeError("live AI-era schedule differs from the sealed performance windows")


def _validate_performance_windows(
    contract: FutureHoldoutContract,
    performance_windows: Mapping[str, tuple[str, str]] | None,
) -> None:
    sealed_windows = dict(contract.performance_windows)
    supplied = sealed_windows if performance_windows is None else dict(performance_windows)
    if supplied == sealed_windows:
        return
    expanded = any(
        name in sealed_windows
        and (bounds[0] < sealed_windows[name][0] or bounds[1] > sealed_windows[name][1])
        for name, bounds in supplied.items()
    )
    if expanded:
        raise RuntimeError("performance window expanded beyond the immutable official bounds")
    raise RuntimeError("official performance windows differ from the immutable contract")


def _validate_holdout_directory(root: Path, holdout: Path) -> None:
    holdout_root = root / "data/holdout"
    if holdout_root.is_symlink():
        raise RuntimeError("data/holdout must be a physical directory")
    if not holdout_root.exists():
        return
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


def _validated_holdout_identity(
    holdout: Path,
    contract: FutureHoldoutContract,
) -> tuple[tuple[str, ...], str]:
    try:
        sessions, data_sha256 = holdout_data_identity(holdout)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        _session_dates(sessions, contract=contract)
    except ValueError as exc:
        if "predates the frozen boundary" in str(exc):
            raise RuntimeError("holdout directory contains an in-sample market row") from exc
        raise RuntimeError(str(exc)) from exc
    return sessions, data_sha256


def validate_holdout_layout(
    repository_root: str | Path,
    *,
    contract: FutureHoldoutContract | None = None,
    performance_windows: Mapping[str, tuple[str, str]] | None = None,
) -> tuple[tuple[str, ...], str]:
    """Validate isolation and return the one-read future-data identity."""

    supplied_root = Path(repository_root)
    if supplied_root.is_symlink():
        raise RuntimeError("holdout repository root must not be a symlink")
    root = supplied_root.resolve()
    reviewed = load_future_holdout_contract()
    if contract is not None and contract != reviewed:
        raise ValueError("holdout layout requires the reviewed sealed contract")
    _validate_live_schedule(reviewed)
    _validate_frozen_boundary(root, reviewed)
    _validate_performance_windows(reviewed, performance_windows)
    holdout = root / reviewed.data_directory
    _validate_holdout_directory(root, holdout)
    return _validated_holdout_identity(holdout, reviewed)


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


__all__ = (
    "_CHECKPOINT_RELATIVE",
    "LAST_IN_SAMPLE_DATE",
    "HOLDOUT_START",
    "HOLDOUT_DATA_DIRECTORY",
    "REVIEW_MILESTONES",
    "REVIEW_CALENDAR_SOURCE",
    "REVIEW_SESSIONS",
    "REVIEWED_PERFORMANCE_WINDOWS",
    "STRATEGY_ANCHOR_COMMIT",
    "STRATEGY_SOURCE_SHA256",
    "STRATEGY_CONFIG_SHA256",
    "STRATEGY_CLI_SHA256",
    "STRATEGY_ACCOUNT_CODE_SHA256",
    "PRIOR_CLOSE_ACCOUNT_SHA256",
    "SCORE_FIELDS",
    "REQUIRED_FUTURE_HOLDOUT_SHA256",
    "_SHA256",
    "_COMMIT",
    "_CONTRACT_FIELDS",
    "_MANIFEST_FIELDS",
    "_ACCOUNT_EXECUTION_FIELDS",
    "_STRATEGY_FIXED_RELATIVES",
    "_STRATEGY_OPERATIONAL_RELATIVES",
    "_CLI_OPERATIONAL_COMMANDS",
    "FutureHoldoutContract",
    "_reject_duplicate_keys",
    "_reject_nonstandard_constant",
    "_canonical_bytes",
    "_canonical_sha256",
    "_read_json_snapshot",
    "_read_json",
    "_repository_root",
    "_git_executable",
    "load_future_holdout_contract",
    "_csv_dates_from_text",
    "_csv_dates",
    "maximum_observed_market_date",
    "_closed_csv_files",
    "validate_holdout_layout",
    "_session_dates",
    "holdout_data_identity",
)

_canonical_sha256 = _holdout_contract_sha256
_git_executable = _holdout_git_executable
_read_json = _read_holdout_json
_reject_duplicate_keys = _reject_duplicate_holdout_keys
_reject_nonstandard_constant = _reject_nonstandard_holdout_constant

ACCOUNT_EXECUTION_FIELDS = _ACCOUNT_EXECUTION_FIELDS
CHECKPOINT_RELATIVE = _CHECKPOINT_RELATIVE
CLI_OPERATIONAL_COMMANDS = _CLI_OPERATIONAL_COMMANDS
COMMIT_PATTERN = _COMMIT
CONTRACT_FIELDS = _CONTRACT_FIELDS
MANIFEST_FIELDS = _MANIFEST_FIELDS
SHA256_PATTERN = _SHA256
STRATEGY_FIXED_RELATIVES = _STRATEGY_FIXED_RELATIVES
STRATEGY_OPERATIONAL_RELATIVES = _STRATEGY_OPERATIONAL_RELATIVES
canonical_sha256 = _canonical_sha256
canonical_bytes = _canonical_bytes
closed_csv_files = _closed_csv_files
csv_dates = _csv_dates
csv_dates_from_text = _csv_dates_from_text
git_executable = _git_executable
read_json = _read_json
read_json_snapshot = _read_json_snapshot
reject_duplicate_keys = _reject_duplicate_keys
reject_nonstandard_constant = _reject_nonstandard_constant
repository_root = _repository_root
session_dates = _session_dates
