"""Task-local, read-only observation of the existing allocator and account replay."""
import argparse
import copy
import dataclasses
import gzip
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from uquant.engine import ProductionEngine
from uquant.portfolio import pipeline as p
from uquant.portfolio_core import current_weights

args = argparse.ArgumentParser()
args.add_argument('--reference', type=Path, required=True)
args.add_argument('--output', type=Path, required=True)
args.add_argument('--shadow', action='store_true')
a = args.parse_args()
base = json.loads(gzip.decompress(a.reference.read_bytes()))
engine = ProductionEngine('data/frozen')
observations, markets, opportunities, candidates = [], {}, {}, {}
original_candidates, original_recovery, original_targets = p._core_candidates, p.allocate_confirmed_recovery, p._book_targets


def core(*args, **kw):
    result = original_candidates(*args, **kw)
    key = str(kw['date'].date())
    markets[key] = copy.deepcopy(kw['market'])
    candidates[key] = result
    return result


def recovery(book, **kw):
    key = str(book.date.date())
    opportunities[key] = kw['opportunity']
    return original_recovery(book, **kw)


def state(book):
    weights, equity = current_weights(book.account, book.prices)
    rebuilt, available = p.committed_capital(account=book.account, prices=book.prices, proposed=book.proposed)
    return dict(equity=equity, cash=book.account.cash, cash_weight=book.account.cash/equity,
                held=weights, proposed=dict(book.proposed), committed=dict(book.committed),
                cash_room=book.cash_room, rebuilt_committed=rebuilt, rebuilt_cash_room=available,
                gross_cap=book.gross_cap, pending=[dataclasses.asdict(o) for o in book.account.pending_orders],
                recovery_targets={s:t.weight for s,t in book.recovery_targets.items()},
                anchors=dict(book.account.anchor_weights), protected=dict(book.account.protected_weights),
                clocks=dict(book.account.candidate_tenure))


def targets(book):
    key = str(book.date.date())
    record = dict(date=key, risk=book.risk.state.value, opportunity=opportunities[key].value,
                  state=state(book), trace=copy.deepcopy(book.trace), market=markets[key])
    blocked = [s for s in candidates[key] if book.trace.get(s,{}).get('entry_gate') == 'RECOVERY_ALLOCATION_ACTIVE'
               and book.weights_now.get(s,0) == 0]
    record['blocked_unheld_ready'] = blocked
    if a.shadow and blocked and record['risk'] == 'NORMAL' and record['state']['cash_weight'] > .05:
        # Share immutable data/config; isolate every mutable book field and account.
        shadow = copy.copy(book)
        for field in ['account','proposed','committed','reasons','mechanisms','replacements','trace',
                      'recovery_targets','recovery_restore_symbols','risk']:
            setattr(shadow, field, copy.deepcopy(getattr(book, field)))
        control = copy.deepcopy((book.account.to_dict(), book.trace, book.proposed, book.committed, book.risk))
        before = state(shadow)
        p._admit_new_cores(shadow, candidates=candidates[key], opportunity=opportunities[key],
                          market=copy.deepcopy(markets[key]))
        # New admissions only: do not hypothesize changing held-position pyramiding.
        record['shadow'] = dict(before=before, after=state(shadow), trace=shadow.trace,
            increments={s:w-before['proposed'].get(s,0) for s,w in shadow.proposed.items()
                        if w>before['proposed'].get(s,0)+1e-12})
        assert control == (book.account.to_dict(), book.trace, book.proposed, book.committed, book.risk), 'shadow mutated live book'
    observations.append(record)
    return original_targets(book)

p._core_candidates, p.allocate_confirmed_recovery, p._book_targets = core, recovery, targets
result = engine.backtest(symbols=base['symbols'], start=base['start'], end=base['end'])
differences = [i for i,(b,c) in enumerate(zip(base['result']['decision_digests'],result['decision_digests'])) if b != c]
out = dict(source_head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
           reference=str(a.reference), decisions_equal=not differences, final_account_equal=result['final_account']==base['result']['final_account'],
           decision_differences=[dict(baseline=base['result']['decision_trace'][i], observed=result['decision_trace'][i]) for i in differences],
           result=result,
           wealth=result['final_wealth'], observations=observations)
a.output.write_bytes(gzip.compress(json.dumps(out,default=str,allow_nan=False).encode(),mtime=0))
rows = [r for r in observations if 'shadow' in r]
print(json.dumps(dict(days=len(observations),blocked_days=len(rows),blocked_records=sum(len(r['blocked_unheld_ready']) for r in rows),
                     fundable_days=sum(bool(r['shadow']['increments']) for r in rows),output=str(a.output))))

print('reconciliation', out['decisions_equal'], out['final_account_equal'], 'different decisions', len(differences))
