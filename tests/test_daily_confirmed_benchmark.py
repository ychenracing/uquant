"""Causal entry confirmation and native research-variant identity checks."""
import gzip
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pytest

from research.cross_ai_benchmark import benchmark_targets, read_benchmark_case, run_benchmark_case
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.types import AccountState, Risk, RiskAssessment, Target


def test_daily_entry_requires_five_current_sessions_and_resets() -> None:
    engine = ProductionEngine('data/frozen')
    dates = pd.bdate_range('2022-01-03', periods=150)
    frame = pd.DataFrame({'close': [10 + i * .1 for i in range(150)],
                          'volume': 10_000_000., 'amount': 1_000_000_000.}, index=dates)
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    state: dict[str, Any] = {}
    risk = RiskAssessment(state=Risk.NORMAL, target_gross_cap=1.0, votes=0,
                          evidence={}, reasons=(), shock_state='NORMAL')

    def decide(i: int, assessment: RiskAssessment = risk) -> tuple[Target, ...]:
        return benchmark_targets(engine=engine, account=account, date=dates[i],
                                 panel={'sz300308': frame}, tradable=('sz300308',),
                                 prices={'sz300308': float(cast(float, frame.at[dates[i], 'close']))},
                                 risk=assessment, state=state, daily_confirmed=True)[0]

    assert all(not decide(i) for i in range(125, 129))
    assert not decide(128)  # repeated observation cannot manufacture the fifth day
    assert decide(129)[0].weight == .30
    frame.at[dates[130], 'close'] = 1.0
    assert not decide(130)
    assert all(not decide(i) for i in range(131, 135))
    frozen = RiskAssessment(state=Risk.RISK_OFF, target_gross_cap=.5, votes=0,
                            evidence={}, reasons=(), shock_state='NORMAL')
    assert not decide(135, frozen)
    assert decide(136)[0].weight == .30


def test_daily_native_replay_has_next_open_fills_and_variant_bound_readback(tmp_path: Path) -> None:
    output = tmp_path / 'daily'
    result = run_benchmark_case(case_id='full', start='2023-01-03', end='2023-01-13',
                                output_dir=output, daily_confirmed=True)
    assert result['status'] == 'COMPLETE', result['error']
    assert read_benchmark_case(output, case_id='full', start='2023-01-03',
                               end='2023-01-13', daily_confirmed=True)['status'] == 'COMPLETE'
    with pytest.raises(ValueError, match='identity'):
        read_benchmark_case(output, case_id='full', start='2023-01-03', end='2023-01-13')
    with gzip.open(output / 'observations.jsonl.gz', 'rt') as stream:
        import json
        rows = [json.loads(line) for line in stream]
    fills = [fill for row in rows for fill in row['new_fills']]
    assert fills
    assert all(fill['signal_date'] < fill['fill_date'] for fill in fills)
    assert all(not row['new_fills'] for row in rows[:5])
