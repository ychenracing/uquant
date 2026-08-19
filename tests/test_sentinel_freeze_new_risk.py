from __future__ import annotations

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    Lifecycle,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
    Target,
)


def _risk() -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={
            "base_freeze_new_risk": False,
            "sentinel_freeze_new_risk": True,
            "freeze_new_risk": True,
        },
        reasons=(),
        shock_state="NONE",
        freeze_new_risk=True,
        reduction_level=0,
        severity="NORMAL",
    )


def _account(*, lifecycle: str = Lifecycle.CORE.value) -> AccountState:
    return AccountState(
        initial_cash=1_000.0,
        cash=500.0,
        positions={
            "old": Position(
                symbol="old",
                shares=5,
                avg_cost=100.0,
                entry_date="2026-01-01",
                highest_close=100.0,
                lifecycle=lifecycle,
            )
        },
        operating_peak=1_000.0,
        capital_peak=1_000.0,
    )


def _target(
    symbol: str,
    weight: float,
    *,
    lifecycle: str = Lifecycle.CORE.value,
    replaces_symbol: str | None = None,
    reason_code: str = "strategy_target",
) -> Target:
    return Target(
        symbol=symbol,
        weight=weight,
        lifecycle=lifecycle,
        alpha_score=0.9,
        confidence=0.9,
        reason="fixture target",
        reason_code=reason_code,
        replaces_symbol=replaces_symbol,
    )


def _allocate(
    account: AccountState,
    targets: tuple[Target, ...],
    monkeypatch,
) -> tuple[Target, ...]:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    def strategy(**_: object) -> tuple[Target, ...]:
        return targets

    monkeypatch.setattr(allocator, "_allocate_strategy", strategy)
    return allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.STRONG_TREND,
        risk=_risk(),
        user_panel={},
        leaders={},
        account=account,
        prices={"old": 100.0, "new": 100.0},
    )


def test_sentinel_freeze_clamps_entries_adds_satellites_and_recovery(monkeypatch) -> None:
    for lifecycle in (
        Lifecycle.CORE.value,
        Lifecycle.ADD1.value,
        Lifecycle.ADD2.value,
        Lifecycle.SATELLITE.value,
        Lifecycle.RECOVERY.value,
    ):
        account = _account(lifecycle=lifecycle)
        actual = _allocate(
            account,
            (
                _target("old", 0.70, lifecycle=lifecycle),
                _target("new", 0.30, lifecycle=lifecycle),
            ),
            monkeypatch,
        )

        assert [(target.symbol, target.weight) for target in actual] == [
            ("old", 0.50)
        ]


def test_sentinel_freeze_preserves_independent_strategy_exit(monkeypatch) -> None:
    actual = _allocate(
        _account(),
        (_target("old", 0.30, reason_code="lifecycle_exit"),),
        monkeypatch,
    )

    assert [(target.symbol, target.weight) for target in actual] == [
        ("old", 0.30)
    ]


def test_sentinel_freeze_suppresses_sell_funded_rotation(monkeypatch) -> None:
    actual = _allocate(
        _account(),
        (
            _target("old", 0.0, reason_code="rotation_exit"),
            _target("new", 0.50, replaces_symbol="old"),
        ),
        monkeypatch,
    )

    assert [(target.symbol, target.weight) for target in actual] == [
        ("old", 0.50)
    ]
