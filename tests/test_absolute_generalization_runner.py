from __future__ import annotations

import copy
import inspect
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from _absolute_generalization_acceptance_fixture import (
    _cell_raw,
    reseal_manifest,
    successful_manifests,
)
from _absolute_generalization_metrics_fixture import complete_replay, payload

import scripts.run_absolute_generalization_acceptance as runner_module
from scripts.run_absolute_generalization_acceptance import (
    CANONICAL_SHARDS,
    build_loo_shard_manifest,
    cache_path_for,
    parse_cli,
    read_cached_cell,
    run,
    selected_scenarios,
    write_cached_cell,
)
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.validation.absolute_generalization import (
    build_leave_one_out_scenarios,
    load_absolute_generalization_contract,
    validate_cell_artifact,
)
from uquant.validation.absolute_generalization.runtime import (
    derive_runtime_cell_artifact,
)

ROOT = Path(__file__).resolve().parents[1]


def _execution_args(*extra: str, shard: str = "loo-a") -> list[str]:
    return [
        "--shard",
        shard,
        "--run-id",
        "run-123",
        "--run-attempt",
        "2",
        "--output",
        "out.json",
        "--cache-dir",
        "cache",
        "--data-dir",
        "data/frozen",
        *extra,
    ]


def _final_args(root: Path, output: Path, upstream: str = "success") -> list[str]:
    return [
        "--shard",
        "final",
        "--run-id",
        "transport-run",
        "--run-attempt",
        "3",
        "--output",
        str(output),
        "--shard-root",
        str(root),
        "--artifact-prefix",
        "absolute-generalization",
        "--upstream-result",
        upstream,
    ]


def test_cli_exposes_only_the_eight_canonical_shards_and_final() -> None:
    assert CANONICAL_SHARDS == (
        "champion",
        "loo-a",
        "loo-b",
        "loo-c",
        "loo-d",
        "loo-e",
        "loo-f",
        "recovery-and-reachability",
    )
    assert parse_cli(_execution_args()).shard == "loo-a"
    with pytest.raises(SystemExit):
        parse_cli([*_execution_args(), "--shard", "loo-z"])


def test_canonical_loo_selection_is_exactly_contract_ordered() -> None:
    contract = load_absolute_generalization_contract()
    expected = {
        name: tuple(symbols) for name, symbols in contract.shards
    }

    for shard, symbols in expected.items():
        options = parse_cli(
            _execution_args(shard=shard)
        )
        scenarios = selected_scenarios(options, contract)
        assert tuple(item.removed_symbol for item in scenarios) == symbols
        assert tuple(item.shard for item in scenarios) == (shard,) * len(symbols)

    assert tuple(
        item.removed_symbol for item in build_leave_one_out_scenarios(contract)
    ) == tuple(sorted(symbol for symbols in expected.values() for symbol in symbols))


def test_symbol_selects_only_its_fixed_loo_shard_in_targeted_mode() -> None:
    contract = load_absolute_generalization_contract()
    options = parse_cli(_execution_args("--symbol", "sz300394"))

    scenarios = selected_scenarios(options, contract)

    assert options.mode == "targeted"
    assert tuple(item.removed_symbol for item in scenarios) == ("sz300394",)


@pytest.mark.parametrize(
    "arguments",
    (
        _execution_args("--symbol=sz300394", "--symbol=sz300394"),
        _execution_args("--symbol", "sz300394", "--symbol=sz300394"),
    ),
)
def test_parse_cli_rejects_duplicate_equals_form_symbol(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit):
        parse_cli(arguments)


@pytest.mark.parametrize(
    "arguments",
    (
        [*_execution_args(), "--shard=loo-a"],
        [*_execution_args(), "--run-id=run-123"],
        [*_execution_args(), "--run-attempt=2"],
        [*_execution_args(), "--output=out.json"],
        [*_execution_args(), "--cache-dir=cache"],
        [*_execution_args(), "--data-dir=data/frozen"],
        [*_final_args(Path("artifacts"), Path("out.json")), "--shard-root=artifacts"],
        [
            *_final_args(Path("artifacts"), Path("out.json")),
            "--artifact-prefix=absolute-generalization",
        ],
        [*_final_args(Path("artifacts"), Path("out.json")), "--upstream-result=success"],
    ),
)
def test_parse_cli_rejects_duplicate_single_value_selectors(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit):
        parse_cli(arguments)


@pytest.mark.parametrize(
    "arguments",
    (
        _execution_args("--symbol", "not-canonical"),
        _execution_args("--symbol", "sz300394", shard="loo-b"),
        [*_execution_args(), "--symbol", "sz300394", "--symbol", "sz300394"],
        _execution_args("--symbol", "sz300394", shard="champion"),
        _execution_args(
            "--symbol", "sz300394", shard="recovery-and-reachability"
        ),
        [
            "--shard",
            "final",
            "--symbol",
            "sz300394",
            "--run-id",
            "run-123",
            "--run-attempt",
            "2",
            "--output",
            "out.json",
            "--shard-root",
            "artifacts",
            "--artifact-prefix",
            "absolute-generalization",
            "--upstream-result",
            "success",
        ],
    ),
)
def test_invalid_or_duplicated_symbol_selection_fails_before_replay(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit):
        parse_cli(arguments)


def test_execution_and_final_transport_options_are_mutually_constrained() -> None:
    with pytest.raises(SystemExit):
        parse_cli([*_execution_args(), "--shard-root", "artifacts"])
    with pytest.raises(SystemExit):
        parse_cli(
            [
                "--shard",
                "final",
                "--run-id",
                "run-123",
                "--run-attempt",
                "2",
                "--output",
                "out.json",
                "--cache-dir",
                "cache",
                "--shard-root",
                "artifacts",
                "--artifact-prefix",
                "absolute-generalization",
                "--upstream-result",
                "success",
            ]
        )


@pytest.mark.parametrize(
    ("cache", "data"),
    (("same", "same"), ("root", "root/frozen"), ("root/cache", "root")),
)
def test_execution_rejects_cache_and_frozen_data_alias_or_nesting(
    cache: str, data: str
) -> None:
    args = _execution_args()
    args[args.index("--cache-dir") + 1] = cache
    args[args.index("--data-dir") + 1] = data
    with pytest.raises(SystemExit):
        parse_cli(args)


@pytest.mark.parametrize("kind", ("cache", "data"))
def test_execution_rejects_symlink_ancestry(tmp_path: Path, kind: str) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(physical, target_is_directory=True)
    cache = linked / "cache" if kind == "cache" else tmp_path / "cache"
    data = linked / "data" if kind == "data" else tmp_path / "data"
    args = _execution_args()
    args[args.index("--cache-dir") + 1] = str(cache)
    args[args.index("--data-dir") + 1] = str(data)
    with pytest.raises(SystemExit):
        parse_cli(args)


def test_cli_has_no_contract_or_universe_override_surface() -> None:
    for option in (
        "--threshold",
        "--universe",
        "--contract",
        "--passed",
        "--production-source",
        "--effective-config",
    ):
        with pytest.raises(SystemExit):
            parse_cli([*_execution_args(), option, str(Path("override"))])


def _scenario(symbol: str = "sz300394"):
    contract = load_absolute_generalization_contract()
    return next(
        item
        for item in build_leave_one_out_scenarios(contract)
        if item.removed_symbol == symbol
    )


def test_cache_contains_only_one_identity_bound_cell_artifact(tmp_path: Path) -> None:
    contract = load_absolute_generalization_contract()
    scenario = _scenario()
    artifact = validate_cell_artifact(_cell_raw(scenario), contract)

    path = write_cached_cell(tmp_path, artifact, scenario, contract)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert set(raw) == {"schema_version", "cache_identity", "cell"}
    assert "passed" not in path.read_text(encoding="utf-8")
    assert read_cached_cell(tmp_path, scenario, contract) == artifact


def test_targeted_cache_cell_is_reusable_by_its_exact_canonical_shard(
    tmp_path: Path,
) -> None:
    contract = load_absolute_generalization_contract()
    scenario = _scenario()
    artifact = validate_cell_artifact(_cell_raw(scenario), contract)
    write_cached_cell(tmp_path, artifact, scenario, contract)

    options = parse_cli(_execution_args("--symbol", scenario.removed_symbol))
    assert selected_scenarios(options, contract) == (scenario,)
    assert read_cached_cell(tmp_path, scenario, contract) == artifact


def test_stale_cache_key_is_a_miss_but_malformed_exact_key_fails_closed(
    tmp_path: Path,
) -> None:
    contract = load_absolute_generalization_contract()
    scenario = _scenario()
    artifact = validate_cell_artifact(_cell_raw(scenario), contract)
    exact = write_cached_cell(tmp_path, artifact, scenario, contract)

    stale = cache_path_for(tmp_path, _scenario("sh600487"), contract)
    assert stale != exact
    assert read_cached_cell(tmp_path, _scenario("sh600487"), contract) is None

    exact.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        read_cached_cell(tmp_path, scenario, contract)


@pytest.mark.parametrize(
    "document",
    (
        b'{"schema_version":1,"cache_identity":"x","cell":{"passed":true}}',
        b'{"schema_version":1,"cache_identity":"x","cell":{"value":NaN}}',
        b'{"schema_version":1,"cache_identity":"x","cell":{"value":Infinity}}',
    ),
)
def test_exact_cache_rejects_self_assertion_and_non_finite_json(
    tmp_path: Path, document: bytes
) -> None:
    contract = load_absolute_generalization_contract()
    scenario = _scenario()
    path = cache_path_for(tmp_path, scenario, contract)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document)

    with pytest.raises(ValueError):
        read_cached_cell(tmp_path, scenario, contract)


def test_cache_rejects_symlink_roots_and_symlink_exact_files(tmp_path: Path) -> None:
    contract = load_absolute_generalization_contract()
    scenario = _scenario()
    artifact = validate_cell_artifact(_cell_raw(scenario), contract)
    physical = tmp_path / "physical"
    physical.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(physical, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        write_cached_cell(linked, artifact, scenario, contract)

    exact = cache_path_for(physical, scenario, contract)
    exact.parent.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "cache.json"
    target.write_bytes(canonical_json_bytes({"not": "trusted"}))
    exact.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        read_cached_cell(physical, scenario, contract)


def test_validation_runtime_derives_a_cell_from_raw_replay_without_pass_claims() -> None:
    contract = load_absolute_generalization_contract()
    replay = complete_replay()

    artifact = derive_runtime_cell_artifact(replay, contract)

    assert artifact == validate_cell_artifact(artifact.to_dict(), contract)
    assert artifact.cell_id == replay.scenario.cell_id
    assert artifact.identities.tradable_role_identity == (
        replay.observations[-1].roles.tradable_identity
    )
    assert "passed" not in json.dumps(artifact.to_dict(), sort_keys=True)


def test_loo_manifest_accepts_owned_production_predicate_facts() -> None:
    """The outer transport delegates a cell's owned evidence to cell validation."""

    from uquant.contracts.strict_json import strict_json_loads

    contract = load_absolute_generalization_contract()
    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    decision["risk_summary"]["flat_book_capital_repair"]["predicate_results"] = [
        {
            "authoritative_state": {"positive_position_symbols": []},
            "code": "ALL_CASH",
            "economic_authority": False,
            "orphan_residue": False,
            "passed": True,
        }
    ]
    replay = replace(
        replay,
        observations=(
            replace(first, decision_payload=payload(decision)),
            *replay.observations[1:],
        ),
    )
    artifact = derive_runtime_cell_artifact(replay, contract)
    options = parse_cli(
        _execution_args(
            "--symbol", replay.scenario.removed_symbol, shard=replay.scenario.shard
        )
    )

    raw = build_loo_shard_manifest(options, (artifact,), contract)

    assert raw["status"] == "COMPLETE"
    assert validate_cell_artifact(raw["cells"][0], contract) == artifact  # type: ignore[index]


def test_validation_runtime_rejects_a_replay_without_observed_role_identity() -> None:
    contract = load_absolute_generalization_contract()
    replay = complete_replay()

    with pytest.raises(ValueError, match="observed role identity"):
        derive_runtime_cell_artifact(
            type(replay)(
                scenario=replay.scenario,
                status="REPLAY_ERROR",
                replay_error="fixture failure",
                initial_cash=replay.initial_cash,
                final_equity=replay.final_equity,
                observations=(),
                final_account_payload=replay.final_account_payload,
            ),
            contract,
        )


def test_targeted_loo_output_is_sealed_but_cannot_enter_final() -> None:
    contract = load_absolute_generalization_contract()
    scenario = _scenario()
    artifact = validate_cell_artifact(_cell_raw(scenario), contract)
    options = parse_cli(_execution_args("--symbol", scenario.removed_symbol))

    raw = build_loo_shard_manifest(options, (artifact,), contract)

    assert raw["mode"] == "targeted"
    assert raw["status"] == "COMPLETE"
    assert raw["canonical_sha256"]
    manifests = successful_manifests()
    index = next(i for i, item in enumerate(manifests) if item["shard"] == scenario.shard)
    manifests[index] = raw
    with pytest.raises(ValueError, match="canonical mode"):
        from uquant.validation.absolute_generalization import aggregate_acceptance

        aggregate_acceptance(manifests, contract)


def test_execution_loo_uses_exact_raw_cache_and_writes_sealed_manifest(
    tmp_path: Path,
) -> None:
    contract = load_absolute_generalization_contract()
    cache = tmp_path / "cache"
    output = tmp_path / "manifest.json"
    options = parse_cli(
        [
            *_execution_args(shard="loo-a"),
        ]
    )
    options = type(options)(
        shard=options.shard,
        symbol=options.symbol,
        run_id=options.run_id,
        run_attempt=options.run_attempt,
        output=output,
        cache_dir=cache,
        data_dir=tmp_path / "unused-data",
        shard_root=options.shard_root,
        artifact_prefix=options.artifact_prefix,
        upstream_result=options.upstream_result,
    )
    for scenario in selected_scenarios(options, contract):
        write_cached_cell(
            cache,
            validate_cell_artifact(_cell_raw(scenario), contract),
            scenario,
            contract,
        )

    assert run(options) == 0
    raw = json.loads(output.read_text(encoding="utf-8"))
    assert raw["shard"] == "loo-a"
    assert raw["status"] == "COMPLETE"
    assert raw["canonical_sha256"]
    assert len(raw["cells"]) == 6


def test_runner_has_a_real_cli_entrypoint() -> None:
    source = inspect.getsource(runner_module)
    assert "def main(" in source
    assert 'if __name__ == "__main__":' in source


def _write_final_manifests(root: Path) -> None:
    for original in successful_manifests():
        raw = copy.deepcopy(original)
        raw["run_id"] = "transport-run"
        raw["run_attempt"] = 3
        reseal_manifest(raw)
        artifact = root / (
            f"absolute-generalization-transport-run-attempt-3-{raw['shard']}"
        )
        artifact.mkdir(parents=True)
        (artifact / "manifest.json").write_bytes(canonical_json_bytes(raw))


def test_final_cli_reads_exact_eight_manifests_and_returns_report_conjunction(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shards"
    root.mkdir()
    _write_final_manifests(root)
    output = tmp_path / "report.json"

    code = run(parse_cli(_final_args(root, output)))
    report = json.loads(output.read_text(encoding="utf-8"))

    assert code == 0
    assert report["runner_success"] is True
    assert report["capability_pass"] is True
    assert report["passed"] is True
    assert report["canonical_sha256"]


def test_non_success_upstream_result_is_downgrade_only_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shards"
    root.mkdir()
    _write_final_manifests(root)
    output = tmp_path / "report.json"

    code = run(parse_cli(_final_args(root, output, upstream="failure")))
    report = json.loads(output.read_text(encoding="utf-8"))

    assert code == 1
    assert report["runner_success"] is False
    assert report["capability_pass"] is True
    assert report["passed"] is False
    assert report["runner_failures"] == ["workflow upstream failure: matrix-result=failure"]


@pytest.mark.parametrize(("upstream", "expected"), (("success", 0), ("failure", 1)))
def test_final_real_cli_process_preserves_blocking_exit_and_sealed_report(
    tmp_path: Path, upstream: str, expected: int
) -> None:
    root = tmp_path / "shards"
    root.mkdir()
    _write_final_manifests(root)
    output = tmp_path / "report.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_absolute_generalization_acceptance.py"),
            *_final_args(root, output, upstream=upstream),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == expected
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is (upstream == "success")
    assert report["canonical_sha256"]


def test_execution_real_cli_process_seals_error_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    output = tmp_path / "manifest.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_absolute_generalization_acceptance.py"),
            "--shard",
            "loo-a",
            "--run-id",
            "error-run",
            "--run-attempt",
            "1",
            "--output",
            str(output),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--data-dir",
            str(tmp_path / "missing-frozen-data"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    raw = json.loads(output.read_text(encoding="utf-8"))
    assert raw["status"] == "ERROR"
    assert raw["run_id"] == "error-run"
    assert raw["canonical_sha256"]


@pytest.mark.parametrize(
    "mutation", ("missing", "unexpected", "targeted", "tamper", "run-id", "attempt")
)
def test_final_cli_fails_closed_on_artifact_set_or_manifest_mutation(
    tmp_path: Path, mutation: str
) -> None:
    root = tmp_path / "shards"
    root.mkdir()
    _write_final_manifests(root)
    output = tmp_path / "report.json"
    loo_a = root / "absolute-generalization-transport-run-attempt-3-loo-a"
    if mutation == "missing":
        (loo_a / "manifest.json").rename(tmp_path / "missing.json")
    elif mutation == "unexpected":
        (root / "absolute-generalization-transport-run-attempt-3-loo-z").mkdir()
    else:
        raw = json.loads((loo_a / "manifest.json").read_text(encoding="utf-8"))
        if mutation == "targeted":
            raw["mode"] = "targeted"
            reseal_manifest(raw)
        elif mutation == "run-id":
            raw["run_id"] = "stale-run"
            reseal_manifest(raw)
        elif mutation == "attempt":
            raw["run_attempt"] = 2
            reseal_manifest(raw)
        else:
            raw["canonical_sha256"] = "0" * 64
        (loo_a / "manifest.json").write_bytes(canonical_json_bytes(raw))

    with pytest.raises(ValueError):
        run(parse_cli(_final_args(root, output)))


def test_final_cli_rejects_symlinked_artifact_roots(tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    _write_final_manifests(physical)
    linked = tmp_path / "linked"
    linked.symlink_to(physical, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        run(parse_cli(_final_args(linked, tmp_path / "report.json")))
