from __future__ import annotations

import inspect
from datetime import date

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.risk_sentinel import history as history_module
from uquant.risk_sentinel.models import (
    CoverageHealth,
    SentinelAssessment,
    SentinelLevel,
    WarmupStatus,
)
from uquant.validation.universe import AIUniverse, UniverseMember


def _frame(dates: pd.DatetimeIndex, *, falling: bool = False) -> pd.DataFrame:
    close = [100.0 - index if falling else 100.0 + index for index in range(len(dates))]
    frame = pd.DataFrame({"close": close}, index=dates)
    frame["ret5"] = frame["close"].pct_change(5, fill_method=None)
    frame["ret10"] = frame["close"].pct_change(10, fill_method=None)
    frame["ma20"] = frame["close"].rolling(20).mean()
    frame["ma60"] = frame["close"].rolling(20).mean()
    return frame


def _member(
    symbol: str,
    industry: str,
    start: date,
    end: date | None = None,
) -> UniverseMember:
    return UniverseMember(
        symbol=symbol,
        ai_domain="fixture",
        industry=industry,
        effective_from=start,
        effective_to=end,
        tradable=True,
        evidence="fixture",
        reviewed_at=start,
    )


def _universe(dates: pd.DatetimeIndex) -> AIUniverse:
    change = dates[23].date()
    return AIUniverse(
        members=(
            _member("a", "optical", dates[0].date(), change),
            _member("a", "storage", change),
            _member("b", "compute", dates[0].date()),
            _member("c", "foundry", dates[0].date()),
            _member("d", "pcb", dates[0].date()),
            _member("new", "semicap", dates[24].date()),
        ),
        sha256="f" * 64,
    )


def _assessment(as_of: str, *, active: bool) -> SentinelAssessment:
    coverage = CoverageHealth(
        status=WarmupStatus.READY,
        confidence=1.0,
        component_observation=1.0,
        subindustry_coverage=1.0,
        held_industry_mapping=1.0,
        reference_warmup=1.0,
        missing_indices=(),
        new_symbols=(),
        stale_symbols=(),
    )
    return SentinelAssessment(
        date=as_of,
        level=SentinelLevel.DEFENSIVE if active else SentinelLevel.NORMAL,
        confidence=1.0,
        suggested_gross_cap=0.5 if active else None,
        freeze_new_risk=active,
        evidence_families=("market_velocity",) if active else (),
        reasons=("velocity",) if active else ("normal",),
        first_evidence_date=as_of if active else None,
        coverage=coverage,
        metrics={
            "broad_fast_return": -0.06 if active else 0.0,
            "tech_fast_return": -0.06 if active else 0.0,
            "synchronized_subindustry_damage": 0.5 if active else 0.0,
        },
    )


def test_timeline_truncates_future_rows_and_resolves_pit_membership_and_industry(
    monkeypatch,
) -> None:
    dates = pd.bdate_range("2026-01-05", periods=28)
    broad = _frame(dates)
    tech = _frame(dates)
    panel = {symbol: _frame(dates) for symbol in ("a", "b", "c", "d", "new")}
    universe = _universe(dates)
    observed: list[tuple[str, tuple[str, ...], str, pd.Timestamp]] = []

    def fake_evaluate(**kwargs):
        as_of = str(kwargs["as_of"])
        reference_panel = kwargs["reference_panel"]
        industries = kwargs["point_in_time_industries"]
        observed.append(
            (
                as_of,
                tuple(reference_panel),
                industries.get("a", "unknown"),
                max(frame.index.max() for frame in reference_panel.values()),
            )
        )
        return _assessment(as_of, active=as_of >= str(dates[25].date()))

    monkeypatch.setattr(history_module, "evaluate_sentinel", fake_evaluate)
    as_of = str(dates[26].date())
    timeline = history_module.build_risk_evidence_timeline(
        as_of=as_of,
        broad_frame=pd.concat([broad, _frame(pd.DatetimeIndex([dates[-1] + pd.Timedelta(days=7)]))]),
        tech_frame=tech,
        reference_panel=dict(reversed(tuple(panel.items()))),
        reference_returns=None,
        universe=universe,
        cfg=DEFAULT_CONFIG,
    )

    assert all(last <= pd.Timestamp(row_date) for row_date, _, _, last in observed)
    before_change = next(item for item in observed if item[0] == str(dates[22].date()))
    after_change = next(item for item in observed if item[0] == str(dates[23].date()))
    before_listing = next(item for item in observed if item[0] == str(dates[23].date()))
    after_listing = next(item for item in observed if item[0] == str(dates[24].date()))
    assert before_change[2] == "optical"
    assert after_change[2] == "storage"
    assert "new" not in before_listing[1]
    assert "new" in after_listing[1]
    assert timeline.as_of == as_of
    assert timeline.sentinel_first_family_dates == (("market_velocity", str(dates[25].date())),)
    assert timeline.incremental_families == ("market_velocity",)


def test_timeline_is_deterministic_and_future_crash_cannot_change_history(monkeypatch) -> None:
    dates = pd.bdate_range("2026-01-05", periods=27)
    universe = _universe(dates)
    broad = _frame(dates)
    tech = _frame(dates)
    panel = {symbol: _frame(dates) for symbol in ("a", "b", "c", "d", "new")}

    monkeypatch.setattr(
        history_module,
        "evaluate_sentinel",
        lambda **kwargs: _assessment(str(kwargs["as_of"]), active=False),
    )
    kwargs = {
        "as_of": str(dates[-2].date()),
        "broad_frame": broad,
        "tech_frame": tech,
        "reference_panel": panel,
        "reference_returns": None,
        "universe": universe,
        "cfg": DEFAULT_CONFIG,
    }
    original = history_module.build_risk_evidence_timeline(**kwargs)
    crashed = tech.copy()
    crashed.loc[dates[-1], "close"] = 1.0
    with_future_crash = history_module.build_risk_evidence_timeline(
        **{**kwargs, "tech_frame": crashed}
    )
    reordered = history_module.build_risk_evidence_timeline(
        **{**kwargs, "reference_panel": dict(reversed(tuple(panel.items())))}
    )

    assert original == with_future_crash == reordered


def test_timeline_public_boundary_has_no_account_or_current_book_input() -> None:
    parameters = set(inspect.signature(history_module.build_risk_evidence_timeline).parameters)

    assert parameters.isdisjoint(
        {"account", "account_state", "held_symbols", "capital_drawdown", "leader_tenure"}
    )
