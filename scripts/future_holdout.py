"""Operational CLI for Future Holdout lanes and the isolated manual Journal."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from uquant.validation.execution_journal import (
    JournalCheckpoint,
    JournalRecord,
    JournalStatus,
    append_filled,
    append_planned,
    append_skipped,
    execution_journal_checkpoint,
    read_execution_journal,
    record_to_dict,
)
from uquant.validation.execution_journal import (
    render_execution_journal as render_execution_events,
)
from uquant.validation.holdout import holdout_data_identity, load_future_holdout_contract
from uquant.validation.holdout_lanes import build_lane_validation_report, load_lane_registry


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/future_holdout.py")
    sub = parser.add_subparsers(dest="command", required=True)
    lanes = sub.add_parser("validate-lanes")
    lanes.add_argument("--repository-root", default=".")
    lanes.add_argument("--registry", default="benchmarks/future_holdout_lane_registry.json")
    lanes.add_argument("--evidence", default="artifacts/holdout/lane_validation.json")

    journal = sub.add_parser("journal")
    journal_sub = journal.add_subparsers(dest="journal_action", required=True)
    planned = journal_sub.add_parser("planned")
    planned.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    planned.add_argument("--plan-id", required=True)
    planned.add_argument("--decision-date", required=True)
    planned.add_argument("--recorded-at", required=True)
    planned.add_argument("--symbol", required=True)
    planned.add_argument("--side", choices=("BUY", "SELL"), required=True)
    planned.add_argument("--planned-weight", type=float, required=True)
    planned.add_argument("--planned-price", type=float, required=True)
    planned.add_argument("--planned-shares", type=int, required=True)
    filled = journal_sub.add_parser("filled")
    filled.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    filled.add_argument("--plan-id", required=True)
    filled.add_argument("--recorded-at", required=True)
    filled.add_argument("--next-open", type=float, required=True)
    filled.add_argument("--actual-time", required=True)
    filled.add_argument("--actual-price", type=float, required=True)
    filled.add_argument("--actual-shares", type=int, required=True)
    filled.add_argument("--broker-order-id", required=True)
    skipped = journal_sub.add_parser("skipped")
    skipped.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    skipped.add_argument("--plan-id", required=True)
    skipped.add_argument("--recorded-at", required=True)
    skipped.add_argument("--next-open", type=float, required=True)
    skipped.add_argument("--manual-skip", required=True)
    report = journal_sub.add_parser("report")
    report.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    checkpoint = journal_sub.add_parser("checkpoint")
    checkpoint.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    verify = journal_sub.add_parser("verify")
    verify.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    verify.add_argument("--checkpoint")
    return parser


def _validate_lanes(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repository_root).resolve()
    contract = load_future_holdout_contract()
    sessions, data_sha256 = holdout_data_identity(root / contract.data_directory)
    report = build_lane_validation_report(
        lanes=load_lane_registry(root / args.registry),
        contract=contract,
        observed_sessions=sessions,
        holdout_data_sha256=data_sha256,
    )
    try:
        tracked = json.loads(
            (root / args.evidence).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("cannot read tracked future holdout lane evidence") from exc
    if tracked != report:
        raise RuntimeError("tracked future holdout lane evidence is stale")
    return report


def _load_checkpoint(path: str | None) -> JournalCheckpoint | None:
    if path is None:
        return None
    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"),
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
            realized_slippage / reference_notional * 10_000.0
            if reference_notional
            else None
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate-lanes":
        print(json.dumps(_validate_lanes(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.journal_action == "planned":
        record = append_planned(
            args.journal,
            plan_id=args.plan_id,
            decision_date=args.decision_date,
            recorded_at=args.recorded_at,
            symbol=args.symbol,
            side=args.side,
            planned_weight=args.planned_weight,
            planned_price=args.planned_price,
            planned_shares=args.planned_shares,
        )
    elif args.journal_action == "filled":
        record = append_filled(
            args.journal,
            plan_id=args.plan_id,
            recorded_at=args.recorded_at,
            next_open=args.next_open,
            actual_time=args.actual_time,
            actual_price=args.actual_price,
            actual_shares=args.actual_shares,
            broker_order_id=args.broker_order_id,
        )
    elif args.journal_action == "skipped":
        record = append_skipped(
            args.journal,
            plan_id=args.plan_id,
            recorded_at=args.recorded_at,
            next_open=args.next_open,
            manual_skip=args.manual_skip,
        )
    elif args.journal_action == "checkpoint":
        records = read_execution_journal(args.journal)
        print(json.dumps(asdict(execution_journal_checkpoint(records)), sort_keys=True))
        return 0
    elif args.journal_action == "verify":
        trusted = _load_checkpoint(args.checkpoint)
        records = read_execution_journal(args.journal, trusted_checkpoint=trusted)
        current = execution_journal_checkpoint(records)
        print(
            json.dumps(
                {
                    "checkpoint": asdict(current),
                    "records": len(records),
                    "status": "VALID",
                },
                sort_keys=True,
            )
        )
        return 0
    else:
        print(render_execution_journal(read_execution_journal(args.journal)))
        return 0
    print(json.dumps(record_to_dict(record), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
