from __future__ import annotations

import pandas as pd

from uquant.risk_sentinel.models import SentinelLevel
from uquant.risk_sentinel.service import evaluate_sentinel


def _frame(*, periods: int = 30, shock: float = 0.0) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-08-19", periods=periods)
    close = [100.0 + value * 0.1 for value in range(periods)]
    if shock:
        for offset in range(min(5, periods)):
            close[-(offset + 1)] *= 1.0 + shock * (5 - offset) / 5
    return pd.DataFrame({"close": close}, index=index)


def test_service_returns_not_ready_without_safe_cap_when_indices_lack_warmup() -> None:
    assessment = evaluate_sentinel(
        as_of="2026-08-19",
        broad_frame=_frame(periods=5),
        tech_frame=_frame(periods=5),
        reference_panel={"a": _frame()},
        point_in_time_industries={"a": "optical"},
        held_symbols=(),
    )

    assert assessment.level is SentinelLevel.NOT_READY
    assert assessment.suggested_gross_cap is None
    assert assessment.freeze_new_risk is True


def test_service_emits_observation_only_opinion_for_synchronized_damage() -> None:
    assessment = evaluate_sentinel(
        as_of="2026-08-19",
        broad_frame=_frame(shock=-0.05),
        tech_frame=_frame(shock=-0.06),
        reference_panel={
            "a": _frame(shock=-0.08),
            "b": _frame(shock=-0.07),
            "c": _frame(shock=-0.09),
            "d": _frame(shock=-0.08),
        },
        point_in_time_industries={
            "a": "optical",
            "b": "optical",
            "c": "storage",
            "d": "storage",
        },
        held_symbols=("a", "c"),
    )

    assert assessment.level in {
        SentinelLevel.DEFENSIVE,
        SentinelLevel.CRITICAL,
    }
    assert assessment.freeze_new_risk is True
    assert assessment.suggested_gross_cap is not None
    assert "breadth_structure" in assessment.evidence_families
    assert "market_velocity" in assessment.evidence_families


def test_service_is_deterministic_and_does_not_mutate_inputs() -> None:
    broad = _frame(shock=-0.03)
    tech = _frame(shock=-0.04)
    panel = {"a": _frame(shock=-0.02), "b": _frame()}
    snapshots = {
        "broad": broad.copy(deep=True),
        "tech": tech.copy(deep=True),
        **{symbol: frame.copy(deep=True) for symbol, frame in panel.items()},
    }
    arguments = {
        "as_of": "2026-08-19",
        "broad_frame": broad,
        "tech_frame": tech,
        "reference_panel": panel,
        "point_in_time_industries": {"a": "optical", "b": "storage"},
        "held_symbols": ("a",),
    }

    first = evaluate_sentinel(**arguments)
    second = evaluate_sentinel(**arguments)

    assert first.to_dict() == second.to_dict()
    pd.testing.assert_frame_equal(broad, snapshots["broad"])
    pd.testing.assert_frame_equal(tech, snapshots["tech"])
    pd.testing.assert_frame_equal(panel["a"], snapshots["a"])
    pd.testing.assert_frame_equal(panel["b"], snapshots["b"])


def test_service_includes_read_only_leader_and_capital_context() -> None:
    assessment = evaluate_sentinel(
        as_of="2026-08-19",
        broad_frame=_frame(),
        tech_frame=_frame(),
        reference_panel={"a": _frame(shock=-0.08), "b": _frame()},
        point_in_time_industries={"a": "optical", "b": "storage"},
        held_symbols=("a",),
        leader_symbols=("a",),
        capital_drawdown=0.09,
    )

    assert assessment.metrics["capital_drawdown"] == 0.09
    assert "capital_damage" in assessment.evidence_families
