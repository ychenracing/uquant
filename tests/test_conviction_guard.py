from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, LeaderScore, Opportunity, Risk, RiskAssessment


def _frame(dates: pd.DatetimeIndex, phase: float) -> pd.DataFrame:
    steps = np.arange(len(dates), dtype=float)
    returns = 0.003 + 0.008 * np.sin(steps * (0.31 + phase) + phase)
    close = np.cumprod(1.0 + returns)
    return pd.DataFrame(
        {
            "close": close,
            "ma20": close * 0.96,
            "ma60": close * 0.90,
            "ret20": 0.12,
            "ret60": 0.25,
            "vol20": 0.02,
            "amount": 1_000_000_000.0,
        },
        index=dates,
    )


def _leader(symbol: str, score: float, industry: str) -> LeaderScore:
    return LeaderScore(
        symbol=symbol,
        score=score,
        confidence=0.95,
        mature=True,
        emerging=False,
        industry=industry,
        components={
            "industry_breadth": 0.85,
            "resilience": 0.80,
            "relative_strength": 0.82,
            "liquidity": 0.95,
            "unknown_industry": 0.0,
        },
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        Risk.NORMAL,
        1.0,
        0,
        {"trend_health": 0.85},
        (),
        "NONE",
    )


def test_choppy_entry_stays_equal_weight_despite_score_dispersion() -> None:
    dates = pd.bdate_range("2025-01-02", periods=100)
    symbols = ("one", "two", "three")
    panel = {symbol: _frame(dates, phase) for symbol, phase in zip(symbols, (0.0, 0.27, 0.63), strict=True)}
    leaders = {
        symbol: _leader(symbol, score, industry)
        for symbol, score, industry in zip(
            symbols,
            (0.95, 0.89, 0.85),
            ("optical", "compute", "equipment"),
            strict=True,
        )
    }
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG)._leader_targets(
        date=dates[-1],
        opportunity=Opportunity.CHOPPY,
        risk=_risk(),
        user_panel=panel,
        leaders=leaders,
        account=account,
        weights_now={},
        prices={symbol: float(panel[symbol].iloc[-1]["close"]) for symbol in symbols},
    )

    assert targets is not None
    live = [target.weight for target in targets if target.weight > 0]
    assert len(live) == 2
    assert live[0] == pytest.approx(live[1])
    assert account.candidate_tenure["conviction_evidence_qualified"] == 0


def test_strong_independent_joint_evidence_can_concentrate_entry() -> None:
    dates = pd.bdate_range("2025-01-02", periods=100)
    symbols = ("one", "two", "three")
    panel = {symbol: _frame(dates, phase) for symbol, phase in zip(symbols, (0.0, 0.27, 0.63), strict=True)}
    leaders = {
        symbol: _leader(symbol, score, industry)
        for symbol, score, industry in zip(
            symbols,
            (0.95, 0.89, 0.85),
            ("optical", "compute", "equipment"),
            strict=True,
        )
    }
    account = AccountState.empty(100.0)

    targets = PortfolioAllocator(DEFAULT_CONFIG)._leader_targets(
        date=dates[-1],
        opportunity=Opportunity.STRONG_TREND,
        risk=_risk(),
        user_panel=panel,
        leaders=leaders,
        account=account,
        weights_now={},
        prices={symbol: float(panel[symbol].iloc[-1]["close"]) for symbol in symbols},
    )

    assert targets is not None
    weights = {target.symbol: target.weight for target in targets if target.weight > 0}
    assert account.candidate_tenure["confidence_sized_entry"] == 1
    assert account.candidate_tenure["conviction_evidence_qualified"] == 1
    assert weights["one"] > weights["two"] > weights["three"]


def test_missing_quality_factor_blocks_concentration_even_in_strong_trend() -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    dates = pd.bdate_range("2025-01-02", periods=100)
    symbols = ["one", "two"]
    panel = {symbol: _frame(dates, phase) for symbol, phase in zip(symbols, (0.0, 0.63), strict=True)}
    leaders = {
        "one": _leader("one", 0.95, "optical"),
        "two": _leader("two", 0.85, "compute"),
    }
    leaders["two"].components["resilience"] = 0.20

    qualified = allocator._conviction_evidence_qualified(
        symbols=symbols,
        leaders=leaders,
        user_panel=panel,
        date=dates[-1],
        high_confidence=True,
    )
    shares = allocator._conviction_shares(
        symbols,
        leaders,
        evidence_qualified=qualified,
    )

    assert not qualified
    assert shares.tolist() == pytest.approx([0.5, 0.5])
