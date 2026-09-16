"""Settled ordinary risk sales must not occupy a fresh recovery account."""
from copy import deepcopy
from pathlib import Path

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


def test_settled_stale_ordinary_rights_release_account_availability(settled_ordinary_account):
    from types import SimpleNamespace

    from uquant.portfolio.recovery.current_cohort import _book_available
    from uquant.risk.pullback import pullback_book_settled

    _, original = settled_ordinary_account
    account = deepcopy(original)
    historical_weights = dict(account.protected_weights)
    assert not pullback_book_settled(account)
    book = SimpleNamespace(account=account, weights_now={}, owned=set())
    assert _book_available(book, set(), set(), [])
    assert account.protected_weights == historical_weights
    assert not account.positions and not account.pending_orders

    # Actual ownership still blocks a new recovery account.
    book.owned = {"sz300308"}
    assert not _book_available(book, set(), set(), [])
