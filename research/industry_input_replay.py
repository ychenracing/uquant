"""Run the fixed main-policy industry-input comparison with native readback."""
from __future__ import annotations

import argparse
import hashlib
from contextlib import nullcontext
from pathlib import Path

from research.cross_ai_acceptance import read_case
from research.cross_ai_strategy import run_production_case, write_json
from uquant.contracts.universe import research_industry_input
from uquant.engine import code_fingerprint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("old", "revised"), required=True)
    parser.add_argument("--case", choices=("full", "remove_all_three", "strict"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--review-sha256", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--end", default="2026-08-05")
    parser.add_argument("--risk-reference-addition", action="append", default=[])
    args = parser.parse_args()
    if code_fingerprint() != args.source_sha256:
        raise ValueError("producer differs from registered integration source")
    if hashlib.sha256(args.review.read_bytes()).hexdigest() != args.review_sha256:
        raise ValueError("review differs from registered input")
    context = (research_industry_input(args.review, expected_sha256=args.review_sha256)
               if args.arm == "revised" else nullcontext())
    case = "remove_all_three" if args.case == "strict" else args.case
    excluded = ("sz300666",) if args.case == "strict" else ()
    risk_additions = tuple(args.risk_reference_addition)
    with context:
        result = run_production_case(case_id=case, start="2023-01-03", end=args.end,
                                     output_dir=args.output, extra_excluded_symbols=excluded,
                                     risk_reference_additions=risk_additions)
        verified = read_case(args.output, case=case, interval=["2023-01-03", args.end],
                             source=args.source_sha256, extra_excluded_symbols=excluded,
                             risk_reference_additions=risk_additions)
        write_json(args.output / "readback.json", {
            "native_readback": True, "arm": args.arm, "case": args.case,
            "review_sha256": args.review_sha256, "result_sha256": result["canonical_sha256"],
            "verified_symbol_pnl": verified["verified_symbol_pnl"],
        })


if __name__ == "__main__":
    main()
