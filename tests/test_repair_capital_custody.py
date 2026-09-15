"""Real repair capital is shared until the ordinary book and intents settle."""
from dataclasses import asdict, replace

import pytest
from test_ordinary_trend_budget import _decide, _scenario

from uquant.account.codec import account_from_dict
from uquant.execution import ExecutionPlanner


def test_active_repair_capital_prevents_duplicate_ordinary_funding():
    policy,account,dates,panel,base,risk=_scenario()
    account.leader_tenure.update({s:20 for s in base})
    high=next(iter(base))
    initial={s:replace(v,score=.81) if s!=high else v for s,v in base.items()}
    limited=replace(risk,target_gross_cap=policy.cfg.core_admission_weight)
    # Establish a real native 20% position; real repair-marker binding is tested separately.
    assert _decide(policy,account,dates[0],panel,initial,limited)
    assert account.pending_orders[0].target_weight==policy.cfg.core_admission_weight
    account.candidate_tenure['ordinary_repair_capital_active']=1
    original=account.pending_orders[0].order_id
    _decide(policy,account,dates[1],panel,base,risk)
    assert len(account.pending_orders)==1
    assert account.pending_orders[0].order_id==original
    account=account_from_dict(asdict(account))
    assert ExecutionPlanner(policy.cfg).execute_open(date=dates[2],account=account,panel=panel)
    _decide(policy,account,dates[2],panel,base,risk)
    assert not any(o.side=='BUY' for o in account.pending_orders)
    assert len(account.positions)==1


def test_settled_repair_book_does_not_limit_a_new_account_admission():
    policy,account,dates,panel,base,risk=_scenario()
    account.leader_tenure.update({s:20 for s in base})
    account.candidate_tenure['ordinary_repair_capital_active']=1
    assert _decide(policy,account,dates[0],panel,base,risk)
    assert account.candidate_tenure.get('ordinary_repair_capital_active',0)==0
    assert sum(o.target_weight for o in account.pending_orders)>policy.cfg.core_admission_weight


@pytest.mark.parametrize('lose_proof',[False,True])
def test_current_full_certificate_can_use_free_capital_beside_repair_holding(lose_proof):
    import pandas as pd
    from test_lifecycle_and_risk import _leader, _strategic_frame
    from test_ordinary_cash_rearm import _decide as repair_decide
    from test_ordinary_cash_rearm import _scenario as _independent_repair
    from test_shared_core_qualification import CHALLENGER, WITNESSES
    from test_strategic_universe_quorum import _risk

    from uquant.models.strategic_universe import build_strategic_universe_roles
    policy,account,dates,panel,base,risk=_independent_repair()
    high=next(iter(base))
    assert repair_decide(policy,account,dates[0],panel,base,risk)
    assert ExecutionPlanner(policy.cfg).execute_open(date=dates[1],account=account,panel=panel)
    account.candidate_tenure['ordinary_repair_capital_active']=1
    account.capital_budget_level=0
    for symbol,score,industry in zip(WITNESSES,(.94,.93,.92),('foundry','equipment','optical'), strict=True):
        panel[symbol]=_strategic_frame(pd.bdate_range(end=dates[-1],periods=260))
        panel[symbol]['open']=panel[symbol]['close']
        panel[symbol]['high']=panel[symbol]['close']*1.01
        panel[symbol]['low']=panel[symbol]['close']*.99
        panel[symbol]['volume']=100_000_000.
        panel[symbol]['amount']=panel[symbol]['close']*panel[symbol]['volume']
        leader=_leader(symbol,score,industry=industry)
        base[symbol]=replace(leader,components={**leader.components,'secular_score':.79})
    roles=build_strategic_universe_roles(as_of=str(dates[-1].date()),tradable_symbols=tuple(panel),
        qualification_reference_symbols=tuple(panel),risk_reference_symbols=('sh000300','sh000682'),
        industries={symbol:leader.industry for symbol,leader in base.items()},available_symbols=(*panel,'sh000300','sh000682'))
    risk=_risk()
    for date in dates[2:5]:
        _decide(policy,account,date,panel,base,risk,roles=roles)
    trace=risk.evidence['core_allocation']['symbols'][CHALLENGER]
    assert trace['entry']['block']=='READY'
    assert trace['entry']['qualification_quorum']=='FULL_COHORT'
    assert any(o.symbol==CHALLENGER and o.side=='BUY' for o in account.pending_orders), str(trace)
    assert account.candidate_tenure['ordinary_repair_capital_active']==1
    assert account.positions[high].shares>0
    assert account.strategic_grant is None
    assert sum(o.target_weight for o in account.pending_orders if o.side=='BUY') <= policy.cfg.core_admission_weight + 1e-12
    restored=account_from_dict(asdict(account))
    if lose_proof:
        panel[CHALLENGER].loc[dates[5],'volume']=10_000.
    fills=ExecutionPlanner(policy.cfg).execute_open(date=dates[5],account=restored,panel=panel)
    assert any(f.symbol==CHALLENGER and f.side=='BUY' for f in fills)
    assert not restored.positions[CHALLENGER].grant_id
    if not lose_proof:
        _decide(policy,restored,dates[6],panel,base,risk,roles=roles)
        assert not any(o.side=='BUY' for o in restored.pending_orders)
    if lose_proof:
        assert any(o.symbol==CHALLENGER for o in restored.pending_orders)
        shares=restored.positions[CHALLENGER].shares
        for symbol in WITNESSES:
            base[symbol]=replace(base[symbol],score=.83 if symbol==CHALLENGER else .2,
                components={**base[symbol].components,'secular_score':.2})
        restored.leader_tenure[CHALLENGER]=20
        risk.evidence.update(ai_fast_return=.16,tech_speed=.16,broad_speed=.02,
                             declining_ratio=.05,below_ma20_ratio=.05)
        _decide(policy,restored,dates[5],panel,base,risk,roles=roles)
        row=risk.evidence['core_allocation']['symbols'][CHALLENGER]
        assert row['pending_entry']['qualification_quorum']=='ORDINARY_CORE'
        assert row['pending_entry_permission_open'] is False
        assert not any(o.symbol==CHALLENGER and o.side=='BUY' for o in restored.pending_orders)
        assert restored.positions[CHALLENGER].shares==shares


def test_repair_origin_drift_and_restart_do_not_consume_independent_allowance():
    from types import SimpleNamespace

    from test_ordinary_cash_rearm import _decide as repair_decide
    from test_ordinary_cash_rearm import _scenario as _independent_repair

    from uquant.portfolio.pipeline import _ordinary_admission_budget
    from uquant.portfolio_core import current_weights
    from uquant.types import StrategicCashRearmState
    policy,account,dates,panel,leaders,risk=_independent_repair()
    assert repair_decide(policy,account,dates[0],panel,leaders,risk)
    assert ExecutionPlanner(policy.cfg).execute_open(date=dates[1],account=account,panel=panel)
    account=account_from_dict(asdict(account))
    account.strategic_cash_rearm=StrategicCashRearmState()
    # A current positive market opens an independent allowance; it does not reset repair origin.
    risk=replace(risk,evidence={**risk.evidence,"tech_ret120":.50})
    prices={s:float(panel[s].loc[dates[1],'close'])*3 for s in account.positions}
    weights,_=current_weights(account,prices)
    assert sum(weights.values())>policy.cfg.core_admission_weight
    book=SimpleNamespace(account=account,policy=policy,risk=risk,committed=weights,
                         weights_now=weights,owned=set())
    assert _ordinary_admission_budget(book)==0
    assert _ordinary_admission_budget(book,independently_qualified=True)==pytest.approx(policy.cfg.core_admission_weight)
    # A surviving marker without its actual order/event cannot exempt capital.
    for key in list(account.candidate_tenure):
        if key.startswith('ordinary_repair_origin:'):
            account.candidate_tenure[key+'-wrong-event']=account.candidate_tenure.pop(key)
    assert _ordinary_admission_budget(book,independently_qualified=True)==0
