"""Pure deterministic future-holdout replay and strict readback."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pandas as pd

from ...account import load_account
from ...config import config_fingerprint
from ...engine import INDEX_SYMBOLS, ProductionEngine
from ...leader import REFERENCE_UNIVERSE
from ...types import Decision, Fill
from ..execution_journal import (
    JournalCheckpoint,
    JournalRecord,
    execution_journal_checkpoint,
    read_execution_journal,
)
from ..generalization import symbol_pnl_concentration
from ..universe import load_ai_universe
from .contract import (
    FutureHoldoutContract,
    _canonical_sha256,
    _read_json,
    _session_dates,
    load_future_holdout_contract,
    runtime_compatibility_value,
)
from .lanes import lane_binding_payload, load_lane_registry
from .manifest import _normalized_scores, _validated_score_values
from .snapshots import _capture_holdout_data, _materialize_overlay
from .source_identity import holdout_source_sha256, validate_prior_close_account

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
    registry_path = Path(__file__).resolve().parents[3] / "benchmarks/future_holdout_lane_registry.json"
    lanes = load_lane_registry(registry_path)
    lane = next((item for item in lanes if item.lane_id == lane_id), None)
    if lane is None:
        raise ValueError(f"unknown future holdout lane: {lane_id}")
    source_sha256 = runtime_compatibility_value("holdout_source_sha256", holdout_source_sha256)(Path(__file__).resolve().parents[3])
    holdout_root = root / reviewed.data_directory
    snapshot = _capture_holdout_data(holdout_root)
    sessions = tuple(session for session in snapshot.sessions if session >= lane.activation_session)
    data_sha256 = snapshot.sha256
    if not sessions:
        raise ValueError("future holdout replay requires at least one observed session")
    _session_dates(sessions, contract=reviewed)
    account = load_account(account_path)
    runtime_compatibility_value("validate_prior_close_account", validate_prior_close_account)(account.to_dict(), frozen_data_dir=root / "data/frozen")
    universe = load_ai_universe()
    user_symbols = universe.symbols
    required_symbols = tuple(sorted(set(user_symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)))

    with tempfile.TemporaryDirectory(prefix="uquant-holdout-overlay-") as temporary:
        overlay = Path(temporary) / "data"
        _materialize_overlay(root, overlay, snapshot)
        engine = ProductionEngine(overlay)
        engine.workspace.load(required_symbols)
        expected_sessions = tuple(
            str(value.date())
            for value in engine.workspace.common_sessions(*INDEX_SYMBOLS)
            if str(value.date()) in set(sessions)
        )
        if expected_sessions != sessions:
            raise ValueError("holdout sessions are not complete across both market indices")
        if any(
            session not in {str(value.date()) for value in engine.workspace.raw_frame(symbol).index}
            for symbol in required_symbols
            for session in sessions
        ):
            raise ValueError("holdout sessions are incomplete across the decision inventory")

        prior_date = pd.Timestamp(reviewed.last_in_sample_date)
        starting_values = {
            symbol: position.shares * engine.workspace.price(symbol, prior_date)
            for symbol, position in account.positions.items()
            if position.shares > 0
        }
        starting_equity = engine.equity(account, prior_date)
        initial_fill_count = len(account.fills)
        equities = [starting_equity]
        decisions: list[dict[str, object]] = []
        raw_user_panel = {symbol: engine.workspace.raw_frame(symbol) for symbol in user_symbols}
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
            symbol: position.shares * engine.workspace.price(symbol, final_date)
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
    if not isinstance(source_sha256, str) or source_sha256 != runtime_compatibility_value("holdout_source_sha256", holdout_source_sha256)(
        Path(__file__).resolve().parents[3]
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
        Path(__file__).resolve().parents[3] / "benchmarks/future_holdout_lane_registry.json"
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
    if set(raw) != _DAILY_DECISION_FIELDS or not isinstance(seal, str) or seal != _canonical_sha256(unsealed):
        raise ValueError("future holdout daily decision hash is invalid")
    if raw != _daily_decision_payload(replay):
        raise ValueError("future holdout daily decision binding is stale")
    return raw

__all__ = (
    "_REPLAY_FIELDS",
    "_DAILY_DECISION_FIELDS",
    "_period_symbol_pnl",
    "_drawdown",
    "_decision_payload",
    "_decision_payload_sha256",
    "replay_future_holdout",
    "read_future_holdout_replay",
    "_daily_decision_payload",
    "read_future_holdout_decision",
)
