"""Corporate-action discontinuities belong to accounting, not signal risk."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.features import causal_close, compute_features, cross_section_returns
from uquant.market import MarketWorkspace, ReplayUniverse
from uquant.risk_sector import observe_deployed_sector
from uquant.risk_sentinel.evidence import _name_evidence
from uquant.risk_sentinel.history import _prepared_name_observations


def _linked_frame(count: int = 300) -> pd.DataFrame:
    index = pd.bdate_range("2022-01-03", periods=count)
    # 100 -> (100 - cash dividend 2) / (1 + bonus shares 1) = 49.
    scale = np.where(np.arange(count) >= 280, 100.0 / 49.0, 1.0)
    shares = np.where(np.arange(count) >= 280, 2.0, 1.0)
    frame = pd.DataFrame(index=index)
    for field, level in (("open", 100.0), ("high", 102.0), ("low", 98.0), ("close", 100.0)):
        frame[field] = level / scale
        frame[f"signal_{field}"] = level
    frame["volume"] = 1000.0 * shares
    frame["amount"] = frame["volume"] * frame["close"]
    frame["adjustment_scale"] = scale
    frame["share_scale"] = shares
    frame["reference_close"] = frame["close"]
    return frame


def test_split_and_cash_preserve_signal_and_executable_levels() -> None:
    raw = _linked_frame()
    features = compute_features(raw, DEFAULT_CONFIG)
    pd.testing.assert_frame_equal(features[raw.columns], raw)
    row = features.iloc[280]
    assert row["close"] == 49.0
    assert row["volume"] == 2000.0
    assert row["ret5"] == 0.0
    assert row["vol20"] == 0.0
    assert row["volume_expansion"] == 1.0
    assert row["atr"] == pytest.approx(4.0 * 49.0 / 100.0)
    assert row["ma20"] == 49.0
    assert row["hhv"] == pytest.approx(102.0 * 49.0 / 100.0)
    history = causal_close(features, raw.index[280])
    assert len(history) == 281
    assert history.iloc[-2] == history.iloc[-1] == 49.0
    assert history.pct_change(fill_method=None).iloc[-1] == 0.0


def test_appended_events_leave_prior_features_and_bounded_history_exact() -> None:
    prefix = _linked_frame()
    full = _linked_frame(320)
    full.loc[full.index[305]:, "adjustment_scale"] *= 3.0
    full.loc[full.index[305]:, "share_scale"] *= 3.0
    full.loc[full.index[305]:, ["open", "high", "low", "close"]] /= 3.0
    full.loc[full.index[305]:, "volume"] *= 3.0
    pd.testing.assert_frame_equal(
        compute_features(prefix, DEFAULT_CONFIG),
        compute_features(full, DEFAULT_CONFIG).loc[:prefix.index[-1]],
        check_exact=True,
    )
    pd.testing.assert_series_equal(
        causal_close(prefix, prefix.index[-1]),
        causal_close(full, prefix.index[-1]),
        check_exact=True,
    )


def test_identity_signal_path_matches_legacy_features_exactly() -> None:
    linked = _linked_frame(280)
    drift = np.exp(np.sin(np.arange(len(linked)) / 17.0) * 0.1)
    for field in ("open", "high", "low", "close"):
        linked[field] *= drift
        linked[f"signal_{field}"] *= drift
    legacy = linked[["open", "high", "low", "close", "volume", "amount"]]
    actual = compute_features(linked, DEFAULT_CONFIG)
    expected = compute_features(legacy, DEFAULT_CONFIG)
    pd.testing.assert_frame_equal(actual[expected.columns], expected, check_exact=True)
    pd.testing.assert_series_equal(causal_close(legacy, legacy.index[100]), legacy.loc[:legacy.index[100], "close"])


def test_risk_history_ignores_split_gap() -> None:
    frame = compute_features(_linked_frame(), DEFAULT_CONFIG)
    date = frame.index[280]
    returns = cross_section_returns({"000001": frame}, date)
    assert returns["000001"].iloc[-1] == 0.0
    observation = observe_deployed_sector(
        date=date, panel={"000001": frame}, symbols={"000001"},
        cfg=DEFAULT_CONFIG, minimum_symbols=1,
    )
    assert observation is not None
    assert observation.equal_return == 0.0
    evidence = _name_evidence(frame, date)
    assert evidence is not None
    assert evidence.fast_return == 0.0
    assert evidence.downside == 0.0
    prepared, cached_returns = _prepared_name_observations({"000001": frame})
    assert prepared["000001"][date] == evidence
    assert cached_returns["000001"].loc[date] == 0.0


def test_workspace_caches_signal_returns_but_quotes_real_price(tmp_path, monkeypatch) -> None:
    raw = _linked_frame()
    workspace = MarketWorkspace(tmp_path, DEFAULT_CONFIG)
    monkeypatch.setattr(workspace.data, "load", lambda symbol: raw)
    workspace.prepare(ReplayUniverse.from_symbols(
        tradable_symbols=("000001",), reference_symbols=("000001",), index_symbols=(),
    ))
    date = raw.index[280]
    assert workspace.price("sz000001", date) == 49.0
    assert workspace.reference_returns().loc[date, "sz000001"] == 0.0
