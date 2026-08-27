from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest

from research.strategic_evidence.contract import load_contract
from research.strategic_evidence.intervention import StrategicOwnerIntervention
from research.strategic_evidence.replay import ReplayRequest, ReplayResult, run_replay
from research.strategic_evidence.trace import RouteTraceRow
from research.strategic_evidence.witness_ablation import (
    DIAGNOSTIC_ONLY,
    ECONOMIC,
    EVIDENCE_REMOVAL,
    FULL_REMOVAL,
    TRADABLE_REMOVAL,
    AblationSpec,
    FirstDivergences,
    cell_from_replay,
    derive_first_divergences,
    derive_symbol_roles,
    enumerate_initial_specs,
    minimal_witness_sets,
    rank_critical_symbols,
    select_bounded_search,
)
from research.strategic_evidence.witness_ablation_runner import (
    BalancedIndustryUniverse,
    assemble_full_route_shard,
    build_resume_identity,
    build_task4_scenario,
    derive_balanced_industry_universe,
    read_cell_shard,
    resolve_ablation_universe,
    task4_sentinel_specs,
    verify_streaming_shard,
    verify_task4_manifest,
    write_cell_shard,
    write_compact_and_manifest,
    write_streaming_shard,
)
from uquant.types import AccountState, Decision

ROOT = Path(__file__).parents[1]


def _row(
    date: str,
    *,
    leaders: tuple[dict[str, object], ...] = (),
    risk: dict[str, object] | None = None,
    targets: tuple[dict[str, object], ...] = (),
    account: str = "a" * 64,
    equity: float = 100.0,
) -> RouteTraceRow:
    return RouteTraceRow(
        date=date,
        reference_context={},
        leaders=leaders,
        risk={} if risk is None else risk,
        opportunity="TREND",
        targets=targets,
        orders=(),
        fills=(),
        account_sha256=account,
        equity=equity,
    )


def _provenance() -> dict[str, str]:
    return {
        "base_commit": "7" * 40,
        "experiment_commit": "8" * 40,
        "production_source_sha256": "1" * 64,
        "research_source_sha256": "2" * 64,
        "config_sha256": "3" * 64,
        "data_manifest_sha256": "4" * 64,
        "universe_sha256": "5" * 64,
        "industry_mapping_sha256": "6" * 64,
        "window_sha256": "7" * 64,
        "scenario_sha256": "8" * 64,
        "python": "3.12.13",
        "numpy": "2.5.1",
        "pandas": "3.0.5",
        "uv": "0.11.33",
        "uv_lock_sha256": "9" * 64,
        "generated_at": "2026-08-26T00:00:00Z",
    }


def test_initial_matrix_has_exact_frozen_coverage_and_labels() -> None:
    """Catches a missing axis or supplemental removal changing 117/49/68."""

    contract = load_contract(ROOT / "benchmarks/strategic_evidence_closure_contract.json")
    specs = enumerate_initial_specs(contract)

    assert len(specs) == 117
    assert len({spec.cell_id for spec in specs}) == 117
    assert sum(spec.evidence_class == ECONOMIC for spec in specs) == 49
    assert sum(spec.evidence_class == DIAGNOSTIC_ONLY for spec in specs) == 68
    canonical = [spec for spec in specs if spec.scope == "CANONICAL_LEAVE_ONE_OUT"]
    assert len(canonical) == 34 * 3
    assert {spec.axis for spec in canonical} == {
        FULL_REMOVAL,
        EVIDENCE_REMOVAL,
        TRADABLE_REMOVAL,
    }


def test_ghost_witness_is_multilabel_without_becoming_an_owner() -> None:
    """Catches route-only qualification evidence being discarded as non-economic."""

    baseline = (
        _row(
            "2024-01-02",
            leaders=({"symbol": "ghost"},),
            risk={"risk_anchor_symbols": ["anchor"]},
            targets=({"symbol": "owner", "weight": 0.9},),
        ),
    )
    singles = {
        "ghost": FirstDivergences(
            route={"date": "2024-01-02", "layer": "leaders"},
            state=None,
            economic=None,
            comparable=True,
            uncompared_reason=None,
        )
    }

    roles = derive_symbol_roles(
        baseline,
        singles,
        decisive_pairs=(("ghost", "pairmate"),),
    )

    assert roles["ghost"] == (
        "qualification witness",
        "ghost witness",
        "decisive-pair member",
    )
    assert roles["owner"] == ("owner",)
    assert roles["anchor"] == ("risk anchor",)


def test_route_state_and_economic_divergence_are_ordered_independently() -> None:
    """Catches one early route date being copied onto later state/economic changes."""

    baseline = (
        _row("2024-01-02", leaders=({"symbol": "a"},)),
        _row("2024-01-03"),
        _row("2024-01-04"),
    )
    variant = (
        _row("2024-01-02", leaders=({"symbol": "b"},)),
        _row("2024-01-03", account="b" * 64),
        _row("2024-01-04", equity=99.0),
    )

    result = derive_first_divergences(baseline, variant, status="SUCCESS")

    assert result.route == {"date": "2024-01-02", "layer": "leaders"}
    assert result.state == {"date": "2024-01-03", "layer": "account"}
    assert result.economic == {"date": "2024-01-04", "layer": "equity"}


def test_critical_ranking_keeps_preregistered_symbols_and_bounds_search() -> None:
    """Catches score sorting displacing preregistered symbols or expanding triples."""

    scores = {
        "z": 100.0,
        "sz300308": 0.0,
        "sz300502": 0.0,
        "sz300394": 0.0,
        "a": 9.0,
        "b": 8.0,
        "c": 7.0,
        "d": 6.0,
        "e": 5.0,
    }
    ranked = rank_critical_symbols(
        scores,
        preregistered=("sz300308", "sz300502", "sz300394"),
    )
    supported = {
        ("a", "sz300308", "z"): True,
        ("a", "b", "outside"): True,
        ("b", "sz300394", "sz300502"): False,
    }
    pairs, triples = select_bounded_search(ranked, supported)

    assert ranked == ("sz300308", "sz300502", "sz300394", "z", "a", "b", "c", "d")
    assert len(pairs) == 28
    assert len(set(pairs)) == 28
    assert triples == (("sz300308", "z", "a"),)


def test_minimal_witness_sets_drop_strict_supersets() -> None:
    """Catches bounded delta debugging reporting a non-minimal triple."""

    assert minimal_witness_sets((("a", "b", "c"), ("a", "b"), ("c", "d"), ("a", "b", "d"))) == (
        ("a", "b"),
        ("c", "d"),
    )


def test_resume_identity_is_exact_across_every_provenance_field() -> None:
    """Catches Task 3-style source/commit rebinding of Task 4 economic shards."""

    spec = AblationSpec(
        scope="CANONICAL_LEAVE_ONE_OUT",
        subject="sz300308",
        removed_symbols=("sz300308",),
        axis=FULL_REMOVAL,
        evidence_class=ECONOMIC,
    )
    identity = build_resume_identity(_provenance(), spec)

    assert build_resume_identity(_provenance(), spec) == identity
    assert build_resume_identity({**_provenance(), "experiment_commit": "a" * 40}, spec) != identity
    assert build_resume_identity({**_provenance(), "research_source_sha256": "b" * 64}, spec) != identity
    assert build_resume_identity({**_provenance(), "scenario_sha256": "c" * 64}, spec) != identity


def test_scenario_binds_exact_initial_matrix_and_search_bounds() -> None:
    """Catches resume treating Task 4 as only the 34 economic single removals."""

    contract = load_contract(ROOT / "benchmarks/strategic_evidence_closure_contract.json")
    specs = enumerate_initial_specs(contract)
    balanced = derive_balanced_industry_universe(ROOT / "data" / "frozen", contract=contract)
    scenario = build_task4_scenario(contract=contract, initial_specs=specs, balanced=balanced)

    assert scenario["required_initial_cell_count"] == 117
    assert scenario["economic_initial_cell_count"] == 49
    assert scenario["diagnostic_initial_cell_count"] == 68
    assert scenario["top_symbol_limit"] == 8
    assert scenario["required_pair_count"] == 28
    assert scenario["required_initial_cell_ids"] == [spec.cell_id for spec in specs]


def test_sentinel_plan_precedes_the_matrix_with_one_economic_and_two_diagnostics() -> None:
    """Catches launching the 117-cell matrix without representative axis sentinels."""

    contract = load_contract(ROOT / "benchmarks/strategic_evidence_closure_contract.json")
    sentinels = task4_sentinel_specs(enumerate_initial_specs(contract))

    assert tuple((spec.subject, spec.axis, spec.evidence_class) for spec in sentinels) == (
        ("sz300308", FULL_REMOVAL, ECONOMIC),
        ("sz300308", EVIDENCE_REMOVAL, DIAGNOSTIC_ONLY),
        ("sz300308", TRADABLE_REMOVAL, DIAGNOSTIC_ONLY),
    )


def test_industry_balanced_is_a_sealed_causal_pit_retained_universe() -> None:
    """Catches interpreting the sixth industry cell as report-pool outer-ring removal."""

    contract = load_contract(ROOT / "benchmarks/strategic_evidence_closure_contract.json")
    specs = enumerate_initial_specs(contract)
    balanced_spec = next(spec for spec in specs if spec.subject == "industry-balanced")
    ordinary_spec = next(spec for spec in specs if spec.subject == "optical")

    balanced = derive_balanced_industry_universe(ROOT / "data" / "frozen", contract=contract)
    balanced_source, balanced_removed = resolve_ablation_universe(
        balanced_spec,
        contract=contract,
        balanced=balanced,
    )
    ordinary_source, ordinary_removed = resolve_ablation_universe(
        ordinary_spec,
        contract=contract,
        balanced=balanced,
    )
    scenario = build_task4_scenario(contract=contract, initial_specs=specs, balanced=balanced)

    assert balanced.evidence_as_of < contract.window["start"]
    assert balanced_source == contract.canonical_universe
    assert tuple(symbol for symbol in balanced_source if symbol not in set(balanced_removed)) == balanced.symbols
    assert ordinary_source == contract.canonical_universe
    assert ordinary_removed != balanced_removed
    assert scenario["balanced_industry_universe"] == balanced.compact()
    assert scenario["balanced_industry_universe"]["symbols_sha256"] == balanced.symbols_sha256

    malformed = BalancedIndustryUniverse(
        evidence_as_of=balanced.evidence_as_of,
        per_industry=balanced.per_industry,
        symbols=balanced.symbols[:-1],
        removed_symbols=balanced.removed_symbols,
        industries=balanced.industries,
        industry_mapping_sha256=balanced.industry_mapping_sha256,
        evidence_sha256=balanced.evidence_sha256,
        symbols_sha256=balanced.symbols_sha256,
    )
    with pytest.raises(ValueError, match="balanced industry universe"):
        build_task4_scenario(contract=contract, initial_specs=specs, balanced=malformed)


def test_partial_replay_error_retains_trace_without_date_alignment() -> None:
    """Catches compact/role derivation aligning a failed prefix to the baseline."""

    request = ReplayRequest(symbols=("sz300308",), start="2024-01-02", end="2024-01-04")
    partial = ReplayResult(
        request=request,
        metrics={},
        trace=(_row("2024-01-02"),),
        final_account={"cash": 100.0},
        intervention_provenance={"removed_symbols": ["sz300308"]},
        status="REPLAY_ERROR",
        error="synthetic failure after first close",
    )
    spec = AblationSpec(
        scope="CANONICAL_LEAVE_ONE_OUT",
        subject="sz300308",
        removed_symbols=("sz300308",),
        axis=FULL_REMOVAL,
        evidence_class=ECONOMIC,
    )

    cell = cell_from_replay(spec, partial)
    divergence = derive_first_divergences(
        (_row("2024-01-02"), _row("2024-01-03"), _row("2024-01-04")),
        partial.trace,
        status=partial.status,
    )

    assert cell.status == "REPLAY_ERROR"
    assert cell.partial_trace_row_count == 1
    assert cell.intervention_provenance == partial.intervention_provenance
    assert divergence == FirstDivergences(
        route=None,
        state=None,
        economic=None,
        comparable=False,
        uncompared_reason="REPLAY_ERROR traces are retained but not date-aligned",
    )


class _FailAfterAppliedIntervention(StrategicOwnerIntervention):
    def preserve_activation(self, account: AccountState, decision: Decision) -> Decision:
        del account, decision
        raise RuntimeError("synthetic failure after applied intervention")


def test_real_mid_replay_error_retains_completed_prefix_and_intervention() -> None:
    """Catches run_replay rebuilding a terminal error with an empty trace."""

    symbols = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")
    result = run_replay(
        "data/frozen",
        ReplayRequest(
            symbols=symbols,
            start="2023-01-03",
            end="2023-01-06",
            intervention_date="2023-01-05",
        ),
        intervention=_FailAfterAppliedIntervention(owner="sz300308", target_gross=0.95),
    )

    assert result.status == "REPLAY_ERROR"
    assert tuple(row.date for row in result.trace) == ("2023-01-03", "2023-01-04")
    assert result.intervention_provenance is not None
    assert result.intervention_provenance["applied"] is True


def test_post_loop_replay_error_retains_every_completed_route_row() -> None:
    """Catches a missing intervention-session check discarding a complete prefix."""

    symbols = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")
    result = run_replay(
        "data/frozen",
        ReplayRequest(
            symbols=symbols,
            start="2023-01-03",
            end="2023-01-10",
            intervention_date="2023-01-07",
        ),
        intervention=StrategicOwnerIntervention(owner="sz300308", target_gross=0.95),
    )

    assert result.status == "REPLAY_ERROR"
    assert tuple(row.date for row in result.trace) == (
        "2023-01-03",
        "2023-01-04",
        "2023-01-05",
        "2023-01-06",
        "2023-01-09",
        "2023-01-10",
    )
    assert result.error == "intervention date is absent from the official replay calendar"


def test_partial_failure_cell_shard_round_trips_and_rejects_identity_rebind(
    tmp_path: Path,
) -> None:
    """Catches resume dropping a failed prefix or accepting a new exact identity."""

    request = ReplayRequest(symbols=("sz300308",), start="2024-01-02", end="2024-01-04")
    result = ReplayResult(
        request=request,
        metrics={},
        trace=(_row("2024-01-02"),),
        final_account={"cash": 100.0},
        intervention_provenance={"removed_symbols": ["sz300308"]},
        status="REPLAY_ERROR",
        error="failure after one retained row",
    )
    spec = AblationSpec(
        scope="CANONICAL_LEAVE_ONE_OUT",
        subject="sz300308",
        removed_symbols=("sz300308",),
        axis=FULL_REMOVAL,
        evidence_class=ECONOMIC,
    )
    cell = cell_from_replay(spec, result)
    divergence = derive_first_divergences((_row("2024-01-02"),), result.trace, status=result.status)
    identity = build_resume_identity(_provenance(), spec)
    path = tmp_path / "partial.jsonl.gz"

    write_cell_shard(
        path,
        cell=cell,
        result=result,
        divergences=divergence,
        provenance=_provenance(),
        resume_identity=identity,
    )
    observed_cell, observed_trace, observed_divergence = read_cell_shard(
        path,
        expected_provenance=_provenance(),
        expected_resume_identity=identity,
    )

    assert observed_cell == cell
    assert observed_trace == result.trace
    assert observed_divergence == divergence
    with pytest.raises(ValueError, match="resume identity differs"):
        read_cell_shard(
            path,
            expected_provenance=_provenance(),
            expected_resume_identity="f" * 64,
        )


class _OnePassRows:
    def __init__(self, count: int, payload_size: int) -> None:
        self.count = count
        self.payload_size = payload_size
        self.iterations = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.iterations += 1
        if self.iterations != 1:
            raise AssertionError("rows were consumed more than once")
        payload = "x" * self.payload_size
        for index in range(self.count):
            yield {"record_type": "ROUTE", "route_index": index, "payload": payload}

    def __len__(self) -> int:
        raise AssertionError("rows were materialized for sizing")


def test_streaming_assembly_and_verification_are_memory_bounded(tmp_path: Path) -> None:
    """Catches gzip assembly expanding every route row in one process."""

    identity = "a" * 64
    sources = []
    fixtures = []
    for index in range(3):
        fixture = _OnePassRows(count=3_000, payload_size=2_048)
        source = tmp_path / f"cell-{index}.jsonl.gz"
        write_streaming_shard(
            source,
            rows=fixture,
            provenance=_provenance(),
            resume_identity=identity,
        )
        fixtures.append(fixture)
        sources.append(source)

    tracemalloc.start()
    destination = tmp_path / "full.jsonl.gz"
    assembled = assemble_full_route_shard(
        destination,
        cell_shards=sources,
        provenance=_provenance(),
        resume_identity=identity,
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert all(fixture.iterations == 1 for fixture in fixtures)
    assert sources[0].read_bytes() == sources[1].read_bytes() == sources[2].read_bytes()
    assert assembled["row_count"] == 9_000
    assert (
        verify_streaming_shard(
            destination,
            expected_provenance=_provenance(),
            expected_resume_identity=identity,
        )
        == assembled
    )
    assert peak < 12 * 1024 * 1024


def test_manifest_is_portable_and_readback_is_linked(tmp_path: Path) -> None:
    """Catches absolute scratch paths entering canonical Task 4 seals."""

    relative_summary = Path("artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_full.json")
    relative_manifest = Path(
        "artifacts/strategic_evidence_closure/checkpoint4_witness_ablation_manifest.json"
    )
    route_metadata = {
        "path": "/tmp/private/full.jsonl.gz",
        "byte_size": 123,
        "bytes_sha256": "a" * 64,
        "row_count": 7,
        "linkage_sha256": "b" * 64,
    }
    outputs = []
    for repository in (tmp_path / "a", tmp_path / "moved" / "b"):
        (repository / relative_summary.parent).mkdir(parents=True)
        summary, manifest = write_compact_and_manifest(
            repository=repository,
            summary_path=repository / relative_summary,
            manifest_path=repository / relative_manifest,
            summary_payload={"schema_version": 1, "cells": []},
            route_metadata=route_metadata,
        )
        outputs.append(
            (
                (repository / relative_summary).read_bytes(),
                (repository / relative_manifest).read_bytes(),
                verify_task4_manifest(
                    repository,
                    summary_path=relative_summary,
                    manifest_path=relative_manifest,
                    route_metadata=route_metadata,
                ),
            )
        )
        assert summary["route_shard"] == {
            key: value for key, value in route_metadata.items() if key != "path"
        }
        assert manifest["summary"]["path"] == relative_summary.as_posix()
        assert "/tmp" not in json.dumps(manifest)

    assert outputs[0] == outputs[1]
