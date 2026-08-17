"""Append-only future data and deterministic post-boundary replay."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import subprocess  # nosec B404
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import pandas as pd

from ..account import load_account
from ..atomic_io import atomic_write_text
from ..config import config_fingerprint
from ..engine import INDEX_SYMBOLS, ProductionEngine
from ..execution_journal import (
    JournalCheckpoint,
    JournalRecord,
    execution_journal_checkpoint,
    read_execution_journal,
)
from ..leader import REFERENCE_UNIVERSE
from ..types import Decision, Fill
from .generalization import symbol_pnl_concentration
from .holdout import (
    FutureHoldoutContract,
    _canonical_sha256,
    _closed_csv_files,
    _csv_dates_from_text,
    _normalized_scores,
    _read_json,
    _session_dates,
    holdout_data_identity,
    holdout_source_sha256,
    load_future_holdout_contract,
    validate_prior_close_account,
)
from .universe import load_ai_universe

_REPLAY_FIELDS = {
    "schema_version",
    "replay_id",
    "contract_sha256",
    "production_source_sha256",
    "holdout_data_sha256",
    "prior_close_account_sha256",
    "sessions",
    "decision_digests",
    "decisions",
    "journal_checkpoint",
    "milestones",
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
    "replay_canonical_sha256",
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


def _capture_holdout_data(root: Path) -> _HoldoutDataSnapshot:
    try:
        paths = _closed_csv_files(root, label="future holdout", missing_ok=False)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    digest = hashlib.sha256()
    sessions: set[str] = set()
    files: list[tuple[str, bytes]] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        relative_bytes = relative.encode()
        digest.update(len(relative_bytes).to_bytes(4, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        try:
            decoded = content.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError(f"cannot inspect market data: {path}") from exc
        sessions.update(_csv_dates_from_text(decoded, path=path))
        files.append((relative, content))
    return _HoldoutDataSnapshot(
        sessions=tuple(sorted(sessions)),
        sha256=digest.hexdigest(),
        files=tuple(files),
    )


def _reject_output_in_protected_data(
    output: str | Path,
    *,
    protected_directories: Sequence[str | Path],
) -> None:
    target = Path(output).resolve(strict=False)
    for protected in protected_directories:
        directory = Path(protected).resolve(strict=False)
        if target == directory or target.is_relative_to(directory):
            raise ValueError(
                f"holdout output is inside a protected data directory: {directory}"
            )


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
                raise ValueError(
                    f"holdout output overlaps an authoritative path: {authoritative}"
                )
    carrier_protected = [
        Path(account_path),
        *outputs,
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
            raise ValueError(
                f"holdout checkpoint overlaps an authoritative path: {authoritative}"
            )


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
        column
        for column in ("open", "high", "low", "close", "volume", "amount")
        if column in header
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
        frozen_header = tuple(
            next(csv.reader(frozen[name].read_text(encoding="utf-8").splitlines()))
        )
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
    if len(existing_sessions) >= len(reviewed.review_sessions) or session != (
        reviewed.review_sessions[len(existing_sessions)]
    ):
        raise ValueError("holdout snapshot must be the next contracted exchange session")

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
                    raise ValueError(
                        f"future market row conflicts with an existing date: {relative}"
                    )
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
        "payload": decision.canonical_payload(
            effective_config_sha256=config_fingerprint()
        ),
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
) -> dict[str, Any]:
    """Replay every observed session from the authenticated prior-close account."""

    root = Path(repository_root).resolve()
    reviewed = load_future_holdout_contract() if contract is None else contract
    source_sha256 = holdout_source_sha256(Path(__file__).resolve().parents[2])
    holdout_root = root / reviewed.data_directory
    snapshot = _capture_holdout_data(holdout_root)
    sessions, data_sha256 = snapshot.sessions, snapshot.sha256
    if not sessions:
        raise ValueError("future holdout replay requires at least one observed session")
    _session_dates(sessions, contract=reviewed)
    account = load_account(account_path)
    validate_prior_close_account(account.to_dict(), frozen_data_dir=root / "data/frozen")
    universe = load_ai_universe()
    user_symbols = universe.symbols
    required_symbols = tuple(
        sorted(set(user_symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS))
    )

    with tempfile.TemporaryDirectory(prefix="uquant-holdout-overlay-") as temporary:
        overlay = Path(temporary) / "data"
        _materialize_overlay(root, overlay, snapshot)
        engine = ProductionEngine(overlay)
        engine._load(required_symbols)
        expected_sessions = tuple(
            str(value.date())
            for value in engine._raw[INDEX_SYMBOLS[0]].index.intersection(
                engine._raw[INDEX_SYMBOLS[1]].index
            )
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
        scores: dict[str, float | int | None] = {
            "final_wealth": final_equity / starting_equity,
            "max_drawdown": _drawdown(equities),
            "account_orders": len(filled_order_ids),
            "gross_turnover": sum(fill.gross_value for fill in new_fills)
            / starting_equity,
            **concentration,
        }
        normalized_scores = _normalized_scores(
            scores,
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
        "schema_version": 1,
        "replay_id": "phase2-future-holdout-replay-v1",
        "contract_sha256": reviewed.sha256,
        "production_source_sha256": source_sha256,
        "holdout_data_sha256": data_sha256,
        "prior_close_account_sha256": reviewed.prior_close_account_sha256,
        "sessions": list(sessions),
        "decision_digests": [str(item["decision_digest"]) for item in decisions],
        "decisions": decisions,
        "journal_checkpoint": asdict(checkpoint),
        "milestones": {
            "fixed": list(reviewed.review_milestones),
            "reached": reached,
            "next": next_milestone,
            "review_action": "REPORT_ONLY",
        },
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
    if (
        not isinstance(source_sha256, str)
        or source_sha256 != holdout_source_sha256(Path(__file__).resolve().parents[2])
    ):
        raise ValueError("future holdout replay source binding is stale")
    expected_sessions = tuple(sessions)
    _session_dates(expected_sessions, contract=contract)
    if (
        raw.get("schema_version") != 1
        or raw.get("replay_id") != "phase2-future-holdout-replay-v1"
        or raw.get("contract_sha256") != contract.sha256
        or raw.get("holdout_data_sha256") != holdout_data_sha256
        or raw.get("prior_close_account_sha256")
        != contract.prior_close_account_sha256
        or tuple(raw.get("sessions", ())) != expected_sessions
    ):
        raise ValueError("future holdout replay binding is stale")
    digests = raw.get("decision_digests")
    decisions = raw.get("decisions")
    if (
        not isinstance(digests, list)
        or not isinstance(decisions, list)
        or len(digests) != len(expected_sessions)
        or len(decisions) != len(expected_sessions)
        or any(
            not isinstance(item, str) or len(item) != 64
            for item in digests
        )
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"date", "decision_digest", "payload"}
            or item.get("date") != session
            or item.get("decision_digest") != digest
            or not isinstance(item.get("payload"), Mapping)
            or cast(Mapping[str, object], item["payload"]).get("date") != session
            or _decision_payload_sha256(
                cast(Mapping[str, object], item["payload"])
            )
            != digest
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
    final_account_sha256 = raw.get("final_account_sha256")
    if not isinstance(final_account_sha256, str) or len(final_account_sha256) != 64:
        raise ValueError("future holdout replay final account identity is malformed")
    return raw


def _checkpoint_payload(replay: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "checkpoint_id": "phase2-future-holdout-journal-checkpoint-v1",
        "contract_sha256": replay.get("contract_sha256"),
        "production_source_sha256": replay.get("production_source_sha256"),
        "prior_close_account_sha256": replay.get("prior_close_account_sha256"),
        "holdout_data_sha256": replay.get("holdout_data_sha256"),
        "replay_canonical_sha256": replay.get("canonical_sha256"),
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
        or raw.get("schema_version") != 1
        or raw.get("checkpoint_id")
        != "phase2-future-holdout-journal-checkpoint-v1"
        or raw.get("contract_sha256") != contract.sha256
        or raw.get("production_source_sha256")
        != holdout_source_sha256(Path(__file__).resolve().parents[2])
        or raw.get("prior_close_account_sha256")
        != contract.prior_close_account_sha256
        or not isinstance(raw.get("holdout_data_sha256"), str)
        or len(cast(str, raw["holdout_data_sha256"])) != 64
        or not isinstance(raw.get("replay_canonical_sha256"), str)
        or len(cast(str, raw["replay_canonical_sha256"])) != 64
    ):
        raise ValueError("future holdout journal checkpoint carrier is invalid")
    checkpoint = raw.get("journal_checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise ValueError("future holdout journal checkpoint is malformed")
    try:
        trusted = JournalCheckpoint(**dict(checkpoint))
    except (TypeError, ValueError) as exc:
        raise ValueError("future holdout journal checkpoint is malformed") from exc
    return raw, trusted


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


def read_future_holdout_decision(
    path: str | Path,
    *,
    replay: Mapping[str, Any],
) -> dict[str, Any]:
    """Read back a daily decision and require its full replay binding."""

    raw = _read_json(Path(path), label="future holdout daily decision")
    seal = raw.get("canonical_sha256")
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    if (
        set(raw) != _DAILY_DECISION_FIELDS
        or not isinstance(seal, str)
        or seal != _canonical_sha256(unsealed)
    ):
        raise ValueError("future holdout daily decision hash is invalid")
    if raw != _daily_decision_payload(replay):
        raise ValueError("future holdout daily decision binding is stale")
    return raw


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
    contract = load_future_holdout_contract(
        root / "benchmarks/future_holdout_contract.json"
    )
    checkpoint_path = root / _CHECKPOINT_RELATIVE
    protected_data = (root / "data/frozen", root / contract.data_directory)
    _reject_output_in_protected_data(
        output_path,
        protected_directories=protected_data,
    )
    if decision_output_path is not None:
        _reject_output_in_protected_data(
            decision_output_path,
            protected_directories=protected_data,
        )
    _reject_authoritative_output_paths(
        repository_root=root,
        output_path=output_path,
        decision_output_path=decision_output_path,
        account_path=account_path,
        journal_path=journal_path,
        holdout_data_directory=contract.data_directory,
        checkpoint_path=checkpoint_path,
    )
    prior_checkpoint = _read_checkpoint_carrier(
        checkpoint_path,
        contract=contract,
    )
    trusted_checkpoint = None if prior_checkpoint is None else prior_checkpoint[1]
    replay = replay_future_holdout(
        repository_root=root,
        account_path=account_path,
        journal_path=journal_path,
        trusted_journal_checkpoint=trusted_checkpoint,
        contract=contract,
    )
    destination = Path(output_path)
    atomic_write_text(
        destination,
        json.dumps(replay, ensure_ascii=False, indent=2) + "\n",
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
    if decision_output_path is not None:
        latest = _daily_decision_payload(replay)
        atomic_write_text(
            decision_output_path,
            json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
            protected_paths=(
                destination,
                account_path,
                *(() if journal_path is None else (journal_path,)),
            ),
        )
        observed_decision = read_future_holdout_decision(
            decision_output_path,
            replay=replay,
        )
        if observed_decision != latest:
            raise RuntimeError("future holdout daily decision changed during readback")
    checkpoint = _checkpoint_payload(replay)
    atomic_write_text(
        checkpoint_path,
        json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
        protected_paths=(
            destination,
            account_path,
            *(() if decision_output_path is None else (decision_output_path,)),
            *(() if journal_path is None else (journal_path,)),
        ),
    )
    observed_checkpoint = _read_checkpoint_carrier(
        checkpoint_path,
        contract=contract,
    )
    if observed_checkpoint is None or observed_checkpoint[0] != checkpoint:
        raise RuntimeError("future holdout journal checkpoint changed during readback")
    return replay
