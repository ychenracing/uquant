from __future__ import annotations

import copy
import hashlib
import json

import pandas as pd
import pytest

from uquant.data import DataContractError, DataStore, normalize_symbol
from uquant.engine import ProductionEngine
from uquant.leader import (
    REFERENCE_UNIVERSE,
    RESEARCH_REFERENCE_UNIVERSE,
    STABLE_REFERENCE_UNIVERSE,
    apply_leader_tenure,
    apply_opportunity_alpha,
    compute_leaders,
    compute_structural_leaders,
    stable_reference_requires_history,
)
from uquant.reference_registry import (
    ReferenceMembership,
    load_reference_registry,
    resolve_reference_symbols,
)
from uquant.types import AccountState, LeaderScore, Opportunity


def test_data_contract_and_manifest(data_dir):
    store = DataStore(data_dir)
    frame = store.load("300308")
    assert frame.index.is_monotonic_increasing and frame.index.is_unique
    assert (frame[["open", "high", "low", "close"]] > 0).all().all()
    assert store.manifest(["300308", "000300"]).digest
    assert normalize_symbol("688008") == "sh688008"
    with pytest.raises(DataContractError):
        store.load("000001")


def test_bounded_manifest_accepts_append_but_detects_prefix_rewrite(tmp_path):
    path = tmp_path / "sh600000.csv"
    original = (
        "date,open,high,low,close,volume,amount\n"
        "2025-01-02,10,11,9,10.5,1000,10500\n"
        "2025-01-03,10.5,12,10,11.5,1200,13800\n"
    )
    path.write_text(original, encoding="utf-8")
    before = DataStore(tmp_path).manifest(["sh600000"], as_of="2025-01-03").digest

    path.write_text(
        original + "2025-01-06,11.5,13,11,12.5,1500,18750\n",
        encoding="utf-8",
    )
    appended = DataStore(tmp_path).manifest(["sh600000"], as_of="2025-01-03").digest
    assert appended == before

    path.write_text(
        original.replace(
            "2025-01-03,10.5,12,10,11.5,1200,13800",
            "2025-01-03,10.5,12,10,11.4,1200,13680",
        ),
        encoding="utf-8",
    )
    rewritten = DataStore(tmp_path).manifest(["sh600000"], as_of="2025-01-03").digest
    assert rewritten != before


def test_account_data_provenance_advances_by_verified_prefix(data_dir):
    symbols = ["sz300308", "sz300502", "sz300394"]
    account = AccountState.empty(2e6)
    first = ProductionEngine(data_dir).decide(
        symbols=symbols,
        as_of="2026-06-29",
        account=account,
    )
    account.pending_orders = list(first.pending_orders)
    previous_hash = account.data_hash

    ProductionEngine(data_dir).decide(
        symbols=symbols,
        as_of="2026-06-30",
        account=account,
    )

    assert account.data_hash_as_of == "2026-06-30"
    assert account.data_hash != previous_hash
    assert account.data_hash_symbols


def test_historical_manifest_and_checksums_are_reproducible(data_dir):
    manifest = json.loads((data_dir / "DATA_MANIFEST.json").read_text(encoding="utf-8"))
    results = {item["symbol"]: item for item in manifest["results"]}
    assert results["sh000300"]["first_date"] == "2014-01-02"
    assert results["sh000682"]["first_date"] == "2014-01-02"
    assert results["sh000682"]["pre_inception_proxy"].startswith("sz399006")
    for line in (data_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        expected, filename = line.split(maxsplit=1)
        path = data_dir / filename.lstrip(" *")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


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
    components = first["sz300308"].components
    assert 0.0 <= components["secular_stability"] <= 1.0
    assert 0.0 <= components["secular_resilience"] <= 1.0
    assert 0.0 <= components["secular_slope60"] <= 1.0
    assert 0.0 <= components["secular_slope120"] <= 1.0


def test_research_reference_staging_cannot_change_production_reference() -> None:
    assert REFERENCE_UNIVERSE is STABLE_REFERENCE_UNIVERSE
    assert RESEARCH_REFERENCE_UNIVERSE[: len(REFERENCE_UNIVERSE)] == REFERENCE_UNIVERSE


def test_history_backfill_requires_every_pre_snapshot_stable_reference() -> None:
    assert stable_reference_requires_history("sh600487", "2022-01-04")
    assert not stable_reference_requires_history("sh688361", "2023-05-19")
    assert not stable_reference_requires_history("unreviewed_symbol", "2022-01-04")


def test_reference_registry_matches_reviewed_production_universe() -> None:
    registry = load_reference_registry()

    assert resolve_reference_symbols("2026-08-11", registry=registry) == tuple(
        sorted(REFERENCE_UNIVERSE)
    )


def test_reference_registry_respects_effective_date_boundaries() -> None:
    registry = (
        ReferenceMembership(
            symbol="member",
            effective_from=pd.Timestamp("2020-01-02"),
            effective_to=pd.Timestamp("2021-01-02"),
            source="reviewed",
            review_status="approved",
        ),
    )

    assert resolve_reference_symbols("2020-01-01", registry=registry) == ()
    assert resolve_reference_symbols("2020-01-02", registry=registry) == ("member",)
    assert resolve_reference_symbols("2021-01-02", registry=registry) == ()


def test_reference_registry_rejects_overlapping_membership(tmp_path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "memberships": [
                    {
                        "symbol": "member",
                        "effective_from": "2020-01-01",
                        "effective_to": None,
                        "source": "reviewed",
                        "review_status": "approved",
                    },
                    {
                        "symbol": "member",
                        "effective_from": "2021-01-01",
                        "effective_to": None,
                        "source": "reviewed",
                        "review_status": "approved",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="overlapping"):
        load_reference_registry(path)


def test_future_mutation_does_not_change_historical_features(data_dir):
    engine = ProductionEngine(data_dir)
    engine._load(["sz300308"])
    date = pd.Timestamp("2025-06-30")
    before = engine._features["sz300308"].loc[date].copy()
    mutated = engine._raw["sz300308"].copy()
    mutated.loc[mutated.index > date, "close"] *= 100.0
    from uquant.features import compute_features

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
    assert scores["sz999999"].industry == "unknown"
    assert scores["sz999999"].components["unknown_industry"] == pytest.approx(1.0)


def test_same_day_opportunity_selects_current_alpha_profile(data_dir) -> None:
    engine = ProductionEngine(data_dir)
    engine._load(set(REFERENCE_UNIVERSE) | {"sh000682"})
    date = pd.Timestamp("2026-06-30")
    panel = {symbol: engine._features[symbol] for symbol in REFERENCE_UNIVERSE}
    structural = compute_structural_leaders(
        panel,
        as_of=date,
        tech=engine._features["sh000682"],
        cfg=engine.cfg,
    )

    trend = apply_opportunity_alpha(structural, opportunity=Opportunity.TREND, cfg=engine.cfg)
    choppy = apply_opportunity_alpha(structural, opportunity=Opportunity.CHOPPY, cfg=engine.cfg)

    assert all(item.components["factor_profile"] == 2.0 for item in trend.values())
    assert all(item.components["factor_profile"] == 0.0 for item in choppy.values())
    assert any(trend[symbol].score != choppy[symbol].score for symbol in trend)


def test_structural_leader_cache_isolated_by_scoring_config(data_dir) -> None:
    engine = ProductionEngine(data_dir)
    engine._load(set(REFERENCE_UNIVERSE) | {"sh000682"})
    date = pd.Timestamp("2025-04-01")
    panel = {symbol: engine._features[symbol] for symbol in REFERENCE_UNIVERSE}
    tech = engine._features["sh000682"]
    cache: dict[tuple[object, ...], dict[str, LeaderScore]] = {}

    compute_structural_leaders(
        panel,
        as_of=date,
        tech=tech,
        cfg=engine.cfg.override(hierarchical_industry_shrinkage_enabled=False),
        score_cache=cache,
    )
    actual = compute_structural_leaders(
        panel,
        as_of=date,
        tech=tech,
        cfg=engine.cfg,
        score_cache=cache,
    )
    expected = compute_structural_leaders(
        panel,
        as_of=date,
        tech=tech,
        cfg=engine.cfg,
    )

    assert actual == expected


def test_leader_tenure_mutates_once_after_same_day_alpha(data_dir) -> None:
    engine = ProductionEngine(data_dir)
    engine._load(set(REFERENCE_UNIVERSE) | {"sh000682"})
    date = pd.Timestamp("2026-06-30")
    panel = {symbol: engine._features[symbol] for symbol in REFERENCE_UNIVERSE}
    account = AccountState.empty(2e6)
    structural = compute_structural_leaders(
        panel,
        as_of=date,
        tech=engine._features["sh000682"],
        cfg=engine.cfg,
    )
    alpha = apply_opportunity_alpha(structural, opportunity=Opportunity.RECOVERY, cfg=engine.cfg)

    assert account.leader_tenure == {}
    apply_leader_tenure(alpha, account=account, cfg=engine.cfg)

    assert max(account.leader_tenure.values(), default=0) <= 1
