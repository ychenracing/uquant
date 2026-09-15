"""Settled ordinary risk sales must not occupy a fresh recovery account."""
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.holding_history import protected_weights_for_current_episode
from uquant.leader import REFERENCE_UNIVERSE
from uquant.types import AccountState

SYMBOLS = (
    "sz300308", "sz300502", "sz300394", "sh688498", "sh601869",
    "sh688256", "sh688008", "sh603986", "sh688072", "sh688082",
    "sh688120", "sh688300", "sz300054", "sh688361", "sz300604",
)


@pytest.fixture(scope="module")
def settled_ordinary_account():
    engine = ProductionEngine(Path(__file__).resolve().parents[1] / "data/frozen")
    engine._load(set(SYMBOLS) | set(INDEX_SYMBOLS) | set(REFERENCE_UNIVERSE))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {symbol: engine._raw[symbol] for symbol in SYMBOLS}
    calendar = engine._raw["sh000300"].index
    for date in calendar[(calendar >= "2025-01-02") & (calendar <= "2025-04-30")]:
        engine.execution.execute_open(date=date, account=account, panel=panel)
        decision = engine.decide(symbols=SYMBOLS, as_of=str(date.date()), account=account)
        account.pending_orders = list(decision.pending_orders)
    assert not account.positions and not account.pending_orders
    assert account.protected_weights
    assert not protected_weights_for_current_episode(account)
    return engine, account


def test_settled_stale_ordinary_rights_do_not_block_new_confirmed_recovery(settled_ordinary_account):
    engine, original = settled_ordinary_account
    account = deepcopy(original)
    panel = {symbol: engine._raw[symbol] for symbol in SYMBOLS}
    for session in ("2025-05-06", "2025-05-07", "2025-05-08"):
        engine.execution.execute_open(date=pd.Timestamp(session), account=account, panel=panel)
        decision = engine.decide(symbols=SYMBOLS, as_of=session, account=account)
        account.pending_orders = list(decision.pending_orders)
    assert decision.risk_summary["freeze_new_risk"] is False
    buys = [o for o in account.pending_orders if o.side == "BUY"]
    assert {o.symbol for o in buys} == {"sz300308", "sz300394", "sz300502"}
    assert all(o.mechanism == "RECOVERY_COHORT" and not o.grant_id and not o.epoch_id for o in buys)
    assert account.protected_weights == original.protected_weights
    fills = engine.execution.execute_open(date=pd.Timestamp("2025-05-09"), account=account, panel=panel)
    assert {f.symbol for f in fills if f.side == "BUY"} == {"sz300308", "sz300394", "sz300502"}
    assert account.cash >= 0
