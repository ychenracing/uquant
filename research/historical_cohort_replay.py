"""Run the fixed historical cohort through the native main-policy decision path."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from research.cross_ai_acceptance import read_case
from research.cross_ai_strategy import _data_root, run_production_case, write_json
from uquant.contracts.universe import research_cohort_input
from uquant.engine import code_fingerprint
from uquant.validation.manifest import verify_data_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--cohort-sha256", required=True)
    parser.add_argument("--research-data-dir", type=Path, required=True)
    parser.add_argument("--research-data-sha256", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end", default="2026-08-05")
    args = parser.parse_args()
    if code_fingerprint() != args.source_sha256:
        raise ValueError("cohort producer differs from committed source")
    root = _data_root(args.research_data_dir, args.research_data_sha256)
    data = verify_data_manifest(root)
    manifest = json.loads((root / "DATA_MANIFEST.json").read_text())
    if manifest.get("cohort_manifest_sha256") != args.cohort_sha256:
        raise ValueError("research data is not bound to this cohort")
    with research_cohort_input(args.cohort, expected_sha256=args.cohort_sha256) as cohort:
        expected = set(cohort.symbols) | {"sh000300", "sh000682"}
        if {path.stem for path in root.glob("*.csv")} != expected:
            raise ValueError("cohort data contains missing or foreign symbols")
        result = run_production_case(
            case_id="full", start="2023-01-03", end=args.end, output_dir=args.output,
            research_data_dir=root, research_data_sha256=args.research_data_sha256,
        )
        verified = read_case(
            args.output, case="full", interval=["2023-01-03", args.end],
            source=args.source_sha256,
        )
        if (result["identity"]["data"] != data or verify_data_manifest(root) != data
                or result["identity"].get("research_universe_sha256") != cohort.sha256):
            raise ValueError("native cohort or data identity differs at readback")
        if hashlib.sha256(args.cohort.read_bytes()).hexdigest() != args.cohort_sha256:
            raise ValueError("cohort changed during replay")
        write_json(args.output / "readback.json", {
            "native_readback": True, "research_only": True,
            "cohort_sha256": args.cohort_sha256, "data": data,
            "result_sha256": result["canonical_sha256"],
            "verified_symbol_pnl": verified["verified_symbol_pnl"],
        })


if __name__ == "__main__":
    main()
