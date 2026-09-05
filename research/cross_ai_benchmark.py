"""Preregistered research-only monthly trend comparator, never production targets.

Keep intact incumbents, admit at monthly reviews, retain valid monthly BUY remainders
through normal execution expiry, and otherwise hold filled weights. No tuning knobs.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import math
import time
import traceback
from itertools import combinations
from pathlib import Path
from typing import Any, cast

import pandas as pd

from research.cross_ai_strategy import CASE_IDS, ROOT, _diagnostic_json, _identity, _write_json, case_symbols
from uquant.account import account_from_dict
from uquant.application.decision import (
    _assess_decision_risk,
    _bind_decision_account_identity,
    _decision_market_context,
    _validated_decision_symbols,
    _verify_decision_provenance,
)
from uquant.attribution import build_daily_ledger_row, build_economic_attribution
from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.contracts.universe import default_ai_universe
from uquant.engine import INDEX_SYMBOLS, ProductionEngine, attach_target_attribution, performance_metrics
from uquant.execution import merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.market import ReplayUniverse
from uquant.models.strategic_universe import build_strategic_universe_declaration
from uquant.opportunity import classify_opportunity
from uquant.portfolio import current_weights
from uquant.risk import assess_risk
from uquant.risk_sentinel import evaluate_sentinel
from uquant.types import AccountState, Risk, RiskAssessment, Target


def benchmark_identity(case_id: str, start: str, end: str) -> dict[str, Any]:
    identity = _identity(case_id, start, end)
    identity['benchmark_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity['research_account_code_sha256'] = hashlib.sha256(canonical_json_bytes({
        key: identity[key] for key in ('source_sha256', 'runner_sha256', 'benchmark_sha256')
    })).hexdigest()
    identity['benchmark'] = 'monthly_ma60_ma120_ret120_three_names_v1'
    return identity


def _closes(frame: pd.DataFrame, date: pd.Timestamp) -> pd.Series:
    close = frame.loc[:date, 'close'].astype(float)
    if not close.empty and (not close.map(math.isfinite).all() or (close <= 0).any()):
        raise ValueError('nonfinite or nonpositive benchmark close')
    return close


def correlation_groups(
    panel: dict[str, pd.DataFrame], symbols: list[str], date: pd.Timestamp, cfg: SystemConfig,
) -> tuple[list[set[str]], set[tuple[str, str]]]:
    """Full paired window; absence is reported, never interpreted as low correlation."""
    symbols = sorted(symbols)
    returns = {s: _closes(panel[s], date).pct_change(fill_method=None).tail(cfg.correlation_window)
               for s in symbols}
    neighbors = {s: {s} for s in symbols}
    missing: set[tuple[str, str]] = set()
    for left, right in combinations(symbols, 2):
        pair = pd.concat([returns[left], returns[right]], axis=1).dropna()
        value = float(pair.iloc[:, 0].corr(pair.iloc[:, 1])) if len(pair) == cfg.correlation_window else math.nan
        if not math.isfinite(value):
            missing.add((left, right))
        elif value > cfg.risk_correlation:
            neighbors[left].add(right)
            neighbors[right].add(left)
    groups: list[set[str]] = []
    unseen = set(symbols)
    while unseen:
        group, frontier = set(), {min(unseen)}
        while frontier:
            symbol = frontier.pop()
            if symbol not in group:
                group.add(symbol)
                frontier.update(neighbors[symbol] - group)
        unseen -= group
        groups.append(group)
    return groups, missing


def _eligible(engine: ProductionEngine, frame: pd.DataFrame, date: pd.Timestamp) -> bool:
    if date not in frame.index:
        return False
    history = _closes(frame, date)
    if len(history) < max(121, engine.cfg.min_history):
        return False
    values = {}
    for field in ('volume', 'amount'):
        if field not in frame:
            raise ValueError(f'missing/nonfinite benchmark {field}')
        values[field] = float(cast(float, frame.at[date, field]))
        if not math.isfinite(values[field]):
            raise ValueError(f'missing/nonfinite benchmark {field}')
    return bool(
        values['volume'] > 0 and values['amount'] > 0
        and engine.allocator._liquidity_confirmed(frame, date)
        and history.iloc[-1] > history.tail(60).mean() > history.tail(120).mean()
        and history.iloc[-1] / history.iloc[-121] - 1 > 0
    )


def benchmark_targets(
    *, engine: ProductionEngine, account: AccountState, date: pd.Timestamp,
    panel: dict[str, pd.DataFrame], tradable: tuple[str, ...], prices: dict[str, float],
    risk: RiskAssessment, state: dict[str, Any],
) -> tuple[tuple[Target, ...], dict[str, Any]]:
    weights, _ = current_weights(account, prices)
    held = {s for s, w in weights.items() if w > 0}
    pending = {order.symbol: order.target_weight for order in account.pending_orders if order.side == 'BUY'}
    below = state.setdefault('below_ma120', {})
    exiting = set(state.setdefault('exiting', [])) & (held | set(pending))
    for symbol in sorted(held | set(pending)):
        if symbol not in panel:
            raise ValueError(f'missing held benchmark panel: {symbol}')
        if date not in panel[symbol].index:
            continue  # suspended positions keep both their mark and existing trend evidence
        close = _closes(panel[symbol], date)
        if len(close) < 120:
            raise ValueError(f'insufficient held benchmark history: {symbol}')
        below[symbol] = below.get(symbol, 0) + 1 if close.iloc[-1] < close.tail(120).mean() else 0
        if below[symbol] >= 2:
            exiting.add(symbol)
    monthly = state.get('review_month') != date.strftime('%Y-%m')
    selected = sorted((held | set(pending)) - exiting)
    blocked: dict[str, str] = {}
    freeze = bool(risk.freeze_new_risk or risk.evidence.get('freeze_new_risk', False)
                  or risk.evidence.get('freeze_new_buys', False)
                  or risk.state in {Risk.RISK_OFF, Risk.CRISIS} or risk.target_gross_cap <= 0)
    if not math.isfinite(risk.target_gross_cap) or risk.target_gross_cap < 0:
        raise ValueError('nonfinite or negative benchmark risk cap')
    if monthly:
        state['review_month'] = date.strftime('%Y-%m')
        candidates = [s for s in tradable if s not in selected and s not in exiting
                      and _eligible(engine, panel[s], date)]
        candidates.sort(key=lambda s: (-float(_closes(panel[s], date).iloc[-1]
                                              / _closes(panel[s], date).iloc[-121] - 1), s))
        for symbol in candidates:
            if len(selected) >= 3:
                break
            if freeze:
                blocked[symbol] = 'risk_freeze'
                continue
            _, missing = correlation_groups(panel, [*selected, symbol], date, engine.cfg)
            if any(symbol in pair for pair in missing):
                blocked[symbol] = 'insufficient_pair_correlation'
                continue
            selected.append(symbol)
    proposed = {s: 0.30 if monthly else max(weights.get(s, 0.0), pending.get(s, 0.0)) for s in selected}
    for symbol in proposed:
        # Freeze cancels any unfilled risk increase, but permits risk and trend sells.
        if freeze:
            proposed[symbol] = min(proposed[symbol], weights.get(symbol, 0.0))
        proposed[symbol] = min(proposed[symbol], engine.cfg.max_symbol_weight)
    groups, missing = correlation_groups(panel, selected, date, engine.cfg)
    # Insufficient correlation also prevents discretionary monthly top-ups to incumbents.
    for pair in missing:
        for symbol in pair:
            if proposed[symbol] > weights.get(symbol, 0.0):
                proposed[symbol] = weights.get(symbol, 0.0)
                blocked[symbol] = 'insufficient_pair_correlation'
    industries: dict[str, set[str]] = {}
    membership = default_ai_universe()
    for symbol in selected:
        industry = membership.industry_of(symbol, str(date.date()))
        if industry == 'unknown':
            raise ValueError(f'unknown benchmark industry: {symbol}')
        industries.setdefault(industry, set()).add(symbol)
    for group in [*industries.values(), *groups]:
        total = sum(proposed[s] for s in group)
        if total > 0.75:
            for symbol in group:
                proposed[symbol] *= 0.75 / total
    gross_cap = min(0.90, engine.cfg.max_gross, risk.target_gross_cap)
    gross = sum(proposed.values())
    if gross > gross_cap:
        proposed = {s: w * gross_cap / gross for s, w in proposed.items()}
    targets = []
    for symbol in sorted(set(proposed) | held | set(pending)):
        weight = proposed.get(symbol, 0.0)
        risk_trim = symbol not in exiting and weight < weights.get(symbol, 0.0) - 1e-12 and (
            risk.target_gross_cap < min(0.90, sum(weights.values())) - 1e-12)
        targets.append(Target(
            symbol, weight, 'CORE', 0.0, 1.0,
            'research risk cap' if risk_trim else 'research trend exit' if symbol in exiting else 'research monthly trend',
            reduction_policy='RISK_PRIORITY' if risk_trim else 'FIFO',
            reason_code='risk_gross_cap' if risk_trim else 'strategy_target',
            exit_kind='portfolio_risk' if risk_trim else 'strategy',
            origin_subsystem='RISK' if risk_trim else 'LEADER',
            mechanism='RISK_GROSS_CAP' if risk_trim else 'LEADER_LIFECYCLE_EXIT' if symbol in exiting else 'LEADER_SELECTION',
            origin_lifecycle='CORE',
        ))
    state['exiting'] = sorted(exiting)
    return tuple(targets), {'monthly_review': monthly, 'blocked': blocked, 'freeze_new_buys': freeze,
                            'correlation_groups': [sorted(g) for g in groups],
                            'missing_correlations': sorted(missing)}


def submit_targets(
    engine: ProductionEngine, account: AccountState, date: pd.Timestamp,
    targets: tuple[Target, ...], prices: dict[str, float],
) -> tuple[Target, ...]:
    previous = list(account.pending_orders)
    targets = attach_target_attribution(signal_date=str(date.date()), targets=targets,
                                        retained_orders=previous, cfg=engine.cfg)
    planned = plan_orders(signal_date=str(date.date()), targets=targets, account=account,
                          prices=prices, cfg=engine.cfg)
    pending = merge_pending_orders(retained=previous, planned=planned, targets=targets, cfg=engine.cfg)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=pending, submitted_date=str(date.date())))
    return targets


def benchmark_decision(
    engine: ProductionEngine, account: AccountState, date: pd.Timestamp, roles: dict[str, tuple[str, ...]],
    state: dict[str, Any], code_hash: str,
) -> dict[str, Any]:
    date, symbols, durable = _validated_decision_symbols(
        symbols=roles['tradable'], as_of=str(date.date()), account=account)
    inputs = _verify_decision_provenance(engine, date=date, user_symbols=symbols,
                                         durable_symbols=durable, account=account,
                                         code_fingerprint_fn=lambda: code_hash)
    market = _decision_market_context(
        engine, inputs=inputs, account=account,
        strategic_universe_declaration=build_strategic_universe_declaration(
            qualification_reference_symbols=roles['qualification'], risk_reference_symbols=roles['risk']))
    if (market.qualification_reference_symbols != roles['qualification']
            or market.risk_reference_symbols != roles['risk']):
        raise RuntimeError('benchmark stock roles differ from declared scenario')
    risk = _assess_decision_risk(inputs=inputs, market=market, account=account,
                                 assess_risk_fn=assess_risk, evaluate_sentinel_fn=evaluate_sentinel)
    _bind_decision_account_identity(inputs=inputs, account=account)
    opportunity = classify_opportunity(date=date, broad=market.broad, tech=market.tech,
                                        reference_panel=market.reference_panel,
                                        leaders={s: market.structural_leaders[s] for s in symbols
                                                 if s in market.structural_leaders},
                                        risk=risk.state, account=account, cfg=market.cfg, reference_context=None)
    targets, observation = benchmark_targets(engine=engine, account=account, date=date,
                                             panel=market.user_panel, tradable=symbols, prices=market.prices,
                                             risk=risk, state=state)
    targets = submit_targets(engine, account, date, targets, market.prices)
    account.last_successful_run = str(date.date())
    account.data_hash, account.data_hash_as_of = inputs.data_digest, str(date.date())
    account.data_hash_symbols, account.code_hash = list(inputs.current_symbols), inputs.current_code_hash
    if account.strategic_grant or account.strategic_epochs:
        raise RuntimeError('research benchmark unexpectedly acquired strategic grant state')
    return {'targets': targets, 'risk': risk, 'opportunity': opportunity, 'observation': observation,
            'prices': market.prices, 'equity': market.equity}


def run_benchmark_case(*, case_id: str, start: str, end: str, output_dir: Path) -> dict[str, Any]:
    if not '2023-01-03' <= start <= end <= '2026-08-05':
        raise ValueError('benchmark requires frozen historical interval')
    pd.Timestamp(start)
    pd.Timestamp(end)
    case_symbols(case_id, start)
    output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    identity = benchmark_identity(case_id, start, end)
    _write_json(output_dir / 'identity.json', identity)
    engine = ProductionEngine(ROOT / 'data/frozen')
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    state: dict[str, Any] = {}
    rows: list[tuple[pd.Timestamp, float]] = []
    ledger: list[dict[str, Any]] = []
    status, error, expected = 'COMPLETE', '', 0
    raw = output_dir / 'observations.jsonl.gz'
    metrics: dict[str, Any] = {}
    accounting: dict[str, Any] = {'reconciled': False}
    attribution: dict[str, Any] = {}
    try:
        engine.workspace.prepare(ReplayUniverse.from_symbols(
            tradable_symbols=(), reference_symbols=(), index_symbols=INDEX_SYMBOLS))
        sessions = engine.workspace.common_sessions(*INDEX_SYMBOLS)
        sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
        expected = len(sessions)
        if expected < 2:
            raise ValueError('benchmark requires at least two sessions')
        with gzip.open(raw, 'wb') as stream:
            for date in sessions:
                roles = case_symbols(case_id, str(date.date()))
                engine.workspace.prepare(ReplayUniverse.from_symbols(
                    tradable_symbols=roles['tradable'], reference_symbols=roles['risk'], index_symbols=INDEX_SYMBOLS))
                before_fills = len(account.fills)
                engine.execution.execute_open(date=date, account=account,
                                              panel={s: engine.workspace.raw_frame(s) for s in roles['tradable']})
                observation = benchmark_decision(engine, account, date, roles, state,
                                                 identity['research_account_code_sha256'])
                equity = observation['equity']
                if not math.isfinite(equity) or not math.isfinite(account.cash) or account.cash < -1e-6:
                    raise RuntimeError('invalid benchmark equity/cash')
                if any(p.shares < 0 for p in account.positions.values()):
                    raise RuntimeError('negative benchmark shares')
                targets, risk = observation['targets'], observation['risk']
                row = build_daily_ledger_row(
                    date=str(date.date()), account=account, close_prices=observation['prices'],
                    previous_equity=rows[-1][1] if rows else account.initial_cash,
                    target_weights={t.symbol: t.weight for t in targets}, target_gross=sum(t.weight for t in targets),
                    risk_gross_cap=risk.target_gross_cap, system_gross_cap=min(0.90, engine.cfg.max_gross),
                    risk_state=risk.state.value, opportunity=observation['opportunity'].value)
                stream.write(canonical_json_bytes(_diagnostic_json({
                    'date': str(date.date()), 'roles': roles, **observation, 'ledger': row,
                    'state': state, 'new_fills': account.fills[before_fills:],
                    'pending_orders': account.pending_orders, 'account_risk': {
                        'capital_peak': account.capital_peak, 'operating_peak': account.operating_peak,
                        'capital_budget_level': account.capital_budget_level, 'chronic_level': account.chronic_level},
                })) + b'\n')
                rows.append((date, equity))
                ledger.append(row)
                if len(rows) % 50 == 0:
                    stream.flush()
                    print(f"benchmark {case_id}: {len(rows)}/{expected} sessions, "
                          f"{date.date()}, {time.monotonic() - started:.1f}s", flush=True)
    except Exception as exc:
        status, error = 'REPLAY_ERROR', f'{type(exc).__name__}: {exc}'
        (output_dir / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
    # Persist failed state before validation: codec errors must not erase the evidence.
    payload = account.to_dict()
    _write_json(output_dir / 'final_account.json', _diagnostic_json(payload))
    _write_json(output_dir / 'benchmark_state.json', _diagnostic_json(state))
    _write_json(output_dir / 'orders.json', _diagnostic_json(account.order_ledger))
    _write_json(output_dir / 'fills.json', _diagnostic_json(account.fills))
    try:
        account_from_dict(payload)
        if rows:
            last = rows[-1][0]
            metrics = performance_metrics(
                equity_rows=rows, fills=account.fills, orders=account.order_ledger,
                initial_cash=account.initial_cash, risk_events=account.risk_events,
                benchmark_total_return=engine.workspace.price('sh000682', last)
                / engine.workspace.price('sh000682', rows[0][0]) - 1)
            metrics['final_wealth'] = rows[-1][1] / account.initial_cash
            attribution = build_economic_attribution(
                account=account, final_prices={s: engine.workspace.price(s, last) for s in account.positions},
                sessions=tuple(str(d.date()) for d, _ in rows), economic_start=start,
                economic_end=str(last.date()), final_equity=rows[-1][1], daily_ledger=ledger,
                benchmark_close={str(d.date()): engine.workspace.price('sh000682', d) for d, _ in rows})
            accounting = attribution['accounting']
            if not accounting['reconciled']:
                raise RuntimeError('benchmark ledger does not reconcile')
        if identity != benchmark_identity(case_id, start, end):
            raise RuntimeError('benchmark identity changed during replay')
    except Exception as exc:
        status, error = 'REPLAY_ERROR', f'{error}; finalization: {type(exc).__name__}: {exc}'
        (output_dir / 'finalization-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
    result = {'schema_version': 1, 'status': status, 'error': error, 'diagnostic_only': True,
              'authoritative_acceptance': False, 'future_holdout_used': False, 'identity': identity,
              'sessions': len(rows), 'expected_sessions': expected, 'metrics': metrics,
              'accounting': accounting, 'attribution': attribution,
              'elapsed_seconds': time.monotonic() - started,
              'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest() if raw.exists() else '',
              'final_account_sha256': hashlib.sha256((output_dir / 'final_account.json').read_bytes()).hexdigest(),
              'benchmark_state_sha256': hashlib.sha256((output_dir / 'benchmark_state.json').read_bytes()).hexdigest()}
    result['canonical_sha256'] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
    _write_json(output_dir / 'result.json', result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASE_IDS, required=True)
    parser.add_argument('--start', default='2023-01-03')
    parser.add_argument('--end', default='2026-08-05')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    result = run_benchmark_case(case_id=args.case, start=args.start, end=args.end, output_dir=args.output_dir)
    print(f"{result['status']}: {result['sessions']}/{result['expected_sessions']} sessions; {result['error']}")
    return 0 if result['status'] == 'COMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
