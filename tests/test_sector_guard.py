from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.risk_sector import observe_deployed_sector, update_sector_guard
from uquant.types import AccountState, Position, Target


def _panel(dates: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    close = np.asarray(
        [100.0, 101.0, 102.0, 103.0, 96.0, 98.0, 91.0, 92.0, 94.0, 96.0, 98.0]
    )
    return {
        symbol: pd.DataFrame({"close": close}, index=dates)
        for symbol in ("arbitrary_a", "arbitrary_b")
    }


def _account() -> AccountState:
    account = AccountState.empty(100.0)
    account.positions = {
        "arbitrary_a": Position("arbitrary_a", shares=1, avg_cost=100.0),
        "arbitrary_b": Position("arbitrary_b", shares=1, avg_cost=100.0),
    }
    return account


def test_sector_observation_uses_only_deployed_symbols() -> None:
    dates = pd.bdate_range("2026-06-01", periods=11)
    panel = _panel(dates)
    panel["unheld_rally"] = pd.DataFrame(
        {"close": np.linspace(100.0, 200.0, len(dates))},
        index=dates,
    )
    observation = observe_deployed_sector(
        date=dates[4],
        panel=panel,
        symbols={"arbitrary_a", "arbitrary_b"},
        cfg=DEFAULT_CONFIG.override(sector_recovery_ma=3),
    )

    assert observation is not None
    assert observation.symbol_count == 2
    assert observation.equal_return < -0.06
    assert observation.positive_breadth == 0.0


def test_sector_observation_accepts_an_exact_recovery_ma_window() -> None:
    dates = pd.bdate_range("2026-06-01", periods=11)
    observation = observe_deployed_sector(
        date=dates[2],
        panel=_panel(dates),
        symbols={"arbitrary_a", "arbitrary_b"},
        cfg=DEFAULT_CONFIG.override(sector_recovery_ma=3),
    )

    assert observation is not None
    assert observation.symbol_count == 2


def test_sector_guard_requires_repeated_shock_and_independent_divergence() -> None:
    dates = pd.bdate_range("2026-06-01", periods=11)
    panel = _panel(dates)
    cfg = DEFAULT_CONFIG.override(
        sector_recovery_ma=3,
        sector_guard_min_sessions=2,
        sector_recovery_confirmations=2,
    )
    account = _account()
    transitions = [
        update_sector_guard(
            date=date,
            calendar=dates,
            panel=panel,
            account=account,
            leadership_divergence=0.60,
            cfg=cfg,
        )
        for date in dates[:10]
    ]

    assert not transitions[4].active
    assert transitions[6].triggered
    assert transitions[6].active
    assert transitions[7].active
    assert transitions[9].recovered
    assert not account.sector_guard_active
    assert account.sector_shock_dates == []

    low_divergence = _account()
    for date in dates[:7]:
        transition = update_sector_guard(
            date=date,
            calendar=dates,
            panel=panel,
            account=low_divergence,
            leadership_divergence=0.20,
            cfg=cfg,
        )
    assert not transition.active


def test_disabling_sector_guard_clears_persisted_state() -> None:
    dates = pd.bdate_range("2026-06-01", periods=11)
    account = _account()
    account.sector_guard_active = True
    account.sector_guard_started = str(dates[2].date())
    account.sector_shock_dates = [str(dates[2].date())]
    account.sector_recovery_streak = 1

    transition = update_sector_guard(
        date=dates[5],
        calendar=dates,
        panel=_panel(dates),
        account=account,
        leadership_divergence=1.0,
        cfg=DEFAULT_CONFIG.override(sector_guard_enabled=False),
    )

    assert not transition.active
    assert not account.sector_guard_active
    assert account.sector_guard_started == ""
    assert account.sector_shock_dates == []
    assert account.sector_recovery_streak == 0


def test_future_dated_sector_shock_cannot_confirm_current_guard() -> None:
    dates = pd.bdate_range("2026-06-01", periods=11)
    account = _account()
    account.sector_shock_dates = ["2099-01-01"]

    transition = update_sector_guard(
        date=dates[4],
        calendar=dates,
        panel=_panel(dates),
        account=account,
        leadership_divergence=1.0,
        cfg=DEFAULT_CONFIG.override(sector_recovery_ma=3),
    )

    assert not transition.active
    assert account.sector_shock_dates == [str(dates[4].date())]


def test_sector_cap_minimizes_target_changes_without_adding_risk() -> None:
    account = _account()
    account.positions["arbitrary_c"] = Position(
        "arbitrary_c",
        shares=1,
        avg_cost=100.0,
    )
    account.positions["arbitrary_a"].highest_close = 140.0
    targets = (
        Target("arbitrary_a", 0.60, "CORE", 0.80, 1.0, "anchor"),
        Target("arbitrary_b", 0.16, "CORE", 0.30, 1.0, "anchor"),
        Target("arbitrary_c", 0.13, "CORE", 0.50, 1.0, "anchor"),
    )
    weights_now = {
        "arbitrary_a": 0.68,
        "arbitrary_b": 0.16,
        "arbitrary_c": 0.13,
    }
    allocator = PortfolioAllocator(DEFAULT_CONFIG)

    capped = allocator._turnover_aware_sector_cap(
        targets=targets,
        weights_now=weights_now,
        account=account,
        gross_cap=0.40,
    )
    reversed_capped = allocator._turnover_aware_sector_cap(
        targets=tuple(reversed(targets)),
        weights_now=weights_now,
        account=account,
        gross_cap=0.40,
    )
    observed = {item.symbol: item.weight for item in capped}

    assert observed == {
        "arbitrary_a": pytest.approx(0.11),
        "arbitrary_b": pytest.approx(0.16),
        "arbitrary_c": pytest.approx(0.13),
    }
    assert observed == {
        item.symbol: item.weight for item in reversed_capped
    }
    assert sum(observed.values()) <= 0.40 + 1e-12
    assert all(observed[symbol] <= weight for symbol, weight in weights_now.items())
    assert sum(
        abs(item.weight - observed[item.symbol]) > 1e-12 for item in targets
    ) == 1
