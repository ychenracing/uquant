"""A filled recovery cohort can hand holding duties to current mature trends."""
from copy import deepcopy
from dataclasses import replace

import pandas as pd
import pytest
import test_confirmed_recovery_pipeline as recovery_cases
from test_confirmed_recovery_pipeline import SYMBOLS
from test_lifecycle_and_risk import _leader
from test_strategic_grant_observation import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.allocation_book import AllocationBook
from uquant.portfolio.recovery.current_cohort import _graduate
from uquant.portfolio_core import current_weights
from uquant.types import Opportunity

recovery_prefix = recovery_cases.recovery_prefix


@pytest.mark.parametrize("blocked", ["", "freeze", "member", "tenure", "restoration", "pending", "missing"])
def test_actual_cohort_graduation_preserves_economics_and_unsettled_responsibilities(recovery_prefix, blocked):
    engine, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-05-09"])
    date = pd.Timestamp("2025-05-12")
    panel = {s: engine.workspace.feature_frame(s) for s in SYMBOLS}
    prices = {s: float(panel[s].loc[date, "close"]) for s in SYMBOLS}
    weights, _ = current_weights(account, prices)
    leaders = {s: replace(_leader(s, .95), mature=True) for s in SYMBOLS}
    account.leader_tenure.update({s: DEFAULT_CONFIG.leader_tenure_days for s in SYMBOLS})
    risk = _risk(frozen=blocked == "freeze")
    if blocked == "member":
        leaders[SYMBOLS[1]] = replace(leaders[SYMBOLS[1]], mature=False)
    if blocked == "tenure":
        account.leader_tenure[SYMBOLS[1]] = 0
    if blocked == "missing":
        panel.pop(SYMBOLS[1])
    if blocked == "restoration":
        account.protected_weights[SYMBOLS[0]] = .6
    if blocked == "pending":
        account.pending_orders = deepcopy(snapshots["2025-05-08"][0].pending_orders)
    before = (account.cash, {s:p.shares for s,p in account.positions.items()},
              deepcopy(account.fills), deepcopy(account.pending_orders), (account.operating_peak, account.capital_peak))
    book = AllocationBook(PortfolioAllocator(DEFAULT_CONFIG), date, risk, panel, leaders,
                          account, prices, weights, set(), {}, dict(weights), dict(weights), 0.)
    assert _graduate(book, set(SYMBOLS), Opportunity.STRONG_TREND, False) == (not blocked)
    assert before == (account.cash, {s:p.shares for s,p in account.positions.items()},
                      account.fills, account.pending_orders, (account.operating_peak, account.capital_peak))
    if not blocked:
        assert not account.anchor_weights
        assert all(p.lifecycle == "CORE" and all(t.lifecycle == "CORE" for t in p.tranches)
                   for p in account.positions.values())
