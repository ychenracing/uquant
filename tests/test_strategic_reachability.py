from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from research.strategic_evidence import reachability, reachability_runner
from research.strategic_evidence.contract import load_contract
from research.strategic_evidence.models import canonical_sha256
from research.strategic_evidence.provenance import seal_payload, write_gzip_shard
from research.strategic_evidence.witness_ablation_runner import recompute_task4_identities
from uquant.account import economic_state_sha256
from uquant.types import AccountState


def test_reachability_module_is_available_for_frozen_task5_contract() -> None:
    assert importlib.util.find_spec("research.strategic_evidence.reachability") is not None


def test_account_checkpoint_uses_production_codec_and_rejects_invalid_budget() -> None:
    payload = AccountState.empty(1_000_000.0).to_dict()

    decoded = reachability.validate_account_checkpoint(payload)

    assert decoded.to_dict() == payload
    invalid = dict(payload)
    invalid["capital_budget_level"] = 5
    with pytest.raises(RuntimeError, match="capital_budget_level"):
        reachability.validate_account_checkpoint(invalid)


def test_synthetic_paths_are_deterministic_causal_and_explicitly_labeled() -> None:
    paths = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )

    assert paths == reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )
    assert tuple(path.path_id for path in paths) == (
        "P01",
        "P02",
        "P03",
        "P04",
        "P05",
        "P06",
    )
    for path in paths:
        assert path.source == "SYNTHETIC"
        assert path.provenance["synthetic_historical_return_claims"] == "FORBIDDEN"
        for index, bar in enumerate(path.bars):
            assert bar.visible_through == bar.date
            assert bar.high >= max(bar.open, bar.close)
            assert bar.low <= min(bar.open, bar.close)
            assert bar.date < "2026-08-06"
            if index:
                assert bar.open == path.bars[index - 1].close


def _node(
    *,
    positive_position: bool = False,
    capital_budget_level: int = 0,
    qualification_streak: int = 3,
) -> reachability.ReachNode:
    return reachability.ReachNode.create(
        risk="NORMAL",
        opportunity="TREND",
        capital_budget_level=capital_budget_level,
        chronic_level=0,
        freeze_new_risk=False,
        strategic_epoch=1,
        strategic_active=True,
        qualification_streak=qualification_streak,
        long_cycle_open=True,
        recovery_owner="NONE",
        protected_or_anchor=False,
        positive_target=False,
        positive_position=positive_position,
    )


def test_reach_node_contains_only_frozen_dimensions_and_has_stable_identity() -> None:
    node = _node(capital_budget_level=2)

    assert node == _node(capital_budget_level=2)
    assert tuple(node.dimensions()) == (
        "risk",
        "opportunity",
        "capital_budget_level",
        "chronic_level",
        "freeze_new_risk",
        "strategic_epoch",
        "strategic_active",
        "qualification_streak",
        "long_cycle_open",
        "recovery_owner",
        "protected_or_anchor",
        "positive_target",
        "positive_position",
    )
    with pytest.raises(ValueError, match="capital_budget_level"):
        replace(node, capital_budget_level=5)
    with pytest.raises(ValueError, match="identity differs"):
        replace(node, chronic_level=1)


def test_tarjan_classifies_only_terminal_components_without_position_exit_as_dead() -> None:
    a = _node(qualification_streak=0)
    b = _node(qualification_streak=1)
    c = _node(positive_position=True, qualification_streak=2)
    d = _node(capital_budget_level=1, qualification_streak=0)
    edges = (
        reachability.ReachEdge(a.node_id, b.node_id),
        reachability.ReachEdge(b.node_id, a.node_id),
        reachability.ReachEdge(c.node_id, c.node_id),
        reachability.ReachEdge(d.node_id, c.node_id),
    )

    analysis = reachability.analyze_terminal_sccs((d, c, b, a), tuple(reversed(edges)))

    dead_components = tuple(item.node_ids for item in analysis if item.dead_state)
    assert dead_components == (tuple(sorted((a.node_id, b.node_id))),)
    assert reachability.analyze_terminal_sccs((a, b, c, d), edges) == analysis


def test_initial_states_use_historical_checkpoint_then_explicit_synthetic_fallback() -> None:
    historical_account = AccountState.empty(1_000_000.0)
    historical_account.opportunity = "TREND"
    historical_account.capital_budget_level = 1
    historical_account.candidate_tenure["strategic_candidate_streak"] = 3
    historical_account.candidate_tenure["strategic_long_cycle_open"] = 1
    checkpoints = reachability.extract_historical_checkpoints(
        (
            {
                "state_id": "S06",
                "date": "2025-01-06",
                "account": historical_account.to_dict(),
                "provenance": {"source": "HISTORICAL", "checkpoint_id": "account-17"},
            },
        )
    )

    states = reachability.build_initial_states(checkpoints=checkpoints, initial_cash=1_000_000.0)

    assert tuple(state.state_id for state in states) == tuple(
        f"S{index:02d}" for index in range(1, 15)
    )
    by_id = {state.state_id: state for state in states}
    assert by_id["S06"].source == "HISTORICAL"
    assert by_id["S06"].provenance["checkpoint_id"] == "account-17"
    assert by_id["S07"].source == "SYNTHETIC"
    assert by_id["S07"].provenance["fallback_reason"] == "HISTORICAL_CHECKPOINT_UNAVAILABLE"
    for state in states:
        decoded = reachability.validate_account_checkpoint(state.account)
        assert state.account_sha256 == economic_state_sha256(decoded)
        assert tuple(state.dimensions) == (
            "risk",
            "opportunity",
            "capital_budget_level",
            "chronic_level",
            "freeze_new_risk",
            "strategic_epoch",
            "strategic_active",
            "qualification_streak",
            "long_cycle_open",
            "recovery_owner",
            "protected_or_anchor",
            "positive_target",
            "positive_position",
        )
        assert state.dimensions == reachability.derive_state_dimensions(decoded)


def test_historical_checkpoint_extraction_rejects_future_holdout_observation() -> None:
    with pytest.raises(ValueError, match="Future Holdout"):
        reachability.extract_historical_checkpoints(
            (
                {
                    "state_id": "S01",
                    "date": "2026-08-06",
                    "account": AccountState.empty(1_000_000.0).to_dict(),
                    "provenance": {"source": "HISTORICAL"},
                },
            )
        )


def test_historical_checkpoint_state_label_must_match_durable_blueprint() -> None:
    account = AccountState.empty(1_000_000.0)
    account.opportunity = "TREND"
    account.capital_budget_level = 2

    with pytest.raises(ValueError, match="blueprint"):
        reachability.extract_historical_checkpoints(
            (
                {
                    "state_id": "S06",
                    "date": "2025-01-06",
                    "account": account.to_dict(),
                    "provenance": {"source": "HISTORICAL"},
                },
            )
        )


def test_initial_state_builder_rejects_duplicate_generator_checkpoints() -> None:
    account = AccountState.empty(1_000_000.0)
    account.opportunity = "TREND"
    account.capital_budget_level = 1
    account.candidate_tenure["strategic_candidate_streak"] = 3
    account.candidate_tenure["strategic_long_cycle_open"] = 1
    checkpoint = reachability.extract_historical_checkpoints(
        (
            {
                "state_id": "S06",
                "date": "2025-01-06",
                "account": account.to_dict(),
                "provenance": {"source": "HISTORICAL"},
            },
        )
    )[0]

    with pytest.raises(ValueError, match="duplicates"):
        reachability.build_initial_states(
            checkpoints=(item for item in (checkpoint, checkpoint)),
            initial_cash=1_000_000.0,
        )


def _session(
    session_date: str,
    *,
    capital_budget_level: int,
    healthy: bool,
    positive_target: bool = False,
    positive_position: bool = False,
    owner: str | None = None,
    epoch: int = 0,
    blockers: tuple[str, ...] = (),
    grant_attempted: bool = False,
    grant_succeeded: bool = False,
) -> reachability.SessionObservation:
    node = reachability.ReachNode.create(
        risk="NORMAL",
        opportunity="TREND",
        capital_budget_level=capital_budget_level,
        chronic_level=0,
        freeze_new_risk=False,
        strategic_epoch=epoch,
        strategic_active=epoch > 0,
        qualification_streak=3,
        long_cycle_open=True,
        recovery_owner="NONE",
        protected_or_anchor=False,
        positive_target=positive_target,
        positive_position=positive_position,
    )
    return reachability.SessionObservation(
        date=session_date,
        node=node,
        healthy=healthy,
        blockers=blockers,
        strategic_owner=owner,
        grant_attempted=grant_attempted,
        grant_succeeded=grant_succeeded,
    )


def test_observation_analysis_counts_only_healthy_repair_and_repeated_crowning() -> None:
    observations = (
        _session("2025-01-02", capital_budget_level=2, healthy=True),
        _session(
            "2025-01-03",
            capital_budget_level=2,
            healthy=False,
            blockers=("DATA_OR_REFERENCE_COVERAGE",),
        ),
        _session("2025-01-06", capital_budget_level=1, healthy=True),
        _session("2025-01-07", capital_budget_level=0, healthy=True),
        _session(
            "2025-01-08",
            capital_budget_level=0,
            healthy=True,
            owner="sz300308",
            epoch=1,
        ),
        _session("2025-01-09", capital_budget_level=0, healthy=True),
        _session(
            "2025-01-10",
            capital_budget_level=0,
            healthy=True,
            positive_target=True,
            owner="sz300502",
            epoch=2,
        ),
        _session(
            "2025-01-13",
            capital_budget_level=0,
            healthy=True,
            positive_target=True,
            positive_position=True,
            owner="sz300502",
            epoch=2,
        ),
    )

    analysis = reachability.analyze_observations(observations)

    assert analysis.healthy_sessions == 7
    assert analysis.capital_budget_repair is not None
    assert analysis.capital_budget_repair.healthy_sessions == 2
    assert analysis.capital_budget_repair.recovered_on == "2025-01-07"
    assert analysis.blocker_timelines == (
        reachability.BlockerTimeline(
            blockers=("DATA_OR_REFERENCE_COVERAGE",),
            start="2025-01-03",
            end="2025-01-03",
            calendar_sessions=1,
            healthy_sessions=0,
        ),
    )
    assert analysis.repeated_crowning.distinct_owners == ("sz300308", "sz300502")
    assert analysis.repeated_crowning.strategic_epochs == (1, 2)
    assert analysis.repeated_crowning.satisfied is True
    assert tuple(finding.observation_id for finding in analysis.findings) == tuple(
        f"R{index}" for index in range(1, 9)
    )
    assert {finding.observation_id: finding.observed for finding in analysis.findings}["R8"] is True
    assert analysis.edges == reachability.build_reach_graph(observations)[1]


def test_r1_requires_an_actual_state_transition() -> None:
    rows = (
        _session("2025-01-02", capital_budget_level=0, healthy=True),
        _session("2025-01-03", capital_budget_level=0, healthy=True),
    )

    analysis = reachability.analyze_observations(rows)

    assert analysis.findings[0] == reachability.ReachabilityFinding(
        observation_id="R1",
        observed=False,
        first_date=None,
        evidence={"node_count": 1, "transition_count": 0},
    )


def test_failed_grant_retry_latency_starts_only_at_explicit_failed_attempt() -> None:
    rows = (
        _session(
            "2025-01-02",
            capital_budget_level=0,
            healthy=True,
            grant_attempted=True,
        ),
        _session("2025-01-03", capital_budget_level=0, healthy=True),
        _session(
            "2025-01-06",
            capital_budget_level=0,
            healthy=True,
            positive_target=True,
            grant_attempted=True,
            grant_succeeded=True,
        ),
    )

    metrics = reachability.analyze_observations(rows).metrics

    assert metrics.failed_grant_retry_healthy_sessions == 2
    assert metrics.failed_grant_retry_trace == (("2025-01-02", "2025-01-06"),)


def test_terminal_scc_zero_target_duration_is_not_hidden_by_positive_position() -> None:
    rows = (
        _session(
            "2025-01-02",
            capital_budget_level=0,
            healthy=True,
            positive_position=True,
        ),
        _session(
            "2025-01-03",
            capital_budget_level=0,
            healthy=True,
            positive_position=True,
        ),
    )

    analysis = reachability.analyze_observations(rows)

    assert analysis.sccs[0].terminal is True
    assert analysis.sccs[0].dead_state is False
    assert analysis.metrics.terminal_scc_healthy_zero_target_duration == 2


@pytest.mark.parametrize(
    ("changes", "expected_blocker"),
    (
        ({"risk": "CAUTION"}, "RISK_NOT_NORMAL"),
        ({"opportunity": "CHOPPY"}, "OPPORTUNITY_NOT_TREND"),
        ({"candidate_eligible": False}, "NO_ABSOLUTE_OWNER_CANDIDATE"),
        ({"coverage_complete": False}, "DATA_OR_REFERENCE_COVERAGE"),
        ({"market_wide_execution_block": True}, "MARKET_WIDE_EXECUTION_BLOCK"),
        ({"target_gross_cap": 0.0}, "NO_TARGET_GROSS_CAPACITY"),
    ),
)
def test_frozen_healthy_session_rule_reports_each_literal_blocker(
    changes: dict[str, object],
    expected_blocker: str,
) -> None:
    facts: dict[str, Any] = {
        "risk": "NORMAL",
        "opportunity": "TREND",
        "candidate_eligible": True,
        "coverage_complete": True,
        "market_wide_execution_block": False,
        "target_gross_cap": 0.5,
    }
    facts.update(changes)

    health = reachability.classify_healthy_session(**facts)

    assert health.healthy is False
    assert expected_blocker in health.blockers


def _provenance() -> dict[str, str]:
    return {
        "base_commit": "0" * 40,
        "experiment_commit": "1" * 40,
        "production_source_sha256": "2" * 64,
        "research_source_sha256": "3" * 64,
        "config_sha256": "4" * 64,
        "data_manifest_sha256": "5" * 64,
        "universe_sha256": "6" * 64,
        "industry_mapping_sha256": "7" * 64,
        "window_sha256": "8" * 64,
        "scenario_sha256": "9" * 64,
        "python": "3.12.13",
        "numpy": "2.5.1",
        "pandas": "3.0.5",
        "uv": "uv 0.11.33",
        "uv_lock_sha256": "a" * 64,
        "generated_at": "2026-08-27T00:00:00+00:00",
    }


def test_reachability_cells_preserve_terminal_failures_and_sealed_readback(tmp_path: Path) -> None:
    specs = reachability.enumerate_reachability_specs()[:3]
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    paths = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )
    success = reachability.run_reachability_cell(
        specs[0],
        state=state,
        path=paths[0],
        observe=lambda: reachability.build_diagnostic_observations(state=state, path=paths[0]),
    )
    replay_error = reachability.run_reachability_cell(
        specs[1],
        state=state,
        path=paths[1],
        observe=lambda: (_ for _ in ()).throw(KeyError("transition payload")),
    )
    insufficient = reachability.run_reachability_cell(
        specs[2],
        state=state,
        path=paths[2],
        observe=lambda: (),
    )
    shard = tmp_path / "reachability.jsonl.gz"

    reachability.write_reachability_shard(
        shard,
        cells=(success,),
        provenance=_provenance(),
        expected_specs=(specs[0],),
        expected_states=(state,),
        expected_paths=(paths[0],),
    )
    rows = reachability.read_reachability_shard(
        shard,
        expected_specs=(specs[0],),
        expected_states=(state,),
        expected_paths=(paths[0],),
        expected_provenance=_provenance(),
    )

    assert tuple(row["status"] for row in rows) == ("SUCCESS",)
    assert replay_error.error == {
        "type": "KeyError",
        "message": "'transition payload'",
        "stage": "TRANSITION",
    }
    assert insufficient.error == {
        "type": "InsufficientSample",
        "message": "reachability cell has no observations",
        "stage": "ANALYSIS",
    }
    assert rows[0]["input_bindings"]["state_account_sha256"] == state.account_sha256
    assert rows[0]["input_bindings"]["path_provenance_sha256"]
    assert rows[0]["payload_sha256"]

    with pytest.raises(KeyboardInterrupt):
        reachability.run_reachability_cell(
            specs[0],
            state=state,
            path=paths[0],
            observe=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
        )


def test_reachability_matrix_is_exact_cartesian_coverage() -> None:
    specs = reachability.enumerate_reachability_specs()

    assert len(specs) == 14 * 6
    assert len({spec.cell_id for spec in specs}) == len(specs)
    assert (specs[0].state_id, specs[0].path_id) == ("S01", "P01")
    assert (specs[-1].state_id, specs[-1].path_id) == ("S14", "P06")


def test_reachability_readback_rejects_resealed_cell_linkage_mutation(tmp_path: Path) -> None:
    spec = reachability.enumerate_reachability_specs()[0]
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    path = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )[0]
    cell = reachability.run_reachability_cell(
        spec,
        state=state,
        path=path,
        observe=lambda: reachability.build_diagnostic_observations(state=state, path=path),
    )
    row = cell.sealed_row()
    row["state_id"] = "S14"
    mutated = seal_payload(row)
    shard = tmp_path / "mutated.jsonl.gz"
    write_gzip_shard(shard, rows=(mutated,), provenance=_provenance())

    with pytest.raises(ValueError, match="coverage"):
        reachability.read_reachability_shard(
            shard,
            expected_specs=(spec,),
            expected_states=(state,),
            expected_paths=(path,),
            expected_provenance=_provenance(),
        )


def test_reachability_readback_rejects_resealed_analysis_mutation(tmp_path: Path) -> None:
    spec = reachability.enumerate_reachability_specs()[0]
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    path = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )[0]
    cell = reachability.run_reachability_cell(
        spec,
        state=state,
        path=path,
        observe=lambda: reachability.build_diagnostic_observations(state=state, path=path),
    )
    row = cell.sealed_row()
    row["analysis"]["healthy_sessions"] += 1
    mutated = seal_payload(row)
    shard = tmp_path / "analysis-mutated.jsonl.gz"
    write_gzip_shard(shard, rows=(mutated,), provenance=_provenance())

    with pytest.raises(ValueError, match="analysis"):
        reachability.read_reachability_shard(
            shard,
            expected_specs=(spec,),
            expected_states=(state,),
            expected_paths=(path,),
            expected_provenance=_provenance(),
        )


def test_causal_transition_paths_materially_diverge_and_use_actual_candidate_facts() -> None:
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    paths = {
        path.path_id: path
        for path in reachability.build_synthetic_paths(
            seed=20260826,
            start="2024-01-02",
            session_count=60,
        )
    }

    trend = reachability.build_diagnostic_observations(state=state, path=paths["P01"])
    decline = reachability.build_diagnostic_observations(state=state, path=paths["P03"])
    locked = reachability.build_diagnostic_observations(state=state, path=paths["P04"])
    witness_missing = reachability.build_diagnostic_observations(state=state, path=paths["P05"])
    assert all(item.candidate_facts is not None for item in (*trend, *locked, *witness_missing))
    trend_facts = tuple(item.candidate_facts for item in trend if item.candidate_facts is not None)
    locked_facts = tuple(item.candidate_facts for item in locked if item.candidate_facts is not None)
    witness_facts = tuple(
        item.candidate_facts for item in witness_missing if item.candidate_facts is not None
    )

    assert trend == reachability.build_diagnostic_observations(state=state, path=paths["P01"])
    assert tuple(item.node.node_id for item in trend) != tuple(item.node.node_id for item in decline)
    assert trend[-1].account_sha256 != decline[-1].account_sha256
    assert any(facts.eligible for facts in trend_facts)
    assert not all(facts.eligible for facts in witness_facts)
    assert any(facts.market_wide_execution_block for facts in locked_facts)
    assert all(facts.visible_through == item.date for facts, item in zip(trend_facts, trend, strict=True))


def test_causal_transition_rejects_path_that_does_not_start_after_checkpoint() -> None:
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    path = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )[0]

    with pytest.raises(ValueError, match="after checkpoint"):
        reachability.build_diagnostic_observations(
            state=replace(state, date=path.bars[0].date),
            path=path,
        )


def test_causal_transition_rejects_caller_state_label_spoofing() -> None:
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    path = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )[0]

    with pytest.raises(ValueError, match="label differs"):
        reachability.build_diagnostic_observations(
            state=replace(state, state_id="S02"),
            path=path,
        )


def test_literal_metrics_come_from_chronological_transitions() -> None:
    states = {
        item.state_id: item
        for item in reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)
    }
    paths = {
        path.path_id: path
        for path in reachability.build_synthetic_paths(
            seed=20260826,
            start="2024-01-02",
            session_count=80,
        )
    }

    repaired = reachability.analyze_observations(
        reachability.build_diagnostic_observations(state=states["S07"], path=paths["P02"])
    )
    repeated = reachability.analyze_observations(
        reachability.build_diagnostic_observations(state=states["S01"], path=paths["P06"])
    )

    assert repaired.metrics.budget_repair_healthy_sessions["2_to_1"] is not None
    assert repaired.nodes
    repaired_rows = reachability.build_diagnostic_observations(
        state=states["S07"], path=paths["P02"]
    )
    assert repaired_rows[-1].node.positive_target is True
    assert repaired.metrics.failed_grant_retry_healthy_sessions is None
    assert repaired.metrics.failed_grant_retry_trace == ()
    assert repaired.metrics.longest_healthy_zero_target_streak == 2
    assert repaired.metrics.terminal_scc_healthy_zero_target_duration == 0
    assert 0.0 <= repaired.metrics.witness_missing_recovery_fraction <= 1.0
    assert repeated.repeated_crowning.transitions == tuple(
        sorted(repeated.repeated_crowning.transitions)
    )
    assert repeated.findings[6].observation_id == "R7"
    assert repeated.findings[7].observation_id == "R8"
    assert repeated.findings[7].evidence["ordered_transitions"] == [
        list(item) for item in repeated.repeated_crowning.transitions
    ]


def test_readback_recomputes_expected_input_bindings_and_sources(tmp_path: Path) -> None:
    spec = reachability.enumerate_reachability_specs()[0]
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    path = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )[0]
    cell = reachability.run_reachability_cell(
        spec,
        state=state,
        path=path,
        observe=lambda: reachability.build_diagnostic_observations(state=state, path=path),
    )
    for name in ("binding", "source"):
        row = cell.sealed_row()
        if name == "binding":
            row["input_bindings"]["state_account_sha256"] = "f" * 64
            scenario_bindings = dict(row["input_bindings"])
            scenario_bindings.pop("cell_scenario_sha256")
            row["input_bindings"]["cell_scenario_sha256"] = canonical_sha256(
                {
                    "cell_id": row["cell_id"],
                    "state_id": row["state_id"],
                    "path_id": row["path_id"],
                    "input_bindings": scenario_bindings,
                }
            )
        else:
            row["state_source"] = "BOGUS"
        shard = tmp_path / f"mutated-{name}.jsonl.gz"
        write_gzip_shard(shard, rows=(seal_payload(row),), provenance=_provenance())

        with pytest.raises(ValueError, match=r"input bindings|source"):
            reachability.read_reachability_shard(
                shard,
                expected_specs=(spec,),
                expected_states=(state,),
                expected_paths=(path,),
                expected_provenance=_provenance(),
            )


def _one_successful_cell() -> tuple[
    reachability.ReachabilityCellSpec,
    reachability.ReachabilityState,
    reachability.SyntheticPath,
    reachability.ReachabilityCellResult,
]:
    spec = reachability.enumerate_reachability_specs()[0]
    state = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)[0]
    path = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )[0]
    cell = reachability.run_reachability_cell(
        spec,
        state=state,
        path=path,
        observe=lambda: reachability.build_diagnostic_observations(state=state, path=path),
    )
    assert cell.status == "SUCCESS"
    return spec, state, path, cell


def _write_mutated_cell(tmp_path: Path, name: str, row: dict[str, Any]) -> Path:
    shard = tmp_path / f"semantic-{name}.jsonl.gz"
    write_gzip_shard(shard, rows=(seal_payload(row),), provenance=_provenance())
    return shard


def test_readback_rejects_resealed_terminal_status_error_and_sample_mutations(
    tmp_path: Path,
) -> None:
    spec, state, path, cell = _one_successful_cell()
    mutations = (
        (
            "insufficient-count",
            {
                "status": "INSUFFICIENT_SAMPLE",
                "observation_count": 1,
                "analysis": None,
                "analysis_sha256": None,
                "error": {
                    "type": "InsufficientSample",
                    "message": "reachability cell has no observations",
                    "stage": "ANALYSIS",
                },
            },
        ),
        (
            "changed-error",
            {
                "status": "INSUFFICIENT_SAMPLE",
                "observation_count": 0,
                "analysis": None,
                "analysis_sha256": None,
                "error": {"type": "KeyError", "message": "'changed'", "stage": "TRANSITION"},
            },
        ),
        (
            "replay-status",
            {
                "status": "REPLAY_ERROR",
                "observation_count": 0,
                "analysis": None,
                "analysis_sha256": None,
                "error": {"type": "KeyError", "message": "'changed'", "stage": "TRANSITION"},
            },
        ),
    )
    for name, changes in mutations:
        row = cell.sealed_row()
        row.update(changes)
        shard = _write_mutated_cell(tmp_path, name, row)

        with pytest.raises(ValueError, match="deterministic result"):
            reachability.read_reachability_shard(
                shard,
                expected_specs=(spec,),
                expected_states=(state,),
                expected_paths=(path,),
                expected_provenance=_provenance(),
            )


def test_readback_rejects_resealed_literal_metric_and_r1_mutations(tmp_path: Path) -> None:
    spec, state, path, cell = _one_successful_cell()
    for name in ("metric", "r1"):
        row = cell.sealed_row()
        if name == "metric":
            row["analysis"]["metrics"]["longest_healthy_zero_target_streak"] += 1
        else:
            row["analysis"]["findings"][0]["observed"] = not row["analysis"]["findings"][0][
                "observed"
            ]
        row["analysis_sha256"] = canonical_sha256(row["analysis"])
        shard = _write_mutated_cell(tmp_path, name, row)

        with pytest.raises(ValueError, match="deterministic result"):
            reachability.read_reachability_shard(
                shard,
                expected_specs=(spec,),
                expected_states=(state,),
                expected_paths=(path,),
                expected_provenance=_provenance(),
            )


def test_readback_rejects_internally_inconsistent_expected_state(tmp_path: Path) -> None:
    spec, state, path, cell = _one_successful_cell()
    inconsistent = replace(state, account_sha256="f" * 64)
    row = cell.sealed_row()
    row["input_bindings"]["state_account_sha256"] = "f" * 64
    scenario_bindings = dict(row["input_bindings"])
    scenario_bindings.pop("cell_scenario_sha256")
    row["input_bindings"]["cell_scenario_sha256"] = canonical_sha256(
        {
            "cell_id": row["cell_id"],
            "state_id": row["state_id"],
            "path_id": row["path_id"],
            "input_bindings": scenario_bindings,
        }
    )
    shard = _write_mutated_cell(tmp_path, "invalid-expected-state", row)

    with pytest.raises(ValueError, match="expected state account identity"):
        reachability.read_reachability_shard(
            shard,
            expected_specs=(spec,),
            expected_states=(inconsistent,),
            expected_paths=(path,),
            expected_provenance=_provenance(),
        )


def test_reachability_runner_module_is_available_for_resumable_matrix() -> None:
    assert importlib.util.find_spec("research.strategic_evidence.reachability_runner") is not None


def test_runner_selects_and_executes_one_diagnostic_cell_with_source_manifest() -> None:
    root = reachability_runner.repository_root()
    manifest = reachability_runner.build_executable_source_manifest(root, require_clean=False)
    specs = reachability_runner.select_specs(state_ids=("S07",), path_ids=("P01",))
    states = reachability.build_initial_states(checkpoints=(), initial_cash=1_000_000.0)
    paths = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=60,
    )

    cells = reachability_runner.run_matrix(states=states, paths=paths, specs=specs)

    assert len(cells) == 1
    assert cells[0].status == "SUCCESS"
    assert cells[0].spec.state_id == "S07"
    assert cells[0].spec.path_id == "P01"
    assert "research/candidate_runner.py" in manifest["files"]
    assert "research/strategic_evidence/reachability.py" in manifest["files"]
    assert "research/strategic_evidence/reachability_runner.py" in manifest["files"]
    assert len(manifest["manifest_sha256"]) == 64
    contract = load_contract(root / "benchmarks/strategic_evidence_closure_contract.json")
    identities = reachability_runner.recompute_reachability_identities(
        root,
        contract=contract,
        research_source_sha256=manifest["manifest_sha256"],
    )
    assert identities["research_source_sha256"] == manifest["manifest_sha256"]
    task4_identities = recompute_task4_identities(root, contract=contract)
    assert {
        field: value
        for field, value in identities.items()
        if field != "research_source_sha256"
    } == {
        field: value
        for field, value in task4_identities.items()
        if field != "research_source_sha256"
    }


def test_runner_rebases_historical_path_to_first_session_after_checkpoint(
    tmp_path: Path,
) -> None:
    account = AccountState.empty(1_000_000.0)
    account.opportunity = "TREND"
    account.capital_budget_level = 1
    account.candidate_tenure["strategic_candidate_streak"] = 3
    account.candidate_tenure["strategic_long_cycle_open"] = 1
    checkpoint = reachability.extract_historical_checkpoints(
        (
            {
                "state_id": "S06",
                "date": "2025-01-06",
                "account": account.to_dict(),
                "provenance": {"source": "HISTORICAL", "checkpoint_id": "S06-live"},
            },
        )
    )
    state = {
        item.state_id: item
        for item in reachability.build_initial_states(
            checkpoints=checkpoint,
            initial_cash=1_000_000.0,
        )
    }["S06"]
    base_path = reachability.build_synthetic_paths(
        seed=20260826,
        start="2024-01-02",
        session_count=8,
    )[0]
    spec = reachability_runner.select_specs(state_ids=("S06",), path_ids=("P01",))[0]

    effective_path = reachability.path_after_checkpoint(state=state, path=base_path)
    cells = reachability_runner.run_matrix(states=(state,), paths=(base_path,), specs=(spec,))

    assert effective_path.bars[0].date == "2025-01-07"
    assert effective_path.provenance["checkpoint_date"] == "2025-01-06"
    assert cells[0].status == "SUCCESS"
    inverse = reachability.run_reachability_cell(
        spec,
        state=state,
        path=base_path,
        observe=lambda: reachability.build_diagnostic_observations(
            state=state,
            path=base_path,
        ),
    )
    assert inverse.status == "REPLAY_ERROR"
    shard = tmp_path / "inverse-historical-path.jsonl.gz"
    write_gzip_shard(shard, rows=(inverse.sealed_row(),), provenance=_provenance())
    with pytest.raises(ValueError, match="input bindings"):
        reachability.read_reachability_shard(
            shard,
            expected_specs=(spec,),
            expected_states=(state,),
            expected_paths=(base_path,),
            expected_provenance=_provenance(),
        )
