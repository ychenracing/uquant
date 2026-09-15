"""Matched frozen-QFQ passive comparison; research, not raw corporate-action audit."""
import dataclasses
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.execution.fees import fee_components
from uquant.execution.market_constraints import market_execution_blocked
from uquant.market import ReplayUniverse
from uquant.validation.universe import default_ai_universe


def run(root, removed):
    cfg = DEFAULT_CONFIG
    universe = default_ai_universe()
    contract = json.loads((root / 'benchmarks/absolute_generalization_acceptance_contract.json').read_text())
    symbols = tuple(s for s in contract['canonical_universe'] if s != removed)
    engine = ProductionEngine(root / 'data/frozen')
    engine.workspace.prepare(ReplayUniverse.from_symbols(tradable_symbols=symbols, reference_symbols=symbols, index_symbols=INDEX_SYMBOLS))
    frames = {s: engine.workspace.raw_frame(s) for s in symbols}
    dates = engine.workspace.common_sessions(*INDEX_SYMBOLS)
    dates = dates[(dates >= pd.Timestamp(contract['window']['start'])) & (dates <= pd.Timestamp(contract['window']['end']))]
    cash = {s: cfg.initial_cash / len(symbols) for s in symbols}
    shares = dict.fromkeys(symbols, 0)
    # Each reserved allocation is deployed once, with capacity-limited partial fills.
    remaining = {}
    finished = set()
    fills, rows = [], []
    peak, dd = cfg.initial_cash, 0.0
    for i, date in enumerate(dates):
        if i:
            signal = dates[i-1]
            active = universe.symbols_as_of(str(signal.date()))
            for symbol in symbols:
                frame = frames[symbol]
                if symbol in finished or symbol not in active or signal not in frame.index or date not in frame.index:
                    continue
                row = frame.loc[date]
                previous = frame.loc[:signal].iloc[-1]['close']
                if market_execution_blocked(symbol, 'BUY', row, previous):
                    continue
                price = float(row['open']) * (1 + cfg.slippage)
                if symbol not in remaining:
                    quantity = math.floor(cash[symbol] / price / 100) * 100
                    while quantity and quantity * price + sum(fee_components('BUY', quantity * price, cfg)) > cash[symbol]:
                        quantity -= 100
                    remaining[symbol] = quantity
                volume = float(row['volume'])
                if float(row.get('amount', 0)) / max(float(row['close']), 1e-12) > volume * 50:
                    volume *= 100
                quantity = min(remaining[symbol], math.floor(volume * cfg.max_volume_participation / 100) * 100)
                while quantity and quantity * price + sum(fee_components('BUY', quantity * price, cfg)) > cash[symbol]:
                    quantity -= 100
                if quantity < (200 if symbol.startswith('sh688') and not shares[symbol] else 100):
                    continue
                fee = sum(fee_components('BUY', quantity * price, cfg))
                cash[symbol] -= quantity * price + fee
                shares[symbol] += quantity
                remaining[symbol] -= quantity
                fills.append(dict(symbol=symbol, signal_date=str(signal.date()), fill_date=str(date.date()), shares=quantity, price=price, fees=fee, slippage=quantity*(price-float(row['open']))))
                if remaining[symbol] == 0:
                    finished.add(symbol)
                assert cash[symbol] >= -1e-8 and signal < date and symbol != removed
        equity = sum(cash.values()) + sum(shares[s] * engine.workspace.price(s, date) for s in symbols if shares[s])
        peak = max(peak, equity)
        dd = max(dd, 1-equity/peak)
        rows.append(dict(session=str(date.date()), equity=equity))
    fees = sum(f['fees'] for f in fills)
    slip = sum(f['slippage'] for f in fills)
    gross = sum(f['shares']*f['price'] for f in fills)
    assert math.isclose(sum(cash.values()) + gross + fees, cfg.initial_cash, abs_tol=1e-6)
    return dict(removed=removed, symbols=symbols, sessions=len(rows), final_wealth=rows[-1]['equity']/cfg.initial_cash, max_drawdown=dd, fees=fees, slippage=slip, gross_turnover=gross/cfg.initial_cash, fills=fills, rows=rows, unfilled=[s for s in symbols if not shares[s]], config=dataclasses.asdict(cfg))


if __name__ == '__main__':
    root = Path.cwd()
    output = Path(__file__).with_name('passive.json')
    assert not output.exists(), 'Never overwrite completed evidence'
    results = {s: run(root,s) for s in ('sz300308','sz300502')}
    record = dict(kind='MATCHED_FROZEN_QFQ_DIAGNOSTIC', convention='Equal reserved initial sleeves across registered remaining universe; prior-session membership and observation; next-open buys; native fees, lot/capacity and limit assumptions; no rebalancing; no final liquidation. Frozen QFQ price convention matches strategy; does not independently audit unadjusted shares, dividends or taxes.', runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), data_manifest_sha256=hashlib.sha256((root/'data/frozen/DATA_MANIFEST.json').read_bytes()).hexdigest(), results=results)
    output.write_text(json.dumps(record,sort_keys=True,indent=2)+'\n')
    assert json.loads(output.read_text()) == json.loads(json.dumps(record))
    print(json.dumps({s:{k:v for k,v in r.items() if k in ('final_wealth','max_drawdown','fees','slippage','sessions','unfilled')} for s,r in results.items()}))
