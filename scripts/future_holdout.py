"""Operational CLI for Future Holdout lanes and the isolated manual Journal."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from uquant.validation.execution_journal import (
    append_filled,
    append_planned,
    append_skipped,
    read_execution_journal,
    record_to_dict,
    render_execution_journal,
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
    else:
        print(render_execution_journal(read_execution_journal(args.journal)))
        return 0
    print(json.dumps(record_to_dict(record), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
