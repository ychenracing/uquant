"""Single command-line interface for daily production and causal replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .account import load_account, migrate_account, save_account
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
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--symbols", nargs="+", required=True)
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--data-dir", required=True)
    backtest.add_argument("--output", default=None)
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
            Path(args.output).write_text(report, encoding="utf-8")
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
    if args.command == "backtest":
        engine = ProductionEngine(args.data_dir)
        result = engine.backtest(symbols=args.symbols, start=args.start, end=args.end)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        print(payload)
        return 0
    return 2
