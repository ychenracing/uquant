"""Historical, role-explicit diagnostics through the production decision seam.

This module produces research evidence, never promotion or production inputs.
The simple comparison benchmark and candidate gates are fixed separately before
any new economic candidate is evaluated.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re

# Only a fixed read-only git identity command is executed, without a shell.
import subprocess  # nosec B404
import tempfile
import time
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, BinaryIO

import pandas as pd

from uquant.account import account_from_dict
from uquant.attribution import build_daily_ledger_row, build_economic_attribution
from uquant.config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.contracts.universe import decision_ai_universe, default_ai_universe
from uquant.engine import INDEX_SYMBOLS, ProductionEngine, code_fingerprint, performance_metrics
from uquant.market import ReplayUniverse
from uquant.models.strategic_universe import build_strategic_universe_declaration
from uquant.types import AccountState
from uquant.validation.manifest import verify_data_manifest

CASE_IDS = ("champion", "full", "remove_all_three", "no_optical")
CHAMPION_SYMBOLS = ("sh603986", "sh688008", "sz300308", "sz300394", "sz300502")
CORE_SYMBOLS = frozenset({"sz300308", "sz300394", "sz300502"})
ROOT = Path(__file__).resolve().parents[1]


def case_symbols(
    case_id: str, as_of: str, *, extra_excluded_symbols: tuple[str, ...] = (),
    risk_reference_additions: tuple[str, ...] = (),
) -> dict[str, tuple[str, ...]]:
    """Bind every stock role to the same causal removal, preserving indices."""
    if case_id not in CASE_IDS:
        raise ValueError(f"unknown cross-AI case: {case_id}")
    if any(not isinstance(symbol, str) or not re.fullmatch(r"(?:sh|sz)[0-9]{6}", symbol)
           or symbol in INDEX_SYMBOLS for symbol in (*extra_excluded_symbols, *risk_reference_additions)):
        raise ValueError("extra exclusions must be canonical stock symbols, never market indexes")
    universe = decision_ai_universe()
    if set((*extra_excluded_symbols, *risk_reference_additions)) - {member.symbol for member in universe.members}:
        raise ValueError("extra exclusions must belong to the frozen stock universe")
    available = universe.symbols_as_of(as_of)
    removed = (
        CORE_SYMBOLS if case_id == "remove_all_three" else
        frozenset(symbol for symbol in available if universe.industry_of(symbol, as_of) == "optical")
        if case_id == "no_optical" else frozenset()
    )
    removed = removed | frozenset(extra_excluded_symbols)
    references = tuple(symbol for symbol in available if symbol not in removed)
    tradable = (
        tuple(symbol for symbol in CHAMPION_SYMBOLS if symbol in available and symbol not in removed)
        if case_id == "champion" else references
    )
    risk = tuple(sorted(set(references) | (set(risk_reference_additions) & set(available))))
    return {"tradable": tradable, "qualification": references, "risk": risk, "indexes": INDEX_SYMBOLS}


def diagnostic_json(value: Any) -> Any:
    """Thaw immutable observations; preserve explicit unavailable diagnostics."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: diagnostic_json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): diagnostic_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [diagnostic_json(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


@contextmanager
def _atomic_evidence_file(path: Path) -> Iterator[BinaryIO]:
    """Publish only complete files; never overwrite evidence, even on recovery."""
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic publication, refusing an existing destination.
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)  # Only this invocation's private temporary.


def _save_observation(directory: Path, identity: dict[str, Any], observation: dict[str, Any]) -> None:
    payload = canonical_json_bytes(observation)
    envelope = {
        "identity_sha256": hashlib.sha256(canonical_json_bytes(identity)).hexdigest(),
        "observation_sha256": hashlib.sha256(payload).hexdigest(),
        "observation": observation,
    }
    with _atomic_evidence_file(directory / f"{observation['date']}.json") as stream:
        stream.write(canonical_json_bytes(envelope) + b"\n")


def rebuild_observation_archive(
    output_dir: Path, destination: Path, *, completed_sessions: int | None = None,
) -> None:
    """Package saved real days without replaying or changing a failed result."""
    identity = strict_json_loads((output_dir / "identity.json").read_bytes())
    if not isinstance(identity, dict):
        raise ValueError("observation identity must be an object")
    all_dates = identity["session_dates"]
    if (not isinstance(all_dates, list) or not all(isinstance(date, str) and
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) for date in all_dates)
            or all_dates != sorted(set(all_dates))):
        raise ValueError("observation session dates must be unique and ordered")
    if completed_sessions is not None and (type(completed_sessions) is not int
            or not 0 <= completed_sessions <= len(all_dates)):
        raise ValueError("invalid completed observation count")
    dates = all_dates if completed_sessions is None else all_dates[:completed_sessions]
    directory = output_dir / "observations"
    if {path.name for path in directory.iterdir()} != {f"{date}.json" for date in dates}:
        raise ValueError("saved observation files differ from expected sessions")
    identity_hash = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    expected_hashes = []
    with _atomic_evidence_file(destination) as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            for date in dates:
                envelope = strict_json_loads((directory / f"{date}.json").read_bytes())
                if not isinstance(envelope, dict) or envelope.get("identity_sha256") != identity_hash:
                    raise ValueError("saved observation identity mismatch")
                observation = envelope.get("observation")
                if not isinstance(observation, dict) or observation.get("date") != date:
                    raise ValueError("saved observation date mismatch")
                payload = canonical_json_bytes(observation)
                digest = hashlib.sha256(payload).hexdigest()
                if digest != envelope.get("observation_sha256"):
                    raise ValueError("saved observation content hash mismatch")
                expected_hashes.append(digest)
                compressed.write(payload + b"\n")
        raw.flush()
        raw.seek(0)
        with gzip.GzipFile(mode="rb", fileobj=raw) as compressed:
            actual_hashes = [hashlib.sha256(line.removesuffix(b"\n")).hexdigest()
                             for line in compressed]
        if actual_hashes != expected_hashes:
            raise ValueError("rebuilt observation archive differs from saved days")


def case_identity(
    case_id: str, start: str, end: str, cfg: SystemConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()  # nosec B603, B607
    return {
        "case_id": case_id,
        "start": start,
        "end": end,
        "source_sha256": code_fingerprint(),
        "config_sha256": config_fingerprint(cfg),
        "runtime": runtime_environment_provenance(ROOT),
        "data": verify_data_manifest(ROOT / "data/frozen"),
        "universe_sha256": hashlib.sha256(
            (ROOT / "uquant/contracts/resources/ai_universe_manifest.json").read_bytes()
        ).hexdigest(),
        **({"research_universe_sha256": decision_ai_universe().sha256}
           if decision_ai_universe() != default_ai_universe() else {}),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "commit": commit,
    }


def run_production_case(
    *, case_id: str, start: str, end: str, output_dir: Path,
    cfg: SystemConfig = DEFAULT_CONFIG,
    initial_cash: float | None = None,
    start_session_offset: int = 0,
    extra_excluded_symbols: tuple[str, ...] = (),
    risk_reference_additions: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Replay one frozen historical case with exact daily observations and fills."""
    if not "2023-01-03" <= start <= end <= "2026-08-05":
        raise ValueError("cross-AI diagnostics require the frozen historical interval")
    pd.Timestamp(start)
    pd.Timestamp(end)
    if type(start_session_offset) is not int or start_session_offset < 0:
        raise ValueError("start_session_offset must be a nonnegative integer")
    if initial_cash is not None:
        if isinstance(initial_cash, bool) or not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        cfg = cfg.override(initial_cash=float(initial_cash))
    exclusions = tuple(sorted(set(extra_excluded_symbols)))
    risk_additions = tuple(sorted(set(risk_reference_additions)))
    case_symbols(case_id, start, extra_excluded_symbols=exclusions, risk_reference_additions=risk_additions)
    output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    identity = case_identity(case_id, start, end, cfg)
    identity.update(effective_config=cfg.to_dict(), initial_cash=cfg.initial_cash,
                    start_session_offset=start_session_offset, extra_excluded_symbols=list(exclusions))
    if risk_additions:
        identity["risk_reference_additions"] = list(risk_additions)
    write_json(output_dir / "identity.json", identity)
    engine = ProductionEngine(ROOT / "data/frozen", cfg=cfg)
    engine.workspace.prepare(ReplayUniverse.from_symbols(
        tradable_symbols=(), reference_symbols=(), index_symbols=INDEX_SYMBOLS,
    ))
    sessions = engine.workspace.common_sessions(*INDEX_SYMBOLS)
    sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))][start_session_offset:]
    identity["session_dates"] = [str(date.date()) for date in sessions]
    identity["effective_start"] = identity["session_dates"][0] if len(sessions) else ""
    write_json(output_dir / "identity.json", identity)
    account = AccountState.empty(cfg.initial_cash)
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    daily_ledger: list[dict[str, Any]] = []
    previous_equity = account.initial_cash
    status, error = "COMPLETE", ""
    raw_path = output_dir / "observations.jsonl.gz"
    observation_directory = output_dir / "observations"
    observation_directory.mkdir(exist_ok=False)
    try:
        if len(sessions) < 2:
            raise ValueError("historical interval has fewer than two sessions")
        for date in sessions:
            session = str(date.date())
            roles = case_symbols(case_id, session, extra_excluded_symbols=exclusions,
                                 risk_reference_additions=risk_additions)
            engine.workspace.prepare(ReplayUniverse.from_symbols(
                tradable_symbols=roles["tradable"],
                reference_symbols=roles["risk"], index_symbols=roles["indexes"],
            ))
            fill_start = len(account.fills)
            engine.execution.execute_open(
                date=date, account=account,
                panel={symbol: engine.workspace.raw_frame(symbol) for symbol in roles["tradable"]},
            )
            equity = engine.equity(account, date)
            observed = engine._observe_decision(
                symbols=roles["tradable"], as_of=session, account=account,
                strategic_universe_declaration=build_strategic_universe_declaration(
                    qualification_reference_symbols=roles["qualification"],
                    risk_reference_symbols=roles["risk"],
                ),
            )
            decision = observed.decision
            actual_roles = observed.observation.strategic_universe_roles
            if (
                actual_roles.tradable_symbols != roles["tradable"]
                or actual_roles.qualification_reference_symbols != roles["qualification"]
                or actual_roles.risk_reference_symbols != tuple(sorted(roles["risk"] + roles["indexes"]))
                or set(engine.workspace.loaded_symbols) - set(roles["risk"] + roles["indexes"])
            ):
                raise RuntimeError("production stock roles differ from declared historical scenario")
            if not math.isfinite(equity) or not math.isfinite(account.cash) or account.cash < -1e-6:
                raise RuntimeError("nonfinite equity or invalid cash in production replay")
            if any(position.shares < 0 for position in account.positions.values()):
                raise RuntimeError("negative production position")
            account.pending_orders = list(decision.pending_orders)
            prices = {
                symbol: engine.workspace.price(symbol, date)
                for symbol, position in account.positions.items() if position.shares > 0
            }
            ledger_row = build_daily_ledger_row(
                date=session, account=account, close_prices=prices, previous_equity=previous_equity,
                target_weights={target.symbol: target.weight for target in decision.targets},
                target_gross=decision.target_gross,
                risk_gross_cap=float(decision.risk_summary["target_gross_cap"]),
                system_gross_cap=float(decision.risk_summary["system_gross_cap"]),
                risk_state=decision.risk.value, opportunity=decision.opportunity.value,
            )
            state = {
                field.name: getattr(account, field.name) for field in fields(account)
                if field.name.startswith("strategic_") or field.name in {
                    "active_leaders", "leader_tenure", "candidate_tenure", "replacement_tenure",
                    "anchor_weights", "protected_weights", "capital_budget_level", "chronic_level",
                    "capital_budget_repair_streak", "operating_peak", "capital_peak", "last_shock_date",
                    "flat_book_capital_repair", "active_strategic_epoch_id", "positions", "cash",
                }
            }
            _save_observation(observation_directory, identity, diagnostic_json({
                "date": session, "equity": equity, "observation": observed.observation,
                "decision": decision.canonical_payload(effective_config_sha256=identity["config_sha256"]),
                "state": state, "new_fills": account.fills[fill_start:], "ledger": ledger_row,
            }))
            equity_rows.append((date, equity))
            daily_ledger.append(ledger_row)
            previous_equity = equity
            if len(equity_rows) % 50 == 0:
                print(f"{case_id}: {len(equity_rows)}/{len(sessions)} sessions, {session}, "
                      f"{time.monotonic() - started:.1f}s", flush=True)
    except Exception as exc:  # preserve the actual failed prefix instead of dropping a case
        status, error = "REPLAY_ERROR", f"{type(exc).__name__}: {exc}"
        (output_dir / "error.txt").write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
    metrics: dict[str, Any] = {}
    accounting: dict[str, Any] = {"reconciled": False}
    attribution: dict[str, Any] = {}
    try:
        account_payload = account.to_dict()
        write_json(output_dir / "final_account.json", account_payload)
        account_from_dict(account_payload)
        if equity_rows:
            last_date = equity_rows[-1][0]
            metrics = performance_metrics(
                equity_rows=equity_rows, fills=account.fills, orders=account.order_ledger,
                initial_cash=account.initial_cash, risk_events=account.risk_events,
                benchmark_total_return=engine.workspace.price("sh000682", last_date)
                / engine.workspace.price("sh000682", equity_rows[0][0]) - 1.0,
            )
            metrics["final_wealth"] = equity_rows[-1][1] / account.initial_cash
            attribution = build_economic_attribution(
                account=account,
                final_prices={
                    symbol: engine.workspace.price(symbol, last_date)
                    for symbol, position in account.positions.items() if position.shares > 0
                },
                sessions=tuple(str(date.date()) for date, _ in equity_rows),
                economic_start=str(equity_rows[0][0].date()), economic_end=str(last_date.date()), final_equity=equity_rows[-1][1],
                daily_ledger=daily_ledger,
                benchmark_close={str(date.date()): engine.workspace.price("sh000682", date) for date, _ in equity_rows},
            )
            accounting = attribution["accounting"]
            if not accounting["reconciled"]:
                raise RuntimeError("production accounting does not reconcile")
        latest_identity = case_identity(case_id, start, end, cfg)
        if any(identity[key] != value for key, value in latest_identity.items()):
            raise RuntimeError("historical replay input or source identity changed during execution")
        rebuild_observation_archive(output_dir, raw_path, completed_sessions=len(equity_rows))
        if raw_path.exists():
            with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
                raw_dates = [json.loads(line)["date"] for line in stream]
            if raw_dates != identity["session_dates"][:len(equity_rows)]:
                raise RuntimeError("raw observation readback differs from executed sessions")
    except Exception as exc:
        status = "REPLAY_ERROR"
        error = f"{error}; finalization: {type(exc).__name__}: {exc}"
        (output_dir / "finalization-error.txt").write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
    result = {
        "schema_version": 1, "status": status, "error": error,
        "diagnostic_only": True, "authoritative_acceptance": False, "future_holdout_used": False,
        "identity": identity, "sessions": len(equity_rows), "expected_sessions": len(sessions),
        "metrics": metrics, "accounting": accounting, "attribution": attribution,
        "elapsed_seconds": time.monotonic() - started,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest() if raw_path.exists() else "",
        "final_account_sha256": hashlib.sha256(
            (output_dir / "final_account.json").read_bytes()
        ).hexdigest() if (output_dir / "final_account.json").exists() else "",
    }
    result["canonical_sha256"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    write_json(output_dir / "result.json", result)
    print(f"{case_id}: {status}, {len(equity_rows)} sessions, {result['elapsed_seconds']:.1f}s, {error}", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=CASE_IDS, required=True)
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end", default="2026-08-05")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial-cash", type=float)
    parser.add_argument("--start-session-offset", type=int, default=0)
    parser.add_argument("--exclude-symbol", action="append", default=[])
    parser.add_argument("--config-overrides", type=Path, help="JSON mapping of explicit configuration changes")
    args = parser.parse_args()
    result = run_production_case(
        case_id=args.case, start=args.start, end=args.end, output_dir=args.output_dir,
        cfg=DEFAULT_CONFIG.override(**json.loads(args.config_overrides.read_text())) if args.config_overrides else DEFAULT_CONFIG,
        initial_cash=args.initial_cash, start_session_offset=args.start_session_offset,
        extra_excluded_symbols=tuple(args.exclude_symbol),
    )
    return 0 if result["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
