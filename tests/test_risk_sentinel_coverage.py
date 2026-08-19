from __future__ import annotations

import pandas as pd

from uquant.risk_sentinel.coverage import assess_coverage
from uquant.risk_sentinel.models import WarmupStatus


def _frame(end: str, periods: int = 25) -> pd.DataFrame:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=periods)
    return pd.DataFrame({"close": range(100, 100 + periods)}, index=index)


def test_coverage_uses_exact_weighted_definition() -> None:
    frames = {
        "a": _frame("2026-08-19"),
        "b": _frame("2026-08-18"),
        "c": _frame("2026-08-19"),
        "d": _frame("2026-08-18"),
    }
    health = assess_coverage(
        as_of="2026-08-19",
        broad_frame=_frame("2026-08-19"),
        tech_frame=_frame("2026-08-19"),
        expected_symbols=("a", "b", "c", "d"),
        reference_panel=frames,
        point_in_time_industries={
            "a": "optical",
            "b": "optical",
            "c": "storage",
            "d": "semicap",
        },
        held_symbols=("a", "missing"),
    )

    assert health.component_observation == 0.5
    assert health.subindustry_coverage == 2 / 3
    assert health.held_industry_mapping == 0.5
    assert health.confidence == 0.45 * 0.5 + 0.35 * (2 / 3) + 0.20 * 0.5
    assert health.status is WarmupStatus.NOT_READY
    assert health.stale_symbols == ("b", "d")


def test_missing_or_stale_index_is_never_ready() -> None:
    reference = {"a": _frame("2026-08-19")}

    missing = assess_coverage(
        as_of="2026-08-19",
        broad_frame=pd.DataFrame(columns=["close"]),
        tech_frame=_frame("2026-08-19"),
        expected_symbols=("a",),
        reference_panel=reference,
        point_in_time_industries={"a": "optical"},
        held_symbols=(),
    )
    stale = assess_coverage(
        as_of="2026-08-19",
        broad_frame=_frame("2026-08-18"),
        tech_frame=_frame("2026-08-19"),
        expected_symbols=("a",),
        reference_panel=reference,
        point_in_time_industries={"a": "optical"},
        held_symbols=(),
    )

    assert missing.status is WarmupStatus.NOT_READY
    assert stale.status is WarmupStatus.NOT_READY
    assert missing.missing_indices == ("sh000300",)
    assert stale.missing_indices == ("sh000300",)


def test_new_reference_member_degrades_warmup_without_opening_failure() -> None:
    health = assess_coverage(
        as_of="2026-08-19",
        broad_frame=_frame("2026-08-19"),
        tech_frame=_frame("2026-08-19"),
        expected_symbols=("new", "ready"),
        reference_panel={
            "new": _frame("2026-08-19", periods=5),
            "ready": _frame("2026-08-19"),
        },
        point_in_time_industries={"new": "optical", "ready": "storage"},
        held_symbols=("ready",),
    )

    assert health.status is WarmupStatus.DEGRADED
    assert health.reference_warmup == 0.5
    assert health.new_symbols == ("new",)


def test_empty_holdings_are_fully_mapped_but_do_not_hide_missing_components() -> None:
    health = assess_coverage(
        as_of="2026-08-19",
        broad_frame=_frame("2026-08-19"),
        tech_frame=_frame("2026-08-19"),
        expected_symbols=("missing",),
        reference_panel={},
        point_in_time_industries={"missing": "optical"},
        held_symbols=(),
    )

    assert health.held_industry_mapping == 1.0
    assert health.component_observation == 0.0
    assert health.status is WarmupStatus.NOT_READY
