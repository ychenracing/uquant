from __future__ import annotations

import copy

import pandas as pd
import pytest

from unified_ai_quant.data import DataContractError, DataStore, normalize_symbol
from unified_ai_quant.engine import ProductionEngine
from unified_ai_quant.leader import REFERENCE_UNIVERSE, compute_leaders
from unified_ai_quant.types import AccountState


def test_data_contract_and_manifest(data_dir):
    store = DataStore(data_dir)
    frame = store.load("300308")
    assert frame.index.is_monotonic_increasing and frame.index.is_unique
    assert (frame[["open", "high", "low", "close"]] > 0).all().all()
    assert store.manifest(["300308", "000300"]).digest
    assert normalize_symbol("688008") == "sh688008"
    with pytest.raises(DataContractError):
        store.load("000001")


def test_fixed_reference_score_is_user_pool_invariant(data_dir):
    engine = ProductionEngine(data_dir)
    symbols = set(REFERENCE_UNIVERSE) | {"sh000682"}
    engine._load(symbols)
    date = pd.Timestamp("2026-06-30")
    panel = {symbol: engine._features[symbol].loc[:date] for symbol in REFERENCE_UNIVERSE}
    first = compute_leaders(
        panel,
        as_of=date,
        tech=engine._features["sh000682"].loc[:date],
        account=AccountState.empty(2e6),
        cfg=engine.cfg,
    )
    smaller = dict(panel)
    smaller["sz300308"] = panel["sz300308"]
    second = compute_leaders(
        smaller,
        as_of=date,
        tech=engine._features["sh000682"].loc[:date],
        account=AccountState.empty(2e6),
        cfg=engine.cfg,
    )
    assert first["sz300308"].score == second["sz300308"].score


def test_future_mutation_does_not_change_historical_features(data_dir):
    engine = ProductionEngine(data_dir)
    engine._load(["sz300308"])
    date = pd.Timestamp("2025-06-30")
    before = engine._features["sz300308"].loc[date].copy()
    mutated = engine._raw["sz300308"].copy()
    mutated.loc[mutated.index > date, "close"] *= 100.0
    from unified_ai_quant.features import compute_features

    after = compute_features(mutated, engine.cfg).loc[date]
    pd.testing.assert_series_equal(before, after)


def test_unknown_history_never_gets_high_confidence(data_dir):
    engine = ProductionEngine(data_dir)
    engine._load(set(REFERENCE_UNIVERSE) | {"sh000682"})
    date = pd.Timestamp("2026-06-30")
    panel = {symbol: engine._features[symbol].loc[:date] for symbol in REFERENCE_UNIVERSE}
    short = copy.deepcopy(panel["sz300308"].tail(30))
    panel["sz999999"] = short
    scores = compute_leaders(
        panel,
        as_of=date,
        tech=engine._features["sh000682"].loc[:date],
        account=AccountState.empty(2e6),
        cfg=engine.cfg,
    )
    assert scores["sz999999"].confidence < engine.cfg.leader_min_confidence
    assert not scores["sz999999"].mature
