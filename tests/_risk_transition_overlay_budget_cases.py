from __future__ import annotations

import numpy as np
import pandas as pd
from test_risk_transitions import (
    _assess,
    _isolated_risk_config,
    _leader,
    _market_frame,
)

from uquant.config import DEFAULT_CONFIG
from uquant.leader import REFERENCE_UNIVERSE
from uquant.risk import (
    _capital_budget_repair_drawdown_confirmed,
    _strategic_crisis_severity,
    _update_capital_budget_ladder,
    assess_risk,
)
from uquant.types import AccountState, Position


def test_acute_overlay_preserves_existing_zero_gross_crisis_owner() -> None:
    dates = pd.bdate_range("2026-01-02", periods=131)
    first_shock, acute_shock, next_session = dates[-3:]
    broad = pd.DataFrame(
        {
            "close": np.linspace(100.0, 110.0, len(dates)),
            "ma20": 100.0,
            "ma60": 95.0,
            "ret5": 0.02,
            "ret10": 0.03,
            "ret20": 0.05,
            "ret60": 0.10,
            "ret120": 0.0,
        },
        index=dates,
    )
    tech = broad.copy()
    tech["ret120"] = 0.60
    reference = pd.DataFrame(
        {
            "close": 120.0,
            "ma20": 100.0,
            "ma60": 90.0,
            "ret5": 0.05,
            "ret20": 0.10,
            "ret60": 0.20,
            "ret120": 0.30,
        },
        index=dates,
    )
    reference_panel = {
        symbol: reference.copy() for symbol in REFERENCE_UNIVERSE
    }
    leaders = {
        symbol: _leader(symbol, score=0.90)
        for symbol in REFERENCE_UNIVERSE
    }
    close = np.full(len(dates), 100.0)
    close[-3:] = [91.0, 85.54, 85.54]
    held = pd.DataFrame(
        {
            "close": close,
            "ma20": 100.0,
            "ma60": 95.0,
            "ret5": 0.0,
            "ret20": 0.0,
            "ret60": 0.0,
            "ret120": 0.0,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )
    held.loc[first_shock, "ret5"] = -0.10
    held.loc[acute_shock:next_session, "ret5"] = -0.13
    symbols = ("held_a", "held_b")
    user_panel = {symbol: held.copy() for symbol in symbols}
    leaders.update({symbol: _leader(symbol) for symbol in symbols})
    account = AccountState(
        initial_cash=20_000.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol,
                shares=100,
                avg_cost=100.0,
                lifecycle="RECOVERY",
            )
            for symbol in symbols
        },
        operating_peak=20_000.0,
        capital_peak=20_000.0,
    )
    cfg = DEFAULT_CONFIG.override(
        dynamic_risk_anchors_enabled=False,
        chronic_overlay_enabled=False,
        caution_confirm_days=99,
        risk_off_confirm_days=99,
        crisis_confirm_days=99,
        sector_recovery_ma=3,
    )

    assessments = [
        assess_risk(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            reference_returns=None,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            equity=200 * float(held.loc[date, "close"]),
            cfg=cfg,
        )
        for date in (first_shock, acute_shock, next_session)
    ]

    assert [item.target_gross_cap for item in assessments] == [0.0, 0.0, 0.0]
    assert assessments[0].severity == "INCOMPLETE_UNIVERSE_UNBACKED"
    assert assessments[0].reasons == (
        "unbacked incomplete-universe capital exit",
    )
    assert assessments[1].evidence["acute_sector_evacuation"] is True
    assert assessments[1].severity == "INCOMPLETE_UNIVERSE_UNBACKED"
    assert assessments[2].severity == "INCOMPLETE_UNIVERSE_UNBACKED"
    assert assessments[2].shock_state == "UNBACKED_COOLDOWN"

def test_protected_restore_cannot_use_overweight_members_to_hide_a_missing_member() -> None:
    dates = pd.bdate_range("2026-01-02", periods=80)
    date = dates[-1]
    symbols = ("overweight_a", "overweight_b", "missing_c")
    healthy = _market_frame(dates)
    account = AccountState.empty(100.0)
    account.cash = 0.0
    account.positions = {
        symbol: Position(
            symbol,
            shares=1,
            avg_cost=100.0,
            entry_date=str(dates[-30].date()),
            highest_close=110.0,
        )
        for symbol in symbols[:2]
    }
    account.protected_weights = {symbol: 0.30 for symbol in symbols}
    cfg = _isolated_risk_config()

    _assess(
        date=date,
        dates=dates,
        states={date: "healthy"},
        account=account,
        cfg=cfg,
        user_panel={symbol: healthy for symbol in symbols},
        equity=100.0,
    )

    assert account.protected_weights == {symbol: 0.30 for symbol in symbols}

def test_capital_budget_repairs_exactly_one_level_per_confirmation_window() -> None:
    account = AccountState.empty(100.0)
    account.capital_budget_level = 4
    repair_days = 3

    for expected_level in (3, 2, 1, 0):
        for _ in range(repair_days - 1):
            _update_capital_budget_ladder(
                account,
                observed_level=0,
                repair_confirmed=True,
                repair_days=repair_days,
            )
            assert account.capital_budget_level == expected_level + 1
        _update_capital_budget_ladder(
            account,
            observed_level=0,
            repair_confirmed=True,
            repair_days=repair_days,
        )
        assert account.capital_budget_level == expected_level
        assert account.capital_budget_repair_streak == 0

def test_capital_budget_repair_requires_drawdown_recovery() -> None:
    cfg = DEFAULT_CONFIG

    assert not _capital_budget_repair_drawdown_confirmed(
        level=3,
        capital_drawdown=0.24,
        operating_drawdown=0.24,
        cfg=cfg,
    )
    assert _capital_budget_repair_drawdown_confirmed(
        level=3,
        capital_drawdown=cfg.capital_budget_level3_dd - 0.001,
        operating_drawdown=cfg.capital_budget_level3_dd - 0.001,
        cfg=cfg,
    )
    assert not _capital_budget_repair_drawdown_confirmed(
        level=1,
        capital_drawdown=cfg.operating_dd_caution + 0.001,
        operating_drawdown=0.0,
        cfg=cfg,
    )

def test_single_core_strategic_crisis_uses_concentrated_severity() -> None:
    assert (
        _strategic_crisis_severity(
            strategic_active=True,
            reference_anchor_confirmed=True,
            live_core_positions=1,
        )
        == "CONCENTRATED"
    )
    assert (
        _strategic_crisis_severity(
            strategic_active=True,
            reference_anchor_confirmed=True,
            live_core_positions=3,
        )
        == "MARKET"
    )

def test_capital_budget_relapse_escalates_immediately_and_resets_repair() -> None:
    account = AccountState.empty(100.0)
    account.capital_budget_level = 3
    account.capital_budget_repair_streak = 2

    _update_capital_budget_ladder(
        account,
        observed_level=4,
        repair_confirmed=False,
        repair_days=3,
    )

    assert account.capital_budget_level == 4
    assert account.capital_budget_repair_streak == 0
