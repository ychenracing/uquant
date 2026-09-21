"""Observe every native mature-capital request without changing decisions."""
# ruff: noqa: E402
# The checkout must precede imports when this file is invoked directly.
import gzip
import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from uquant.portfolio import pipeline
from uquant.portfolio.capital import funded_increment
from uquant.portfolio.leaders import cycle
from uquant.risk import confirmed_break, transition_resolution, transitions
from uquant.risk.protected_recovery import capture_protected_holdings
from uquant.types import Lifecycle, Opportunity

rows, captures = [], []
original = pipeline.add_mature_leaders


def observe(book, opportunity):
    cfg, account = book.policy.cfg, book.account
    for symbol, position in account.positions.items():
        if position.shares <= 0 or symbol not in book.leaders:
            continue
        leader = book.leaders[symbol]
        stages = {t.lifecycle for t in position.tranches if t.shares > 0} or {position.lifecycle}
        mfe = max((max(t.mfe, book.prices[symbol] / max(t.avg_cost, 1e-12) - 1)
                   for t in position.tranches if t.shares > 0),
                  default=book.prices[symbol] / max(position.avg_cost, 1e-12) - 1)
        increment = 0.0
        if (Lifecycle.ADD1.value not in stages and Lifecycle.ADD2.value not in stages
                and not account.candidate_tenure.get('confidence_sized_entry') and mfe >= cfg.add1_min_mfe):
            increment = cfg.add1_weight
        elif (Lifecycle.ADD1.value in stages and Lifecycle.ADD2.value not in stages
              and opportunity is Opportunity.STRONG_TREND and mfe >= cfg.add2_min_mfe):
            increment = cfg.add2_weight
        detail = {}
        current = book.weights_now[symbol]
        funded = funded_increment(cfg=cfg, symbol=symbol, desired=min(cfg.max_symbol_weight, current+increment),
            current=current, committed=book.committed, cash_room=book.cash_room, leaders=book.leaders,
            user_panel=book.user_panel, date=book.date, gross_cap=book.gross_cap, diagnostics=detail)
        rows.append(dict(date=str(book.date.date()), symbol=symbol, mature=leader.mature,
            score=leader.score, confidence=leader.confidence, owned=symbol in book.owned,
            anchor=symbol in account.anchor_weights, shares=position.shares, cash=account.cash,
            pending=[dict(symbol=o.symbol, side=o.side) for o in account.pending_orders],
            repair=bool(account.candidate_tenure.get('ordinary_repair_capital_active')),
            open=cycle._pyramid_open(book, opportunity), eligible=cycle._pyramid_candidate(book, symbol),
            entry=book.trace.get(symbol, {}).get('entry'), current=current,
            proposed=book.proposed.get(symbol, 0.0), committed=dict(book.committed),
            cooldown=book.policy._add_cooldown_complete(account=account, frame=book.user_panel[symbol],
                date=book.date, cooldown_sessions=cfg.add_tranche_cooldown_sessions),
            increment=increment, funded=funded, capital=detail, mfe=mfe))
    return original(book, opportunity)


def capture(**kwargs):
    capture_protected_holdings(**kwargs)
    account = kwargs['account']
    total = sum(account.protected_weights.values())
    if abs(total-1.0) < 1e-12:
        captures.append(dict(date=str(kwargs['date']), weights=dict(account.protected_weights)))


pipeline.add_mature_leaders = observe
for module in (confirmed_break, transitions, transition_resolution):
    module.capture_protected_holdings = capture
output = Path(sys.argv[sys.argv.index('--output')+1])
try:
    runpy.run_path(str(ROOT/'artifacts/unified-allocation/replay.py'), run_name='__main__')
finally:
    output.with_suffix('.events.json.gz').write_bytes(gzip.compress(
        json.dumps(dict(rows=rows, normalized_capture_candidates=captures), default=str).encode(), mtime=0))
