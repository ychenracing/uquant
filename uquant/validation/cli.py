"""Command-line orchestration for deterministic release validation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .ai_era import AI_ERA_WINDOWS
from .competitor import run_competitor_gate
from .generalization import run_generalization
from .generalization_matrix import run_generalization_matrix
from .manifest import verify_data_manifest
from .promotion import run_promotion


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_industries(path: str | Path) -> dict[str, str]:
    source = Path(path)
    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"cannot load generalization industry map: {source}") from exc
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError("generalization industry map must be a non-empty JSON object")
    if any(
        not isinstance(symbol, str) or not symbol or not isinstance(industry, str) or not industry
        for symbol, industry in raw.items()
    ):
        raise RuntimeError("generalization industry map must contain non-empty string pairs")
    return raw


def _require_reviewed_reference(path: str | Path, *, gate: str) -> Path:
    source = Path(path)
    if not source.is_file():
        raise RuntimeError(
            f"{gate} gate is fail-closed: reviewed reference is missing: {source}; "
            "refusing to create a placeholder"
        )
    return source


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m uquant.validation")
    sub = parser.add_subparsers(dest="command", required=True)
    manifest = sub.add_parser("data-manifest")
    manifest.add_argument("--data-dir", required=True)
    promotion = sub.add_parser("promotion")
    promotion.add_argument("--data-dir", required=True)
    promotion.add_argument(
        "--baseline",
        default=str(Path("benchmarks") / "promotion_baseline.json"),
    )
    promotion.add_argument("--profile", choices=("full",), default="full")
    promotion.add_argument("--output", default=None)
    generalization = sub.add_parser("generalization")
    generalization.add_argument("--data-dir", required=True)
    generalization.add_argument("--universe", nargs="+", required=True)
    generalization.add_argument("--industries", required=True)
    generalization.add_argument("--prior-symbols", nargs="+", required=True)
    generalization.add_argument("--start", required=True)
    generalization.add_argument("--end", required=True)
    generalization.add_argument(
        "--baseline",
        default=str(Path("benchmarks") / "generalization_baseline.json"),
    )
    generalization.add_argument("--lookback-sessions", type=int, default=120)
    generalization.add_argument("--random-seed-count", type=int, default=300)
    generalization.add_argument("--output", default=None)
    matrix = sub.add_parser("generalization-matrix")
    matrix.add_argument("--data-dir", required=True)
    matrix.add_argument("--window", action="append", choices=tuple(AI_ERA_WINDOWS), default=None)
    matrix.add_argument("--lookback-sessions", type=int, default=120)
    matrix.add_argument("--output", default=None)
    competitor = sub.add_parser("competitor")
    competitor.add_argument("--data-dir", required=True)
    competitor.add_argument(
        "--reference",
        default=str(Path("benchmarks") / "competitor_matrix_reference.json"),
    )
    competitor.add_argument("--output", default=None)
    holdout_lanes = sub.add_parser("holdout-lanes")
    holdout_lanes.add_argument("--repository-root", default=".")
    holdout_lanes.add_argument(
        "--registry",
        default=str(Path("benchmarks") / "future_holdout_lane_registry.json"),
    )
    holdout_lanes.add_argument(
        "--evidence",
        default=str(Path("artifacts") / "holdout" / "lane_validation.json"),
    )
    journal = sub.add_parser("holdout-journal")
    journal_sub = journal.add_subparsers(dest="journal_action", required=True)
    journal_plan = journal_sub.add_parser("planned")
    journal_plan.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    journal_plan.add_argument("--plan-id", required=True)
    journal_plan.add_argument("--decision-date", required=True)
    journal_plan.add_argument("--recorded-at", required=True)
    journal_plan.add_argument("--symbol", required=True)
    journal_plan.add_argument("--side", choices=("BUY", "SELL"), required=True)
    journal_plan.add_argument("--planned-weight", type=float, required=True)
    journal_plan.add_argument("--planned-price", type=float, required=True)
    journal_plan.add_argument("--planned-shares", type=int, required=True)
    journal_fill = journal_sub.add_parser("filled")
    journal_fill.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    journal_fill.add_argument("--plan-id", required=True)
    journal_fill.add_argument("--recorded-at", required=True)
    journal_fill.add_argument("--next-open", type=float, required=True)
    journal_fill.add_argument("--actual-time", required=True)
    journal_fill.add_argument("--actual-price", type=float, required=True)
    journal_fill.add_argument("--actual-shares", type=int, required=True)
    journal_fill.add_argument("--broker-order-id", required=True)
    journal_skip = journal_sub.add_parser("skipped")
    journal_skip.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    journal_skip.add_argument("--plan-id", required=True)
    journal_skip.add_argument("--recorded-at", required=True)
    journal_skip.add_argument("--next-open", type=float, required=True)
    journal_skip.add_argument("--manual-skip", required=True)
    journal_report = journal_sub.add_parser("report")
    journal_report.add_argument("--journal", default="future_holdout_execution_journal.jsonl")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one validation command and return a process-compatible status."""
    args = _parser().parse_args(argv)
    if args.command == "holdout-journal":
        from .execution_journal import (
            append_filled,
            append_planned,
            append_skipped,
            read_execution_journal,
            record_to_dict,
            render_execution_journal,
        )

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
    if args.command == "data-manifest":
        report = verify_data_manifest(args.data_dir)
    elif args.command == "holdout-lanes":
        from .holdout import holdout_data_identity, load_future_holdout_contract
        from .holdout_lanes import build_lane_validation_report, load_lane_registry

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
    elif args.command == "promotion":
        report = run_promotion(
            data_dir=args.data_dir,
            baseline=args.baseline,
            profile=args.profile,
        )
    elif args.command == "generalization":
        if args.random_seed_count < 1:
            raise RuntimeError("generalization random-seed count must be positive")
        baseline = _require_reviewed_reference(args.baseline, gate="generalization")
        report = run_generalization(
            data_dir=args.data_dir,
            universe=args.universe,
            industries=_load_industries(args.industries),
            prior_symbols=args.prior_symbols,
            start=args.start,
            end=args.end,
            baseline_path=baseline,
            lookback_sessions=args.lookback_sessions,
            random_seeds=range(args.random_seed_count),
        )
    elif args.command == "generalization-matrix":
        report = run_generalization_matrix(
            data_dir=args.data_dir,
            window_names=tuple(args.window) if args.window is not None else None,
            lookback_sessions=args.lookback_sessions,
        )
    elif args.command == "competitor":
        reference = _require_reviewed_reference(args.reference, gate="competitor")
        report = run_competitor_gate(
            data_dir=args.data_dir,
            reference_path=reference,
        )
    else:
        raise AssertionError(f"unhandled validation command: {args.command}")
    if args.command == "generalization-matrix":
        payload = json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if getattr(args, "output", None):
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.get("passed", True) else 1
