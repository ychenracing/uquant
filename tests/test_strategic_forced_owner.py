from __future__ import annotations

from pathlib import Path

from research.strategic_evidence.forced_owner import (
    EligibilityObservation,
    ForcedOwnerCell,
    activation_from_rows,
    enumerate_forced_owner_controls,
    forced_owner_cell_from_result,
    replay_trace_sha256,
    required_forced_owner_cell_ids,
    routes_canonically_equal,
    select_negative_controls,
    verify_forced_owner_trace_shard,
    write_forced_owner_trace_shard,
)
from research.strategic_evidence.intervention import StrategicOwnerIntervention
from research.strategic_evidence.replay import (
    ReplayRequest,
    ReplayResult,
    common_activation_date,
    common_activation_target_gross,
    run_replay,
)
from research.strategic_evidence.trace import RouteTraceRow


def _observation(symbol: str, **changes: object) -> EligibilityObservation:
    base: dict[str, object] = {
        "symbol": symbol,
        "date": "2024-01-05",
        "visible_sessions": 300,
        "liquidity_confirmed": True,
        "leader_score": 0.91,
        "leader_confidence": 0.71,
        "secular_score": 0.81,
        "secular_confidence": 0.80,
        "momentum60": 0.51,
        "momentum120": 0.52,
        "relative_strength": 0.53,
        "trend_persistence": 2 / 3,
        "ret120": 0.12,
        "risk": "NORMAL",
        "opportunity": "TREND",
        "independent_market_confirmation": True,
    }
    base.update(changes)
    return EligibilityObservation(**base)


def test_activation_is_first_epoch_increment_or_first_strategic_target() -> None:
    """Catches a common date selected from a later, manually reported epoch."""

    rows = (
        {"date": "2024-01-02", "strategic_epoch": 0, "strategic_targets": ()},
        {"date": "2024-01-03", "strategic_epoch": 1, "strategic_targets": ("sz300308",)},
        {"date": "2024-01-04", "strategic_epoch": 2, "strategic_targets": ("sz300502",)},
    )

    assert activation_from_rows(rows) == "2024-01-03"


def test_negative_controls_use_only_activation_date_evidence_and_stable_ties() -> None:
    """Catches ranking weak-trend qualifiers by return instead of lexical symbol."""

    observations = (
        _observation("sz000001", leader_score=0.20),
        _observation("sz000002", leader_score=0.20),
        _observation("sz000003", ret120=-0.01, trend_persistence=0.5),
        _observation("sz000004", ret120=-0.20, trend_persistence=0.4),
        _observation("sz000005", secular_confidence=0.10, leader_score=0.30),
        _observation("sz000006", secular_confidence=0.10, leader_score=0.30),
    )

    controls = select_negative_controls(observations)

    assert controls == {
        "LOWEST_LIQUID_LEADER_SCORE": "sz000001",
        "NEGATIVE_RET120_AND_WEAK_TREND": "sz000003",
        "LOWEST_SECULAR_CONFIDENCE_FAILING_ABSOLUTE": "sz000005",
    }


def test_control_cases_keep_overlapping_positive_and_negative_roles_as_16_cells() -> None:
    """Catches owner deduplication silently shrinking the frozen 8-by-2 matrix."""

    controls = enumerate_forced_owner_controls(
        positive_controls=("p1", "p2", "p3", "p4", "shared"),
        negative_controls={
            "LOWEST_LIQUID_LEADER_SCORE": "n1",
            "NEGATIVE_RET120_AND_WEAK_TREND": "shared",
            "LOWEST_SECULAR_CONFIDENCE_FAILING_ABSOLUTE": "n3",
        },
    )

    assert len(controls) == 8
    assert len(required_forced_owner_cell_ids(controls)) == 16
    assert [control.control_id for control in controls if control.owner == "shared"] == [
        "POSITIVE_CONTROL:shared",
        "NEGATIVE_RET120_AND_WEAK_TREND",
    ]


def test_native_eligibility_requires_every_frozen_absolute_predicate() -> None:
    """Catches a native date selected without independent market confirmation."""

    eligible = _observation("sz300308")
    failed = _observation("sz300502", independent_market_confirmation=False)

    assert eligible.native_eligible is True
    assert failed.native_eligible is False


def test_eligibility_evidence_names_nonfinite_causal_inputs_as_null() -> None:
    """Catches point-in-time NaN factors breaking canonical evidence sealing."""

    evidence = _observation("sz300308", momentum60=float("nan")).evidence()

    assert evidence["momentum60"] is None
    assert evidence["nonfinite_fields"] == ["momentum60"]


def test_forced_owner_rewrites_point_in_time_industry_identity() -> None:
    """Catches a forced BUY retaining the baseline owner's industry identity."""

    symbols = (
        "sz300308",
        "sz300502",
        "sz300394",
        "sh688008",
        "sh603986",
        "sh688037",
    )
    baseline = run_replay(
        "data/frozen", ReplayRequest(symbols=symbols, start="2023-01-03", end="2023-01-10")
    )
    forced = run_replay(
        "data/frozen",
        ReplayRequest(
            symbols=symbols,
            start="2023-01-03",
            end="2023-01-10",
            intervention_date=common_activation_date(baseline),
        ),
        intervention=StrategicOwnerIntervention(
            owner="sh688037", target_gross=common_activation_target_gross(baseline)
        ),
    )

    assert forced.status == "SUCCESS"
    assert forced.final_account["order_ledger"][0]["industry_at_entry"] == "design"
    activation = common_activation_date(baseline)
    original_row = next(row for row in baseline.trace if row.date == activation)
    forced_row = next(row for row in forced.trace if row.date == activation)
    assert original_row.targets and len(forced_row.targets) == 1
    if len(original_row.targets) > 1:
        audit = forced.intervention_provenance["activation_counterfactual"]
        assert audit["kind"] == "COUNTERFACTUAL_UNFILLED_COHORT"
        assert audit["production_qualification_evidence"] is False
        assert audit["source_targets"] == list(original_row.targets)
        assert audit["source_orders"] == list(original_row.orders)
        assert forced_row.orders[0]["order_id"] not in {order["order_id"] for order in original_row.orders}
    else:
        assert forced_row.orders[0]["order_id"] == original_row.orders[0]["order_id"]
        assert forced_row.orders[0]["event_id"] != original_row.orders[0]["event_id"]
    assert forced_row.targets[0]["weight"] == sum(target["weight"] for target in original_row.targets)
    assert forced_row.targets[0]["industry_at_entry"] == "design"
    assert not forced_row.fills
    first_fill = next(fill for row in forced.trace for fill in row.fills)
    assert first_fill["symbol"] == "sh688037" and first_fill["industry_at_entry"] == "design"
    assert first_fill["fill_date"] > activation
    assert first_fill["event_id"] == forced_row.orders[0]["event_id"]


def test_native_intervention_without_a_production_target_is_retained_as_replay_error() -> None:
    """Catches a malformed required native cell escaping instead of being retained."""

    result = run_replay(
        "data/frozen",
        ReplayRequest(
            symbols=("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
            start="2023-01-03",
            end="2023-01-06",
            intervention_date="2023-01-03",
        ),
        intervention=StrategicOwnerIntervention(owner="sz300502", target_gross=0.95),
    )

    assert result.status == "REPLAY_ERROR"
    assert result.metrics == {}
    assert result.intervention_provenance is not None
    assert result.intervention_provenance["applied"] is True
    assert len(result.intervention_provenance["before_account_sha256"]) == 64

    cell = forced_owner_cell_from_result(
        control_id="POSITIVE_CONTROL:sz300502",
        owner="sz300502",
        mode="NATIVE_ELIGIBILITY_DATE",
        intervention_date="2023-01-03",
        selection_evidence={"owner_role": "POSITIVE_CONTROL"},
        result=result,
    )
    compact = cell.compact()
    assert cell.intervention_count == 1
    assert compact["intervention_provenance"] == result.intervention_provenance
    assert compact["metric_null_reasons"] == {"all_economic_metrics": "REPLAY_ERROR"}


def test_no_native_cell_has_zero_interventions_and_no_audit() -> None:
    """Catches a no-date terminal row inheriting another cell's intervention audit."""

    cell = ForcedOwnerCell.no_native(
        control_id="POSITIVE_CONTROL:sz300308",
        owner="sz300308",
        selection_evidence={"first_native_eligibility": None},
    )

    assert cell.intervention_count == 0
    assert cell.intervention_provenance is None


def test_trace_shard_readback_normalizes_nested_route_tuples(tmp_path: Path) -> None:
    """Catches JSON tuple/list conversion being mistaken for a trace mutation."""

    row = RouteTraceRow(
        "2024-01-05",
        {"evidence_families": {"market": ("index_velocity",)}},
        ({"symbol": "sz300308"},),
        {},
        "TREND",
        (),
        (),
        (),
        "a" * 64,
        1.0,
        target_gross=0,
    )
    request = ReplayRequest(
        symbols=("sz300308",),
        start="2024-01-04",
        end="2024-01-05",
        scenario="forced-owner:POSITIVE_CONTROL:sz300308:COMMON_ACTIVATION_DATE",
        intervention_date="2024-01-05",
    )
    intervention = {
        "applied": True,
        "source_owner": "sz300308",
        "forced_owner": "sz300308",
        "target_gross": 0.95,
        "before_account_sha256": "6" * 64,
        "after_account_sha256": "7" * 64,
    }
    result = ReplayResult(
        request=request,
        metrics={"final_equity": 1.0},
        trace=(row,),
        final_account={"positions": {}},
        intervention_provenance=intervention,
    )
    cell = ForcedOwnerCell(
        control_id="POSITIVE_CONTROL:sz300308",
        owner="sz300308",
        mode="COMMON_ACTIVATION_DATE",
        intervention_date="2024-01-05",
        status="SUCCESS",
        selection_evidence={"owner_role": "POSITIVE_CONTROL"},
        metrics={"final_equity": 1.0},
        metric_null_reasons={},
        final_account_sha256="a" * 64,
        trace_sha256=replay_trace_sha256(result),
        intervention_count=1,
        intervention_provenance=intervention,
    )
    provenance = {
        "base_commit": "a" * 40, "experiment_commit": "b" * 40,
        "production_source_sha256": "c" * 64, "research_source_sha256": "d" * 64,
        "config_sha256": "e" * 64, "data_manifest_sha256": "f" * 64,
        "universe_sha256": "1" * 64, "industry_mapping_sha256": "2" * 64,
        "window_sha256": "3" * 64, "scenario_sha256": "4" * 64,
        "python": "3.12", "numpy": "2", "pandas": "3", "uv": "x",
        "uv_lock_sha256": "5" * 64, "generated_at": "2026-08-26T00:00:00Z",
    }
    shard = tmp_path / "routes.jsonl.gz"
    metadata = write_forced_owner_trace_shard(
        shard,
        cells=(cell,),
        results={cell.cell_id: result},
        provenance=provenance,
    )
    readback = verify_forced_owner_trace_shard(
        shard,
        expected_cells=(cell,),
        expected_provenance=provenance,
    )

    assert metadata == readback.metadata
    assert metadata["cell_route_row_counts"] == {cell.cell_id: 1}
    assert metadata["row_seal_count"] == 2
    assert routes_canonically_equal(readback.results[cell.cell_id].trace, (row,)) is True
