"""Compare canonical Phase 1 decisions and economic state across two committed trees."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from uquant.validation.equivalence import compare_phase1_commits


def main(argv: list[str] | None = None) -> int:
    """Run the exact full Phase 1 cross-commit differential proof."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = compare_phase1_commits(
        frozen_root=args.frozen_root,
        candidate_root=args.candidate_root,
        data_dir=args.data_dir,
    )
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
