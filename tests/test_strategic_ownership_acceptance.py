from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import scripts.run_strategic_ownership_acceptance as ownership_runner
from research.strategic_evidence.replay import ReplayRequest, ReplayResult
from research.strategic_evidence.trace import RouteTraceRow
from scripts.run_strategic_ownership_acceptance import (
    SCENARIO_NAMES,
    SHARD_NAMES,
    actual_epoch_facts,
    load_contract,
    run_acceptance_shard,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]


def _row(
    session: str,
    *,
    grant_id: str = "",
    epoch_id: str = "",
    owner: str = "",
    fill: bool = False,
) -> RouteTraceRow:
    target = (
        {
            "authorization_id": "",
            "epoch_id": epoch_id,
            "grant_id": grant_id,
            "lifecycle": "CORE",
            "mechanism": "STRATEGIC_COHORT",
            "origin_subsystem": "STRATEGIC",
            "symbol": owner,
            "weight": 0.20,
        },
    ) if grant_id else ()
    order = (
        {
            "epoch_id": epoch_id,
            "grant_id": grant_id,
            "order_id": "O000000001",
            "side": "BUY",
            "symbol": owner,
            "target_weight": 0.20,
        },
    ) if grant_id else ()
    fills = (
        {
            "epoch_id": epoch_id,
            "fill_date": session,
            "grant_id": grant_id,
            "order_id": "O000000001",
            "shares": 100,
            "side": "BUY",
            "symbol": owner,
        },
    ) if fill else ()
    strategic_risk = (
        {
            "strategic_grant": {
                "authorization_id": "",
                "grant_id": grant_id,
                "previous_grant_id": "",
            },
            "strategic_qualification": {
                "candidate_symbol": owner,
                "qualification_ready": True,
                "qualification_signature": "",
            },
        }
        if grant_id
        else {}
    )
    return RouteTraceRow(
        date=session,
        reference_context={},
        leaders=(),
        risk={"state": "NORMAL", **strategic_risk},
        opportunity="TREND",
        targets=target,
        orders=order,
        fills=fills,
        account_sha256="a" * 64,
        equity=2_000_000.0,
        target_gross=0.20 if grant_id else 0.0,
        intervention_provenance=None,
        cash=2_000_000.0,
        position_shares={},
        close_marks={},
    )


def _result(*, filled: bool = True) -> ReplayResult:
    grant_id = "grant_" + "a" * 64
    epoch_id = "epoch_" + "b" * 64
    owner = "sz300308"
    epoch = {
        "active_session": "2026-01-06" if filled else "",
        "closed_session": "",
        "epoch_id": epoch_id,
        "first_fill_session": "2026-01-06" if filled else "",
        "grant_id": grant_id,
        "opened_session": "2026-01-05",
        "owner_symbol": owner,
        "previous_epoch_id": "",
        "realized_status": "ACTIVE" if filled else "PROBE",
    }
    return ReplayResult(
        request=ReplayRequest(
            symbols=(owner,),
            start="2026-01-05",
            end="2026-01-06",
        ),
        metrics={"final_equity": 2_000_000.0, "max_drawdown": 0.0},
        trace=(
            _row("2026-01-05", grant_id=grant_id, epoch_id=epoch_id, owner=owner),
            _row(
                "2026-01-06",
                grant_id=grant_id,
                epoch_id=epoch_id,
                owner=owner,
                fill=filled,
            ),
        ),
        final_account={
            "fills": list(_row(
                "2026-01-06",
                grant_id=grant_id,
                epoch_id=epoch_id,
                owner=owner,
                fill=filled,
            ).fills),
            "initial_cash": 2_000_000.0,
            "order_ledger": [{"order_id": "O000000001"}],
            "strategic_epochs": [epoch],
        },
        intervention_provenance=None,
    )


def test_ownership_contract_is_the_exact_bounded_shard_set() -> None:
    contract = load_contract()

    validate_contract(contract)

    assert SHARD_NAMES == (
        "champion",
        "critical",
        "ghost-a",
        "ghost-b",
        "continuity",
    )
    assert set(contract["shards"]) == set(SHARD_NAMES)
    scenario_ids = {
        item["scenario_id"]
        for items in contract["shards"].values()
        for item in items
    }
    assert set(SCENARIO_NAMES) == scenario_ids
    assert scenario_ids == {
        "champion-5",
        "report-13",
        "remove-sz300308",
        "remove-sz300502",
        "remove-sz300394",
        "remove-sh603688",
        "remove-sh688008",
        "remove-sh688082",
        "remove-sz002409",
        "remove-sz300666",
        "same-industry-crowning",
        "cross-industry-crowning",
        "failed-first-grant",
    }
    encoded = json.dumps(contract, sort_keys=True).lower()
    assert "234" not in encoded
    assert "extended performance" not in encoded
    assert "extended economic" not in encoded
    assert "generalization acceptance" not in encoded


def test_ownership_workflow_is_bounded_cached_and_blocking() -> None:
    path = ROOT / ".github" / "workflows" / "strategic-ownership-acceptance.yml"
    workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert workflow["name"] == "Strategic Ownership Acceptance"
    assert workflow["on"] == {
        "pull_request": "",
        "push": {"branches": ["main"]},
        "workflow_dispatch": {},
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["UV_VERSION"] == "0.11.33"
    shard = workflow["jobs"]["ownership-shard"]
    assert shard["strategy"]["fail-fast"] == "false"
    assert tuple(shard["strategy"]["matrix"]["shard"]) == SHARD_NAMES
    rendered = str(workflow).lower()
    assert "actions/cache@" in rendered
    assert "scripts/run_strategic_ownership_acceptance.py" in rendered
    assert "--scenario" not in rendered
    assert "strategic ownership acceptance" in rendered
    assert "234" not in rendered
    assert "extended performance" not in rendered
    assert "extended economic" not in rendered
    assert "generalization-matrix" not in rendered
    aggregate = workflow["jobs"]["strategic-ownership-acceptance"]
    assert aggregate["name"] == "Strategic Ownership Acceptance"
    assert aggregate["if"] == "${{ always() }}"
    assert set(aggregate["needs"]) == {"ownership-tests", "ownership-shard"}


def test_actual_epoch_facts_require_target_order_and_matching_real_fill() -> None:
    from test_cross_ai_ownership_continuity import continuity_replay

    facts = actual_epoch_facts(continuity_replay(owners=("sz300308",)))

    assert len(facts) == 1
    assert facts[0]["owner_symbol"] == "sz300308"
    assert facts[0]["target_session"] == "2026-01-05"
    assert facts[0]["order_session"] == "2026-01-05"
    assert facts[0]["fill_session"] == "2026-01-06"
    assert facts[0]["active_session"] == "2026-01-06"
    assert facts[0]["previous_grant_id"] == ""


def test_probe_ledger_without_fill_is_not_an_actual_epoch() -> None:
    assert actual_epoch_facts(_result(filled=False)) == []


def test_actual_epoch_facts_reject_duplicate_epoch_identity() -> None:
    result = _result()
    duplicate = dict(result.final_account["strategic_epochs"][0])  # type: ignore[index]
    broken = replace(
        result,
        final_account={**result.final_account, "strategic_epochs": [duplicate, duplicate]},
    )

    with pytest.raises(ValueError, match="duplicate strategic epoch"):
        actual_epoch_facts(broken)


def test_single_scenario_is_diagnostic_and_reuses_only_complete_identity_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def execute(
        _contract: object,
        *,
        spec: object,
    ) -> dict[str, object]:
        assert isinstance(spec, dict)
        scenario_id = str(spec["scenario_id"])
        calls.append(scenario_id)
        return {"scenario_id": scenario_id, "status": "PASS"}

    monkeypatch.setattr(ownership_runner, "_execute_scenario", execute)
    output = tmp_path / "scenario.json"
    cache = tmp_path / "cache"

    result = run_acceptance_shard(
        shard="critical",
        scenario="remove-sz300394",
        output=output,
        cache_dir=cache,
    )

    assert calls == ["remove-sz300394"]
    assert result["authoritative_acceptance"] is False
    assert result["diagnostic_only"] is True
    assert result["selected_scenario"] == "remove-sz300394"
    assert result["cache_hit"] is False
    assert result["cache_dependencies"] == {}
    assert [row["scenario_id"] for row in result["scenarios"]] == [
        "remove-sz300394"
    ]
    identity = result["cache_identity_payload"]
    assert isinstance(identity, dict)
    assert set(identity) == {
        "config_sha256",
        "frozen_data",
        "full_package_source_sha256",
        "grant_contract_sha256",
        "grant_runner_source_sha256",
        "ownership_contract_sha256",
        "production_source_sha256",
        "runner_source_sha256",
        "runtime",
        "scenario",
        "schema_version",
        "source_surface_registry_sha256",
        "validation_runner_source_sha256",
    }
    assert identity["scenario"]["scenario_id"] == "remove-sz300394"

    monkeypatch.setattr(
        ownership_runner,
        "_execute_scenario",
        lambda *_args, **_kwargs: pytest.fail("complete-identity cache was not reused"),
    )
    cached = run_acceptance_shard(
        shard="critical",
        scenario="remove-sz300394",
        output=output,
        cache_dir=cache,
    )
    assert cached["cache_hit"] is True


def test_single_alias_scenario_runs_only_its_contract_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_cross_ai_ownership_continuity import continuity_replay

    assert "same-industry-crowning" in SCENARIO_NAMES
    calls: list[str] = []

    def execute(
        _contract: object,
        *,
        spec: object,
    ) -> dict[str, object]:
        assert isinstance(spec, dict)
        scenario_id = str(spec["scenario_id"])
        calls.append(scenario_id)
        return ownership_runner._continuity_summary(load_contract(), continuity_replay())

    monkeypatch.setattr(ownership_runner, "_execute_scenario", execute)
    result = run_acceptance_shard(
        shard="continuity",
        scenario="same-industry-crowning",
        output=tmp_path / "alias.json",
        cache_dir=tmp_path / "cache",
    )

    assert calls == ["remove-sz300502"]
    assert [row["scenario_id"] for row in result["scenarios"]] == [
        "same-industry-crowning"
    ]
    assert set(result["cache_dependencies"]) == {"remove-sz300502"}


@pytest.mark.parametrize("scenario", ("remove-sz300502", "same-industry-crowning"))
@pytest.mark.parametrize("status", ("REPLAY_ERROR", "INSUFFICIENT_SAMPLE"))
def test_failed_ownership_replay_preserves_raw_without_cache_or_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, status: str,
) -> None:
    from dataclasses import asdict, replace

    from test_cross_ai_ownership_continuity import continuity_replay

    replay = replace(
        continuity_replay(), status=status,
        error="pending order event_id differs from canonical derivation",
    )
    monkeypatch.setattr(ownership_runner, "_frozen_replay", lambda *args, **kwargs: replay)
    monkeypatch.setattr(ownership_runner, "_cache_identity_context", lambda contract: {"test": "failed raw"})
    output = tmp_path / "evidence" / "failed.json"
    cache = tmp_path / "cache"

    with pytest.raises(RuntimeError, match="pending order event_id differs from canonical derivation"):
        run_acceptance_shard(shard="continuity", scenario=scenario, output=output, cache_dir=cache)

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["status"] == "FAIL"
    assert evidence["authoritative_acceptance"] is False
    assert evidence["cache_hit"] is False
    assert evidence["selected_scenario"] == scenario
    assert len(evidence["scenarios"]) == 1
    failure = evidence["scenarios"][0]
    assert failure["scenario_id"] == "remove-sz300502"
    assert failure["status"] == "FAIL"
    assert failure["replay_status"] == status
    assert failure["error"] == replay.error
    assert ownership_runner._canonical_sha256(failure["raw_replay"]) == ownership_runner._canonical_sha256(asdict(replay))
    assert failure["raw_replay_sha256"] == ownership_runner._canonical_sha256(asdict(replay))
    assert "same_industry_witness" not in failure
    assert list(cache.iterdir()) == []


@pytest.mark.parametrize("status, exit_code", [("PASS", 0), ("FAIL", 1)])
def test_ownership_cli_dispatches_one_diagnostic_scenario(
    status: str, exit_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        ownership_runner,
        "run_acceptance_shard",
        lambda **options: (observed.update(options), {"status": status})[1],
    )
    output = tmp_path / "ownership.json"
    cache = tmp_path / "cache"

    assert ownership_runner.main(
        [
            "--shard",
            "critical",
            "--scenario",
            "remove-sz300394",
            "--output",
            str(output),
            "--cache-dir",
            str(cache),
        ]
    ) == exit_code
    assert observed == {
        "cache_dir": cache,
        "output": output,
        "scenario": "remove-sz300394",
        "shard": "critical",
    }


def _historical_champion_raw():
    """Immutable historical raw tests adapters, never current candidate economics."""
    import gzip

    return json.loads(gzip.decompress((ROOT / 'tests/fixtures/absolute_champion_runtime_raw.json.gz').read_bytes()))


def test_champion_adapter_reconstructs_raw_and_records_actual_source() -> None:
    raw = _historical_champion_raw()
    result = ownership_runner._champion_evidence(
        load_contract(), raw=raw, scenario_id='champion-5',
        expected_source=raw['final_account']['code_hash'],
    )
    assert result['status'] == 'PASS'
    assert result['acceptance_basis']['mode'] == 'current_candidate'
    assert result['acceptance_basis']['production_source_sha256'] == raw['final_account']['code_hash']
    assert result['metrics']['final_wealth'] == 24.509661802900865
    assert result['raw_replay'] == raw
    assert result['violations'] == []


@pytest.mark.parametrize('mutation', ['source', 'duplicate_fill', 'no_fill', 'missing_raw'])
def test_champion_adapter_retains_rejected_raw(mutation: str) -> None:
    raw = _historical_champion_raw()
    source = raw['final_account']['code_hash']
    if mutation == 'source':
        source = 'f' * 64
    elif mutation == 'duplicate_fill':
        raw['final_account']['fills'].append(raw['final_account']['fills'][0].copy())
    elif mutation == 'no_fill':
        raw['final_account']['fills'] = []
    else:
        raw = {}
    result = ownership_runner._champion_evidence(
        load_contract(), raw=raw, scenario_id='champion-5', expected_source=source,
    )
    assert result['status'] == 'FAIL'
    assert result['violations']
    assert result['raw_replay'] == raw


def test_champion_adapter_preserves_ownership_absolute_limits() -> None:
    raw = _historical_champion_raw()
    contract = load_contract()
    contract['champion']['minimum_final_wealth'] = 25.0
    contract['thresholds']['maximum_drawdown'] = 0.25
    result = ownership_runner._champion_evidence(
        contract, raw=raw, scenario_id='champion-5',
        expected_source=raw['final_account']['code_hash'],
    )
    assert result['status'] == 'FAIL'
    assert result['violations'] == [
        'champion preservation wealth differs', 'champion preservation drawdown differs',
    ]
    assert result['raw_replay'] == raw


def test_champion_cache_cannot_accept_summary_without_raw(tmp_path: Path) -> None:
    path = tmp_path / 'champion.json'
    ownership_runner._write_cache(path, identity='test-only', payload={
        'scenario_id': 'champion-5', 'status': 'PASS',
        'acceptance_basis': {'mode': 'current_candidate'},
        'metrics': {'final_wealth': 25.0},
    })
    assert ownership_runner._read_cache(path, identity='test-only') is None


@pytest.mark.parametrize("scenario", ("remove-sz300308", "remove-sz300502"))
@pytest.mark.parametrize("stage", ("actual_epoch_facts", "_validate_full_removal"))
def test_successful_replay_strict_rejection_preserves_raw_and_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, stage: str,
) -> None:
    from dataclasses import asdict

    from test_cross_ai_ownership_continuity import continuity_replay

    replay = continuity_replay()
    error = ValueError("filled epoch has non-realized status")

    def reject(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(ownership_runner, "_frozen_replay", lambda *args, **kwargs: replay)
    monkeypatch.setattr(ownership_runner, stage, reject)
    monkeypatch.setattr(ownership_runner, "_cache_identity_context", lambda contract: {"test": "strict raw"})
    output, cache = tmp_path / "failed.json", tmp_path / "cache"
    with pytest.raises(ValueError) as observed:
        run_acceptance_shard(
            shard="critical" if scenario == "remove-sz300308" else "continuity",
            scenario=scenario, output=output, cache_dir=cache,
        )
    assert observed.value is error
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "FAIL"
    assert evidence["authoritative_acceptance"] is False
    assert evidence["cache_hit"] is False
    failure = evidence["scenarios"][-1]
    assert failure["scenario_id"] == scenario
    assert failure["status"] == "FAIL"
    assert failure["replay_status"] == "SUCCESS"
    assert failure["replay_error"] is None
    assert failure["error_type"] == "ValueError"
    assert failure["error"] == str(error)
    assert failure["raw_replay_sha256"] == ownership_runner._canonical_sha256(asdict(replay))
    assert ownership_runner._canonical_sha256(failure["raw_replay"]) == failure["raw_replay_sha256"]
    assert list(cache.iterdir()) == []


@pytest.mark.parametrize("cached_source", (False, True))
def test_missing_same_industry_witness_persists_source_raw_without_alias_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached_source: bool,
) -> None:
    from dataclasses import asdict

    from test_cross_ai_ownership_continuity import continuity_replay

    replay = continuity_replay(owners=("sz300308", "sh688008"))
    monkeypatch.setattr(ownership_runner, "_frozen_replay", lambda *args, **kwargs: replay)
    monkeypatch.setattr(ownership_runner, "_cache_identity_context", lambda contract: {"test": "alias raw"})
    output, cache = tmp_path / "failed.json", tmp_path / "cache"
    if cached_source:
        run_acceptance_shard(
            shard="continuity", scenario="remove-sz300502", output=output, cache_dir=cache,
        )
        monkeypatch.setattr(ownership_runner, "_frozen_replay", lambda *args, **kwargs: pytest.fail("source replayed"))
    with pytest.raises(RuntimeError, match="no adjacent real same-industry successor"):
        run_acceptance_shard(
            shard="continuity", scenario="same-industry-crowning", output=output, cache_dir=cache,
        )
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "FAIL"
    assert evidence["authoritative_acceptance"] is False
    failure = evidence["scenarios"][-1]
    assert failure["scenario_id"] == "same-industry-crowning"
    assert failure["status"] == "FAIL"
    assert failure["replay_status"] == "SUCCESS"
    assert failure["error_type"] == "RuntimeError"
    assert failure["raw_replay_sha256"] == ownership_runner._canonical_sha256(asdict(replay))
    assert ownership_runner._canonical_sha256(failure["raw_replay"]) == failure["raw_replay_sha256"]
    assert "same_industry_witness" not in failure
    entries = list(cache.iterdir())
    assert len(entries) == 1
    assert entries[0].name.startswith("remove-sz300502-")
    assert not list(cache.glob("same-industry-crowning-*"))


@pytest.mark.parametrize("scenario", ("report-13", "cross-industry-crowning", "failed-first-grant"))
def test_fixture_and_report_post_replay_failures_retain_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str,
) -> None:
    from dataclasses import asdict

    from test_cross_ai_ownership_continuity import continuity_replay

    replay = continuity_replay()
    error = RuntimeError("strict post-replay rejection")

    def reject(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(ownership_runner, "_cache_identity_context", lambda contract: {"test": "fixture raw"})
    monkeypatch.setattr(ownership_runner, "_frozen_replay", lambda *args, **kwargs: replay)
    monkeypatch.setattr(ownership_runner, "run_replay", lambda *args, **kwargs: replay)
    monkeypatch.setattr(ownership_runner, "_cross_industry_fixture", lambda root: None)
    monkeypatch.setattr(ownership_runner, "_failed_grant_fixture", lambda root: None)
    monkeypatch.setattr(ownership_runner, "_failed_grant_replay", lambda root: (replay, []))
    monkeypatch.setattr(ownership_runner, "_require_economic_thresholds", reject)
    output, cache = tmp_path / "failed.json", tmp_path / "cache"
    with pytest.raises(RuntimeError) as observed:
        run_acceptance_shard(
            shard="champion" if scenario == "report-13" else "continuity",
            scenario=scenario, output=output, cache_dir=cache,
        )
    if scenario == "failed-first-grant":
        assert "exactly two economic grants" in str(observed.value)
    else:
        assert observed.value is error
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "FAIL"
    assert evidence["authoritative_acceptance"] is False
    failure = evidence["scenarios"][-1]
    assert failure["scenario_id"] == scenario
    assert failure["replay_status"] == "SUCCESS"
    assert failure["error"] == str(observed.value)
    assert failure["raw_replay_sha256"] == ownership_runner._canonical_sha256(asdict(replay))
    assert ownership_runner._canonical_sha256(failure["raw_replay"]) == failure["raw_replay_sha256"]
    assert list(cache.iterdir()) == []
