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


def _reject_duplicate_cli_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
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


def _validation_parser() -> argparse.ArgumentParser:
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
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one validation command and return a process-compatible status."""
    args = _parser().parse_args(argv)
    if args.command == "data-manifest":
        report = verify_data_manifest(args.data_dir)
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
    else:
        reference = _require_reviewed_reference(args.reference, gate="competitor")
        report = run_competitor_gate(
            data_dir=args.data_dir,
            reference_path=reference,
        )
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


_parser = _validation_parser
_reject_duplicate_keys = _reject_duplicate_cli_keys
