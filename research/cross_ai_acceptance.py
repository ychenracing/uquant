"""Executable, preregistered cross-AI economic comparison gates.

Passing this comparison never substitutes for the complete L4 promotion gate.
Missing, failed, mismatched or unsealed cases fail closed and remain visible.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from research.cross_ai_strategy import ROOT, case_symbols
from uquant.account import load_account
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.engine import code_fingerprint, performance_metrics

CONTRACT_PATH = ROOT / 'benchmarks/cross_ai_core_strategy_contract.json'
REMOVALS = ('remove_all_three', 'no_optical')
CONTINUOUS = 'continuous_ai_era'
HALVES = ('h1_2023', 'h2_2023', 'h1_2024', 'h2_2024')


def number(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'missing/nonfinite numeric metric: {key}')
    return float(value)


def read_case(directory: Path, *, case: str, interval: list[str], source: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((directory / 'result.json').read_text())
    seal = result.pop('canonical_sha256')
    if seal != hashlib.sha256(canonical_json_bytes(result)).hexdigest():
        raise ValueError('case result seal mismatch')
    if result['status'] != 'COMPLETE' or result['sessions'] != result['expected_sessions']:
        raise ValueError(f"incomplete case: {result['status']}: {result.get('error', '')}")
    if result['future_holdout_used'] or not result['accounting']['reconciled']:
        raise ValueError('protected data or unreconciled account')
    identity = result['identity']
    if (identity['case_id'] != case or [identity['start'], identity['end']] != interval
            or identity['source_sha256'] != source
            or identity['config_sha256'] != config_fingerprint(DEFAULT_CONFIG)
            or identity['runner_sha256'] != hashlib.sha256((ROOT / 'research/cross_ai_strategy.py').read_bytes()).hexdigest()):
        raise ValueError('case/source/config/interval identity mismatch')
    raw = directory / 'observations.jsonl.gz'
    account_path = directory / 'final_account.json'
    if hashlib.sha256(raw.read_bytes()).hexdigest() != result['raw_sha256']:
        raise ValueError('raw observation seal mismatch')
    if hashlib.sha256(account_path.read_bytes()).hexdigest() != result['final_account_sha256']:
        raise ValueError('raw account seal mismatch')
    account = load_account(account_path)
    if account.code_hash != source or account.initial_cash != DEFAULT_CONFIG.initial_cash:
        raise ValueError('account source/initial-capital mismatch')
    previous, rows = '', 0
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    with gzip.open(raw, 'rt', encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            date = row['date']
            if not interval[0] <= date <= interval[1] or date <= previous:
                raise ValueError('noncausal or out-of-interval observation')
            roles = case_symbols(case, date)
            observed = row['observation']['strategic_universe_roles']
            for field, expected in (
                ('tradable_symbols', roles['tradable']),
                ('qualification_reference_symbols', roles['qualification']),
                ('risk_reference_symbols', tuple(sorted(roles['risk'] + roles['indexes']))),
            ):
                if tuple(observed[field]) != expected:
                    raise ValueError(f'role mismatch: {field}')
            if any(fill['signal_date'] >= fill['fill_date'] for fill in row['new_fills']):
                raise ValueError('fill does not follow its signal')
            rows += 1
            previous = date
            equity_rows.append((pd.Timestamp(date), number(row, 'equity')))
    if rows != result['sessions']:
        raise ValueError('raw observation row count mismatch')
    for field in ('final_wealth', 'max_drawdown', 'account_orders', 'annual_turnover', 'fees', 'slippage_cost'):
        number(result['metrics'], field)
    recomputed = performance_metrics(
        equity_rows=equity_rows, fills=account.fills, orders=account.order_ledger,
        initial_cash=account.initial_cash, risk_events=account.risk_events,
        benchmark_total_return=number(result['metrics'], 'benchmark_total_return'))
    recomputed['final_wealth'] = equity_rows[-1][1] / account.initial_cash
    for field in ('final_wealth', 'max_drawdown', 'account_orders', 'annual_turnover', 'fees', 'slippage_cost'):
        if not math.isclose(number(result['metrics'], field), number(recomputed, field), rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError(f'literal metric differs from raw equity/orders/fills: {field}')
    result['canonical_sha256'] = seal
    return result


def check_metrics(
    *, case: str, window: str, metrics: dict[str, Any], baseline: dict[str, Any],
    benchmark: dict[str, Any], thresholds: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    wealth, drawdown, orders = (number(metrics, key) for key in ('final_wealth', 'max_drawdown', 'account_orders'))
    t = thresholds
    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)
    if case in ('champion', 'full'):
        require(wealth >= t[f'{case}_minimum_final_wealth'], 'champion/full wealth floor')
        require(drawdown <= t[f'{case}_maximum_drawdown'], 'champion/full drawdown ceiling')
        require(orders <= t[f'{case}_maximum_orders'], 'champion/full order ceiling')
        return failures
    require(drawdown <= t['removal_maximum_drawdown'], 'removal drawdown ceiling')
    if window == CONTINUOUS:
        require(wealth >= t['removal_minimum_final_wealth'], 'substantial removal wealth floor')
        require(wealth - number(baseline, 'final_wealth') >= t['removal_minimum_wealth_delta'], 'removal wealth delta')
        require(wealth >= number(benchmark, 'final_wealth') * t['removal_benchmark_wealth_ratio'], 'same-pool benchmark floor')
        require(orders <= t['removal_maximum_orders'], 'removal order ceiling')
        require(number(metrics, 'annual_turnover') <= t['removal_maximum_annual_turnover'], 'turnover ceiling')
        cost = number(metrics, 'fees') + number(metrics, 'slippage_cost')
        require(cost / DEFAULT_CONFIG.initial_cash <= t['removal_maximum_all_in_cost_initial_cash_fraction'], 'all-in cost ceiling')
    elif window in HALVES:
        require(wealth >= number(baseline, 'final_wealth') * t['half_year_minimum_wealth_ratio_to_valid_baseline'], 'half-year wealth retention')
        require(drawdown <= number(baseline, 'max_drawdown') + t['half_year_maximum_drawdown_buffer'], 'half-year drawdown retention')
        require(orders <= t['half_year_maximum_orders'], 'half-year order ceiling')
    else:
        require(wealth >= max(t['post2025_minimum_final_wealth'], number(benchmark, 'final_wealth') * t['post2025_benchmark_wealth_ratio']), 'disjoint later-window benchmark floor')
        require(orders <= t['post2025_maximum_orders'], 'later-window order ceiling')
    return failures


def evaluate(candidate_root: Path, *, principal_only: bool = False) -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text())
    baseline_path = ROOT / contract['basis']['baseline_file']
    if hashlib.sha256(baseline_path.read_bytes()).hexdigest() != contract['basis']['baseline_file_sha256']:
        raise ValueError('frozen baseline identity mismatch')
    baseline = json.loads(baseline_path.read_text())
    frozen = {row['case']: row for row in baseline['records']}
    source = code_fingerprint()
    rows: list[dict[str, Any]] = []
    improved = dict.fromkeys(REMOVALS, 0)
    specs = [(case, CONTINUOUS) for case in contract['cases']]
    if not principal_only:
        specs += [(case, window) for case in REMOVALS for window in (*HALVES, 'bull_crash_2025_2026')]
    for case, window in specs:
        name = case if window == CONTINUOUS else f'{case}-{window}'
        old = frozen[f'production-{name}']
        comparator = frozen[f'benchmark-{name}']
        row: dict[str, Any] = {'case': case, 'window': window, 'path': str(candidate_root / name),
                               'status': 'FAIL', 'failures': [], 'old_status': old['status']}
        try:
            report = read_case(candidate_root / name, case=case, interval=contract['windows'][window], source=source)
            identity = report['identity']
            for key in ('data', 'universe_sha256', 'runtime'):
                if identity[key] != old['identity'][key]:
                    raise ValueError(f'baseline comparison input mismatch: {key}')
            metrics = report['metrics']
            failures = check_metrics(case=case, window=window, metrics=metrics, baseline=old['metrics'],
                                      benchmark=comparator['metrics'], thresholds=contract['thresholds'])
            row.update(metrics={key: metrics[key] for key in ('final_wealth', 'max_drawdown', 'account_orders', 'fees', 'slippage_cost')},
                       failures=failures, status='FAIL' if failures else 'PASS', result_seal=report['canonical_sha256'])
            if case in REMOVALS and window in HALVES:
                wealth = number(metrics, 'final_wealth')
                improved[case] += int(
                    wealth - number(old['metrics'], 'final_wealth') >= contract['thresholds']['improved_pre2025_wealth_delta_minimum']
                    and wealth >= contract['thresholds']['improved_window_final_wealth_minimum']
                    and wealth >= number(comparator['metrics'], 'final_wealth'))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError) as exc:
            row['failures'] = [f'{type(exc).__name__}: {exc}']
        rows.append(row)
    cross_failures = [] if principal_only else [f'{case}: no required disjoint pre-2025 improvement'
        for case, count in improved.items() if count < contract['thresholds']['improved_pre2025_windows_minimum']]
    return {'contract_id': contract['contract_id'], 'contract_sha256': hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest(),
            'scope': 'principal_diagnostic' if principal_only else 'nominal_comparison',
            'status': 'PASS' if all(r['status'] == 'PASS' for r in rows) and not cross_failures else 'FAIL',
            'authoritative_promotion': False, 'source_sha256': source, 'rows': rows,
            'cross_window_failures': cross_failures,
            'remaining_final_gates': [s for s in contract['final_required_evidence'] if s != 'nominal']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-root', type=Path, required=True)
    parser.add_argument('--principal-only', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.candidate_root, principal_only=args.principal_only)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(result['scope'], result['status'])
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
