"""The existing risk cap and lifecycle ordering dominate economic retention."""
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Lifecycle, Target


def test_equal_health_retains_stronger_core_before_saving_one_order():
    account = AccountState.empty(100.)
    targets = (Target("strong", .6, Lifecycle.CORE.value, .95, .95, "core"),
               Target("weaker", .2, Lifecycle.CORE.value, .5, .95, "core"))
    reduced = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets, weights_now={"strong": .6, "weaker": .2}, account=account, gross_cap=.3)
    weights = {t.symbol:t.weight for t in reduced}
    assert weights == pytest.approx({"strong": .3, "weaker": 0.})
    assert sum(weights.values()) == pytest.approx(.3)
    assert all(t.origin_subsystem == "RISK" for t in reduced)


def test_incremental_lifecycle_cannot_buy_priority_with_higher_alpha():
    account = AccountState.empty(100.)
    targets = (Target("core", .6, Lifecycle.CORE.value, .4, .95, "core"),
               Target("satellite", .2, Lifecycle.SATELLITE.value, .99, .99, "satellite"))
    reduced = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets, weights_now={"core": .6, "satellite": .2}, account=account, gross_cap=.3)
    assert {t.symbol:t.weight for t in reduced} == pytest.approx({"core": .3, "satellite": 0.})
