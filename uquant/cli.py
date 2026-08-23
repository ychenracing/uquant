"""Single command-line interface for daily production and causal replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .account import (
    economic_state_sha256,
    load_account,
    migrate_account,
    migrate_code_identity,
    save_account,
)
from .atomic_io import atomic_write_text, validate_atomic_output_boundary
from .broker import sync_broker_snapshot
from .config import DEFAULT_CONFIG
from .engine import ProductionEngine, code_fingerprint
from .leader import REFERENCE_UNIVERSE
from .report import render_daily_report
from .types import AccountState


def _parser() -> argparse.ArgumentParser:
    """Build the complete production CLI without reading process arguments."""

    parser = argparse.ArgumentParser(
        prog="uquant",
        description="Causal A-share daily portfolio decisions and replay",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("account-init")
    init.add_argument("--output", default="account_state.json")
    init.add_argument("--cash", type=float, default=DEFAULT_CONFIG.initial_cash)
    init.add_argument("--data-dir", required=True)
    init.add_argument("--symbols", nargs="+", required=True)
    init.add_argument(
        "--date",
        default=None,
        help="bounded data-provenance date (defaults to latest common date)",
    )
    daily = sub.add_parser("daily")
    daily.add_argument("--symbols", nargs="+", required=True)
    daily.add_argument("--date", required=True)
    daily.add_argument("--account", required=True)
    daily.add_argument("--data-dir", required=True)
    daily.add_argument("--output", default=None)
    daily.add_argument(
        "--broker-snapshot",
        default=None,
        help="authoritative JSON cash/positions/fills snapshot applied before decision",
    )
    sync = sub.add_parser("account-sync")
    sync.add_argument("--account", required=True)
    sync.add_argument("--snapshot", required=True)
    migrate = sub.add_parser("account-migrate")
    migrate.add_argument("--account", required=True)
    migrate.add_argument(
        "--output",
        default=None,
        help="destination account file (defaults to atomic in-place normalization)",
    )
    migrate.add_argument(
        "--acknowledge-code-change",
        action="store_true",
        help="confirm that the reviewed production code fingerprint becomes authoritative",
    )
    code_migrate = sub.add_parser("account-code-migrate")
    code_migrate.add_argument("--account", required=True)
    code_migrate.add_argument(
        "--output",
        default=None,
        help="destination account file (defaults to atomic in-place migration)",
    )
    code_migrate.add_argument(
        "--acknowledge-code-change",
        action="store_true",
        help="confirm a code-identity-only migration with no economic-state changes",
    )
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--symbols", nargs="+", required=True)
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--data-dir", required=True)
    backtest.add_argument("--output", default=None)
    holdout = sub.add_parser("holdout-manifest")
    holdout.add_argument("--account", required=True)
    holdout.add_argument(
        "--metrics",
        default=None,
        help="deterministic holdout replay artifact (required after sessions exist)",
    )
    holdout.add_argument("--journal", default=None)
    holdout.add_argument("--output", default="benchmarks/future_holdout_manifest.json")
    holdout_append = sub.add_parser("holdout-append")
    holdout_append.add_argument("--snapshot-dir", required=True)
    holdout_replay = sub.add_parser("holdout-replay")
    holdout_replay.add_argument("--account", required=True)
    holdout_replay.add_argument("--journal", default="execution_journal.jsonl")
    holdout_replay.add_argument(
        "--output",
        default="artifacts/future_holdout_replay.json",
    )
    holdout_replay.add_argument(
        "--decision-output",
        default="artifacts/future_holdout_decision.json",
    )
    journal = sub.add_parser("execution-journal")
    journal_sub = journal.add_subparsers(dest="journal_action", required=True)
    journal_plan = journal_sub.add_parser("planned")
    journal_plan.add_argument("--journal", default="execution_journal.jsonl")
    journal_plan.add_argument("--plan-id", required=True)
    journal_plan.add_argument("--recorded-at", required=True)
    journal_plan.add_argument("--symbol", required=True)
    journal_plan.add_argument("--side", choices=("BUY", "SELL"), required=True)
    journal_plan.add_argument("--planned-price", type=float, required=True)
    journal_plan.add_argument("--planned-shares", type=int, required=True)
    journal_fill = journal_sub.add_parser("filled")
    journal_fill.add_argument("--journal", default="execution_journal.jsonl")
    journal_fill.add_argument("--plan-id", required=True)
    journal_fill.add_argument("--recorded-at", required=True)
    journal_fill.add_argument("--next-open", type=float, required=True)
    journal_fill.add_argument("--actual-time", required=True)
    journal_fill.add_argument("--actual-price", type=float, required=True)
    journal_fill.add_argument("--actual-shares", type=int, required=True)
    journal_skip = journal_sub.add_parser("skipped")
    journal_skip.add_argument("--journal", default="execution_journal.jsonl")
    journal_skip.add_argument("--plan-id", required=True)
    journal_skip.add_argument("--recorded-at", required=True)
    journal_skip.add_argument("--next-open", type=float, required=True)
    journal_skip.add_argument("--manual-skip", required=True)
    journal_report = journal_sub.add_parser("report")
    journal_report.add_argument("--journal", default="execution_journal.jsonl")
    journal_report.add_argument("--output", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one CLI command and return a process-compatible exit status."""
    args = _parser().parse_args(argv)
    if args.command == "account-init":
        engine = ProductionEngine(args.data_dir)
        symbols = (
            set(args.symbols)
            | set(REFERENCE_UNIVERSE)
            | {
                "sh000300",
                "sh000682",
            }
        )
        latest = engine.data.manifest(symbols)
        snapshot_date = args.date or latest.end
        manifest = engine.data.manifest(symbols, as_of=snapshot_date)
        state = AccountState.empty(args.cash)
        state.data_hash = manifest.digest
        state.data_hash_as_of = snapshot_date
        state.data_hash_symbols = list(manifest.symbols)
        state.code_hash = code_fingerprint()
        save_account(state, args.output)
        print(args.output)
        return 0
    if args.command == "daily":
        exact_inputs: list[str] = [args.account]
        if args.broker_snapshot:
            exact_inputs.append(args.broker_snapshot)
        daily_protected = tuple(Path(path) for path in exact_inputs)
        if args.output:
            daily_protected = validate_atomic_output_boundary(
                args.output,
                protected_paths=exact_inputs,
                protected_roots=(args.data_dir,),
            )
        engine = ProductionEngine(args.data_dir)
        account = load_account(args.account)
        if args.broker_snapshot:
            snapshot = json.loads(Path(args.broker_snapshot).read_text(encoding="utf-8"))
            sync_broker_snapshot(account, snapshot)
        decision = engine.decide(symbols=args.symbols, as_of=args.date, account=account)
        account.pending_orders = list(decision.pending_orders)
        save_account(account, args.account)
        report = render_daily_report(decision, account)
        if args.output:
            atomic_write_text(args.output, report, protected_paths=daily_protected)
        print(report)
        return 0
    if args.command == "account-sync":
        account = load_account(args.account)
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        summary = sync_broker_snapshot(account, snapshot)
        save_account(account, args.account)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "account-migrate":
        destination = args.output or args.account
        state = migrate_account(
            args.account,
            destination,
            new_code_hash=code_fingerprint(),
            acknowledge_code_change=args.acknowledge_code_change,
        )
        print(
            json.dumps(
                {
                    "account": destination,
                    "schema_version": state.schema_version,
                    "code_hash": state.code_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "account-code-migrate":
        destination = args.output or args.account
        state = migrate_code_identity(
            args.account,
            destination,
            new_code_hash=code_fingerprint(),
            acknowledge_code_change=args.acknowledge_code_change,
        )
        print(
            json.dumps(
                {
                    "account": destination,
                    "schema_version": state.schema_version,
                    "code_hash": state.code_hash,
                    "economic_state_sha256": economic_state_sha256(state),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "backtest":
        backtest_protected: tuple[Path, ...] = ()
        if args.output:
            backtest_protected = validate_atomic_output_boundary(
                args.output,
                protected_roots=(args.data_dir,),
            )
        engine = ProductionEngine(args.data_dir)
        result = engine.backtest(symbols=args.symbols, start=args.start, end=args.end)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            atomic_write_text(args.output, payload, protected_paths=backtest_protected)
        print(payload)
        return 0
    if args.command == "holdout-manifest":
        from .validation.holdout import generate_future_holdout_manifest

        holdout_manifest = generate_future_holdout_manifest(
            account_path=args.account,
            output_path=args.output,
            metrics_path=args.metrics,
            journal_path=args.journal,
        )
        print(json.dumps(holdout_manifest, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "holdout-append":
        from .validation.holdout_runtime import append_holdout_snapshot

        root = Path(__file__).resolve().parents[1]
        result = append_holdout_snapshot(
            repository_root=root,
            snapshot_dir=args.snapshot_dir,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "holdout-replay":
        from .validation.holdout_runtime import generate_future_holdout_replay

        root = Path(__file__).resolve().parents[1]
        replay = generate_future_holdout_replay(
            repository_root=root,
            account_path=args.account,
            output_path=args.output,
            decision_output_path=args.decision_output,
            journal_path=args.journal,
        )
        print(json.dumps(replay, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "execution-journal":
        from .observation.execution_journal import (
            append_filled,
            append_planned,
            append_skipped,
            read_execution_journal,
            record_to_dict,
        )

        if args.journal_action == "planned":
            record = append_planned(
                args.journal,
                plan_id=args.plan_id,
                recorded_at=args.recorded_at,
                symbol=args.symbol,
                side=args.side,
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
            from .report import render_execution_journal

            rendered = render_execution_journal(read_execution_journal(args.journal))
            if args.output:
                atomic_write_text(
                    args.output,
                    rendered,
                    protected_paths=(args.journal,),
                )
            print(rendered)
            return 0
        print(json.dumps(record_to_dict(record), ensure_ascii=False, sort_keys=True))
        return 0
    return 2
