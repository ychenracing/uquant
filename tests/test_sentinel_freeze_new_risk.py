from __future__ import annotations

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    Position,
    Risk,
    RiskAssessment,
    Target,
)


def _risk(*, authorized: bool = True) -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={
            "base_freeze_new_risk": False,
            "sentinel_freeze_new_risk": True,
            "freeze_new_risk": authorized,
        },
        reasons=(),
        shock_state="NONE",
        freeze_new_risk=authorized,
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
    mechanism: str = "",
) -> Target:
    return Target(
        symbol=symbol,
        weight=weight,
        lifecycle=lifecycle,
        alpha_score=0.9,
        confidence=0.9,
        reason="fixture target",
        reason_code=reason_code,
        mechanism=mechanism,
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
            _target(
                "old",
                0.0,
                reason_code="rotation_exit",
                mechanism=AttributionMechanism.LEADER_ROTATION.value,
            ),
            _target("new", 0.50, replaces_symbol="old"),
        ),
        monkeypatch,
    )

    assert [(target.symbol, target.weight) for target in actual] == [
        ("old", 0.50)
    ]


def test_sentinel_freeze_suppresses_multi_symbol_rotation_atomically(
    monkeypatch,
) -> None:
    account = AccountState(
        initial_cash=1_000.0,
        cash=0.0,
        positions={
            symbol: Position(
                symbol=symbol,
                shares=5,
                avg_cost=100.0,
                entry_date="2026-01-01",
                highest_close=100.0,
                lifecycle=Lifecycle.CORE.value,
            )
            for symbol in ("old-a", "old-b")
        },
        operating_peak=1_000.0,
        capital_peak=1_000.0,
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    planned = (
        _target(
            "old-a",
            0.0,
            reason_code="rotation_exit",
            mechanism=AttributionMechanism.LEADER_ROTATION.value,
        ),
        _target(
            "old-b",
            0.0,
            reason_code="rotation_exit",
            mechanism=AttributionMechanism.LEADER_ROTATION.value,
        ),
        _target("new-a", 0.55, replaces_symbol="old-a"),
        _target("new-b", 0.45, replaces_symbol="old-b"),
    )
    monkeypatch.setattr(allocator, "_allocate_strategy", lambda **_: planned)

    actual = allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.STRONG_TREND,
        risk=_risk(),
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 100.0 for symbol in ("old-a", "old-b", "new-a", "new-b")},
    )

    assert [(target.symbol, target.weight) for target in actual] == [
        ("old-a", 0.50),
        ("old-b", 0.50),
    ]
    assert account.positions.keys() == {"old-a", "old-b"}


def test_sentinel_diagnostics_cannot_bypass_formal_freeze_authority(monkeypatch) -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    account = _account()
    expected = (_target("old", 0.70), _target("new", 0.30))
    monkeypatch.setattr(allocator, "_allocate_strategy", lambda **_: expected)

    actual = allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.STRONG_TREND,
        risk=_risk(authorized=False),
        user_panel={},
        leaders={},
        account=account,
        prices={"old": 100.0, "new": 100.0},
    )

    assert actual == expected


def test_sentinel_counterfactual_planning_cannot_mutate_account(monkeypatch) -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    account = _account()

    def mutating_strategy(*, account: AccountState, **_: object) -> tuple[Target, ...]:
        account.rotation_dates.append("2026-08-19")
        account.active_leaders = ["new"]
        account.strategic_epoch += 1
        return (_target("new", 0.50),)

    monkeypatch.setattr(allocator, "_allocate_strategy", mutating_strategy)
    actual = allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.STRONG_TREND,
        risk=_risk(),
        user_panel={},
        leaders={},
        account=account,
        prices={"old": 100.0, "new": 100.0},
    )

    assert [(target.symbol, target.weight) for target in actual] == [("old", 0.50)]
    assert account.rotation_dates == []
    assert account.active_leaders == []
    assert account.strategic_epoch == 0


def test_coincident_independent_exit_is_not_suppressed_by_replacement_buy(monkeypatch) -> None:
    actual = _allocate(
        _account(),
        (
            _target(
                "old",
                0.30,
                reason_code="lifecycle_exit",
                mechanism=AttributionMechanism.LEADER_LIFECYCLE_EXIT.value,
            ),
            _target("new", 0.20, replaces_symbol="old"),
        ),
        monkeypatch,
    )

    assert [(target.symbol, target.weight) for target in actual] == [("old", 0.30)]


def test_real_allocator_sentinel_freeze_holds_capital_and_advances_only_observation_clocks() -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    account = _account()
    before = account.to_dict()

    actual = allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.STRONG_TREND,
        risk=_risk(),
        user_panel={},
        leaders={},
        account=account,
        prices={"old": 100.0},
    )

    assert all(target.symbol == "old" and target.weight <= 0.50 for target in actual)
    after = account.to_dict()
    assert after["candidate_tenure"] == {
        "strategic_cohort_qualification": 0,
        "strategic_long_cycle_open": 0,
        "strategic_eligibility_session": pd.Timestamp("2026-08-19").toordinal(),
        "strategic_repair_observed_session": pd.Timestamp("2026-08-19").toordinal(),
    }
    after["candidate_tenure"] = before["candidate_tenure"]
    assert after["replacement_tenure"] == {"lifecycle_exit:old": 0}
    after["replacement_tenure"] = before["replacement_tenure"]
    assert after == before


def test_strategy_owned_exit_cleanup_commits_without_entry_state(monkeypatch) -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    account = _account()
    account.active_leaders = ["old"]

    def exiting_strategy(*, account: AccountState, **_: object) -> tuple[Target, ...]:
        account.active_leaders.remove("old")
        account.lifecycle_events.append(
            {"date": "2026-08-19", "symbol": "old", "event": "lifecycle_exit"}
        )
        account.rotation_dates.append("2026-08-19")
        return (
            _target(
                "old",
                0.0,
                reason_code="lifecycle_exit",
                mechanism=AttributionMechanism.LEADER_LIFECYCLE_EXIT.value,
            ),
        )

    monkeypatch.setattr(allocator, "_allocate_strategy", exiting_strategy)
    actual = allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.STRONG_TREND,
        risk=_risk(),
        user_panel={},
        leaders={},
        account=account,
        prices={"old": 100.0},
    )

    assert [(target.symbol, target.weight) for target in actual] == [("old", 0.0)]
    assert account.active_leaders == []
    assert account.lifecycle_events[-1]["event"] == "lifecycle_exit"
    assert account.rotation_dates == []
