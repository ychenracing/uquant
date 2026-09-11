"""Real repair capital is shared until the ordinary book and intents settle."""
from dataclasses import asdict,replace
from test_ordinary_trend_budget import _decide,_scenario
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
