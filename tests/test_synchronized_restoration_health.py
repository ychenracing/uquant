"""A synchronized repair label cannot override current account damage."""
import pandas as pd
import pytest
from _recovery_restore_completion_cases import _restore_panel

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, LeaderScore, Opportunity, Position, Risk, RiskAssessment


@pytest.mark.parametrize("votes,damage,allowed", [(0, .1, True), (5, .1, False), (0, .9, False)])
def test_synchronized_restoration_requires_current_health(votes, damage, allowed):
    account = AccountState(initial_cash=10000., cash=8000.,
        positions={"lead": Position("lead", shares=2000, avg_cost=1., entry_date="2025-01-01")},
        protected_weights={"lead": .5}, capital_budget_level=1,
        last_shock_date="2026-01-02", operating_peak=10000., capital_peak=10000.)
    risk = RiskAssessment(Risk.CAUTION, .92, votes,
        {"transition_damage": damage}, ("two-day synchronized leader repair",),
        "RECOVERY", freeze_new_risk=True, reduction_level=1)
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-01-07"), opportunity=Opportunity.RECOVERY,
        risk=risk, user_panel=_restore_panel(["lead"]),
        leaders={"lead": LeaderScore("lead", .8, 1., True, False, "materials", {})},
        account=account, prices={"lead": 1.})
    weight = next(t.weight for t in targets if t.symbol == "lead")
    assert weight == pytest.approx(.5 if allowed else .2)
    assert account.positions["lead"].shares == 2000
    assert account.protected_weights == {"lead": .5}
