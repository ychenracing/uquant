"""The original tactical signal remains reachable without a risk freeze."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.universe import historical_industry_classification
from uquant.engine import ProductionEngine
from uquant.portfolio.pipeline import _caution_probe_book_open
from uquant.types import AccountState, Risk, RiskAssessment


def test_native_normal_tactical_signal_submits_and_fills():
    import json

    root = Path(__file__).resolve().parents[1]
    symbols = json.loads((root / "benchmarks/promotion_baseline.json").read_text())["pools"]["e"]
    # Minimized under the v1 classification; v2 revises this pool's industry evidence.
    with historical_industry_classification():
        raw = ProductionEngine(root / "data/frozen").backtest(
            symbols=symbols, start="2023-07-03", end="2023-07-06")
    orders = raw["final_account"]["order_ledger"]
    assert len(orders) == 1
    order = orders[0]
    assert (order["signal_date"], order["symbol"], order["side"]) == ("2023-07-05", "sh688072", "BUY")
    assert order["target_weight"] == DEFAULT_CONFIG.tactical_probe_weight
    assert order["filled_shares"] > 0
    assert order["mechanism"] == "TACTICAL_REBOUND"
    assert not order["grant_id"] and not order["epoch_id"]


@pytest.mark.parametrize("state,freeze,evidence,capital,expected", [
    (Risk.NORMAL, False, {}, 0, True),
    (Risk.CAUTION, False, {}, 0, True),
    (Risk.CAUTION, True, {}, 0, True),
    (Risk.NORMAL, True, {}, 0, False),
    (Risk.NORMAL, False, {"freeze_new_risk": True}, 0, False),
    (Risk.CAUTION, True, {"sentinel_freeze_new_risk": True}, 0, False),
    (Risk.NORMAL, False, {}, 1, False),
    (Risk.CRISIS, False, {}, 0, False),
])
def test_unfrozen_route_does_not_bypass_existing_freeze_owners(state, freeze, evidence, capital, expected):
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.capital_budget_level = capital
    risk = RiskAssessment(state, 1., 0, evidence, (), "NONE", freeze_new_risk=freeze)
    book = SimpleNamespace(account=account, risk=risk, owned=set())
    assert _caution_probe_book_open(book) is expected
