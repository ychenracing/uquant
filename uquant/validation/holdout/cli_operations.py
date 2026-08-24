"""Operational CLI for Future Holdout lanes and the isolated manual Journal."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from uquant.atomic_io import atomic_write_text, validate_atomic_output_boundary
from uquant.validation.execution_journal import (
    JournalCheckpoint,
    JournalRecord,
    JournalStatus,
    execution_journal_checkpoint,
    read_execution_journal,
)
from uquant.validation.execution_journal import (
    render_execution_journal as render_execution_events,
)
from uquant.validation.holdout import holdout_data_identity, load_future_holdout_contract
from uquant.validation.holdout_lanes import build_lane_validation_report, load_lane_registry

CANONICAL_JOURNAL_PATH = "future_holdout_execution_journal.jsonl"
CANONICAL_JOURNAL_CHECKPOINT_PATH = "future_holdout_execution_journal.checkpoint.json"
CANONICAL_LOCAL_LANE_REPORT_PATH = "future_holdout_lane_report.json"
CANONICAL_DIFFERENTIAL_JOURNAL_PATH = "risk_differential_observations.jsonl"


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result




def build_local_lane_report(args: argparse.Namespace) -> dict[str, Any]:
    """Recompute an untracked report from the operator's local future data."""

    root = Path(args.repository_root).resolve()
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    sessions, data_sha256 = holdout_data_identity(root / contract.data_directory)
    return build_lane_validation_report(
        lanes=load_lane_registry(root / args.registry),
        contract=contract,
        observed_sessions=sessions,
        holdout_data_sha256=data_sha256,
    )


def _read_tracked_lane_evidence(root: Path, evidence: str) -> dict[str, Any]:
    try:
        tracked = json.loads(
            (root / evidence).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("cannot read tracked future holdout lane evidence") from exc
    if not isinstance(tracked, dict):
        raise RuntimeError("tracked future holdout lane evidence is malformed")
    return tracked


def _validate_static_lanes(args: argparse.Namespace) -> dict[str, Any]:
    """Validate tracked zero-session evidence without reading local observations."""

    root = Path(args.repository_root).resolve()
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    with tempfile.TemporaryDirectory(prefix="uquant-empty-holdout-") as empty:
        sessions, data_sha256 = holdout_data_identity(empty)
    report = build_lane_validation_report(
        lanes=load_lane_registry(root / args.registry),
        contract=contract,
        observed_sessions=sessions,
        holdout_data_sha256=data_sha256,
    )
    if _read_tracked_lane_evidence(root, args.evidence) != report:
        raise RuntimeError("tracked future holdout lane evidence is stale")
    return report


def _write_local_lane_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repository_root).resolve()
    report = build_local_lane_report(args)
    destination = Path(args.output)
    if not destination.is_absolute():
        destination = root / destination
    contract = load_future_holdout_contract(root / "benchmarks/future_holdout_contract.json")
    protected = validate_atomic_output_boundary(
        destination,
        protected_paths=(root / args.registry,),
        protected_roots=(root / contract.data_directory,),
    )
    atomic_write_text(
        destination,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        protected_paths=protected,
    )
    return report




def load_journal_checkpoint(
    path: str | None,
    *,
    missing_ok: bool = False,
) -> JournalCheckpoint | None:
    """Load one trusted external tail checkpoint without accepting loose JSON."""

    if path is None:
        return None
    source = Path(path)
    if missing_ok and not source.exists():
        return None
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "sequence",
            "record_sha256",
        }:
            raise ValueError("checkpoint schema is malformed")
        return JournalCheckpoint(**payload)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("cannot read trusted execution journal checkpoint") from exc


def write_journal_checkpoint(
    journal: str | Path,
    output: str | Path,
) -> JournalCheckpoint:
    """Atomically persist the current verified Journal tail."""

    records = read_execution_journal(journal)
    checkpoint = execution_journal_checkpoint(records)
    atomic_write_text(
        output,
        json.dumps(asdict(checkpoint), sort_keys=True) + "\n",
        protected_paths=(journal,),
    )
    return checkpoint


def read_trusted_execution_journal(
    journal: str | Path,
    checkpoint: str | Path,
) -> tuple[JournalRecord, ...]:
    """Verify one Journal against an external tail, except for a truly empty bootstrap."""

    trusted = load_journal_checkpoint(str(checkpoint), missing_ok=True)
    records = read_execution_journal(journal, trusted_checkpoint=trusted)
    if trusted is None and records:
        raise ValueError("nonempty execution journal requires a trusted checkpoint")
    return records


def summarize_execution_journal(
    records: tuple[JournalRecord, ...],
) -> dict[str, Any]:
    """Aggregate observed execution without importing any strategy state."""

    plans: dict[str, JournalRecord] = {}
    filled_by_plan: dict[str, int] = {}
    skipped: set[str] = set()
    reference_notional = 0.0
    realized_slippage = 0.0
    for item in records:
        if item.status is JournalStatus.PLANNED:
            plans[item.plan_id] = item
            filled_by_plan[item.plan_id] = 0
        elif item.status is JournalStatus.FILLED:
            shares = cast(int, item.actual_shares)
            filled_by_plan[item.plan_id] += shares
            reference_notional += cast(float, item.next_open) * shares
            realized_slippage += cast(float, item.slippage_value)
        else:
            skipped.add(item.plan_id)

    states = {"filled": 0, "partial": 0, "open": 0, "skipped": 0}
    for plan_id, plan in plans.items():
        filled = filled_by_plan[plan_id]
        planned = cast(int, plan.planned_shares)
        if plan_id in skipped:
            states["skipped"] += 1
        elif filled == planned:
            states["filled"] += 1
        elif filled:
            states["partial"] += 1
        else:
            states["open"] += 1

    planned_shares = sum(cast(int, plan.planned_shares) for plan in plans.values())
    filled_shares = sum(filled_by_plan.values())
    return {
        "schema_version": 1,
        "plan_count": len(plans),
        "filled_plans": states["filled"],
        "partial_plans": states["partial"],
        "open_plans": states["open"],
        "skipped_plans": states["skipped"],
        "planned_shares": planned_shares,
        "filled_shares": filled_shares,
        "fill_ratio": filled_shares / planned_shares if planned_shares else 0.0,
        "reference_notional": reference_notional,
        "realized_slippage": realized_slippage,
        "weighted_slippage_bps": (
            realized_slippage / reference_notional * 10_000.0 if reference_notional else None
        ),
    }


def render_execution_journal(records: tuple[JournalRecord, ...]) -> str:
    """Render aggregate operator outcomes before the immutable event rows."""

    summary = summarize_execution_journal(records)
    weighted_slippage = summary["weighted_slippage_bps"]
    event_report = render_execution_events(records)
    _, separator, event_table = event_report.partition("\n\n")
    if not separator:
        raise ValueError("execution journal event report is malformed")
    lines = [
        "# Future Holdout Manual Execution Journal",
        "",
        "## Execution Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Plans | {summary['plan_count']} |",
        "| Filled / Partial / Open / Skipped | "
        f"{summary['filled_plans']} / {summary['partial_plans']} / "
        f"{summary['open_plans']} / {summary['skipped_plans']} |",
        f"| Planned / Filled shares | {summary['planned_shares']} / {summary['filled_shares']} |",
        f"| Fill ratio | {summary['fill_ratio']:.2%} |",
        f"| Realized slippage | {summary['realized_slippage']:.4f} |",
        "| Weighted slippage | "
        + ("N/A" if weighted_slippage is None else f"{weighted_slippage:.4f} bps")
        + " |",
        "",
        "## Events",
        "",
        event_table,
    ]
    return "\n".join(lines)



validate_static_lanes = _validate_static_lanes
write_local_lane_report = _write_local_lane_report
