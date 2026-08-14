from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from uquant.account import load_account, migrate_account, save_account
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import (
    ProductionEngine,
    _decision_config_for_universe,
    code_fingerprint,
)
from uquant.leader import REFERENCE_UNIVERSE
from uquant.report import render_daily_report
from uquant.types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Fill,
    OriginSubsystem,
    PendingOrder,
    Position,
    ReductionPolicy,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

SYMBOLS = ["sz300308", "sz300502", "sz300394", "sh688008", "sh603986"]
RISK_REGRESSION_POOLS = (
    tuple(SYMBOLS[:3]),
    tuple(SYMBOLS),
    tuple(REFERENCE_UNIVERSE),
)
POOL_D = (
    "sz300308",
    "sz300502",
    "sz300394",
    "sh688498",
    "sh601869",
    "sh688256",
    "sh688008",
    "sh603986",
    "sh688072",
    "sh688082",
    "sh688120",
    "sh688300",
    "sz300054",
    "sh688361",
    "sz300604",
)


def _identity(
    *,
    signal_date: str = "2026-01-05",
    symbol: str = "sz300308",
    target_weight: float = 0.5,
    lifecycle: str = "CORE",
    reduction_policy: str = ReductionPolicy.FIFO.value,
    reason_code: str = "strategy_target",
    exit_kind: str = "strategy",
) -> dict[str, str | None]:
    fields: dict[str, str | None] = {
        "origin_subsystem": OriginSubsystem.LEADER.value,
        "mechanism": AttributionMechanism.LEADER_SELECTION.value,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }
    fields["event_id"] = derive_attribution_event_id(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_lifecycle=lifecycle,
        origin_subsystem=OriginSubsystem.LEADER.value,
        mechanism=AttributionMechanism.LEADER_SELECTION.value,
        replaces_symbol=None,
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy=reduction_policy,
        reason_code=reason_code,
        exit_kind=exit_kind,
    )
    return fields


def _refresh_payload_event_id(order: dict[str, object]) -> None:
    order["event_id"] = derive_attribution_event_id(
        signal_date=str(order["signal_date"]),
        symbol=str(order["symbol"]),
        target_weight=float(order["target_weight"]),
        lifecycle=str(order["lifecycle"]),
        origin_lifecycle=str(order["origin_lifecycle"]),
        origin_subsystem=str(order["origin_subsystem"]),
        mechanism=str(order["mechanism"]),
        replaces_symbol=(
            str(order["replaces_symbol"])
            if order["replaces_symbol"] is not None
            else None
        ),
        industry_at_entry=str(order["industry_at_entry"]),
        industry_manifest_sha256=str(order["industry_manifest_sha256"]),
        reduction_policy=str(order["reduction_policy"]),
        reason_code=str(order["reason_code"]),
        exit_kind=str(order["exit_kind"]),
    )


def test_decision_config_is_invariant_to_unrelated_universe_size() -> None:
    assert not DEFAULT_CONFIG.same_day_leader_pipeline_enabled
    assert not DEFAULT_CONFIG.group_balanced_reference_enabled
    assert not DEFAULT_CONFIG.hierarchical_industry_shrinkage_enabled
    assert not DEFAULT_CONFIG.evidence_family_voting_enabled
    assert _decision_config_for_universe(3) is DEFAULT_CONFIG
    assert _decision_config_for_universe(9) is DEFAULT_CONFIG
    assert _decision_config_for_universe(10) is DEFAULT_CONFIG
    assert _decision_config_for_universe(32) is DEFAULT_CONFIG
    explicit = DEFAULT_CONFIG.override(adaptive_broad_universe_compatibility_enabled=False)
    assert _decision_config_for_universe(3, explicit) is explicit
    assert _decision_config_for_universe(32, explicit) is explicit


def test_determinism_one_target_and_hard_constraints(data_dir):
    engine = ProductionEngine(data_dir)
    initial = AccountState.empty(2e6)
    first, state1 = engine.deterministic_decision(symbols=SYMBOLS, as_of="2026-06-30", account=initial)
    second, state2 = engine.deterministic_decision(
        symbols=list(reversed(SYMBOLS)), as_of="2026-06-30", account=initial
    )
    assert first.decision_digest == second.decision_digest
    first_payload = first.canonical_payload(
        effective_config_sha256=config_fingerprint(engine.cfg)
    )
    second_payload = second.canonical_payload(
        effective_config_sha256=config_fingerprint(engine.cfg)
    )
    assert first_payload == second_payload
    assert first_payload["effective_config_sha256"] == config_fingerprint(engine.cfg)
    assert state1.to_dict() == state2.to_dict()
    assert len({item.symbol for item in first.targets}) == len(first.targets)
    positive = [item for item in first.targets if item.weight > 0]
    assert len(positive) <= 6
    assert sum(item.weight for item in positive) <= 1.0 + 1e-9
    assert max((item.weight for item in positive), default=0.0) <= 0.60


def test_backtest_reports_the_exact_effective_config_hash(data_dir) -> None:
    engine = ProductionEngine(data_dir)
    result = engine.backtest(
        symbols=SYMBOLS,
        start="2026-06-25",
        end="2026-06-30",
    )

    assert result["effective_config_sha256"] == config_fingerprint(engine.cfg)


def test_shared_engine_leader_cache_isolated_by_adaptive_config(data_dir):
    """A prior small-pool replay must not seed broad-pool structural scores."""
    as_of = "2026-06-30"
    pristine = ProductionEngine(data_dir)
    expected, expected_state = pristine.deterministic_decision(
        symbols=POOL_D,
        as_of=as_of,
        account=AccountState.empty(2e6),
    )

    shared = ProductionEngine(data_dir)
    shared.deterministic_decision(
        symbols=SYMBOLS,
        as_of=as_of,
        account=AccountState.empty(2e6),
    )
    actual, actual_state = shared.deterministic_decision(
        symbols=POOL_D,
        as_of=as_of,
        account=AccountState.empty(2e6),
    )

    assert actual.decision_digest == expected.decision_digest
    assert actual_state.to_dict() == expected_state.to_dict()


def test_state_round_trip_and_fail_closed_hashes(data_dir, tmp_path):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    decision = engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    state.pending_orders = list(decision.pending_orders)
    state.strategic_cohort_symbols = ["sz300308", "sz300394", "sz300502"]
    state.strategic_cohort_targets = {"sz300308": 0.30}
    state.strategic_exit_bands = {"sz300308": [0.10, 0.08, 0.06]}
    state.strategic_active_bands = {"sz300308": [True, False, False]}
    state.strategic_restore_weights = {"sz300308": 0.30}
    path = tmp_path / "account.json"
    save_account(state, path)
    assert load_account(path).to_dict() == state.to_dict()
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_account(corrupt)
    missing_hash = tmp_path / "missing.json"
    payload = copy.deepcopy(state.to_dict())
    payload["data_hash"] = ""
    missing_hash.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_account(missing_hash)


def test_order_state_migrates_sequence_and_rejects_broken_references(tmp_path):
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "code"
    state.order_ledger = [
        AccountOrder(
            order_id="O000000007",
            signal_date="2026-01-05",
            submitted_date="2026-01-05",
            symbol="sz300308",
            side="BUY",
            target_weight=0.5,
            reason="entry",
            lifecycle="CORE",
            **_identity(),
        )
    ]
    payload = state.to_dict()
    payload.pop("next_order_sequence")
    migrated = tmp_path / "migrated-account.json"
    migrated.write_text(json.dumps(payload), encoding="utf-8")
    assert load_account(migrated).next_order_sequence == 8

    payload["next_order_sequence"] = 7
    collision = tmp_path / "collision-account.json"
    collision.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="reuse an order id"):
        load_account(collision)

    payload["next_order_sequence"] = 8
    payload["pending_orders"] = [
        {
            "signal_date": "2026-01-05",
            "symbol": "sz300308",
            "side": "BUY",
            "target_weight": 0.5,
            "reason": "entry",
            "lifecycle": "CORE",
            "remaining_shares": 0,
            "attempts": 0,
            "order_id": "O000000999",
            **_identity(),
        }
    ]
    unknown = tmp_path / "unknown-order-account.json"
    unknown.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown account order"):
        load_account(unknown)


def test_legacy_account_requires_acknowledged_schema_migration(tmp_path):
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "old-code"
    legacy_payload = state.to_dict()
    legacy_payload.pop("schema_version")
    legacy_payload.pop("account_migrations")
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="explicit migration"):
        load_account(legacy)
    with pytest.raises(RuntimeError, match="acknowledge"):
        migrate_account(
            legacy,
            legacy,
            new_code_hash=code_fingerprint(),
            acknowledge_code_change=False,
        )

    migrated = migrate_account(
        legacy,
        legacy,
        new_code_hash=code_fingerprint(),
        acknowledge_code_change=True,
    )
    loaded = load_account(legacy)
    assert loaded.schema_version == ACCOUNT_SCHEMA_VERSION
    assert loaded.code_hash == code_fingerprint()
    assert loaded.initial_cash == migrated.initial_cash
    assert loaded.account_migrations[-1]["from_schema"] == 1


def test_legacy_position_migration_synthesizes_an_already_sellable_tranche(tmp_path):
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "old-code"
    state.positions = {
        "sz300308": Position(
            "sz300308",
            shares=300,
            avg_cost=10.0,
            entry_date="2025-01-02",
            highest_close=14.0,
            lifecycle="ADD1",
        )
    }
    payload = state.to_dict()
    payload["schema_version"] = 2
    payload["positions"]["sz300308"].pop("tranches")
    legacy = tmp_path / "legacy-position.json"
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    migrated = migrate_account(
        legacy,
        legacy,
        new_code_hash="new-code",
        acknowledge_code_change=True,
    )
    loaded = load_account(legacy)

    assert migrated.schema_version == ACCOUNT_SCHEMA_VERSION
    assert loaded.positions["sz300308"].shares == 300
    assert len(loaded.positions["sz300308"].tranches) == 1
    tranche = loaded.positions["sz300308"].tranches[0]
    assert tranche.tranche_id == "legacy:sz300308:1"
    assert tranche.lifecycle == "ADD1"
    assert tranche.shares == 300
    assert tranche.avg_cost == pytest.approx(10.0)
    assert loaded.positions["sz300308"].sellable_shares("2025-01-02") == 300


def test_schema_v3_rejects_nonfinite_or_unreconciled_position_lots(tmp_path):
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "code"
    state.positions = {
        "sz300308": Position(
            "sz300308",
            shares=100,
            avg_cost=10.0,
            entry_date="2026-01-02",
            highest_close=12.0,
            tranches=[
                Tranche(
                    "lot-1",
                    "CORE",
                    100,
                    10.0,
                    "2026-01-02",
                    "2026-01-03",
                    12.0,
                    lowest_close=9.0,
                    **_identity(
                        signal_date="2026-01-02",
                        target_weight=0.0,
                    ),
                )
            ],
        )
    }
    valid = state.to_dict()
    cases = {
        "missing-lots": lambda item: item["positions"]["sz300308"].update(tranches=[]),
        "share-mismatch": lambda item: item["positions"]["sz300308"].update(shares=200),
        "nonfinite-cost": lambda item: item["positions"]["sz300308"].update(avg_cost=float("nan")),
        "negative-lot": lambda item: item["positions"]["sz300308"]["tranches"][0].update(shares=-1),
    }

    for name, mutate in cases.items():
        payload = copy.deepcopy(valid)
        mutate(payload)
        malformed = tmp_path / f"{name}.json"
        malformed.write_text(json.dumps(payload), encoding="utf-8")
        # Non-finite JavaScript extensions are rejected by the JSON parser;
        # structurally valid JSON reaches the position/tranche invariants.
        with pytest.raises(RuntimeError, match=r"position|tranche|missing or corrupt"):
            load_account(malformed)


def test_pending_and_ledger_immutable_order_metadata_must_match(tmp_path):
    identity = _identity()
    pending = PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason="entry",
        lifecycle="CORE",
        order_id="O000000001",
        entry_score=0.80,
        entry_confidence=0.90,
        entry_regime="TREND",
        entry_industry_strength=0.70,
        **identity,
    )
    ledger = AccountOrder(
        order_id="O000000001",
        signal_date=pending.signal_date,
        submitted_date=pending.signal_date,
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status="OPEN",
        entry_score=pending.entry_score,
        entry_confidence=pending.entry_confidence,
        entry_regime=pending.entry_regime,
        entry_industry_strength=pending.entry_industry_strength,
        **identity,
    )
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "code"
    state.pending_orders = [pending]
    state.order_ledger = [ledger]
    state.next_order_sequence = 2
    valid = state.to_dict()
    changes = {
        "signal_date": "2026-01-06",
        "symbol": "sz300502",
        "side": "SELL",
        "target_weight": 0.40,
        "reason": "different reason",
        "lifecycle": "ADD1",
        "reduction_policy": ReductionPolicy.RISK_PRIORITY.value,
        "reason_code": "different_code",
        "exit_kind": "portfolio_risk",
        "entry_score": 0.70,
        "entry_confidence": 0.80,
        "entry_regime": "WEAK",
        "entry_industry_strength": 0.60,
    }

    for field, changed in changes.items():
        payload = copy.deepcopy(valid)
        payload["pending_orders"][0][field] = changed
        _refresh_payload_event_id(payload["pending_orders"][0])
        malformed = tmp_path / f"order-{field}.json"
        malformed.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RuntimeError, match=rf"immutable metadata.*{field}"):
            load_account(malformed)


def test_account_root_must_be_a_json_object(tmp_path):
    malformed = tmp_path / "array.json"
    malformed.write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeError, match="JSON object"):
        load_account(malformed)


def test_account_nested_collections_must_match_the_schema(tmp_path):
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "code"
    payload = state.to_dict()
    payload["positions"] = []
    malformed = tmp_path / "invalid-positions.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="violates schema"):
        load_account(malformed)


def test_broker_order_metric_excludes_unfilled_submissions():
    orders = [
        AccountOrder(
            order_id="O000000001",
            signal_date="2026-01-05",
            submitted_date="2026-01-05",
            symbol="sz300308",
            side="BUY",
            target_weight=0.5,
            reason="entry",
            lifecycle="CORE",
            requested_shares=100,
            filled_shares=100,
            status="FILLED",
        ),
        AccountOrder(
            order_id="O000000002",
            signal_date="2026-01-06",
            submitted_date="2026-01-06",
            symbol="sz300502",
            side="BUY",
            target_weight=0.5,
            reason="entry",
            lifecycle="CORE",
            requested_shares=100,
            status="OPEN",
        ),
    ]
    fills = [
        Fill(
            signal_date="2026-01-05",
            fill_date="2026-01-06",
            symbol="sz300308",
            side="BUY",
            shares=100,
            price=10.0,
            gross_value=1000.0,
            commission=5.0,
            stamp_duty=0.0,
            transfer_fee=0.1,
            slippage_cost=0.0,
            reason="entry",
            lifecycle="CORE",
            order_id="O000000001",
        )
    ]
    from uquant.engine import performance_metrics

    metrics = performance_metrics(
        equity_rows=[
            (pd.Timestamp("2026-01-05"), 2e6),
            (pd.Timestamp("2026-01-06"), 2e6),
        ],
        fills=fills,
        orders=orders,
        initial_cash=2e6,
        risk_events=[],
        benchmark_total_return=0.0,
    )
    assert metrics["account_orders"] == 1
    assert metrics["submitted_account_orders"] == 2
    assert len(metrics["order_ledger"]) == 1
    assert len(metrics["submission_ledger"]) == 2


def test_backtest_and_daily_share_decision_kernel(data_dir):
    engine = ProductionEngine(data_dir)
    account = AccountState.empty(2e6)
    decision = engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=account)
    report = render_daily_report(decision, account)
    assert decision.decision_digest in report
    assert config_fingerprint(engine.cfg) in report
    assert "Opportunity" in report and "Tomorrow" in report


def test_structured_sector_guard_counts_as_first_risk_reduction():
    from uquant.engine import performance_metrics

    reduced = Fill(
        signal_date="2026-01-05",
        fill_date="2026-01-06",
        symbol="sz300308",
        side="SELL",
        shares=100,
        price=10.0,
        gross_value=1_000.0,
        commission=5.0,
        stamp_duty=0.5,
        transfer_fee=0.1,
        slippage_cost=0.0,
        reason="portfolio rebalance",
        lifecycle="CORE",
        exit_kind="sector_guard",
    )

    observed = performance_metrics(
        equity_rows=[
            (pd.Timestamp("2026-01-05"), 2e6),
            (pd.Timestamp("2026-01-06"), 1.99e6),
        ],
        fills=[reduced],
        orders=[],
        initial_cash=2e6,
        risk_events=[],
        benchmark_total_return=0.0,
    )

    assert observed["first_reduce"] == "2026-01-06"


def test_decision_keeps_omitted_durable_symbols_in_strategy_panel(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
):
    from uquant.types import Risk, RiskAssessment

    omitted = SYMBOLS[3]
    account = AccountState(
        initial_cash=2e6,
        cash=1_900_000.0,
        positions={
            omitted: Position(
                omitted,
                shares=1_000,
                avg_cost=100.0,
                entry_date="2026-01-05",
            )
        },
        protected_weights={omitted: 0.05},
        operating_peak=2e6,
        capital_peak=2e6,
    )
    observed: dict[str, set[str]] = {}

    def normal_risk(**kwargs):
        observed["user_panel"] = set(kwargs["user_panel"])
        return RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")

    monkeypatch.setattr("uquant.engine.assess_risk", normal_risk)
    ProductionEngine(data_dir).decide(
        symbols=SYMBOLS[:3],
        as_of="2026-06-30",
        account=account,
    )

    assert omitted in observed["user_panel"]


def test_decision_keeps_sector_guard_cohort_in_risk_panel(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
):
    from uquant.types import Risk, RiskAssessment

    omitted = SYMBOLS[3]
    account = AccountState.empty(2e6)
    account.sector_guard_active = True
    account.sector_guard_started = "2026-06-20"
    account.sector_guard_symbols = [omitted]
    observed: dict[str, set[str]] = {}

    def normal_risk(**kwargs):
        observed["user_panel"] = set(kwargs["user_panel"])
        return RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")

    monkeypatch.setattr("uquant.engine.assess_risk", normal_risk)
    ProductionEngine(data_dir).decide(
        symbols=SYMBOLS[:3],
        as_of="2026-06-30",
        account=account,
    )

    assert omitted in observed["user_panel"]


def test_future_dated_state_fails_closed(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.last_successful_run = "2027-01-01"
    with pytest.raises(RuntimeError):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)


def test_decision_state_advances_at_most_once_per_session(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    persisted = copy.deepcopy(state.to_dict())

    with pytest.raises(RuntimeError, match="strictly after"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    with pytest.raises(RuntimeError, match="strictly after"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-29", account=state)

    assert state.to_dict() == persisted


def test_decision_cannot_predate_authoritative_broker_snapshot(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.broker_as_of = "2026-06-30"

    with pytest.raises(RuntimeError, match="authoritative broker snapshot"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-29", account=state)

    decision = engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    assert decision.date == state.broker_as_of


def test_daily_decision_marks_position_and_tranche_excursions(data_dir):
    engine = ProductionEngine(data_dir)
    date = pd.Timestamp("2026-06-30")
    symbol = SYMBOLS[0]
    close = float(engine.data.load(symbol).loc[date, "close"])
    cheap = Tranche(
        tranche_id="cheap-core",
        lifecycle="CORE",
        shares=100,
        avg_cost=close / 2.0,
        entry_date="2026-01-02",
        sellable_date="2026-01-05",
        highest_close=close / 2.0,
        lowest_close=close * 3.0,
    )
    expensive = Tranche(
        tranche_id="expensive-core",
        lifecycle="CORE",
        shares=100,
        avg_cost=close * 2.0,
        entry_date="2026-01-02",
        sellable_date="2026-01-05",
        highest_close=close / 2.0,
        lowest_close=close * 3.0,
    )
    state = AccountState.empty(2e6)
    state.positions[symbol] = Position(
        symbol=symbol,
        shares=200,
        avg_cost=(cheap.avg_cost + expensive.avg_cost) / 2.0,
        entry_date="2026-01-02",
        highest_close=close / 2.0,
        tranches=[cheap, expensive],
    )

    engine.decide(symbols=SYMBOLS, as_of=str(date.date()), account=state)

    position = state.positions[symbol]
    assert position.highest_close == pytest.approx(close)
    by_id = {item.tranche_id: item for item in position.tranches}
    assert by_id["cheap-core"].highest_close == pytest.approx(close)
    assert by_id["cheap-core"].lowest_close == pytest.approx(close)
    assert by_id["cheap-core"].mfe == pytest.approx(1.0)
    assert by_id["cheap-core"].mae == pytest.approx(0.0)
    assert by_id["expensive-core"].highest_close == pytest.approx(close)
    assert by_id["expensive-core"].lowest_close == pytest.approx(close)
    assert by_id["expensive-core"].mfe == pytest.approx(0.0)
    assert by_id["expensive-core"].mae == pytest.approx(-0.5)


def test_stale_code_hash_fails_closed(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.code_hash = "stale-code-hash"
    with pytest.raises(RuntimeError, match="code hash"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)


def test_pre_listing_symbols_are_point_in_time_invisible(data_dir):
    result = ProductionEngine(data_dir).backtest(
        symbols=(*SYMBOLS, "sh688146"),
        start="2023-01-03",
        end="2023-02-28",
    )
    assert result["start"] == "2023-01-03"
    assert all(fill["symbol"] != "sh688146" for fill in result["final_account"]["fills"])
    assert all(order["symbol"] != "sh688146" for order in result["order_ledger"])


def test_recent_shock_window_preserves_capital_across_pool_sizes(data_dir):
    engine = ProductionEngine(data_dir)
    for symbols in RISK_REGRESSION_POOLS:
        result = engine.backtest(
            symbols=symbols,
            start="2026-07-21",
            end="2026-08-05",
        )
        assert result["final_wealth"] > 0.85
        assert result["max_drawdown"] < 0.15
        assert result["account_orders"] <= 3
