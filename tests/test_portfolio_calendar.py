from __future__ import annotations

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio_leaders import LeaderPortfolioPolicy
from uquant.types import AccountState, LeaderScore, Opportunity, Risk, RiskAssessment


def _leader(symbol: str, score: float) -> LeaderScore:
    return LeaderScore(
        symbol=symbol,
        score=score,
        confidence=0.95,
        mature=True,
        emerging=False,
        industry=symbol,
        components={},
    )


def _normal_risk() -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={},
        reasons=(),
        shock_state="NONE",
    )


def _panels() -> tuple[pd.DatetimeIndex, tuple[dict[str, pd.DataFrame], ...]]:
    dates = pd.bdate_range("2026-01-02", periods=30)
    full = pd.DataFrame({"close": 1.0}, index=dates)
    late = full.iloc[-1:]
    return dates, (
        {"late": late, "full": full},
        {"full": full, "late": late},
    )


def test_dynamic_k_elapsed_sessions_are_independent_of_first_user_symbol() -> None:
    dates, panels = _panels()
    policy = LeaderPortfolioPolicy(DEFAULT_CONFIG)
    candidates = [_leader("late", 0.90), _leader("full", 0.80)]

    observed: list[int] = []
    for panel in panels:
        account = AccountState.empty(100.0)
        account.dynamic_k = 1
        account.last_k_change_date = str(dates[0].date())
        account.candidate_tenure["dynamic_k_target"] = 2
        account.candidate_tenure["dynamic_k_target_streak"] = 2
        observed.append(
            policy._dynamic_k(
                date=dates[-1],
                opportunity=Opportunity.TREND,
                risk=_normal_risk(),
                candidates=candidates,
                user_panel=panel,
                account=account,
            )
        )

    assert observed == [2, 2]


def test_rotation_budget_uses_union_clock_and_discards_old_events() -> None:
    dates, panels = _panels()
    policy = LeaderPortfolioPolicy(DEFAULT_CONFIG)
    old = str(dates[-23].date())
    recent = str(dates[-5].date())

    outcomes: list[tuple[bool, list[str]]] = []
    for panel in panels:
        account = AccountState.empty(100.0)
        account.rotation_dates = [old, recent]
        allowed = policy._rotation_allowed(account, dates[-1], panel)
        outcomes.append((allowed, account.rotation_dates))

    assert outcomes == [(True, [recent]), (True, [recent])]
