"""Reclassify sealed diagnostics without pretending to recompute industry features."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from research.opportunity_capture_audit import rank_ic

RECEIPT_SHA = '72e2ff527087caa3e0d849aea33f789e13af5c15907f1718cac36e07c54999bd'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cohort_record(row: dict[str, Any], mapping: dict[str, dict[str, Any]], *, corrected: bool) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Keep all endpoint labels in a cohort or reject it, never impute missing."""
    if corrected:
        unresolved = [x['symbol'] for x in row['features']
                      if mapping[x['symbol']]['conservative_known_by'] >= row['date']]
        if unresolved:
            return None, {'date': row['date'], 'horizon': row['horizon'],
                          'reason': 'business_source_not_yet_available', 'symbols': unresolved}
    features = [x for x in row['features'] if
                (mapping[x['symbol']]['research_industry'] if corrected else x['industry']) != 'optical']
    if len(features) < 3:
        return None, {'date': row['date'], 'horizon': row['horizon'], 'reason': 'fewer_than_three'}
    labels = row['labels']
    for x in features:
        label = labels[x['symbol']]
        if not row['date'] < label['entry_date'] < label['exit_date'] <= '2026-08-05':
            raise ValueError('noncausal or protected endpoint')
    outcomes = [labels[x['symbol']]['gross_return'] for x in features]
    if not all(math.isfinite(x) for x in outcomes):
        raise ValueError('nonfinite label')
    pool = mean(outcomes)
    methods = {}
    for method, key in (('score', 'score'), ('momentum', 'ret120')):
        selected = sorted(features, key=lambda x: (-x[key], x['symbol']))[:3]
        value = mean(labels[x['symbol']]['gross_return'] for x in selected)
        methods[method] = {'selected': [x['symbol'] for x in selected], 'return': value,
                           'excess': value - pool,
                           'ic': rank_ic([x[key] for x in features], outcomes)}
    return {'date': row['date'], 'horizon': row['horizon'], 'cohort': row['cohort'],
            'members': [x['symbol'] for x in features], 'pool_return': pool, 'methods': methods}, None


def summaries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = defaultdict(list)
    for row in records:
        for period in ('all', '2023-24' if row['date'] < '2025' else '2025-26'):
            groups[(row['cohort'], row['horizon'], period)].append(row)
    return [{'cohort': c, 'horizon': h, 'period': p, 'anchors': len(rows),
             'methods': {m: {'mean_excess': mean(r['methods'][m]['excess'] for r in rows),
                              'positive_fraction': mean(r['methods'][m]['excess'] > 0 for r in rows),
                              'mean_ic': mean(v for r in rows if (v := r['methods'][m]['ic']) is not None)
                              if any(r['methods'][m]['ic'] is not None for r in rows) else None}
                         for m in ('score', 'momentum')}}
            for (c, h, p), rows in sorted(groups.items())]


def analyze(receipt: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    mapping = {x['symbol']: x for x in taxonomy['members']}
    original_members = receipt['universe_provenance']['members']
    if len(mapping) != 34 or set(mapping) != {x['symbol'] for x in original_members}:
        raise ValueError('incomplete/duplicate taxonomy membership')
    for x in original_members:
        if mapping[x['symbol']]['recorded_industry'] != x['industry']:
            raise ValueError('original taxonomy mismatch')
    result: dict[str, Any] = {'diagnostic_only': True, 'future_holdout_used': False,
              'industry_first_recomputed': False, 'production_scores_recomputed': False,
              'new_native_accounts': False, 'cases': {}}
    for name, case in receipt['cases'].items():
        by_industry: defaultdict[str, float] = defaultdict(float)
        for symbol, item in case['capture'].items():
            by_industry[mapping[symbol]['research_industry']] += item['net_pnl']
        total = sum(by_industry.values())
        original_total = sum(case['net_industry_pnl'].values())
        if not math.isclose(total, original_total, rel_tol=1e-12, abs_tol=1e-6):
            raise ValueError('PnL reconciliation failed')
        old, new, excluded = [], [], []
        for row in case['records']:
            # Base cohorts include formerly optical names; old nonoptical rows
            # cannot restore names or uncensor previously dropped endpoints.
            if row['cohort'] not in ('all', 'mature'):
                continue
            a, ea = cohort_record(row, mapping, corrected=False)
            b, eb = cohort_record(row, mapping, corrected=True)
            if ea or eb:
                excluded.append({'base_cohort': row['cohort'], 'old': ea, 'new': eb})
                continue
            if not (a is not None and b is not None):
                raise RuntimeError('Evidence validation failed: a is not None and b is not None')
            old.append(a)
            new.append(b)
        result['cases'][name] = {
            'original_source': case['source'], 'original_result_seal': case['result_seal'],
            'total_net_pnl': total, 'original_industry_pnl': case['net_industry_pnl'],
            'corrected_industry_pnl': dict(sorted(by_industry.items())),
            'optical_pnl_share': by_industry['optical'] / total if total else None,
            'retained_optical_profit_symbols': {s: x['net_pnl'] for s, x in case['capture'].items()
                                               if mapping[s]['research_industry'] == 'optical' and x['net_pnl']},
            'paired_old_summary': summaries(old), 'paired_new_summary': summaries(new),
            'paired_old_records': old, 'paired_new_records': new, 'excluded': excluded}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--taxonomy', type=Path, default=Path('benchmarks/industry_input_v2/taxonomy.json'))
    parser.add_argument('--receipt', type=Path, default=Path('benchmarks/opportunity_capture_receipts.json.gz'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if digest(args.receipt) != RECEIPT_SHA:
        raise ValueError('unexpected saved receipt identity')
    taxonomy = json.loads(args.taxonomy.read_text())
    manifest = Path('uquant/contracts/resources/ai_universe_manifest.json')
    if digest(manifest) != taxonomy['frozen_manifest_sha256']:
        raise ValueError('frozen manifest changed')
    with gzip.open(args.receipt, 'rt') as stream:
        result = analyze(json.load(stream), taxonomy)
    result['inputs'] = {'receipt_sha256': RECEIPT_SHA, 'taxonomy_sha256': digest(args.taxonomy),
                        'script_sha256': digest(Path(__file__)), 'manifest_sha256': digest(manifest)}
    with args.output.open('xb') as stream:
        stream.write(gzip.compress(json.dumps(result, sort_keys=True, allow_nan=False).encode(), mtime=0))
    print(digest(args.output))


if __name__ == '__main__':
    main()
