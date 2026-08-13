from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import uquant.risk as risk_module
from uquant.config import DEFAULT_CONFIG
from uquant.engine import attribution
from uquant.portfolio import PortfolioAllocator
from uquant.risk_sector import observe_deployed_sector, update_sector_guard
from uquant.types import (
    AccountState,
    Fill,
    Lifecycle,
    Opportunity,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
    Tranche,
)


def _panel(dates: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    close = np.asarray([100.0, 101.0, 102.0, 103.0, 96.0, 98.0, 91.0, 92.0, 94.0, 96.0, 98.0])
    return {symbol: pd.DataFrame({"close": close}, index=dates) for symbol in ("arbitrary_a", "arbitrary_b")}


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


def test_sector_guard_recovery_observes_the_trigger_cohort_after_sparse_cut() -> None:
    dates = pd.bdate_range("2026-06-01", periods=11)
    panel = _panel(dates)
    panel["arbitrary_c"] = panel["arbitrary_a"].copy()
    cfg = DEFAULT_CONFIG.override(
        sector_recovery_ma=3,
        sector_guard_min_sessions=2,
        sector_recovery_confirmations=2,
    )
    account = _account()
    account.positions["arbitrary_c"] = Position("arbitrary_c", shares=1, avg_cost=100.0)

    for date in dates[:7]:
        transition = update_sector_guard(
            date=date,
            calendar=dates,
            panel=panel,
            account=account,
            leadership_divergence=0.60,
            cfg=cfg,
        )
    assert transition.triggered
    assert account.sector_guard_symbols == ["arbitrary_a", "arbitrary_b", "arbitrary_c"]

    # The guard's own sparse cap may leave a single economic survivor.  The
    # original three-name shock cohort remains the recovery observation set.
    account.positions.pop("arbitrary_b")
    account.positions.pop("arbitrary_c")
    assert list(account.positions) == ["arbitrary_a"]
    for date in dates[7:10]:
        transition = update_sector_guard(
            date=date,
            calendar=dates,
            panel=panel,
            account=account,
            leadership_divergence=0.60,
            cfg=cfg,
        )

    assert transition.recovered
    assert not account.sector_guard_active
    assert account.sector_guard_symbols == []


def test_sector_guard_uses_weighted_exposure_when_equal_weight_breadth_is_benign() -> None:
    dates = pd.bdate_range("2026-06-01", periods=4)
    panel = {
        "concentrated_loser": pd.DataFrame(
            {"close": (100.0, 96.0, 92.16, 88.4736)},
            index=dates,
        ),
        "small_winner": pd.DataFrame(
            {"close": (100.0, 102.0, 104.04, 106.1208)},
            index=dates,
        ),
    }
    account = AccountState.empty(1_000.0)
    account.positions = {
        "concentrated_loser": Position("concentrated_loser", shares=8, avg_cost=100.0),
        "small_winner": Position("small_winner", shares=2, avg_cost=100.0),
    }
    cfg = DEFAULT_CONFIG.override(sector_recovery_ma=3)

    first = update_sector_guard(
        date=dates[2],
        calendar=dates,
        panel=panel,
        account=account,
        leadership_divergence=0.60,
        cfg=cfg,
    )
    second = update_sector_guard(
        date=dates[3],
        calendar=dates,
        panel=panel,
        account=account,
        leadership_divergence=0.60,
        cfg=cfg,
    )

    assert first.shock and not first.triggered
    assert second.triggered and second.active
    assert second.observation is not None
    assert second.observation.equal_return > cfg.sector_shock_return
    assert second.observation.positive_breadth > cfg.sector_shock_breadth
    assert second.observation.weighted_return <= cfg.sector_weighted_shock_return
    assert second.observation.negative_exposure >= cfg.sector_weighted_negative_exposure


def test_confirmed_acute_sector_collapse_requires_full_weighted_and_breadth_damage() -> None:
    dates = pd.bdate_range("2026-06-01", periods=4)
    cfg = DEFAULT_CONFIG.override(sector_recovery_ma=3)

    def transition_for(daily_multiplier: float):
        close = np.asarray(
            [
                100.0,
                100.0 * daily_multiplier,
                100.0 * daily_multiplier**2,
                100.0 * daily_multiplier**3,
            ]
        )
        panel = {
            symbol: pd.DataFrame({"close": close}, index=dates)
            for symbol in ("arbitrary_a", "arbitrary_b")
        }
        account = _account()
        update_sector_guard(
            date=dates[2],
            calendar=dates,
            panel=panel,
            account=account,
            leadership_divergence=1.0,
            cfg=cfg,
        )
        return update_sector_guard(
            date=dates[3],
            calendar=dates,
            panel=panel,
            account=account,
            leadership_divergence=1.0,
            cfg=cfg,
        )

    acute = transition_for(0.94)
    ordinary = transition_for(0.97)

    assert acute.triggered and ordinary.triggered
    assert risk_module._acute_sector_evacuation_required(acute, cfg)
    assert not risk_module._acute_sector_evacuation_required(ordinary, cfg)


def test_disabling_sector_guard_clears_persisted_state() -> None:
    dates = pd.bdate_range("2026-06-01", periods=11)
    account = _account()
    account.sector_guard_active = True
    account.sector_guard_started = str(dates[2].date())
    account.sector_guard_symbols = ["arbitrary_a", "arbitrary_b"]
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
    assert account.sector_guard_symbols == []
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


def test_sector_cap_preserves_winner_and_reduces_weak_late_tranche_first() -> None:
    account = _account()
    account.positions["arbitrary_c"] = Position(
        "arbitrary_c",
        shares=1,
        avg_cost=100.0,
    )
    account.positions["arbitrary_a"].highest_close = 140.0
    account.positions["arbitrary_a"].tranches = [
        Tranche(
            "winner_core",
            Lifecycle.CORE.value,
            1,
            100.0,
            "2026-01-02",
            "2026-01-05",
            140.0,
            mfe=0.40,
            mae=-0.02,
            entry_score=0.90,
        )
    ]
    account.positions["arbitrary_b"].tranches = [
        Tranche(
            "late_add2",
            Lifecycle.ADD2.value,
            1,
            100.0,
            "2026-05-28",
            "2026-05-29",
            101.0,
            mfe=0.01,
            mae=-0.18,
            entry_score=0.30,
        )
    ]
    targets = (
        Target("arbitrary_a", 0.60, "CORE", 0.80, 1.0, "anchor"),
        Target("arbitrary_b", 0.16, Lifecycle.ADD2.value, 0.30, 1.0, "late add"),
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
        gross_cap=0.75,
    )
    reversed_capped = allocator._turnover_aware_sector_cap(
        targets=tuple(reversed(targets)),
        weights_now=weights_now,
        account=account,
        gross_cap=0.75,
    )
    observed = {item.symbol: item.weight for item in capped}

    assert observed == {
        "arbitrary_a": pytest.approx(0.60),
        "arbitrary_b": pytest.approx(0.02),
        "arbitrary_c": pytest.approx(0.13),
    }
    assert observed == {item.symbol: item.weight for item in reversed_capped}
    assert sum(observed.values()) <= 0.75 + 1e-12
    assert all(observed[symbol] <= weight for symbol, weight in weights_now.items())
    assert sum(abs(item.weight - observed[item.symbol]) > 1e-12 for item in targets) == 1
    reduced = next(item for item in capped if item.symbol == "arbitrary_b")
    assert reduced.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
    assert reduced.reason_code == "risk_gross_cap"
    assert reduced.exit_kind == "risk"
    assert "risk gross cap" in reduced.reason


@pytest.mark.parametrize(
    ("risk", "expected_reason", "expected_code", "expected_exit_kind"),
    (
        (
            RiskAssessment(Risk.RISK_OFF, 0.50, 2, {}, (), "RISK_OFF"),
            "portfolio risk-off gross cap",
            "risk_off",
            "risk_off",
        ),
        (
            RiskAssessment(Risk.CRISIS, 0.50, 4, {}, (), "SHOCK"),
            "portfolio crisis gross cap",
            "crisis",
            "crisis",
        ),
        (
            RiskAssessment(
                Risk.CAUTION,
                0.50,
                1,
                {"capital_budget_level": 2},
                (),
                "CAPITAL_GUARD",
            ),
            "capital budget gross cap",
            "capital_budget",
            "capital_budget",
        ),
        (
            RiskAssessment(
                Risk.RISK_OFF,
                0.82,
                2,
                {"sector_guard_active": True, "capital_budget_level": 3},
                (),
                "SECTOR_GUARD",
            ),
            "sector guard gross cap",
            "sector_guard",
            "sector_guard",
        ),
    ),
    ids=("risk_off", "crisis", "capital_budget", "sector_guard_precedence"),
)
def test_allocator_preserves_structured_risk_reduction_owner(
    monkeypatch: pytest.MonkeyPatch,
    risk: RiskAssessment,
    expected_reason: str,
    expected_code: str,
    expected_exit_kind: str,
) -> None:
    account = AccountState.empty(100.0)
    account.cash = 0.0
    account.positions = {
        "durable_core": Position("durable_core", shares=60, avg_cost=1.0),
        "late_add": Position("late_add", shares=40, avg_cost=1.0),
    }
    strategy_targets = (
        Target("durable_core", 0.60, Lifecycle.CORE.value, 0.90, 1.0, "core target"),
        Target("late_add", 0.40, Lifecycle.ADD2.value, 0.40, 1.0, "add target"),
    )

    def fixed_strategy(
        _allocator: PortfolioAllocator,
        **_kwargs: object,
    ) -> tuple[Target, ...]:
        return strategy_targets

    monkeypatch.setattr(PortfolioAllocator, "_allocate_strategy", fixed_strategy)
    reduced = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=pd.Timestamp("2026-08-10"),
        opportunity=Opportunity.CHOPPY,
        risk=risk,
        user_panel={},
        leaders={},
        account=account,
        prices={"durable_core": 1.0, "late_add": 1.0},
    )

    changed = [target for target in reduced if target.weight + 1e-12 < 0.40]
    assert changed
    assert all(target.reason.startswith(expected_reason) for target in changed)
    assert all(target.reason_code == expected_code for target in changed)
    assert all(target.exit_kind == expected_exit_kind for target in changed)


def test_attribution_separates_sector_guard_from_other_risk_reductions() -> None:
    def risk_fill(symbol: str, *, reason_code: str, exit_kind: str) -> Fill:
        return Fill(
            signal_date="2026-08-07",
            fill_date="2026-08-10",
            symbol=symbol,
            side="SELL",
            shares=10,
            price=1.0,
            gross_value=10.0,
            commission=0.0,
            stamp_duty=0.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            reason="hard gross reduction",
            lifecycle=Lifecycle.CORE.value,
            reduction_policy=ReductionPolicy.RISK_PRIORITY.value,
            reason_code=reason_code,
            exit_kind=exit_kind,
        )

    result = attribution(
        [
            risk_fill("sector_name", reason_code="sector_guard", exit_kind="sector_guard"),
            risk_fill("market_name", reason_code="risk_off", exit_kind="risk_off"),
        ]
    )

    assert result["by_reason"]["sector_guard"]["fills"] == 1
    assert result["by_reason"]["risk_off"]["fills"] == 1


def test_sparse_cap_uses_global_lot_priority_before_symbol_alpha() -> None:
    account = AccountState.empty(100.0)
    account.positions = {
        "mixed_high_alpha": Position(
            "mixed_high_alpha",
            shares=3,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    "mixed_core",
                    Lifecycle.CORE.value,
                    2,
                    1.0,
                    "2026-01-02",
                    "2026-01-05",
                    1.2,
                    mae=-0.02,
                    entry_score=0.90,
                ),
                Tranche(
                    "mixed_add2",
                    Lifecycle.ADD2.value,
                    1,
                    1.0,
                    "2026-05-20",
                    "2026-05-21",
                    1.0,
                    mae=-0.18,
                    entry_score=0.90,
                ),
            ],
        ),
        "healthy_core": Position(
            "healthy_core",
            shares=2,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    "healthy_core_lot",
                    Lifecycle.CORE.value,
                    2,
                    1.0,
                    "2026-01-02",
                    "2026-01-05",
                    1.1,
                    mae=-0.02,
                    entry_score=0.40,
                )
            ],
        ),
    }
    targets = (
        Target("mixed_high_alpha", 0.60, Lifecycle.CORE.value, 0.90, 1.0, "mixed"),
        Target("healthy_core", 0.40, Lifecycle.CORE.value, 0.40, 1.0, "healthy"),
    )

    capped = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={"mixed_high_alpha": 0.60, "healthy_core": 0.40},
        account=account,
        gross_cap=0.70,
    )
    observed = {target.symbol: target.weight for target in capped}

    assert observed == pytest.approx({"mixed_high_alpha": 0.30, "healthy_core": 0.40})
    assert next(target for target in capped if target.symbol == "mixed_high_alpha").reduction_policy == (
        ReductionPolicy.RISK_PRIORITY.value
    )


def test_strategic_damage_guard_retains_the_healthier_core_before_stale_alpha() -> None:
    account = AccountState.empty(100.0)
    account.positions = {
        "healthy_core": Position(
            "healthy_core",
            shares=50,
            avg_cost=1.0,
            highest_close=1.0,
            lifecycle=Lifecycle.CORE.value,
        ),
        "damaged_core": Position(
            "damaged_core",
            shares=50,
            avg_cost=1.0,
            highest_close=1.0,
            lifecycle=Lifecycle.CORE.value,
        ),
    }
    targets = (
        Target("healthy_core", 0.50, Lifecycle.CORE.value, 0.50, 1.0, "healthy"),
        Target("damaged_core", 0.50, Lifecycle.CORE.value, 0.99, 1.0, "stale alpha"),
    )

    capped = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={target.symbol: target.weight for target in targets},
        account=account,
        gross_cap=0.50,
        risk_reason="strategic transition damage gross cap",
        risk_reason_code="strategic_damage_guard",
        prices={"healthy_core": 0.98, "damaged_core": 0.80},
    )

    assert {target.symbol: target.weight for target in capped} == pytest.approx(
        {"healthy_core": 0.50, "damaged_core": 0.0}
    )


def test_sparse_cap_sells_mixed_satellite_before_another_symbols_add2() -> None:
    account = AccountState.empty(100.0)
    account.positions = {
        "mixed": Position(
            "mixed",
            shares=2,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    "mixed_core",
                    Lifecycle.CORE.value,
                    1,
                    1.0,
                    "2026-01-02",
                    "2026-01-05",
                    1.1,
                ),
                Tranche(
                    "mixed_satellite",
                    Lifecycle.SATELLITE.value,
                    1,
                    1.0,
                    "2026-06-01",
                    "2026-06-02",
                    1.0,
                ),
            ],
        ),
        "add2": Position(
            "add2",
            shares=3,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    "pure_add2",
                    Lifecycle.ADD2.value,
                    3,
                    1.0,
                    "2026-05-01",
                    "2026-05-04",
                    1.0,
                )
            ],
        ),
    }
    targets = (
        Target("mixed", 0.40, Lifecycle.CORE.value, 0.90, 1.0, "mixed"),
        Target("add2", 0.30, Lifecycle.ADD2.value, 0.80, 1.0, "add2"),
    )

    capped = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={"mixed": 0.40, "add2": 0.30},
        account=account,
        gross_cap=0.40,
    )

    assert {target.symbol: target.weight for target in capped} == pytest.approx({"mixed": 0.20, "add2": 0.20})


def test_sparse_cap_reaches_exact_gross_when_every_position_is_recovery() -> None:
    account = AccountState.empty(100.0)
    symbols = ("recovery_a", "recovery_b", "recovery_c")
    account.positions = {
        symbol: Position(
            symbol,
            shares=30,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    f"{symbol}_lot",
                    Lifecycle.RECOVERY.value,
                    30,
                    1.0,
                    "2026-01-02",
                    "2026-01-05",
                    1.0,
                )
            ],
        )
        for symbol in symbols
    }
    targets = tuple(
        Target(symbol, 0.30, Lifecycle.RECOVERY.value, 0.80, 1.0, "recovery") for symbol in symbols
    )

    capped = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={symbol: 0.30 for symbol in symbols},
        account=account,
        gross_cap=0.60,
    )

    assert sum(target.weight for target in capped) == pytest.approx(0.60)
    assert sum(target.weight > 0 for target in capped) == 2


def test_healthy_core_priority_precedes_recovery_retention_utility() -> None:
    account = AccountState.empty(100.0)

    def position(symbol: str, lifecycle: Lifecycle, *, shares: int, entry_score: float) -> Position:
        return Position(
            symbol,
            shares=shares,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    f"{symbol}_lot",
                    lifecycle.value,
                    shares,
                    1.0,
                    "2026-01-02",
                    "2026-01-05",
                    1.1,
                    mae=-0.02,
                    entry_score=entry_score,
                )
            ],
        )

    account.positions = {
        "healthy_core": position("healthy_core", Lifecycle.CORE, shares=60, entry_score=0.90),
        "small_recovery": position("small_recovery", Lifecycle.RECOVERY, shares=12, entry_score=0.30),
        "strong_recovery": position("strong_recovery", Lifecycle.RECOVERY, shares=19, entry_score=0.80),
    }
    targets = (
        Target("healthy_core", 0.60, Lifecycle.CORE.value, 0.90, 1.0, "core"),
        Target("small_recovery", 0.12, Lifecycle.RECOVERY.value, 0.30, 1.0, "small"),
        Target("strong_recovery", 0.19, Lifecycle.RECOVERY.value, 0.80, 1.0, "strong"),
    )

    capped = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={target.symbol: target.weight for target in targets},
        account=account,
        gross_cap=0.255,
    )
    observed = {target.symbol: target.weight for target in capped}

    assert observed == pytest.approx(
        {
            "healthy_core": 0.255,
            "small_recovery": 0.0,
            "strong_recovery": 0.0,
        }
    )


def test_locked_recovery_anchors_share_core_priority_and_reduce_sparsely() -> None:
    account = AccountState.empty(100.0)

    def position(symbol: str, lifecycle: Lifecycle, shares: int) -> Position:
        return Position(
            symbol,
            shares=shares,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    f"{symbol}_lot",
                    lifecycle.value,
                    shares,
                    1.0,
                    "2026-01-02",
                    "2026-01-05",
                    1.1,
                    mae=-0.02,
                    entry_score=0.80,
                )
            ],
        )

    account.positions = {
        "lead": position("lead", Lifecycle.CORE, 60),
        "reserve_a": position("reserve_a", Lifecycle.RECOVERY, 16),
        "reserve_b": position("reserve_b", Lifecycle.RECOVERY, 16),
    }
    account.anchor_weights = {"lead": 0.60, "reserve_a": 0.16, "reserve_b": 0.16}
    account.candidate_tenure["recovery_cohort_locked"] = 1
    targets = (
        Target("lead", 0.60, Lifecycle.CORE.value, 0.90, 1.0, "lead"),
        Target("reserve_a", 0.16, Lifecycle.RECOVERY.value, 0.80, 1.0, "reserve"),
        Target("reserve_b", 0.16, Lifecycle.RECOVERY.value, 0.79, 1.0, "reserve"),
    )

    capped = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={target.symbol: target.weight for target in targets},
        account=account,
        gross_cap=0.40,
    )

    assert {target.symbol: target.weight for target in capped} == pytest.approx(
        {"lead": 0.08, "reserve_a": 0.16, "reserve_b": 0.16}
    )


def test_global_lifecycle_priority_dominates_a_sparser_fragile_plan() -> None:
    account = AccountState.empty(100.0)

    def position(symbol: str, lifecycle: Lifecycle) -> Position:
        return Position(
            symbol,
            shares=100,
            avg_cost=1.0,
            tranches=[
                Tranche(
                    f"{symbol}_lot",
                    lifecycle.value,
                    100,
                    1.0,
                    "2026-01-02",
                    "2026-01-05",
                    1.1,
                    mae=-0.02,
                    entry_score=0.50,
                )
            ],
        )

    account.positions = {
        "healthy_core": position("healthy_core", Lifecycle.CORE),
        "late_add2": position("late_add2", Lifecycle.ADD2),
        "fragile_satellite": position("fragile_satellite", Lifecycle.SATELLITE),
    }
    targets = (
        Target("healthy_core", 0.40, Lifecycle.CORE.value, 0.40, 1.0, "healthy"),
        Target("late_add2", 0.30, Lifecycle.ADD2.value, 0.90, 1.0, "late add"),
        Target(
            "fragile_satellite",
            0.30,
            Lifecycle.SATELLITE.value,
            0.95,
            1.0,
            "satellite",
        ),
    )

    capped = PortfolioAllocator(DEFAULT_CONFIG)._sparse_risk_reduce(
        targets=targets,
        weights_now={target.symbol: target.weight for target in targets},
        account=account,
        gross_cap=0.60,
    )
    observed = {target.symbol: target.weight for target in capped}

    assert observed == pytest.approx(
        {
            "healthy_core": 0.40,
            "late_add2": 0.20,
            "fragile_satellite": 0.0,
        }
    )
    satellite = next(target for target in capped if target.symbol == "fragile_satellite")
    assert satellite.reduction_policy == ReductionPolicy.RISK_PRIORITY.value
