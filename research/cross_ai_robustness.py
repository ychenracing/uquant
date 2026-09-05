"""Deterministic frozen robustness shards; never a substitute for complete L4.

Plan once, execute individual source-matching shards, and evaluate every required
shard. Existing evidence is verified and reused, never overwritten or silently skipped.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import statistics
import traceback
from pathlib import Path
from typing import Any

from research.cross_ai_acceptance import CONTRACT_PATH, config_payload_sha256, number, read_case
from research.cross_ai_strategy import ROOT, run_production_case, write_json
from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.engine import code_fingerprint

CASES = ('champion', 'remove_all_three', 'no_optical')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, 'canonical_sha256': hashlib.sha256(canonical_json_bytes(value)).hexdigest()}


def _read_sealed(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text())
    seal = value.pop('canonical_sha256')
    if seal != hashlib.sha256(canonical_json_bytes(value)).hexdigest():
        raise ValueError(f'seal mismatch: {path}')
    return value


def _contract() -> tuple[dict[str, Any], dict[str, Any]]:
    contract = json.loads(CONTRACT_PATH.read_text())
    baseline_path = ROOT / contract['basis']['baseline_file']
    if _sha(baseline_path) != contract['basis']['baseline_file_sha256']:
        raise ValueError('frozen baseline seal mismatch')
    baseline = json.loads(baseline_path.read_text())
    old = next(row['identity'] for row in baseline['records'] if row['case'] == 'production-champion')
    return contract, old


def scenario_specs(contract: dict[str, Any], candidate_config: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    def add(role: str, group: str, label: str, case: str, **options: Any) -> None:
        specs.append({'id': f'{role}-{label}-{case}', 'source_role': role, 'group': group,
                      'case': case, 'overrides': {}, 'offset': 0, **options})
    for case in CASES:
        add('new', 'nominal', 'nominal', case)
    for profile, changes in contract['profiles'].items():
        group = 'cost_stress' if profile == 'cost_stress' else 'parameter_neighbors'
        if group == 'parameter_neighbors' and len(changes) != 1:
            raise ValueError('neighbor must change exactly one frozen field')
        absent = sorted(set(changes) - set(candidate_config))
        if absent and group == 'cost_stress':
            raise ValueError('cost stress controls cannot be waived')
        for case in CASES:
            add('new', group, profile, case, overrides=changes, deleted_fields=absent)
    conditions = contract['initial_conditions']
    for case in conditions['cases']:
        for offset in conditions['start_session_offsets']:
            for role in ('old', 'new'):
                add(role, 'paired_initial_conditions', f'offset{offset}', case, offset=offset,
                    pair=f'old-offset{offset}-{case}')
        for multiplier in conditions['initial_cash_multipliers']:
            label = f'cash{multiplier:g}'
            for role in ('old', 'new'):
                add(role, 'paired_initial_conditions', label, case, cash_multiplier=multiplier,
                    pair=f'old-{label}-{case}')
    for case in contract['contributor_robustness']['cases']:
        for role in ('old', 'new'):
            add(role, 'best_contributor_removal', 'best_removed', case,
                contributor_from=f'new-nominal-{case}', pair=f'old-best_removed-{case}')
    return specs


def make_plan(*, candidate_source: str, old_config: dict[str, Any] | None = None) -> dict[str, Any]:
    contract, old = _contract()
    original_config = DEFAULT_CONFIG.to_dict() if old_config is None else old_config
    if config_payload_sha256(original_config) != old['config_sha256']:
        raise ValueError('supply --old-config-json from the verified baseline checkout')
    if len(candidate_source) != 64 or any(c not in '0123456789abcdef' for c in candidate_source):
        raise ValueError('candidate source must be an exact SHA256 fingerprint')
    candidate_config = DEFAULT_CONFIG.to_dict()
    return _seal({
        'schema_version': 1, 'contract_sha256': _sha(CONTRACT_PATH), 'contract_id': contract['contract_id'],
        'runner_sha256': _sha(ROOT / 'research/cross_ai_strategy.py'),
        'evaluator_sha256': _sha(ROOT / 'research/cross_ai_acceptance.py'),
        'orchestrator_sha256': _sha(Path(__file__)),
        'sources': {'new': {'source_sha256': candidate_source, 'config': candidate_config},
                    'old': {'source_sha256': old['source_sha256'], 'config': original_config}},
        'input_identity': {key: old[key] for key in ('data', 'runtime', 'universe_sha256')},
        'interval': contract['windows']['continuous_ai_era'],
        'specs': scenario_specs(contract, candidate_config),
        'initial_condition_design': 'one factor at a time; offsets and cash multipliers are not crossed',
        'tail_population': 'all new-source non-deleted continuous robustness shards, including nominal; old pairs excluded',
        'authoritative_promotion': False,
    })


def validate_plan(plan: dict[str, Any]) -> None:
    contract, old = _contract()
    if (plan['contract_sha256'] != _sha(CONTRACT_PATH)
            or plan['runner_sha256'] != _sha(ROOT / 'research/cross_ai_strategy.py')
            or plan['evaluator_sha256'] != _sha(ROOT / 'research/cross_ai_acceptance.py')
            or plan['orchestrator_sha256'] != _sha(Path(__file__))
            or plan['interval'] != contract['windows']['continuous_ai_era']
            or plan['sources']['old']['source_sha256'] != old['source_sha256']
            or config_payload_sha256(plan['sources']['old']['config']) != old['config_sha256']
            or plan['input_identity'] != {key: old[key] for key in ('data', 'runtime', 'universe_sha256')}
            or plan['specs'] != scenario_specs(contract, plan['sources']['new']['config'])):
        raise ValueError('plan differs from frozen scenario/source/runner contract')


def effective_config(plan: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    config = dict(plan['sources'][spec['source_role']]['config'])
    config.update(spec['overrides'])
    if 'cash_multiplier' in spec:
        config['initial_cash'] *= spec['cash_multiplier']
    return config


def deletion_evidence(fields: list[str], source: str) -> dict[str, Any]:
    if code_fingerprint() != source:
        raise ValueError('deletion proof requires exact candidate source')
    present = set(fields) & set(DEFAULT_CONFIG.to_dict())
    reads: list[str] = []
    inventory: dict[str, str] = {}
    for path in sorted((ROOT / 'uquant').rglob('*.py')):
        relative = str(path.relative_to(ROOT))
        inventory[relative] = _sha(path)
        for node in ast.walk(ast.parse(path.read_text())):
            value = (node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name)
                     else node.arg if isinstance(node, ast.keyword)
                     else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
            if value in fields:
                reads.append(f'{relative}:{getattr(node, "lineno", 0)}:{value}')
    if present or reads:
        raise ValueError(f'control is not deleted: config={sorted(present)}, executable references={reads[:8]}')
    return {'source_sha256': source, 'fields': fields, 'config_absent': True,
            'executable_references': reads, 'production_file_sha256': inventory}


def largest_positive_contributor(report: dict[str, Any]) -> str:
    pnl: dict[str, Any] = report['verified_symbol_pnl']
    ranked = sorted((symbol for symbol in pnl if number(pnl, symbol) > 0), key=lambda s: (-number(pnl, s), s))
    if not ranked:
        raise ValueError('nominal has no positive-PnL symbol; contributor deletion is unavailable')
    return ranked[0]


def _spec(plan: dict[str, Any], shard: str) -> dict[str, Any]:
    return next(spec for spec in plan['specs'] if spec['id'] == shard)


def read_shard(root: Path, plan: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    exclusions: tuple[str, ...] = ()
    if 'contributor_from' in spec:
        nominal = read_shard(root, plan, _spec(plan, spec['contributor_from']))
        exclusions = (largest_positive_contributor(nominal),)
    report = read_case(root / spec['id'], case=spec['case'], interval=plan['interval'],
                       source=plan['sources'][spec['source_role']]['source_sha256'],
                       effective_config=effective_config(plan, spec), start_session_offset=spec['offset'],
                       extra_excluded_symbols=exclusions, runner_sha256=plan['runner_sha256'])
    if any(report['identity'][key] != value for key, value in plan['input_identity'].items()):
        raise ValueError('shard data/universe/runtime differs from paired frozen input')
    return report


def run_shard(root: Path, plan: dict[str, Any], shard: str) -> dict[str, Any]:
    validate_plan(plan)
    spec = _spec(plan, shard)
    root.mkdir(parents=True, exist_ok=True)
    checkpoint = root / f'{shard}.checkpoint.json'
    outcome: dict[str, Any] = {'shard': shard, 'status': 'FAIL', 'source_role': spec['source_role'],
                               'contract_sha256': plan['contract_sha256'], 'reused': False}
    try:
        source = plan['sources'][spec['source_role']]['source_sha256']
        if code_fingerprint() != source or DEFAULT_CONFIG.to_dict() != plan['sources'][spec['source_role']]['config']:
            raise ValueError('execute this shard in its matching source/config checkout')
        if spec.get('deleted_fields'):
            proof = deletion_evidence(spec['deleted_fields'], source)
            directory = root / shard
            if directory.exists():
                if _read_sealed(directory / 'deletion.json') != proof:
                    raise ValueError('existing deletion evidence mismatch')
                outcome['reused'] = True
            else:
                directory.mkdir()
                write_json(directory / 'deletion.json', _seal(proof))
            outcome.update(status='CONTROL_DELETED', fields=spec['deleted_fields'])
        else:
            if (root / shard).exists():
                report = read_shard(root, plan, spec)
                outcome['reused'] = True
            else:
                exclusions: tuple[str, ...] = ()
                if 'contributor_from' in spec:
                    nominal = read_shard(root, plan, _spec(plan, spec['contributor_from']))
                    exclusions = (largest_positive_contributor(nominal),)
                config = DEFAULT_CONFIG.override(**effective_config(plan, spec))
                run_production_case(case_id=spec['case'], start=plan['interval'][0], end=plan['interval'][1],
                                    output_dir=root / shard, cfg=config, start_session_offset=spec['offset'],
                                    extra_excluded_symbols=exclusions)
                report = read_shard(root, plan, spec)
            outcome.update(status='COMPLETE', result_seal=report['canonical_sha256'])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError) as exc:
        outcome.update(error=f'{type(exc).__name__}: {exc}', traceback="".join(traceback.format_exception(exc)))
    # Each attempt gets its own checkpoint; neither failed raw cases nor prior attempts are overwritten.
    suffix = 1
    while checkpoint.exists():
        checkpoint = root / f'{shard}.checkpoint-{suffix}.json'
        suffix += 1
    write_json(checkpoint, _seal(outcome))
    return outcome


def metric_failures(spec: dict[str, Any], metrics: dict[str, Any], nominal: dict[str, Any],
                    paired: dict[str, Any] | None, thresholds: dict[str, Any], cash: float) -> list[str]:
    group, case = spec['group'], spec['case']
    t = thresholds
    wealth = number(metrics, 'final_wealth')
    failures: list[str] = []
    def require(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)
    if group == 'cost_stress':
        require(wealth >= number(nominal, 'final_wealth') * t['cost_stress_minimum_wealth_ratio'], 'cost wealth retention')
        if case != 'champion':
            require(wealth >= t['cost_stress_removal_minimum_wealth'], 'cost removal wealth floor')
    elif group == 'parameter_neighbors':
        require(wealth >= number(nominal, 'final_wealth') * t['sensitivity_minimum_wealth_ratio'], 'neighbor wealth retention')
        if case != 'champion':
            require(wealth >= t['sensitivity_removal_minimum_wealth'], 'neighbor removal wealth floor')
    elif group == 'paired_initial_conditions':
        if paired is None:
            raise ValueError('missing independently executed old-source initial condition')
        require(wealth >= number(paired, 'final_wealth') * t['initial_conditions_minimum_wealth_ratio_to_paired_baseline'],
                'paired initial wealth retention')
    elif group == 'best_contributor_removal':
        if paired is None:
            raise ValueError('missing old-source contributor removal')
        require(wealth >= t['new_best_contributor_removal_minimum_wealth'], 'best contributor removal wealth floor')
    require(number(metrics, 'max_drawdown') <= t['champion_maximum_drawdown'], 'absolute drawdown ceiling')
    require(number(metrics, 'account_orders') <= t['champion_maximum_orders' if case == 'champion' else 'removal_maximum_orders'],
            'absolute order ceiling')
    if case != 'champion':
        require(number(metrics, 'annual_turnover') <= t['removal_maximum_annual_turnover'], 'absolute turnover ceiling')
        require((number(metrics, 'fees') + number(metrics, 'slippage_cost')) / cash
                <= t['removal_maximum_all_in_cost_initial_cash_fraction'], 'absolute cost ceiling')
    return failures


def evaluate_robustness(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan(plan)
    contract, _ = _contract()
    t = contract['thresholds']
    rows: list[dict[str, Any]] = []
    reports: dict[str, dict[str, Any]] = {}
    for spec in plan['specs']:
        row: dict[str, Any] = {'shard': spec['id'], 'group': spec['group'], 'status': 'FAIL', 'failures': []}
        try:
            if spec.get('deleted_fields'):
                proof = _read_sealed(root / spec['id'] / 'deletion.json')
                if proof != deletion_evidence(spec['deleted_fields'], plan['sources']['new']['source_sha256']):
                    raise ValueError('deletion proof differs from actual source inventory')
                row.update(status='CONTROL_DELETED', fields=spec['deleted_fields'])
            else:
                report = read_shard(root, plan, spec)
                reports[spec['id']] = report
                row.update(status='PASS', result_seal=report['canonical_sha256'], metrics=report['metrics'])
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, EOFError) as exc:
            row['failures'] = [f'{type(exc).__name__}: {exc}']
        rows.append(row)
    ratios: dict[str, list[float]] = {case: [] for case in CASES}
    population: list[dict[str, Any]] = []
    for spec, row in zip(plan['specs'], rows, strict=True):
        if row['status'] != 'PASS' or spec['source_role'] != 'new':
            continue
        try:
            metrics = reports[spec['id']]['metrics']
            nominal = reports[f"new-nominal-{spec['case']}"]['metrics']
            paired_report = reports[spec['pair']] if 'pair' in spec else None
            if paired_report:
                pair_identity = paired_report['identity']
                own_identity = reports[spec['id']]['identity']
                for key in ('initial_cash', 'start_session_offset', 'extra_excluded_symbols', 'session_dates', 'data', 'universe_sha256', 'runtime'):
                    if pair_identity[key] != own_identity[key]:
                        raise ValueError(f'paired scenario mismatch: {key}')
            failures = metric_failures(spec, metrics, nominal,
                                       paired_report['metrics'] if paired_report else None, t,
                                       effective_config(plan, spec)['initial_cash'])
            row.update(failures=failures, status='FAIL' if failures else 'PASS')
            if spec['group'] == 'parameter_neighbors':
                ratios[spec['case']].append(number(metrics, 'final_wealth') / number(nominal, 'final_wealth'))
            population.append(metrics)
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            row.update(status='FAIL', failures=[f'{type(exc).__name__}: {exc}'])
    aggregate_failures: list[str] = []
    for case, values in ratios.items():
        expected = sum(s['case'] == case and s['group'] == 'parameter_neighbors' and not s.get('deleted_fields')
                       for s in plan['specs'])
        if len(values) != expected:
            aggregate_failures.append(f'{case}: missing neighbor metrics')
        elif values and statistics.median(values) < t['sensitivity_median_wealth_ratio']:
            aggregate_failures.append(f'{case}: neighbor median retention')
    expected_population = sum(s['source_role'] == 'new' and not s.get('deleted_fields') for s in plan['specs'])
    tails: dict[str, float] = {}
    if len(population) != expected_population or not population:
        aggregate_failures.append('missing complete robustness tail population')
    else:
        def quantile(key: str, q: float) -> float:
            values = sorted(number(row, key) for row in population)
            point = (len(values) - 1) * q
            lower = math.floor(point)
            return values[lower] + (values[math.ceil(point)] - values[lower]) * (point - lower)
        tails = {'p90_drawdown': quantile('max_drawdown', 0.9), 'p10_wealth': quantile('final_wealth', 0.1),
                 'p90_orders': quantile('account_orders', 0.9),
                 'positive_fraction': sum(number(r, 'final_wealth') > 1.0 for r in population) / len(population)}
        for key, limit, upper in [('p90_drawdown', t['maximum_p90_drawdown'], True),
                                   ('p10_wealth', t['minimum_p10_wealth'], False),
                                   ('p90_orders', t['maximum_p90_orders'], True),
                                   ('positive_fraction', t['minimum_positive_return_fraction'], False)]:
            if (tails[key] > limit) if upper else (tails[key] < limit):
                aggregate_failures.append(f'robustness tail: {key}')
    groups = {'cost_stress', 'parameter_neighbors', 'paired_initial_conditions', 'best_contributor_removal'}
    return _seal({'scope': 'frozen_robustness_comparison', 'contract_sha256': plan['contract_sha256'],
                  'status': 'PASS' if all(r['status'] in {'PASS', 'CONTROL_DELETED'} for r in rows)
                  and not aggregate_failures else 'FAIL', 'authoritative_promotion': False,
                  'rows': rows, 'aggregate_failures': aggregate_failures, 'tail_population': plan['tail_population'],
                  'tail_metrics': tails, 'neighbor_ratios': ratios,
                  'remaining_final_gates': [gate for gate in contract['final_required_evidence'] if gate not in groups]})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan_parser = commands.add_parser('plan')
    plan_parser.add_argument('--candidate-source', required=True)
    plan_parser.add_argument('--old-config-json', type=Path)
    plan_parser.add_argument('--output', type=Path, required=True)
    for command in ('run', 'evaluate'):
        child = commands.add_parser(command)
        child.add_argument('--plan', type=Path, required=True)
        child.add_argument('--root', type=Path, required=True)
        if command == 'run':
            child.add_argument('--shard', required=True)
        else:
            child.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'plan':
        result = make_plan(candidate_source=args.candidate_source,
                           old_config=json.loads(args.old_config_json.read_text()) if args.old_config_json else None)
    else:
        plan = _read_sealed(args.plan)
        result = (run_shard(args.root, plan, args.shard) if args.command == 'run'
                  else evaluate_robustness(args.root, plan))
    if args.command != 'run':
        with args.output.open('xb') as stream:
            stream.write(canonical_json_bytes(result) + b'\n')
    print(result.get('status', 'PLAN_WRITTEN'))
    return 0 if result.get('status', 'PASS') in {'PASS', 'COMPLETE', 'CONTROL_DELETED'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
