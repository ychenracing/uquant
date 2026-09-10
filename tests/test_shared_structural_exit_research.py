"""Native FULL holdings share the already confirmed structural exit."""
from dataclasses import asdict, replace

from test_shared_core_qualification import _decide
from test_strategic_cohort_deployment_settlement import SYMBOLS, _native_full
from test_strategic_grant_observation import _risk

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner


def test_native_full_member_uses_confirmed_exit_and_preserves_restart_identity():
    policy, account, dates, panel, leaders, roles = _native_full()
    _decide(policy, account, dates[0], panel, leaders, roles, risk=_risk(frozen=False))
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    symbol = SYMBOLS[0]
    # Leave the original entry untouched; hold long enough before deterioration.
    start = DEFAULT_CONFIG.min_hold_days + 1
    for date in dates[1:start]:
        _decide(policy, account, date, panel, leaders, roles, risk=_risk(frozen=False))
    weakened = {**leaders, symbol: replace(leaders[symbol], mature=False)}
    frame = panel[symbol]
    for key in ("ma20", "ma60"):
        frame.loc[dates[start]:, key] = frame.loc[dates[start]:, "close"] * 1.1
    for date in dates[start:start + DEFAULT_CONFIG.replacement_confirm_days]:
        _decide(policy, account, date, panel, weakened, roles, risk=_risk(frozen=False))
    orders = [o for o in account.pending_orders if o.symbol == symbol and o.side == "SELL"]
    assert len(orders) == 1 and orders[0].target_weight == 0
    assert orders[0].mechanism == "STRATEGIC_TRAILING_EXIT"
    assert orders[0].epoch_id == account.positions[symbol].epoch_id
    original = asdict(orders[0])
    restored = account_from_dict(asdict(account))
    _decide(policy, restored, date, panel, weakened, roles, risk=_risk(frozen=False))
    assert asdict(next(o for o in restored.pending_orders if o.symbol == symbol)) == original
    fill_date = dates[dates.get_loc(date) + 1]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=fill_date, account=restored, panel=panel)
    assert any(f.symbol == symbol and f.side == "SELL" for f in fills)
    assert symbol not in restored.positions or restored.positions[symbol].shares == 0
    assert any(p.shares > 0 for s, p in restored.positions.items() if s != symbol)
