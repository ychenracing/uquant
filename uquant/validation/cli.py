"""Command-line orchestration for deterministic release validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import verify_data_manifest
from .promotion import run_promotion


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
    promotion.add_argument("--profile", choices=("quick", "full"), default="quick")
    promotion.add_argument("--output", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one validation command and return a process-compatible status."""
    args = _parser().parse_args(argv)
    if args.command == "data-manifest":
        report = verify_data_manifest(args.data_dir)
    else:
        report = run_promotion(
            data_dir=args.data_dir,
            baseline=args.baseline,
            profile=args.profile,
        )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if getattr(args, "output", None):
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report.get("passed", True) else 1
