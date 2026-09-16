"""Diagnostic prefix only; deliberately ineligible for acceptance evidence."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suppress-admission", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite diagnostic")
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))
    from uquant.config import DEFAULT_CONFIG
    from uquant.engine import ProductionEngine
    from uquant.portfolio import pipeline
    from uquant.types import AccountState

    if args.suppress_admission:
        def suppress(book, *, candidates, opportunity, market):
            for symbol in candidates:
                book.record(symbol)["entry_gate"] = "DIAGNOSTIC_SUPPRESSED_ADMISSION"
        pipeline._admit_new_cores = suppress

    cfg = dataclasses.replace(DEFAULT_CONFIG, **{
        key: getattr(DEFAULT_CONFIG, key) * 2 for key in
        ("commission_rate", "min_commission", "stamp_duty", "transfer_fee", "slippage")
    })
    symbols = sorted(json.loads((root / "benchmarks/generalization_tradeoff_acceptance.json").read_text())["universe"])
    engine = ProductionEngine(root / "data/frozen", cfg)
    engine.workspace.prepare(engine.workspace.bind_tradable(symbols))
    sessions = engine.workspace.common_sessions(*engine.workspace.universe.index_symbols)
    sessions = sessions[(sessions >= pd.Timestamp("2023-01-03")) & (sessions <= pd.Timestamp("2024-05-07"))]
    account = AccountState.empty(cfg.initial_cash)
    rows = []
    for date in sessions:
        engine.execution.execute_open(date=date, account=account, panel={s: engine._raw[s] for s in symbols})
        equity = engine.equity(account, date)
        decision = engine.decide(symbols=symbols, as_of=str(date.date()), account=account)
        rows.append({
            "date": str(date.date()), "equity": equity,
            "capital_drawdown": 1 - equity / account.capital_peak,
            "operating_drawdown": 1 - equity / account.operating_peak,
            "capital_level": account.capital_budget_level,
            "repair_streak": account.capital_budget_repair_streak,
            "risk": decision.risk.value,
            "targets": {target.symbol: target.weight for target in decision.targets},
        })
        account.pending_orders = list(decision.pending_orders)
    output = {
        "method": "DIAGNOSTIC_ONLY_PATCHED_PREFIX_NOT_ACCEPTANCE",
        "suppressed_function": "pipeline._admit_new_cores" if args.suppress_admission else None,
        "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "source_tree": subprocess.check_output(["git", "rev-parse", "HEAD:uquant"], cwd=root, text=True).strip(),
        "diagnostic_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cost_multiplier": 2, "rows": rows,
        "fills": [{key: getattr(fill, key) for key in
                   ("fill_date", "signal_date", "symbol", "side", "shares", "reason")}
                  for fill in account.fills],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(output, stream, indent=2, default=str)
    print(json.dumps({"path": str(args.output), "last": rows[-1]}, default=str))


if __name__ == "__main__":
    main()
