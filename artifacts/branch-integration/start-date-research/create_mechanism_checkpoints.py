import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.account import save_account
from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.types import AccountState

contract = json.loads(Path("../uquant/artifacts/branch-integration/START_DATE_CONTRACT.json").read_text())
out = Path("../mechanism-checkpoints")
out.mkdir(exist_ok=True)
for key, name, cut in [
    ("mechanism_window", "recovery-2025", "2025-05-07"),
    ("additional_mechanism_window", "normal-tactical-2023", "2023-07-04"),
]:
    w = contract[key]
    symbols = sorted(w["symbols"])
    e = ProductionEngine("data/frozen")
    e.workspace.prepare(e.workspace.bind_tradable(symbols))
    a = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {s: e._raw[s] for s in symbols}
    cal = e.workspace.common_sessions(*e.workspace.universe.index_symbols)
    curve = []
    for date in cal[(cal >= w["start"]) & (cal <= cut)]:
        e.execution.execute_open(date=date, account=a, panel=panel)
        curve.append({"date": str(date.date()), "equity": e.equity(a, date)})
        d = e.decide(symbols=symbols, as_of=str(date.date()), account=a)
        a.pending_orders = list(d.pending_orders)
    save_account(a, out / (name + "-prefix-account.json"))
    (out / (name + "-prefix.json")).write_text(
        json.dumps(
            {
                "start": w["start"],
                "checkpoint": cut,
                "end": w["end"],
                "symbols": symbols,
                "equity_curve": curve,
                "source_head": "33e310ec6aee04b50b2fa907c3e5935e1c88c557",
                "selection": "Last session before the first affected mechanism decision, within the previously registered window.",
            },
            indent=2,
        )
        + "\n"
    )
    print(name, cut, "cash", a.cash, "real_fills", len(a.fills), flush=True)
