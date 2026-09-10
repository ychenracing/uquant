"""Research holding handoff must preserve real shares and ordinary hard risk."""
# ruff: noqa: F811 -- pytest injects the imported shared native-prefix fixture.
from copy import deepcopy

import pandas as pd
import pytest
from test_confirmed_recovery_pipeline import SYMBOLS, recovery_prefix  # noqa: F401
from test_lifecycle_and_risk import _leader

from uquant.config import DEFAULT_CONFIG
from uquant.holding_history import recovery_owner_open, tactical_owner_entry
from uquant.portfolio import PortfolioAllocator
from uquant.types import Opportunity, Risk, RiskAssessment


def test_actual_probe_survives_ordinary_exit_until_original_recovery_handoff(recovery_prefix):
    _, snapshots = recovery_prefix
    held, _ = snapshots["2025-04-21"]
    assert held.positions["sz300308"].shares == 15300
    assert not any(fill.side == "SELL" for fill in held.fills)
    assert tactical_owner_entry(held, "sz300308") is not None
    promoted, _ = snapshots["2025-05-06"]
    assert promoted.positions["sz300308"].shares == held.positions["sz300308"].shares
    events = [e for e in promoted.lifecycle_events if e.get("event") == "TACTICAL_RECOVERY_HANDOFF"]
    assert len(events) == 1 and events[0]["date"] == "2025-05-06"
    assert recovery_owner_open(promoted, "sz300308")
    assert promoted.fills == held.fills


@pytest.mark.parametrize("field,value", [("order_id", "unrelated"), ("observed_fill_count", 99999), ("shares", 1)])
def test_handoff_cannot_borrow_another_order_or_fabricate_shares(recovery_prefix, field, value):
    _, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-05-06"])
    event = next(e for e in account.lifecycle_events if e.get("event") == "TACTICAL_RECOVERY_HANDOFF")
    event[field] = value
    assert not recovery_owner_open(account, "sz300308")


def test_actual_tactical_holding_still_obeys_crisis_liquidation(recovery_prefix):
    engine, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-04-21"])
    date = pd.Timestamp("2025-04-22")
    panel = {symbol: engine._features[symbol] for symbol in SYMBOLS}
    risk = RiskAssessment(Risk.CRISIS, 0., 4, {}, ("crisis",), "SHOCK", freeze_new_risk=True, reduction_level=3)
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date, opportunity=Opportunity.WEAK, risk=risk, user_panel=panel,
        leaders={symbol: _leader(symbol, .9) for symbol in SYMBOLS}, account=account,
        prices={s: float(f.loc[date, "close"]) for s, f in panel.items()})
    assert targets and all(target.weight == 0. for target in targets)
    assert not any(e.get("event") == "TACTICAL_RECOVERY_HANDOFF" for e in account.lifecycle_events)
    assert account.positions["sz300308"].shares == 15300  # allocation never pretends to execute


def test_unbacked_tactical_label_cannot_supply_holding_rights(recovery_prefix):
    _, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-04-21"])
    account.fills.clear()
    assert tactical_owner_entry(account, "sz300308") is None
    assert not recovery_owner_open(account, "sz300308")
