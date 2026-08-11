from __future__ import annotations

import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.industry import compute_industry_signals
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, LeaderScore, Lifecycle


def _raw(ret20: float, ret60: float, ret120: float, above: float) -> dict[str, float]:
    return {
        "ret20": ret20,
        "ret60": ret60,
        "ret120": ret120,
        "above20": above,
        "above60": above,
        "accel": ret20 - ret60 / 3.0,
    }


def _leader(
    symbol: str,
    *,
    industry: str,
    score: float,
    industry_score: float,
    breadth: float,
) -> LeaderScore:
    return LeaderScore(
        symbol=symbol,
        score=score,
        confidence=1.0,
        mature=True,
        emerging=False,
        industry=industry,
        components={
            "industry_rotation_strength": industry_score,
            "industry_confidence": 1.0,
            "industry_breadth20": breadth,
            "industry_return60": 0.10,
        },
    )


def test_industry_signal_is_order_invariant_and_rewards_breadth() -> None:
    raw = {
        "a1": _raw(0.30, 0.60, 0.80, 1.0),
        "a2": _raw(0.20, 0.50, 0.70, 1.0),
        "b1": _raw(-0.10, -0.20, -0.30, 0.0),
        "b2": _raw(-0.05, -0.10, -0.20, 0.0),
    }
    industries = {"a1": "winner", "a2": "winner", "b1": "laggard", "b2": "laggard"}
    first = compute_industry_signals(
        raw=raw,
        reference_symbols=raw,
        industries=industries,
        minimum_members=2,
    )
    second = compute_industry_signals(
        raw=dict(reversed(list(raw.items()))),
        reference_symbols=reversed(tuple(raw)),
        industries=industries,
        minimum_members=2,
    )

    assert first == second
    assert first["winner"].score > first["laggard"].score
    assert first["winner"].breadth20 == pytest.approx(1.0)
    assert first["laggard"].breadth60 == pytest.approx(0.0)


def test_sparse_industry_strength_is_shrunk_toward_neutral() -> None:
    signals = compute_industry_signals(
        raw={
            "solo": _raw(0.50, 0.80, 1.00, 1.0),
            "b1": _raw(-0.10, -0.20, -0.30, 0.0),
            "b2": _raw(-0.05, -0.10, -0.20, 0.0),
        },
        reference_symbols=("solo", "b1", "b2"),
        industries={"solo": "sparse", "b1": "broad", "b2": "broad"},
        minimum_members=2,
    )

    assert signals["sparse"].confidence == pytest.approx(0.5)
    assert signals["sparse"].score < 1.0
    assert signals["sparse"].score > 0.5


def test_industry_handoff_needs_cross_group_edge_and_weak_incumbent() -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    incumbent = _leader(
        "old",
        industry="old_group",
        score=0.70,
        industry_score=0.40,
        breadth=0.30,
    )
    challenger = _leader(
        "new",
        industry="new_group",
        score=0.85,
        industry_score=0.75,
        breadth=1.0,
    )

    assert allocator._industry_handoff(
        challenger=challenger,
        incumbent=incumbent,
    )
    assert not allocator._industry_handoff(
        challenger=LeaderScore(
            symbol="peer",
            score=0.90,
            confidence=1.0,
            mature=True,
            emerging=False,
            industry="old_group",
            components=challenger.components,
        ),
        incumbent=incumbent,
    )


def test_low_confidence_unknowns_share_one_aggregate_cap() -> None:
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    leaders = {
        symbol: LeaderScore(
            symbol=symbol,
            score=0.8,
            confidence=0.8,
            mature=True,
            emerging=False,
            industry="unknown",
            components={"unknown_industry": 1.0},
        )
        for symbol in ("unknown-a", "unknown-b")
    }

    targets = allocator._targets(
        proposed={"unknown-a": 0.30, "unknown-b": 0.30},
        leaders=leaders,
        account=AccountState.empty(2_000_000.0),
        lifecycle=Lifecycle.CORE,
        reason="unknown confidence cap contract",
    )

    assert sum(target.weight for target in targets) == pytest.approx(
        DEFAULT_CONFIG.unknown_industry_weight_cap
    )
    assert all(target.weight <= DEFAULT_CONFIG.unknown_industry_weight_cap for target in targets)
