"""Read-only observation of ordinary replacement feasibility; never changes targets."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.features import scalar
from uquant.portfolio import pipeline
from uquant.portfolio.capital import funded_increment
from research.performance_diagnostic import _run_trace, _source_sha256, _trace_adapter_sha256

original = pipeline._admit_new_cores
rows = []

def observe(book, *, candidates, opportunity):
    if book.risk.state.value == 'NORMAL' and pipeline._core_opportunity_open(opportunity):
        account, cfg = book.account, book.policy.cfg
        occupied, eligible, _ = pipeline._fresh_core_selection(book, candidates)
        for challenger in eligible:
            score = book.leaders[challenger]
            for incumbent, current in book.weights_now.items():
                if current <= 0 or incumbent not in book.leaders or incumbent not in book.user_panel:
                    continue
                position, frame = account.positions[incumbent], book.user_panel[incumbent]
                row = frame.loc[book.date]
                broken = scalar(row, 'close') < scalar(row, f'ma{cfg.trend_fast}') and scalar(row, f'ret{cfg.trend_fast}') < 0
                edge = (score.score - book.policy._retention_score(incumbent, book.leaders, account)
                        - .01 - (.15 if score.industry == book.leaders[incumbent].industry else 0)
                        - .05 * max(0., 1. - score.confidence) + (.08 if broken else 0))
                protected = incumbent in book.owned or bool(position.grant_id or position.epoch_id)
                remaining = max(0., book.proposed.get(incumbent, 0.) - cfg.replacement_transfer_cap)
                released = current - remaining
                detail = {}
                feasible = funded_increment(cfg=cfg, symbol=challenger, desired=cfg.core_admission_weight,
                    current=0., committed={**book.committed, incumbent:remaining},
                    cash_room=book.cash_room+max(0.,released), leaders=book.leaders,
                    user_panel=book.user_panel, date=book.date, gross_cap=book.gross_cap, diagnostics=detail)
                rows.append(dict(date=str(book.date.date()), incumbent=incumbent, challenger=challenger,
                    edge=edge, required_edge=cfg.replacement_edge, broken=bool(broken), protected=protected,
                    held_sessions=len(frame.loc[position.entry_date:book.date]) if position.entry_date else 0,
                    min_hold_days=cfg.min_hold_days, sellable_shares=position.sellable_shares(str(book.date.date())),
                    shares=position.shares, current=current, remaining=remaining, released=released,
                    occupied=len(occupied), max_positions=cfg.max_positions,
                    pending_orders=bool(account.pending_orders),
                    reducing=any(book.proposed.get(s,0.) < w for s,w in book.weights_now.items()),
                    restore_right=account.strategic_restore_weights.get(incumbent,0.),
                    protected_weight=account.protected_weights.get(incumbent,0.),
                    cash_room=book.cash_room, feasible=feasible, feasibility=detail,
                    signal_ready=True))
    return original(book,candidates=candidates,opportunity=opportunity)

pipeline._admit_new_cores = observe
root=Path.cwd(); pool,start,end,out=sys.argv[1:]
spec=json.loads(Path('benchmarks/promotion_baseline.json').read_text())
a=argparse.Namespace(source_root=str(root),data_dir=str(root/'data/frozen'),start=start,end=end,set=[],expected_patch_sha256=None,
 expected_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),expected_source_sha256=_source_sha256(root),
 expected_trace_adapter_sha256=_trace_adapter_sha256(root),symbols=','.join(spec['pools'][pool]))
result=_run_trace(a)
result['allocation_observations']=rows
result['observer_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
Path(out).write_text(json.dumps(result,default=str))
print(result['metrics'], 'pairs',len(rows),flush=True)
