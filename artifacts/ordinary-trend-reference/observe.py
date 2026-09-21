"""Retain causal request diagnostics without altering the native replay result."""
# ruff: noqa: E402
import gzip
import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from uquant.portfolio import pipeline

original = pipeline._reference_trend
rows = []


def observe(book, *, admission_open):
    original(book, admission_open=admission_open)
    rows.append(dict(date=str(book.date.date()), clock=book.risk.evidence['ordinary_trend_clock'],
                     permission_open=admission_open, risk=book.risk.state.value,
                     gross_cap=book.gross_cap, actual=book.weights_now,
                     proposed=dict(book.proposed), committed=dict(book.committed),
                     unreserved_cash=book.cash_room, cash=book.account.cash,
                     owned=sorted(book.owned), recovery=sorted(book.recovery_targets),
                     symbols=book.trace,
                     pending=[dict(symbol=o.symbol, side=o.side, weight=o.target_weight,
                                   order_id=o.order_id) for o in book.account.pending_orders]))


pipeline._reference_trend = observe
output = Path(sys.argv[sys.argv.index('--output') + 1])
try:
    runpy.run_path(str(ROOT / 'artifacts/unified-allocation/replay.py'), run_name='__main__')
finally:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix('.requests.json.gz').open('xb') as stream:
        stream.write(gzip.compress(json.dumps(rows, default=str).encode(), mtime=0))
