"""Append-only future data and deterministic post-boundary replay."""

from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import math
import os
import shutil
import stat
import subprocess  # nosec B404
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from ..account import load_account
from ..atomic_io import atomic_write_text
from ..config import config_fingerprint
from ..engine import INDEX_SYMBOLS, ProductionEngine
from ..leader import REFERENCE_UNIVERSE
from ..types import Decision, Fill
from .execution_journal import (
    JournalCheckpoint,
    JournalRecord,
    execution_journal_checkpoint,
    read_execution_journal,
)
from .generalization import symbol_pnl_concentration
from .holdout import (
    FutureHoldoutContract,
    _canonical_sha256,
    _closed_csv_files,
    _csv_dates_from_text,
    _normalized_scores,
    _read_json,
    _session_dates,
    _validated_score_values,
    holdout_data_identity,
    holdout_source_sha256,
    load_future_holdout_contract,
    validate_prior_close_account,
)
from .holdout_lanes import lane_binding_payload, load_lane_registry
from .universe import load_ai_universe

_REPLAY_FIELDS = {
    "schema_version",
    "replay_id",
    "contract_sha256",
    "production_source_sha256",
    "holdout_data_sha256",
    "prior_close_account_sha256",
    "sessions",
    "lane_binding",
    "decision_digests",
    "decisions",
    "journal_checkpoint",
    "milestones",
    "score_status",
    "observed_metrics",
    "scores",
    "final_account_sha256",
    "canonical_sha256",
}
_DAILY_DECISION_FIELDS = {
    "schema_version",
    "decision_id",
    "contract_sha256",
    "production_source_sha256",
    "holdout_data_sha256",
    "prior_close_account_sha256",
    "replay_canonical_sha256",
    "session",
    "decision",
    "journal_checkpoint",
    "milestones",
    "report_only",
    "canonical_sha256",
}
_CHECKPOINT_FIELDS = {
    "schema_version",
    "checkpoint_id",
    "contract_sha256",
    "production_source_sha256",
    "prior_close_account_sha256",
    "holdout_data_sha256",
    "sessions",
    "decision_digests",
    "replay_canonical_sha256",
    "replay_output_path",
    "replay_output_sha256",
    "decision_output_path",
    "decision_output_sha256",
    "journal_checkpoint",
    "canonical_sha256",
}
_CHECKPOINT_RELATIVE = Path("artifacts/future_holdout_checkpoint.json")
_AUTHORITATIVE_REPOSITORY_RELATIVES = (
    ".git",
    ".github",
    "AGENTS.md",
    "artifacts/phase2",
    "benchmarks",
    "pyproject.toml",
    "requirements.txt",
    "research",
    "scripts",
    "tests",
    "uquant",
    "uv.lock",
)


@dataclass(frozen=True, slots=True)
class _HoldoutDataSnapshot:
    sessions: tuple[str, ...]
    sha256: str
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class _ArtifactSnapshot:
    payload: bytes | None
    mode: int | None


def _snapshot_files_sha256(files: Sequence[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, content in files:
        relative_bytes = relative.encode()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _capture_holdout_data(root: Path) -> _HoldoutDataSnapshot:
    try:
        paths = _closed_csv_files(root, label="future holdout", missing_ok=False)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    sessions: set[str] = set()
    files: list[tuple[str, bytes]] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        try:
            decoded = content.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"cannot inspect market data: {path}") from exc
        sessions.update(_csv_dates_from_text(decoded, path=path))
        files.append((relative, content))
    return _HoldoutDataSnapshot(
        sessions=tuple(sorted(sessions)),
        sha256=_snapshot_files_sha256(files),
        files=tuple(files),
    )


def _validated_snapshot_prefix_sha256(
    snapshot: _HoldoutDataSnapshot,
    *,
    prefix_sessions: Sequence[str],
) -> str:
    inventories: dict[str, set[str]] = {}
    for relative, content in snapshot.files:
        path = Path(relative)
        if len(path.parts) != 2 or path.parts[0] not in snapshot.sessions:
            raise ValueError("future holdout data is not stored as daily snapshots")
        session, name = path.parts
        try:
            dates = _csv_dates_from_text(content.decode("utf-8"), path=path)
        except (RuntimeError, UnicodeError) as exc:
            raise ValueError("future holdout daily snapshot is malformed") from exc
        if dates != (session,):
            raise ValueError("future holdout daily snapshot must contain its one session")
        inventories.setdefault(session, set()).add(name)
    if set(inventories) != set(snapshot.sessions) or any(
        inventory != next(iter(inventories.values())) for inventory in inventories.values()
    ):
        raise ValueError("future holdout daily snapshot inventory is incomplete")
    prefix = set(prefix_sessions)
    selected = tuple(item for item in snapshot.files if Path(item[0]).parts[0] in prefix)
    if prefix and {Path(relative).parts[0] for relative, _ in selected} != prefix:
        raise ValueError("future holdout checkpointed data prefix is incomplete")
    return _snapshot_files_sha256(selected)


def _reject_output_in_protected_data(
    output: str | Path,
    *,
    protected_directories: Sequence[str | Path],
) -> None:
    target = Path(output).resolve(strict=False)
    for protected in protected_directories:
        directory = Path(protected).resolve(strict=False)
        if target == directory or target.is_relative_to(directory):
            raise ValueError(f"holdout output is inside a protected data directory: {directory}")


def _paths_overlap(left: str | Path, right: str | Path) -> bool:
    first = Path(left).resolve(strict=False)
    second = Path(right).resolve(strict=False)
    if first == second or first.is_relative_to(second) or second.is_relative_to(first):
        return True
    if first.exists() and second.exists():
        try:
            return os.path.samefile(first, second)
        except OSError:
            return False
    return False


def _resolved_path_text(path: str | Path) -> str:
    return str(Path(path).resolve(strict=False))


def _read_protected_artifact(path: str | Path, *, label: str) -> bytes:
    source = Path(path)
    current = source.absolute()
    while True:
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink")
        if current == current.parent:
            break
        current = current.parent
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise ValueError(f"{label} is missing or unsafe") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{label} is missing or unsafe")
        with os.fdopen(os.dup(descriptor), "rb") as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def _git_metadata_paths(repository_root: Path) -> tuple[Path, ...]:
    marker = repository_root / ".git"
    paths = [marker]
    if marker.is_file() and not marker.is_symlink():
        try:
            prefix, raw_git_dir = marker.read_text(encoding="utf-8").strip().split(":", 1)
        except (OSError, UnicodeError, ValueError):
            return tuple(paths)
        if prefix != "gitdir" or not raw_git_dir.strip():
            return tuple(paths)
        git_dir = Path(raw_git_dir.strip())
        if not git_dir.is_absolute():
            git_dir = repository_root / git_dir
        git_dir = git_dir.resolve(strict=False)
    elif marker.is_dir() and not marker.is_symlink():
        git_dir = marker.resolve(strict=False)
    else:
        return tuple(paths)
    paths.append(git_dir)
    common_marker = git_dir / "commondir"
    if common_marker.is_file() and not common_marker.is_symlink():
        try:
            raw_common = common_marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            raw_common = ""
        if raw_common:
            common_dir = Path(raw_common)
            if not common_dir.is_absolute():
                common_dir = git_dir / common_dir
            paths.append(common_dir.resolve(strict=False))
    return tuple(dict.fromkeys(paths))


def _tracked_repository_paths(repository_root: Path) -> tuple[Path, ...]:
    if not (repository_root / ".git").exists():
        return ()
    git = shutil.which("git")
    if git is None:
        raise ValueError("cannot resolve tracked repository paths")
    try:
        completed = subprocess.run(  # nosec B603
            [git, "-C", str(repository_root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
        names = completed.stdout.decode("utf-8").split("\0")
    except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot resolve tracked repository paths") from exc
    paths: list[Path] = []
    for name in names:
        if not name:
            continue
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("tracked repository path escapes its root")
        paths.append(repository_root / relative)
    return tuple(paths)


def _reject_authoritative_output_paths(
    *,
    repository_root: Path,
    output_path: str | Path,
    decision_output_path: str | Path | None,
    account_path: str | Path,
    journal_path: str | Path | None,
    holdout_data_directory: str,
    checkpoint_path: Path,
    lock_paths: Sequence[Path],
) -> None:
    outputs = [Path(output_path)]
    if decision_output_path is not None:
        outputs.append(Path(decision_output_path))
    if len(outputs) == 2 and _paths_overlap(outputs[0], outputs[1]):
        raise ValueError("holdout outputs overlap")
    git_metadata = _git_metadata_paths(repository_root)
    tracked_paths = _tracked_repository_paths(repository_root)
    protected: list[Path] = [
        Path(account_path),
        checkpoint_path,
        *lock_paths,
        repository_root / "data/frozen",
        repository_root / holdout_data_directory,
        *(repository_root / relative for relative in _AUTHORITATIVE_REPOSITORY_RELATIVES),
        *git_metadata,
        *tracked_paths,
    ]
    if journal_path is not None:
        protected.append(Path(journal_path))
    for output in outputs:
        for authoritative in protected:
            if _paths_overlap(output, authoritative):
                raise ValueError(f"holdout output overlaps an authoritative path: {authoritative}")
    carrier_protected = [
        Path(account_path),
        *outputs,
        *lock_paths,
        repository_root / "data/frozen",
        repository_root / holdout_data_directory,
        *(repository_root / relative for relative in _AUTHORITATIVE_REPOSITORY_RELATIVES),
        *git_metadata,
        *tracked_paths,
    ]
    if journal_path is not None:
        carrier_protected.append(Path(journal_path))
    for authoritative in carrier_protected:
        if _paths_overlap(checkpoint_path, authoritative):
            raise ValueError(f"holdout checkpoint overlaps an authoritative path: {authoritative}")


def _csv_inventory(root: Path, *, label: str) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{label} must be a physical directory")
    paths = tuple(sorted(root.glob("*.csv")))
    if not paths or any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError(f"{label} CSV inventory is missing or unsafe")
    return {path.name: path for path in paths}


def _one_snapshot_row(path: Path, *, expected_header: tuple[str, ...]) -> tuple[str, bytes]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"holdout snapshot contains an unsafe file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
        reader = csv.DictReader(text.splitlines())
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"cannot read holdout snapshot: {path}") from exc
    if header != expected_header:
        raise ValueError(f"holdout snapshot header differs from frozen data: {path.name}")
    if len(rows) != 1 or not rows[0].get("date"):
        raise ValueError("each holdout snapshot CSV must contain exactly one market row")
    try:
        session = pd.Timestamp(rows[0]["date"]).date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"holdout snapshot contains an invalid date: {path.name}") from exc
    if session != rows[0]["date"]:
        raise ValueError(f"holdout snapshot contains a non-canonical date: {path.name}")
    numeric_columns = tuple(
        column for column in ("open", "high", "low", "close", "volume", "amount") if column in header
    )
    try:
        numeric_values = tuple(float(rows[0][column]) for column in numeric_columns)
    except (TypeError, ValueError) as exc:
        raise ValueError("holdout snapshot requires finite OHLCV and amount values") from exc
    if not numeric_columns or any(not math.isfinite(value) for value in numeric_values):
        raise ValueError("holdout snapshot requires finite OHLCV and amount values")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    writer.writerow(rows[0])
    return session, output.getvalue().encode("utf-8")


def append_holdout_snapshot(
    *,
    repository_root: str | Path,
    snapshot_dir: str | Path,
    contract: FutureHoldoutContract | None = None,
) -> dict[str, object]:
    """Atomically append one complete daily snapshot outside the frozen prefix."""

    root = Path(repository_root).resolve()
    reviewed = load_future_holdout_contract() if contract is None else contract
    frozen = _csv_inventory(root / "data/frozen", label="frozen market data")
    snapshot_root = Path(snapshot_dir)
    snapshot = _csv_inventory(snapshot_root, label="holdout snapshot")
    unsupported = tuple(
        path
        for path in snapshot_root.iterdir()
        if path.is_symlink() or path.is_dir() or path.suffix.lower() != ".csv"
    )
    if unsupported:
        raise ValueError("holdout snapshot contains unsupported entries")
    if set(snapshot) != set(frozen):
        raise ValueError("holdout snapshot files must exactly match the frozen CSV inventory")

    encoded: dict[str, bytes] = {}
    sessions: set[str] = set()
    for name in sorted(frozen):
        frozen_header = tuple(next(csv.reader(frozen[name].read_text(encoding="utf-8").splitlines())))
        session, content = _one_snapshot_row(
            snapshot[name],
            expected_header=frozen_header,
        )
        sessions.add(session)
        encoded[name] = content
    if len(sessions) != 1:
        raise ValueError("holdout snapshot files must contain one common session")
    session = next(iter(sessions))

    holdout_root = root / reviewed.data_directory
    existing_sessions, _ = holdout_data_identity(holdout_root)
    try:
        _session_dates(existing_sessions, contract=reviewed)
    except ValueError as exc:
        raise ValueError("existing holdout data is not a contracted session prefix") from exc
    destination = holdout_root / session
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("holdout daily append destination is unsafe")
        observed = {
            path.name: path.read_bytes()
            for path in destination.iterdir()
            if path.is_file() and not path.is_symlink()
        }
        if observed != encoded or len(tuple(destination.iterdir())) != len(encoded):
            raise ValueError("holdout snapshot conflicts with the immutable daily append")
        return {"session": session, "files": len(encoded), "idempotent": True}
    if (
        len(existing_sessions) >= len(reviewed.review_sessions)
        or session != (reviewed.review_sessions[len(existing_sessions)])
    ):
        raise ValueError("holdout snapshot must be the next contracted exchange session")
    if existing_sessions:
        checkpoint_path = root / _CHECKPOINT_RELATIVE
        prior_checkpoint = _read_checkpoint_carrier(
            checkpoint_path,
            contract=reviewed,
        )
        if prior_checkpoint is None:
            raise ValueError("prior daily replay checkpoint is required before the next holdout append")
        prior_payload, _ = prior_checkpoint
        _verify_checkpoint_artifacts(prior_payload, contract=reviewed)
        if (
            tuple(prior_payload["sessions"]) != existing_sessions
            or prior_payload["holdout_data_sha256"] != holdout_data_identity(holdout_root)[1]
        ):
            raise ValueError("prior daily replay checkpoint does not match the current holdout prefix")

    holdout_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{session}-", dir=holdout_root))
    try:
        for name, content in encoded.items():
            path = staging / name
            path.write_bytes(content)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        os.replace(staging, destination)
        directory_fd = os.open(
            holdout_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"session": session, "files": len(encoded), "idempotent": False}


def _merged_csv_text(
    frozen: Path,
    future_files: Sequence[tuple[str, bytes]],
) -> str:
    try:
        frozen_text = frozen.read_text(encoding="utf-8")
        frozen_reader = csv.DictReader(frozen_text.splitlines())
        header = tuple(frozen_reader.fieldnames or ())
        rows = {str(row["date"]): dict(row) for row in frozen_reader}
    except (OSError, UnicodeError, csv.Error, KeyError) as exc:
        raise ValueError(f"cannot materialize frozen market data: {frozen}") from exc
    for relative, content in future_files:
        try:
            reader = csv.DictReader(content.decode("utf-8").splitlines())
            if tuple(reader.fieldnames or ()) != header:
                raise ValueError(f"future market header differs: {relative}")
            for row in reader:
                session = str(row.get("date", ""))
                if not session:
                    raise ValueError(f"future market row lacks a date: {relative}")
                if session in rows and rows[session] != dict(row):
                    raise ValueError(f"future market row conflicts with an existing date: {relative}")
                rows[session] = dict(row)
        except (UnicodeError, csv.Error) as exc:
            raise ValueError(f"cannot materialize future market data: {relative}") from exc
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(header), lineterminator="\n")
    writer.writeheader()
    for session in sorted(rows):
        writer.writerow(rows[session])
    return output.getvalue()


def _materialize_overlay(
    root: Path,
    destination: Path,
    snapshot: _HoldoutDataSnapshot,
) -> None:
    frozen = _csv_inventory(root / "data/frozen", label="frozen market data")
    by_name: dict[str, list[tuple[str, bytes]]] = {name: [] for name in frozen}
    for relative, content in snapshot.files:
        name = Path(relative).name
        if name not in by_name:
            raise ValueError(f"future holdout contains an unknown market file: {name}")
        by_name[name].append((relative, content))
    if any(not paths for paths in by_name.values()):
        raise ValueError("future holdout is incomplete for deterministic replay")
    destination.mkdir(parents=True, exist_ok=False)
    for name in sorted(frozen):
        (destination / name).write_text(
            _merged_csv_text(frozen[name], sorted(by_name[name])),
            encoding="utf-8",
        )


def _period_symbol_pnl(
    *,
    starting_values: Mapping[str, float],
    final_values: Mapping[str, float],
    fills: Sequence[Fill],
) -> dict[str, float]:
    pnl = {symbol: -float(value) for symbol, value in starting_values.items()}
    for fill in fills:
        fees = fill.commission + fill.stamp_duty + fill.transfer_fee
        cash_flow = -(fill.gross_value + fees) if fill.side == "BUY" else fill.gross_value - fees
        pnl[fill.symbol] = pnl.get(fill.symbol, 0.0) + cash_flow
    for symbol, value in final_values.items():
        pnl[symbol] = pnl.get(symbol, 0.0) + float(value)
    return dict(sorted(pnl.items()))


def _drawdown(values: Sequence[float]) -> float:
    peak = -math.inf
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, 1.0 - value / max(peak, 1e-12))
    return maximum


def _decision_payload(decision: Decision) -> dict[str, object]:
    return {
        "date": decision.date,
        "decision_digest": decision.decision_digest,
        "payload": decision.canonical_payload(effective_config_sha256=config_fingerprint()),
    }


def _decision_payload_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def replay_future_holdout(
    *,
    repository_root: str | Path,
    account_path: str | Path,
    journal_path: str | Path | None = None,
    trusted_journal_checkpoint: JournalCheckpoint | None = None,
    contract: FutureHoldoutContract | None = None,
    lane_id: str = "champion_pre_sentinel",
) -> dict[str, Any]:
    """Replay every observed session from the authenticated prior-close account."""

    root = Path(repository_root).resolve()
    reviewed = load_future_holdout_contract() if contract is None else contract
    registry_path = Path(__file__).resolve().parents[2] / "benchmarks/future_holdout_lane_registry.json"
    lanes = load_lane_registry(registry_path)
    lane = next((item for item in lanes if item.lane_id == lane_id), None)
    if lane is None:
        raise ValueError(f"unknown future holdout lane: {lane_id}")
    source_sha256 = holdout_source_sha256(Path(__file__).resolve().parents[2])
    holdout_root = root / reviewed.data_directory
    snapshot = _capture_holdout_data(holdout_root)
    sessions = tuple(session for session in snapshot.sessions if session >= lane.activation_session)
    data_sha256 = snapshot.sha256
    if not sessions:
        raise ValueError("future holdout replay requires at least one observed session")
    _session_dates(sessions, contract=reviewed)
    account = load_account(account_path)
    validate_prior_close_account(account.to_dict(), frozen_data_dir=root / "data/frozen")
    universe = load_ai_universe()
    user_symbols = universe.symbols
    required_symbols = tuple(sorted(set(user_symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)))

    with tempfile.TemporaryDirectory(prefix="uquant-holdout-overlay-") as temporary:
        overlay = Path(temporary) / "data"
        _materialize_overlay(root, overlay, snapshot)
        engine = ProductionEngine(overlay)
        engine._load(required_symbols)
        expected_sessions = tuple(
            str(value.date())
            for value in engine._raw[INDEX_SYMBOLS[0]].index.intersection(engine._raw[INDEX_SYMBOLS[1]].index)
            if str(value.date()) in set(sessions)
        )
        if expected_sessions != sessions:
            raise ValueError("holdout sessions are not complete across both market indices")
        if any(
            session not in {str(value.date()) for value in engine._raw[symbol].index}
            for symbol in required_symbols
            for session in sessions
        ):
            raise ValueError("holdout sessions are incomplete across the decision inventory")

        prior_date = pd.Timestamp(reviewed.last_in_sample_date)
        starting_values = {
            symbol: position.shares * engine._price(symbol, prior_date)
            for symbol, position in account.positions.items()
            if position.shares > 0
        }
        starting_equity = engine.equity(account, prior_date)
        initial_fill_count = len(account.fills)
        equities = [starting_equity]
        decisions: list[dict[str, object]] = []
        raw_user_panel = {symbol: engine._raw[symbol] for symbol in user_symbols}
        for session in sessions:
            replay_date = pd.Timestamp(session)
            engine.execution.execute_open(
                date=replay_date,
                account=account,
                panel=raw_user_panel,
            )
            equities.append(engine.equity(account, replay_date))
            decision = engine.decide(
                symbols=user_symbols,
                as_of=session,
                account=account,
            )
            account.pending_orders = list(decision.pending_orders)
            decisions.append(_decision_payload(decision))

        final_date = pd.Timestamp(sessions[-1])
        final_equity = engine.equity(account, final_date)
        final_values = {
            symbol: position.shares * engine._price(symbol, final_date)
            for symbol, position in account.positions.items()
            if position.shares > 0
        }
        new_fills = account.fills[initial_fill_count:]
        symbol_pnl = _period_symbol_pnl(
            starting_values=starting_values,
            final_values=final_values,
            fills=new_fills,
        )
        expected_profit = final_equity - starting_equity
        if abs(sum(symbol_pnl.values()) - expected_profit) > max(
            1e-6,
            abs(expected_profit) * 1e-10,
        ):
            raise RuntimeError("holdout symbol PnL does not reconcile to replay equity")
        concentration = symbol_pnl_concentration(symbol_pnl)
        filled_order_ids = {fill.order_id for fill in new_fills if fill.order_id}
        observed_metrics = _validated_score_values(
            {
                "final_wealth": final_equity / starting_equity,
                "max_drawdown": _drawdown(equities),
                "account_orders": len(filled_order_ids),
                "gross_turnover": sum(fill.gross_value for fill in new_fills) / starting_equity,
                **concentration,
            }
        )
        normalized_scores = _normalized_scores(
            observed_metrics if len(sessions) >= reviewed.review_milestones[0] else None,
            sessions=sessions,
            contract=reviewed,
        )

    records: tuple[JournalRecord, ...]
    if journal_path is None:
        if trusted_journal_checkpoint is not None and trusted_journal_checkpoint.sequence:
            raise ValueError("journal path is required after a trusted checkpoint exists")
        records = ()
    else:
        records = read_execution_journal(
            journal_path,
            trusted_checkpoint=trusted_journal_checkpoint,
        )
    checkpoint = execution_journal_checkpoint(records)
    reached = [value for value in reviewed.review_milestones if len(sessions) >= value]
    next_milestone = next(
        (value for value in reviewed.review_milestones if value > len(sessions)),
        None,
    )
    replay: dict[str, Any] = {
        "schema_version": 2,
        "replay_id": "phase2-future-holdout-replay-v2",
        "contract_sha256": reviewed.sha256,
        "production_source_sha256": source_sha256,
        "holdout_data_sha256": data_sha256,
        "prior_close_account_sha256": reviewed.prior_close_account_sha256,
        "sessions": list(sessions),
        "lane_binding": lane_binding_payload(lane),
        "decision_digests": [str(item["decision_digest"]) for item in decisions],
        "decisions": decisions,
        "journal_checkpoint": asdict(checkpoint),
        "milestones": {
            "fixed": list(reviewed.review_milestones),
            "reached": reached,
            "next": next_milestone,
            "review_action": "REPORT_ONLY",
        },
        "score_status": (f"MILESTONE_{reached[-1]}_REVIEWABLE" if reached else "NON_REVIEWABLE"),
        "observed_metrics": observed_metrics,
        "scores": normalized_scores,
        "final_account_sha256": _canonical_sha256(account.to_dict()),
    }
    replay["canonical_sha256"] = _canonical_sha256(replay)
    return replay


def read_future_holdout_replay(
    path: str | Path,
    *,
    contract: FutureHoldoutContract,
    sessions: Sequence[str],
    holdout_data_sha256: str,
) -> dict[str, Any]:
    """Read back and validate the complete deterministic replay artifact."""

    raw = _read_json(Path(path), label="future holdout replay")
    if set(raw) != _REPLAY_FIELDS:
        raise ValueError("future holdout replay schema is malformed")
    seal = raw.get("canonical_sha256")
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    if not isinstance(seal, str) or seal != _canonical_sha256(unsealed):
        raise ValueError("future holdout replay hash is invalid")
    source_sha256 = raw.get("production_source_sha256")
    if not isinstance(source_sha256, str) or source_sha256 != holdout_source_sha256(
        Path(__file__).resolve().parents[2]
    ):
        raise ValueError("future holdout replay source binding is stale")
    expected_sessions = tuple(sessions)
    _session_dates(expected_sessions, contract=contract)
    if (
        raw.get("schema_version") != 2
        or raw.get("replay_id") != "phase2-future-holdout-replay-v2"
        or raw.get("contract_sha256") != contract.sha256
        or raw.get("holdout_data_sha256") != holdout_data_sha256
        or raw.get("prior_close_account_sha256") != contract.prior_close_account_sha256
        or tuple(raw.get("sessions", ())) != expected_sessions
    ):
        raise ValueError("future holdout replay binding is stale")
    lanes = load_lane_registry(
        Path(__file__).resolve().parents[2] / "benchmarks/future_holdout_lane_registry.json"
    )
    lane = next(
        (item for item in lanes if lane_binding_payload(item) == raw.get("lane_binding")),
        None,
    )
    if lane is None or any(session < lane.activation_session for session in expected_sessions):
        raise ValueError("future holdout replay lane binding is stale")
    digests = raw.get("decision_digests")
    decisions = raw.get("decisions")
    if (
        not isinstance(digests, list)
        or not isinstance(decisions, list)
        or len(digests) != len(expected_sessions)
        or len(decisions) != len(expected_sessions)
        or any(not isinstance(item, str) or len(item) != 64 for item in digests)
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"date", "decision_digest", "payload"}
            or item.get("date") != session
            or item.get("decision_digest") != digest
            or not isinstance(item.get("payload"), Mapping)
            or cast(Mapping[str, object], item["payload"]).get("date") != session
            or _decision_payload_sha256(cast(Mapping[str, object], item["payload"])) != digest
            for item, session, digest in zip(
                decisions,
                expected_sessions,
                digests,
                strict=True,
            )
        )
    ):
        raise ValueError("future holdout replay decisions are malformed")
    journal = raw.get("journal_checkpoint")
    if (
        not isinstance(journal, Mapping)
        or set(journal) != {"schema_version", "sequence", "record_sha256"}
        or journal.get("schema_version") != 1
        or not isinstance(journal.get("sequence"), int)
        or cast(int, journal["sequence"]) < 0
        or not isinstance(journal.get("record_sha256"), str)
        or len(cast(str, journal["record_sha256"])) != 64
    ):
        raise ValueError("future holdout replay journal checkpoint is malformed")
    milestones = raw.get("milestones")
    reached = [value for value in contract.review_milestones if len(expected_sessions) >= value]
    next_milestone = next(
        (value for value in contract.review_milestones if value > len(expected_sessions)),
        None,
    )
    if milestones != {
        "fixed": list(contract.review_milestones),
        "reached": reached,
        "next": next_milestone,
        "review_action": "REPORT_ONLY",
    }:
        raise ValueError("future holdout replay milestone policy is malformed")
    scores = raw.get("scores")
    if not isinstance(scores, Mapping):
        raise ValueError("future holdout replay scores are malformed")
    _normalized_scores(scores, sessions=expected_sessions, contract=contract)
    observed_metrics = raw.get("observed_metrics")
    if not isinstance(observed_metrics, Mapping):
        raise ValueError("future holdout replay observed metrics are malformed")
    _validated_score_values(observed_metrics)
    expected_score_status = f"MILESTONE_{reached[-1]}_REVIEWABLE" if reached else "NON_REVIEWABLE"
    if raw.get("score_status") != expected_score_status:
        raise ValueError("future holdout replay score status is malformed")
    final_account_sha256 = raw.get("final_account_sha256")
    if not isinstance(final_account_sha256, str) or len(final_account_sha256) != 64:
        raise ValueError("future holdout replay final account identity is malformed")
    return raw


def _checkpoint_payload(
    replay: Mapping[str, Any],
    *,
    replay_output_path: str | Path,
    replay_output_bytes: bytes,
    decision_output_path: str | Path,
    decision_output_bytes: bytes,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 2,
        "checkpoint_id": "phase2-future-holdout-daily-checkpoint-v2",
        "contract_sha256": replay.get("contract_sha256"),
        "production_source_sha256": replay.get("production_source_sha256"),
        "prior_close_account_sha256": replay.get("prior_close_account_sha256"),
        "holdout_data_sha256": replay.get("holdout_data_sha256"),
        "sessions": replay.get("sessions"),
        "decision_digests": replay.get("decision_digests"),
        "replay_canonical_sha256": replay.get("canonical_sha256"),
        "replay_output_path": _resolved_path_text(replay_output_path),
        "replay_output_sha256": hashlib.sha256(replay_output_bytes).hexdigest(),
        "decision_output_path": _resolved_path_text(decision_output_path),
        "decision_output_sha256": hashlib.sha256(decision_output_bytes).hexdigest(),
        "journal_checkpoint": replay.get("journal_checkpoint"),
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def _read_checkpoint_carrier(
    path: str | Path,
    *,
    contract: FutureHoldoutContract,
) -> tuple[dict[str, Any], JournalCheckpoint] | None:
    source = Path(path)
    if not source.exists():
        return None
    raw = _read_json(source, label="future holdout journal checkpoint")
    seal = raw.get("canonical_sha256")
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    if (
        set(raw) != _CHECKPOINT_FIELDS
        or not isinstance(seal, str)
        or seal != _canonical_sha256(unsealed)
        or raw.get("schema_version") != 2
        or raw.get("checkpoint_id") != "phase2-future-holdout-daily-checkpoint-v2"
        or raw.get("contract_sha256") != contract.sha256
        or raw.get("production_source_sha256") != holdout_source_sha256(Path(__file__).resolve().parents[2])
        or raw.get("prior_close_account_sha256") != contract.prior_close_account_sha256
        or not isinstance(raw.get("holdout_data_sha256"), str)
        or len(cast(str, raw["holdout_data_sha256"])) != 64
        or not isinstance(raw.get("replay_canonical_sha256"), str)
        or len(cast(str, raw["replay_canonical_sha256"])) != 64
        or not isinstance(raw.get("replay_output_path"), str)
        or not Path(cast(str, raw["replay_output_path"])).is_absolute()
        or not isinstance(raw.get("replay_output_sha256"), str)
        or len(cast(str, raw["replay_output_sha256"])) != 64
        or not isinstance(raw.get("decision_output_path"), str)
        or not Path(cast(str, raw["decision_output_path"])).is_absolute()
        or not isinstance(raw.get("decision_output_sha256"), str)
        or len(cast(str, raw["decision_output_sha256"])) != 64
    ):
        raise ValueError("future holdout journal checkpoint carrier is invalid")
    sessions = raw.get("sessions")
    digests = raw.get("decision_digests")
    if (
        not isinstance(sessions, list)
        or not sessions
        or any(not isinstance(value, str) for value in sessions)
        or _session_dates(sessions, contract=contract) != tuple(sessions)
        or not isinstance(digests, list)
        or len(digests) != len(sessions)
        or any(not isinstance(value, str) or len(value) != 64 for value in digests)
    ):
        raise ValueError("future holdout journal checkpoint history is malformed")
    checkpoint = raw.get("journal_checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise ValueError("future holdout journal checkpoint is malformed")
    try:
        trusted = JournalCheckpoint(**dict(checkpoint))
    except (TypeError, ValueError) as exc:
        raise ValueError("future holdout journal checkpoint is malformed") from exc
    return raw, trusted


def _verify_checkpoint_artifacts(
    checkpoint: Mapping[str, Any],
    *,
    contract: FutureHoldoutContract,
) -> dict[str, Any]:
    replay_path = cast(str, checkpoint["replay_output_path"])
    try:
        before = _read_protected_artifact(
            replay_path,
            label="prior deterministic replay artifact",
        )
        if hashlib.sha256(before).hexdigest() != checkpoint["replay_output_sha256"]:
            raise ValueError("prior deterministic replay artifact hash changed")
        replay = read_future_holdout_replay(
            replay_path,
            contract=contract,
            sessions=cast(Sequence[str], checkpoint["sessions"]),
            holdout_data_sha256=cast(str, checkpoint["holdout_data_sha256"]),
        )
        after = _read_protected_artifact(
            replay_path,
            label="prior deterministic replay artifact",
        )
        if before != after:
            raise ValueError("prior deterministic replay artifact changed during readback")
        if (
            replay["canonical_sha256"] != checkpoint["replay_canonical_sha256"]
            or replay["decision_digests"] != checkpoint["decision_digests"]
            or replay["journal_checkpoint"] != checkpoint["journal_checkpoint"]
        ):
            raise ValueError("prior deterministic replay artifact checkpoint is stale")
    except (OSError, ValueError) as exc:
        raise ValueError("prior deterministic replay artifact is missing or changed") from exc

    decision_path = cast(str, checkpoint["decision_output_path"])
    try:
        before = _read_protected_artifact(
            decision_path,
            label="prior daily decision artifact",
        )
        if hashlib.sha256(before).hexdigest() != checkpoint["decision_output_sha256"]:
            raise ValueError("prior daily decision artifact hash changed")
        read_future_holdout_decision(decision_path, replay=replay)
        after = _read_protected_artifact(
            decision_path,
            label="prior daily decision artifact",
        )
        if before != after:
            raise ValueError("prior daily decision artifact changed during readback")
    except (OSError, ValueError) as exc:
        raise ValueError("prior daily decision artifact is missing or changed") from exc
    return replay


def _validate_daily_replay_continuity(
    replay: Mapping[str, Any],
    *,
    prior_checkpoint: Mapping[str, Any] | None,
    contract: FutureHoldoutContract,
) -> None:
    sessions = replay.get("sessions")
    digests = replay.get("decision_digests")
    if (
        not isinstance(sessions, list)
        or not sessions
        or any(not isinstance(value, str) for value in sessions)
        or _session_dates(sessions, contract=contract) != tuple(sessions)
        or not isinstance(digests, list)
        or len(digests) != len(sessions)
    ):
        raise ValueError("future holdout replay daily history is malformed")
    if prior_checkpoint is None:
        if len(sessions) != 1:
            raise ValueError("future holdout replay requires exactly one uncheckpointed daily session")
        return

    prior_sessions = cast(list[str], prior_checkpoint["sessions"])
    prior_digests = cast(list[str], prior_checkpoint["decision_digests"])
    if sessions[: len(prior_sessions)] != prior_sessions:
        raise ValueError("future holdout replay changed the checkpointed session prefix")
    if len(sessions) not in {len(prior_sessions), len(prior_sessions) + 1}:
        raise ValueError("future holdout replay requires exactly one uncheckpointed daily session")
    if digests[: len(prior_digests)] != prior_digests:
        raise ValueError("future holdout replay changed a checkpointed daily decision")
    if (
        len(sessions) == len(prior_sessions)
        and replay.get("holdout_data_sha256") != prior_checkpoint["holdout_data_sha256"]
    ):
        raise ValueError("future holdout replay changed the checkpointed data prefix")


def _daily_decision_payload(replay: Mapping[str, Any]) -> dict[str, Any]:
    sessions = replay.get("sessions")
    decisions = replay.get("decisions")
    if not isinstance(sessions, list) or not sessions or not isinstance(decisions, list):
        raise ValueError("future holdout replay cannot produce a daily decision")
    latest: dict[str, Any] = {
        "schema_version": 1,
        "decision_id": "phase2-future-holdout-daily-decision-v1",
        "contract_sha256": replay.get("contract_sha256"),
        "production_source_sha256": replay.get("production_source_sha256"),
        "holdout_data_sha256": replay.get("holdout_data_sha256"),
        "prior_close_account_sha256": replay.get("prior_close_account_sha256"),
        "replay_canonical_sha256": replay.get("canonical_sha256"),
        "session": sessions[-1],
        "decision": decisions[-1],
        "journal_checkpoint": replay.get("journal_checkpoint"),
        "milestones": replay.get("milestones"),
        "report_only": True,
    }
    latest["canonical_sha256"] = _canonical_sha256(latest)
    return latest


def _canonical_carrier_path(path: str | Path) -> Path:
    """Resolve lexical aliases only after rejecting every visible symlink component."""

    current = Path(path).absolute()
    while True:
        if current.is_symlink():
            raise ValueError("future holdout evidence artifact contains a symlink")
        if current == current.parent:
            break
        current = current.parent
    return Path(path).resolve(strict=False)


def _artifact_snapshots(paths: Sequence[Path]) -> dict[Path, _ArtifactSnapshot]:
    """Capture exact carrier bytes and modes before an evidence update."""

    snapshots: dict[Path, _ArtifactSnapshot] = {}
    for path in paths:
        if not path.is_absolute():
            raise ValueError("future holdout evidence artifact path is not canonical")
        current = path.absolute()
        while True:
            if current.is_symlink():
                raise ValueError("future holdout evidence artifact contains a symlink")
            if current == current.parent:
                break
            current = current.parent
        if not path.exists():
            snapshots[path] = _ArtifactSnapshot(payload=None, mode=None)
            continue
        payload = _read_protected_artifact(
            path,
            label="future holdout evidence artifact",
        )
        mode: int | None = None
        if os.name != "nt":
            try:
                status = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise ValueError("cannot inspect future holdout evidence artifact mode") from exc
            if not stat.S_ISREG(status.st_mode):
                raise ValueError("future holdout evidence artifact is unsafe")
            mode = stat.S_IMODE(status.st_mode)
        snapshots[path] = _ArtifactSnapshot(payload=payload, mode=mode)
    return snapshots


def _link_bytes_if_absent(
    path: Path,
    payload: bytes,
    *,
    mode: int | None = None,
) -> bool:
    """Publish exact bytes without replacing a concurrently installed generation."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.rollback-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    preserve_temporary = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        except BaseException as exc:
            preserve_temporary = True
            exc.add_note(f"rollback bytes preserved for recovery at {temporary}")
            raise
        return True
    finally:
        if not preserve_temporary:
            temporary.unlink(missing_ok=True)


def _restore_owned_artifact(
    path: Path,
    payload: bytes | None,
    expected: bytes,
    *,
    mode: int | None = None,
) -> None:
    """Atomically claim one generation, then restore without overwriting a successor."""

    descriptor, quarantine_name = tempfile.mkstemp(
        prefix=f".{path.name}.claimed-",
        dir=path.parent,
    )
    os.close(descriptor)
    quarantine = Path(quarantine_name)
    quarantine.unlink()
    claimed = False
    preserve_quarantine = False
    try:
        try:
            os.replace(path, quarantine)
            claimed = True
        except FileNotFoundError:
            return
        try:
            current = _read_protected_artifact(
                quarantine,
                label="future holdout rollback artifact",
            )
        except ValueError:
            with suppress(FileExistsError):
                os.link(quarantine, path, follow_symlinks=False)
            return
        if current != expected:
            with suppress(FileExistsError):
                os.link(quarantine, path, follow_symlinks=False)
            return
        if payload is not None:
            _link_bytes_if_absent(path, payload, mode=mode)
    except BaseException as exc:
        if claimed:
            preserve_quarantine = True
            exc.add_note(f"claimed carrier preserved for recovery at {quarantine}")
        raise
    finally:
        if not preserve_quarantine:
            quarantine.unlink(missing_ok=True)


def _restore_artifact_snapshots(
    snapshots: Mapping[Path, _ArtifactSnapshot],
    owned: Mapping[Path, bytes],
) -> tuple[BaseException, ...]:
    """Restore only carriers that still contain this transaction's bytes."""

    failures: list[BaseException] = []
    for path, snapshot in snapshots.items():
        expected = owned.get(path)
        if expected is None:
            continue
        try:
            _restore_owned_artifact(
                path,
                snapshot.payload,
                expected,
                mode=snapshot.mode,
            )
        except BaseException as exc:
            failures.append(exc)
    return tuple(failures)


def _artifact_bundle_lock_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    """Return globally stable lock identities for every canonical carrier."""

    locks = {
        Path(tempfile.gettempdir())
        / f"uquant-future-holdout-carrier-{hashlib.sha256(str(path).encode('utf-8')).hexdigest()}.lock"
        for path in paths
    }
    return tuple(sorted(locks, key=str))


@contextmanager
def _artifact_bundle_lock(
    repository_root: Path,
    carrier_paths: Sequence[Path] = (),
) -> Iterator[None]:
    """Serialize complete replay/decision/checkpoint evidence transactions."""

    lock_paths = tuple(
        sorted(
            {
                _artifact_bundle_lock_path(repository_root),
                *_artifact_bundle_lock_paths(carrier_paths),
            },
            key=str,
        )
    )
    descriptors: list[int] = []
    primary: BaseException | None = None
    try:
        for lock_path in lock_paths:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            current = lock_path.absolute()
            while True:
                if current.is_symlink():
                    raise ValueError("future holdout evidence lock contains a symlink")
                if current == current.parent:
                    break
                current = current.parent
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                raise ValueError("future holdout evidence lock is unsafe") from exc
            descriptors.append(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("future holdout evidence lock is unsafe")
        yield
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_failures: list[OSError] = []
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError as exc:
                cleanup_failures.append(exc)
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_failures.append(exc)
        if cleanup_failures:
            notes = tuple(
                f"future holdout lock cleanup also failed: {type(exc).__name__}: {exc}"
                for exc in cleanup_failures
            )
            if primary is not None:
                for note in notes:
                    primary.add_note(note)
            else:
                failure = RuntimeError("future holdout evidence lock cleanup failed")
                for note in notes:
                    failure.add_note(note)
                raise failure from cleanup_failures[0]


def _artifact_bundle_lock_path(repository_root: Path) -> Path:
    """Place the stable lock outside every repository evidence inventory."""

    identity = hashlib.sha256(str(repository_root.resolve()).encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / f"uquant-future-holdout-{identity}.lock"


def read_future_holdout_decision(
    path: str | Path,
    *,
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    """Read back a daily decision and require its full replay binding."""

    raw = _read_json(Path(path), label="future holdout daily decision")
    seal = raw.get("canonical_sha256")
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    if set(raw) != _DAILY_DECISION_FIELDS or not isinstance(seal, str) or seal != _canonical_sha256(unsealed):
        raise ValueError("future holdout daily decision hash is invalid")
    if raw != _daily_decision_payload(replay):
        raise ValueError("future holdout daily decision binding is stale")
    return raw


def _generate_future_holdout_replay_locked(
    *,
    repository_root: Path,
    account_path: str | Path,
    output_path: Path,
    decision_output_path: Path | None,
    checkpoint_path: Path,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate and persist evidence while the caller owns the bundle lock."""

    root = repository_root
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    destination = output_path
    decision_destination = decision_output_path
    protected_data = (root / "data/frozen", root / contract.data_directory)
    _reject_output_in_protected_data(
        destination,
        protected_directories=protected_data,
    )
    if decision_destination is not None:
        _reject_output_in_protected_data(
            decision_destination,
            protected_directories=protected_data,
        )
    _reject_authoritative_output_paths(
        repository_root=root,
        output_path=destination,
        decision_output_path=decision_destination,
        account_path=account_path,
        journal_path=journal_path,
        holdout_data_directory=contract.data_directory,
        checkpoint_path=checkpoint_path,
        lock_paths=(
            _artifact_bundle_lock_path(root),
            *_artifact_bundle_lock_paths(
                (
                    destination,
                    checkpoint_path,
                    *(() if decision_destination is None else (decision_destination,)),
                )
            ),
        ),
    )
    if decision_destination is None:
        raise ValueError("future holdout replay requires a daily decision output artifact")
    prior_checkpoint = _read_checkpoint_carrier(
        checkpoint_path,
        contract=contract,
    )
    prior_payload = None if prior_checkpoint is None else prior_checkpoint[0]
    if prior_payload is not None:
        if prior_payload["replay_output_path"] != _resolved_path_text(destination) or prior_payload[
            "decision_output_path"
        ] != _resolved_path_text(decision_destination):
            raise ValueError("future holdout replay must reuse the checkpointed output paths")
        _verify_checkpoint_artifacts(prior_payload, contract=contract)
    trusted_checkpoint = None if prior_checkpoint is None else prior_checkpoint[1]
    replay = replay_future_holdout(
        repository_root=root,
        account_path=account_path,
        journal_path=journal_path,
        trusted_journal_checkpoint=trusted_checkpoint,
        contract=contract,
    )
    holdout_root = root / contract.data_directory
    if holdout_root.exists():
        snapshot = _capture_holdout_data(holdout_root)
        _validated_snapshot_prefix_sha256(
            snapshot,
            prefix_sessions=snapshot.sessions,
        )
        replay_sessions = tuple(cast(Sequence[str], replay.get("sessions", ())))
        if snapshot.sessions != replay_sessions or snapshot.sha256 != replay.get("holdout_data_sha256"):
            raise ValueError("future holdout data changed during deterministic replay")
        if (
            prior_payload is not None
            and _validated_snapshot_prefix_sha256(
                snapshot,
                prefix_sessions=cast(Sequence[str], prior_payload["sessions"]),
            )
            != prior_payload["holdout_data_sha256"]
        ):
            raise ValueError("future holdout changed the checkpointed data prefix")
    _validate_daily_replay_continuity(
        replay,
        prior_checkpoint=prior_payload,
        contract=contract,
    )
    snapshots = _artifact_snapshots((destination, decision_destination, checkpoint_path))
    owned: dict[Path, bytes] = {}
    try:
        replay_text = json.dumps(replay, ensure_ascii=False, indent=2) + "\n"
        owned[destination] = replay_text.encode("utf-8")
        atomic_write_text(
            destination,
            replay_text,
            protected_paths=(
                account_path,
                root / "data/frozen",
                root / contract.data_directory,
                *(() if journal_path is None else (journal_path,)),
            ),
        )
        observed = read_future_holdout_replay(
            destination,
            contract=contract,
            sessions=tuple(replay["sessions"]),
            holdout_data_sha256=str(replay["holdout_data_sha256"]),
        )
        if observed != replay:
            raise RuntimeError("future holdout replay changed during readback")
        latest = _daily_decision_payload(replay)
        decision_text = json.dumps(latest, ensure_ascii=False, indent=2) + "\n"
        owned[decision_destination] = decision_text.encode("utf-8")
        atomic_write_text(
            decision_destination,
            decision_text,
            protected_paths=(
                destination,
                account_path,
                *(() if journal_path is None else (journal_path,)),
            ),
        )
        observed_decision = read_future_holdout_decision(
            decision_destination,
            replay=replay,
        )
        if observed_decision != latest:
            raise RuntimeError("future holdout daily decision changed during readback")
        replay_output_bytes = _read_protected_artifact(
            destination,
            label="deterministic replay artifact",
        )
        decision_output_bytes = _read_protected_artifact(
            decision_destination,
            label="daily decision artifact",
        )
        checkpoint = _checkpoint_payload(
            replay,
            replay_output_path=destination,
            replay_output_bytes=replay_output_bytes,
            decision_output_path=decision_destination,
            decision_output_bytes=decision_output_bytes,
        )
        checkpoint_text = json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n"
        owned[checkpoint_path] = checkpoint_text.encode("utf-8")
        atomic_write_text(
            checkpoint_path,
            checkpoint_text,
            protected_paths=(
                destination,
                account_path,
                decision_destination,
                *(() if journal_path is None else (journal_path,)),
            ),
        )
        observed_checkpoint = _read_checkpoint_carrier(
            checkpoint_path,
            contract=contract,
        )
        if observed_checkpoint is None or observed_checkpoint[0] != checkpoint:
            raise RuntimeError("future holdout journal checkpoint changed during readback")
        _verify_checkpoint_artifacts(checkpoint, contract=contract)
    except BaseException as primary:
        for failure in _restore_artifact_snapshots(snapshots, owned):
            primary.add_note(f"future holdout rollback also failed: {type(failure).__name__}: {failure}")
        raise
    return replay


def generate_future_holdout_replay(
    *,
    repository_root: str | Path,
    account_path: str | Path,
    output_path: str | Path,
    decision_output_path: str | Path | None = None,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate, atomically persist, and re-read the deterministic replay."""

    root = Path(repository_root).resolve()
    checkpoint = _canonical_carrier_path(root / _CHECKPOINT_RELATIVE)
    destination = _canonical_carrier_path(output_path)
    decision_destination = (
        None if decision_output_path is None else _canonical_carrier_path(decision_output_path)
    )
    carriers = (
        destination,
        checkpoint,
        *(() if decision_destination is None else (decision_destination,)),
    )
    with _artifact_bundle_lock(root, carriers):
        return _generate_future_holdout_replay_locked(
            repository_root=root,
            account_path=account_path,
            output_path=destination,
            decision_output_path=decision_destination,
            checkpoint_path=checkpoint,
            journal_path=journal_path,
        )
