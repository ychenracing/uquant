"""Read exact historical daily recovery state through native decision/execution.

Use -P and PYTHONPATH/cwd pointing to the immutable phase-one checkout. This
observational loop follows that checkout's backtest order without patching it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path

from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.leader import REFERENCE_UNIVERSE
from uquant.types import AccountState
from uquant.validation.promotion import _runtime_provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    runtime = _runtime_provenance("data/frozen")
    assert runtime["production"]["commit"] == "9bb58420365b471ee11b4cdfe31793008233ad50"
    symbols = ("sz300308", "sz300394", "sz300502")
    engine = ProductionEngine("data/frozen")
    engine._load(set(symbols) | set(INDEX_SYMBOLS) | set(REFERENCE_UNIVERSE))
    calendar = engine._raw["sh000300"].index.intersection(engine._raw["sh000682"].index)
    dates = calendar[(calendar >= "2025-04-01") & (calendar <= "2026-06-30")]
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {symbol: engine._raw[symbol] for symbol in symbols}
    with gzip.open(args.output / "observations.jsonl.gz", "wt") as stream:
        for date in dates:
            fills = engine.execution.execute_open(date=date, account=account, panel=panel)
            decision = engine.decide(symbols=symbols, as_of=str(date.date()), account=account)
            account.pending_orders = list(decision.pending_orders)
            row = {
                "date": str(date.date()), "equity": engine.equity(account, date),
                "decision": decision.canonical_payload(effective_config_sha256=config_fingerprint()),
                "risk_summary": decision.risk_summary, "account": account.to_dict(),
                "new_fill_count": len(fills),
            }
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    wealth = engine.equity(account, dates[-1]) / account.initial_cash
    orders = sum(order.filled_shares > 0 for order in account.order_ledger)
    assert math.isclose(wealth, 13.166460741078918, rel_tol=1e-12) and orders == 10
    assert _runtime_provenance("data/frozen") == runtime
    result = {
        "status": "COMPLETE", "scope": "historical_native_daily_trace_not_new_economic_candidate",
        "runtime": runtime, "sessions": len(dates), "wealth": wealth, "orders": orders,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "raw_sha256": hashlib.sha256((args.output / "observations.jsonl.gz").read_bytes()).hexdigest(),
    }
    (args.output / "result.json").write_text(json.dumps(result, sort_keys=True) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
