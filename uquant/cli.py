"""Single command-line interface for daily production and causal replay."""

# Chinese punctuation is intentional in user-facing report text.
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .account import (
    economic_state_sha256,
    load_account,
    migrate_code_identity,
    save_account,
)
from .broker import sync_broker_snapshot
from .config import DEFAULT_CONFIG, config_fingerprint
from .config.input import load_public_config
from .engine import ProductionEngine, code_fingerprint
from .infrastructure.atomic_files import atomic_write_text, validate_atomic_output_boundary
from .leader import REFERENCE_UNIVERSE
from .report import render_daily_html, render_daily_report
from .types import AccountState


def _uquant_cli_parser() -> argparse.ArgumentParser:
    """Build the complete production CLI without reading process arguments."""

    parser = argparse.ArgumentParser(
        prog="uquant",
        description="Causal A-share daily portfolio decisions and replay",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("account-init")
    init.add_argument("--output", default="account_state.json")
    init.add_argument("--cash", type=float, default=None)
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
    daily.add_argument("--html-output", default=None)
    daily.add_argument(
        "--broker-snapshot",
        default=None,
        help="authoritative JSON cash/positions/fills snapshot applied before decision",
    )
    sync = sub.add_parser("account-sync")
    sync.add_argument("--account", required=True)
    sync.add_argument("--snapshot", required=True)
    code_migrate = sub.add_parser("account-code-migrate")
    code_migrate.add_argument("--account", required=True)
    code_migrate.add_argument(
        "--output",
        default=None,
        help="destination account file (defaults to atomic in-place rebinding)",
    )
    code_migrate.add_argument(
        "--acknowledge-code-change",
        action="store_true",
        help="confirm code-identity rebinding with no economic-state changes",
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
    for command in (init, daily, backtest):
        command.add_argument("--config", default=None, help="strict JSON public settings")
    return parser


def _run_account_init(args: argparse.Namespace) -> int:
    cfg = load_public_config(args.config, initial_cash=args.cash)
    cash = cfg.initial_cash
    validate_atomic_output_boundary(args.output, protected_paths=([args.config] if args.config else []),
                                    protected_roots=(args.data_dir,))
    engine = ProductionEngine(args.data_dir, cfg)
    symbols = set(args.symbols) | set(REFERENCE_UNIVERSE) | {"sh000300", "sh000682"}
    latest = engine.data.manifest(symbols)
    snapshot_date = args.date or latest.end
    manifest = engine.data.manifest(symbols, as_of=snapshot_date)
    state = AccountState.empty(cash)
    state.account_migrations.append({"migration_type": "configuration_binding",
                                     "effective_config_sha256": config_fingerprint(cfg)})
    state.data_hash = manifest.digest
    state.data_hash_as_of = snapshot_date
    state.data_hash_symbols = list(manifest.symbols)
    state.code_hash = code_fingerprint()
    save_account(state, args.output)
    print(args.output)
    return 0


def _daily_output_boundary(args: argparse.Namespace) -> tuple[list[str], dict[str, tuple[Path, ...]]]:
    exact_inputs = [args.account, *([args.broker_snapshot] if args.broker_snapshot else []),
                    *([args.config] if args.config else [])]
    outputs = [path for path in (args.output, args.html_output) if path is not None]
    protected = {}
    for index, path in enumerate(outputs):
        protected[path] = validate_atomic_output_boundary(
            path, protected_paths=[*exact_inputs, *outputs[:index], *outputs[index+1:]],
            protected_roots=(args.data_dir,))
        target = Path(path)
        if target.exists() and not target.is_file():
            raise ValueError(f"report destination is not a regular file: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Check actual directory writability before the decision mutates state.
        with tempfile.TemporaryFile(dir=target.parent):
            pass
    return exact_inputs, protected


def _run_daily(args: argparse.Namespace) -> int:
    cfg = load_public_config(args.config)
    exact_inputs, protected = _daily_output_boundary(args)
    account = load_account(args.account)
    bindings = [event["effective_config_sha256"] for event in account.account_migrations
                if event.get("migration_type") == "configuration_binding"]
    expected = bindings[-1] if bindings else config_fingerprint(DEFAULT_CONFIG)
    if expected != config_fingerprint(cfg):
        raise ValueError("account configuration identity differs; no automatic configuration migration is available")
    if any(epoch.config_identity != "config:" + expected for epoch in account.strategic_epochs):
        raise ValueError("account strategic configuration identity differs")
    engine = ProductionEngine(args.data_dir, cfg)
    if args.broker_snapshot:
        snapshot = json.loads(Path(args.broker_snapshot).read_text(encoding="utf-8"))
        sync_broker_snapshot(account, snapshot)
    decision = engine.decide(symbols=args.symbols, as_of=args.date, account=account)
    account.pending_orders = list(decision.pending_orders)
    report = render_daily_report(decision, account)
    documents = []
    if args.output:
        documents.append((args.output, report))
    if args.html_output:
        documents.append((args.html_output, render_daily_html(decision, account)))
    staged = []
    try:
        for path, content in documents:
            fd, temporary = tempfile.mkstemp(prefix=Path(path).name + ".ready-", dir=Path(path).parent)
            os.close(fd)
            atomic_write_text(temporary, content, protected_paths=exact_inputs)
            staged.append((path, temporary))
    except Exception:
        for _, temporary in staged:
            Path(temporary).unlink(missing_ok=True)
        raise
    try:
        save_account(account, args.account)
    except Exception as exc:
        print(f"账户保存未能确认完成：{args.account}：{exc}。账户文件可能已替换，请先只读核对。"
              f"已渲染报告保留：{staged}。不得直接重跑 daily；核对账户与报告审计中的账户状态后恢复文件。",
              file=sys.stderr)
        return 1
    published = []
    for path, temporary in staged:
        try:
            validate_atomic_output_boundary(path, protected_paths=protected[path])
            atomic_write_text(path, Path(temporary).read_text(encoding="utf-8"),
                              protected_paths=protected[path])
            Path(temporary).unlink()
            published.append(path)
        except (OSError, ValueError) as exc:
            print(f"账户已保存：{args.account}；报告已发布：{published}；发布失败：{path}：{exc}。"
                  f"未发布的已渲染文件：{[(p, t) for p, t in staged if p not in published]}。"
                  "恢复时将对应 ready 文件移至报告目标路径；不要重新运行 daily 补报告。", file=sys.stderr)
            return 1
    print(report)
    return 0


def _run_account_code_migration(args: argparse.Namespace) -> int:
    destination = args.output or args.account
    state = migrate_code_identity(
        args.account,
        destination,
        new_code_hash=code_fingerprint(),
        acknowledge_code_change=args.acknowledge_code_change,
    )
    payload = {
        "account": destination,
        "schema_version": state.schema_version,
        "code_hash": state.code_hash,
    }
    payload["economic_state_sha256"] = economic_state_sha256(state)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _run_backtest(args: argparse.Namespace) -> int:
    cfg = load_public_config(args.config)
    backtest_protected: tuple[Path, ...] = ()
    if args.output:
        backtest_protected = validate_atomic_output_boundary(
            args.output,
            protected_paths=([args.config] if args.config else []),
            protected_roots=(args.data_dir,),
        )
    engine = ProductionEngine(args.data_dir, cfg)
    result = engine.backtest(symbols=args.symbols, start=args.start, end=args.end)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        atomic_write_text(args.output, payload, protected_paths=backtest_protected)
    print(payload)
    return 0


def _run_execution_journal(args: argparse.Namespace) -> int:
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
            atomic_write_text(args.output, rendered, protected_paths=(args.journal,))
        print(rendered)
        return 0
    print(json.dumps(record_to_dict(record), ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run one CLI command and return a process-compatible exit status."""
    args = _uquant_cli_parser().parse_args(argv)
    if args.command == "account-init":
        return _run_account_init(args)
    if args.command == "daily":
        return _run_daily(args)
    if args.command == "account-sync":
        account = load_account(args.account)
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        summary = sync_broker_snapshot(account, snapshot)
        save_account(account, args.account)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "account-code-migrate":
        return _run_account_code_migration(args)
    if args.command == "backtest":
        return _run_backtest(args)
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
        return _run_execution_journal(args)
    return 2
