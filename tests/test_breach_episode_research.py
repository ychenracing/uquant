"""New observed risk evidence can create a new sale, never a duplicate receipt."""
from dataclasses import asdict
from types import SimpleNamespace

from test_strategic_exit_band_settlement import _band_sale
from test_strategic_probe_holding import OWNER, _decide_and_submit

from uquant.account.codec import account_from_dict
from uquant.application.decision import mark_account_positions
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner


def _recovery_then_breach(*, missing_recovery=False):
    allocator, account, dates, panel, leaders, roles = _band_sale()
    close = float(panel[OWNER].loc[dates[0], "close"])
    peak_date = panel[OWNER].index[panel[OWNER].index.get_loc(dates[0]) - 1]
    panel[OWNER].loc[peak_date, "close"] = close + 1.0
    runtime = SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    )
    mark_account_positions(runtime, account, peak_date)
    if missing_recovery:
        panel[OWNER].loc[dates[0], "ma20"] = close * 1.1
        panel[OWNER].loc[dates[0], "ret20"] = -.05
        pending = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
        if pending:
            fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
                date=dates[1], account=account, panel={OWNER: panel[OWNER]},
            )
            assert fills and all(fill.side == "SELL" for fill in fills)
        assert all(account.strategic_active_bands[OWNER])
        dates = dates[1:]
    panel[OWNER].loc[dates[0], "ma20"] = close * .95
    panel[OWNER].loc[dates[0], "ret20"] = .05
    if missing_recovery:
        panel[OWNER].loc[dates[0], "atr"] = float("nan")
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    for column in ("open", "close", "high", "low"):
        panel[OWNER].loc[dates[1:4], column] = close
    panel[OWNER].loc[dates[1:4], "ma20"] = close * 1.1
    panel[OWNER].loc[dates[1:4], "ret20"] = -.05
    return allocator, account, dates, panel, leaders, roles


def test_recovered_then_new_breach_creates_one_new_native_exit():
    allocator, account, dates, panel, leaders, roles = _recovery_then_breach()
    previous_bands = list(account.strategic_exit_bands[OWNER])
    assert not any(account.strategic_active_bands[OWNER])
    orders = _decide_and_submit(allocator, account, dates[1], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].mechanism == "STRATEGIC_TRAILING_EXIT"
    assert orders[0].target_weight < sum(previous_bands)
    new_bands = list(account.strategic_exit_bands[OWNER])
    restored = account_from_dict(asdict(account))
    repeated = _decide_and_submit(allocator, restored, dates[1], panel, leaders, roles)
    assert len(repeated) == 1 and repeated[0].order_id == orders[0].order_id
    assert repeated[0].target_weight == orders[0].target_weight
    assert restored.strategic_exit_bands[OWNER] == new_bands
    shares = restored.positions[OWNER].shares
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[2], account=restored, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    assert restored.positions[OWNER].shares < shares
    assert not _decide_and_submit(allocator, restored, dates[2], panel, leaders, roles)
    assert restored.strategic_exit_bands[OWNER] == new_bands


def test_missing_recovery_observation_cannot_rearm_a_settled_exit():
    allocator, account, dates, panel, leaders, roles = _recovery_then_breach(missing_recovery=True)
    previous_bands = list(account.strategic_exit_bands[OWNER])
    assert all(account.strategic_active_bands[OWNER])
    assert not _decide_and_submit(allocator, account, dates[1], panel, leaders, roles)
    assert account.strategic_exit_bands[OWNER] == previous_bands
