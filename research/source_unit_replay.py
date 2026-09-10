"""Replay one registered input-only source-unit arm through native decisions."""
import argparse
import hashlib
from pathlib import Path

from research.cross_ai_acceptance import read_case
from research.cross_ai_strategy import research_data_root, run_production_case, write_json
from uquant.contracts.universe import research_industry_input
from uquant.engine import code_fingerprint
from uquant.validation.manifest import verify_data_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('full', 'remove_all_three', 'strict'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--review-sha256', required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--research-data-dir', type=Path)
    parser.add_argument('--research-data-sha256')
    parser.add_argument('--end', default='2026-08-05')
    args = parser.parse_args()
    if code_fingerprint() != args.source_sha256:
        raise ValueError('producer source differs from preregistration')
    if hashlib.sha256(args.review.read_bytes()).hexdigest() != args.review_sha256:
        raise ValueError('taxonomy source differs from preregistration')
    root = research_data_root(args.research_data_dir, args.research_data_sha256)
    expected_data = verify_data_manifest(root)
    case = 'remove_all_three' if args.case == 'strict' else args.case
    excluded = ('sz300666',) if args.case == 'strict' else ()
    with research_industry_input(args.review, expected_sha256=args.review_sha256):
        result = run_production_case(
            case_id=case, start='2023-01-03', end=args.end, output_dir=args.output,
            extra_excluded_symbols=excluded, research_data_dir=args.research_data_dir,
            research_data_sha256=args.research_data_sha256,
        )
        verified = read_case(args.output, case=case, interval=['2023-01-03', args.end],
                             source=args.source_sha256, extra_excluded_symbols=excluded)
        if result['identity']['data'] != expected_data or verify_data_manifest(root) != expected_data:
            raise ValueError('research data identity differs at native readback')
        if result['identity'].get('research_data_manifest_sha256') != args.research_data_sha256:
            raise ValueError('research data seal missing or different')
        write_json(args.output / 'readback.json', {
            'native_readback': True, 'case': args.case, 'research_only': True,
            'data': expected_data, 'review_sha256': args.review_sha256,
            'result_sha256': result['canonical_sha256'],
            'verified_symbol_pnl': verified['verified_symbol_pnl'],
        })


if __name__ == '__main__':
    main()
