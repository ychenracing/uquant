from __future__ import annotations

import ast
import copy
import json
import subprocess
from collections import Counter
from collections.abc import Mapping
from typing import cast

import pytest

from ._analysis import FINAL_BUDGETS, ROOT, architecture_snapshot, measured_debt
from ._task10_cli_transport import (
    current_heads_adapter_transport_unit_digests,
    phase2_ablation_public_owner_transport_unit_digests,
    production_observation_transport_unit_digests,
    public_cli_seam_transport_unit_digests,
)
from ._task10_inventory import (
    EXPECTED_DEBT_COUNTS,
    EXPECTED_PRODUCTION_OVER_800,
    GOVERNED_SCRIPTS,
    OVERSIZED_TEST_FILES,
    TASK10_START_COMMIT,
    TASK10_START_TREE,
    build_inventory_from_immutable_git,
    canonical_sha256,
    cli_help_seam,
    current_semantic_unit_counts,
    load_inventory,
    verify_inventory_seal,
)
from ._task10_owner_transport import (
    expand_task10_portfolio_pipeline,
    expand_task10_risk_assessment,
    expand_task10_risk_market_stage,
    task10_task8_reviewed_sources,
)
from ._task10_portfolio_transport import expand_task10_checkpoint1_method
from ._task10_reviewed_owner_transport import (
    expand_reviewed_task10_owner,
    reviewed_task10_owner_source,
)
from ._task10_task5_transport import task5_reviewed_source, validate_task5_private_transport
from ._task10_task6_transport import (
    reviewed_task6_debt_definition,
    task6_reviewed_source,
    validate_task6_decision_owner_transport,
)
from ._task10_test_relocations import (
    TEST_RELOCATION_PATHS,
    _projected_analysis_counts,
    analysis_legacy_unit_counts,
    task7_legacy_unit_counts,
    verify_test_relocations,
)


def _records_by_path(value: object) -> dict[str, Mapping[str, object]]:
    assert isinstance(value, list)
    records: dict[str, Mapping[str, object]] = {}
    for row in value:
        assert isinstance(row, Mapping)
        path = str(row["path"])
        assert path not in records
        records[path] = row
    return records


def _initial_unit_counts(records: object) -> Counter[str]:
    counts: Counter[str] = Counter()
    for record in _records_by_path(records).values():
        units = record["semantic_units"]
        assert isinstance(units, list)
        for unit in units:
            assert isinstance(unit, Mapping)
            counts[str(unit["ast_sha256"])] += 1
            assertions = unit["assertion_sha256"]
            decorators = unit["decorator_sha256"]
            assert isinstance(assertions, list)
            assert isinstance(decorators, list)
            assert all(isinstance(value, str) and len(value) == 64 for value in assertions)
            assert all(isinstance(value, str) and len(value) == 64 for value in decorators)
    return counts


def _immutable_source(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{TASK10_START_COMMIT}:{path}"],
        cwd=ROOT,
        text=True,
    )


def test_task10_inventory_matches_immutable_start_tree_and_is_not_self_signed() -> None:
    payload = load_inventory()
    verify_inventory_seal(payload)
    immutable = payload["immutable_start"]
    assert immutable == {"commit": TASK10_START_COMMIT, "tree": TASK10_START_TREE}
    assert payload == build_inventory_from_immutable_git()
    assert payload["architecture_debt_counts"] == EXPECTED_DEBT_COUNTS

    resigned = copy.deepcopy(payload)
    records = _records_by_path(resigned["oversized_test_files"])
    first = dict(records[OVERSIZED_TEST_FILES[0]])
    units = copy.deepcopy(first["semantic_units"])
    assert isinstance(units, list) and units
    assert isinstance(units[0], dict)
    units[0]["ast_sha256"] = "0" * 64
    first["semantic_units"] = units
    for index, row in enumerate(cast(list[object], resigned["oversized_test_files"])):
        if isinstance(row, Mapping) and row["path"] == first["path"]:
            cast(list[object], resigned["oversized_test_files"])[index] = first
            break
    unsigned = dict(resigned)
    unsigned.pop("artifact_sha256")
    resigned["artifact_sha256"] = canonical_sha256(unsigned)
    verify_inventory_seal(resigned)
    assert resigned != build_inventory_from_immutable_git()


def test_task10_inventory_covers_exact_start_debt_files_seams_and_reproducibility() -> None:
    payload = load_inventory()
    debt = payload["architecture_debt"]
    assert isinstance(debt, Mapping)
    assert {category: len(rows) for category, rows in debt.items()} == EXPECTED_DEBT_COUNTS
    over_800 = payload["expected_production_over_800"]
    assert isinstance(over_800, list)
    assert tuple(over_800) == EXPECTED_PRODUCTION_OVER_800
    assert set(_records_by_path(payload["governed_cli_scripts"])) == set(GOVERNED_SCRIPTS)
    assert set(_records_by_path(payload["oversized_test_files"])) == set(OVERSIZED_TEST_FILES)
    assert payload["requirements_sha256"] == (
        "77c1e3d4685e5f55c0b3e7bf23ff101ff957973cc2e80aa4e5cdb4d02f0e2731"
    )
    assert payload["reproducibility"] == {
        "archive": f"git archive --format=tar {TASK10_START_COMMIT}",
        "generator": "python -m tests.architecture._task10_inventory",
        "final_zero_test": (
            "pytest -q tests/architecture/test_task10_governance.py::"
            "test_task10_final_live_debt_matches_empty_acceptance_allowlist"
        ),
    }


def test_task10_cli_help_and_failure_seams_match_immutable_start() -> None:
    records = _records_by_path(load_inventory()["governed_cli_scripts"])
    for relative in GOVERNED_SCRIPTS:
        assert cli_help_seam(ROOT / relative, ROOT) == records[relative]["help_seam"]


def test_task10_governed_test_units_and_assertions_are_bidirectionally_preserved() -> None:
    payload = load_inventory()
    initial = _initial_unit_counts(payload["oversized_test_files"])
    baseline = payload["test_repository_unit_multiplicity"]
    assert isinstance(baseline, Mapping)
    analysis_paths = set(TEST_RELOCATION_PATHS["tests/architecture/_analysis.py"])
    task7_paths = set(
        TEST_RELOCATION_PATHS["tests/architecture/test_task7_risk_boundaries.py"]
    )
    current_paths = tuple(
        path
        for path in (ROOT / "tests").rglob("*.py")
        if path.name not in {"_task10_inventory.py", "test_task10_governance.py"}
        and path.relative_to(ROOT).as_posix() not in analysis_paths | task7_paths
    )
    current = current_semantic_unit_counts(current_paths)
    current.update(
        _projected_analysis_counts(
            immutable_source=_immutable_source("tests/architecture/_analysis.py"),
            current_sources={
                relative: (ROOT / relative).read_text(encoding="utf-8")
                for relative in TEST_RELOCATION_PATHS["tests/architecture/_analysis.py"]
            },
            root=ROOT,
        )
    )
    current.update(
        task7_legacy_unit_counts(
            immutable_source=_immutable_source(
                "tests/architecture/test_task7_risk_boundaries.py"
            ),
            current_sources={
                relative: (ROOT / relative).read_text(encoding="utf-8")
                for relative in task7_paths
            },
        )
    )
    assert {digest: current[digest] for digest in initial} == {
        digest: int(baseline[digest]) for digest in initial
    }


def test_task10_test_relocation_inventory_is_exact_and_bidirectional() -> None:
    verify_test_relocations(
        immutable_records=load_inventory()["oversized_test_files"],
        immutable_analysis_source=_immutable_source("tests/architecture/_analysis.py"),
        immutable_task7_source=_immutable_source(
            "tests/architecture/test_task7_risk_boundaries.py"
        ),
        root=ROOT,
    )


def test_task10_test_relocation_inventory_rejects_unknown_missing_and_duplicate_paths() -> None:
    source = "tests/test_execution.py"
    targets = TEST_RELOCATION_PATHS[source]
    mutations = (
        {**TEST_RELOCATION_PATHS, source: (*targets, "tests/_unknown_execution_cases.py")},
        {**TEST_RELOCATION_PATHS, source: targets[:-1]},
        {**TEST_RELOCATION_PATHS, source: (*targets, targets[-1])},
    )
    for relocation_paths in mutations:
        with pytest.raises(AssertionError):
            verify_test_relocations(
                immutable_records=load_inventory()["oversized_test_files"],
                immutable_analysis_source=_immutable_source("tests/architecture/_analysis.py"),
                immutable_task7_source=_immutable_source(
                    "tests/architecture/test_task7_risk_boundaries.py"
                ),
                root=ROOT,
                relocation_paths=relocation_paths,
            )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "tests/architecture/_analysis_authorities.py",
            '"uquant.portfolio.allocation_closure": "production_safe",',
            '"uquant.portfolio.allocation_closure": "validation_runner",',
        ),
        (
            "tests/architecture/_analysis_authorities.py",
            '"uquant.risk.market_book",',
            '"uquant.risk.market_book_typo",',
        ),
        (
            "tests/architecture/_analysis_debt.py",
            "root, source_texts, governed_source_texts",
            "root, dict(source_texts or {}), governed_source_texts",
        ),
        (
            "tests/architecture/_analysis_debt.py",
            '*governed_private_edges["qualified"],',
            '*governed_private_edges["direct"],',
        ),
    ),
)
def test_task10_analysis_legacy_transport_rejects_unknown_live_governance_mutation(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    sources = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in TEST_RELOCATION_PATHS["tests/architecture/_analysis.py"]
    }
    assert original in sources[relative]
    sources[relative] = sources[relative].replace(original, mutation, 1)
    with pytest.raises(AssertionError):
        analysis_legacy_unit_counts(
            immutable_source=_immutable_source("tests/architecture/_analysis.py"),
            current_sources=sources,
            root=ROOT,
        )


@pytest.mark.parametrize(
    ("original", "mutation"),
    (
        (
            "task10_source_surface_projection(identifier, expected)",
            "task10_source_surface_projection(identifier, set(expected))",
        ),
        (
            "task10_task7_historical_base_lines(root=ROOT, current_row=base)",
            "task10_task7_historical_base_lines(root=ROOT, current_row=dict(base))",
        ),
    ),
)
def test_task10_task7_test_transport_rejects_unknown_call_arguments(
    original: str,
    mutation: str,
) -> None:
    relative = "tests/architecture/test_task7_risk_boundaries.py"
    sources = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in TEST_RELOCATION_PATHS[relative]
    }
    assert original in sources[relative]
    sources[relative] = sources[relative].replace(original, mutation, 1)
    with pytest.raises(AssertionError):
        task7_legacy_unit_counts(
            immutable_source=_immutable_source(relative),
            current_sources=sources,
        )


def test_task10_temporary_current_private_import_transport_is_removed() -> None:
    for relative in (
        "tests/architecture/_analysis_debt.py",
        "tests/architecture/_analysis_relocations.py",
    ):
        assert "_TASK10_PRIVATE_IMPORT" not in (ROOT / relative).read_text(encoding="utf-8")


def test_task10_policy_evaluation_uses_exact_owned_contract_identities() -> None:
    from uquant.validation.generalization_policy import projection, schema

    projection_names = (
        "attribution_neutral_equality_sha256",
        "candidate_contract_sha256",
    )
    schema_names = (
        "ARTIFACT_FIELDS_V1",
        "ARTIFACT_FIELDS_V2",
        "ATTRIBUTION_DEFINITION",
        "CELL_FIELDS_V1",
        "CELL_FIELDS_V2",
        "EVIDENCE_FIELDS",
        "ROOT",
        "artifact_equality_sha256",
        "metric_payload",
        "metrics_reconciled_from_raw",
        "provenance_schema_failures",
        "replay_error",
        "schema_failures",
    )
    for owner, names in ((projection, projection_names), (schema, schema_names)):
        for public_name in names:
            assert getattr(owner, public_name) is getattr(owner, f"_{public_name}")


@pytest.mark.parametrize(
    ("original", "mutation"),
    (
        (
            "raw, files, status_value = _validated_backup_manifest(root)",
            "raw, files, status_value = _validated_backup_manifest(Path(root))",
        ),
        (
            "root=root,\n            account=account,",
            "root=Path(root),\n            account=account,",
        ),
        (
            "_validate_backup_inventory(root, files)",
            "_validate_backup_carriers(root, files)",
        ),
    ),
)
def test_task10_production_observation_transport_rejects_unknown_stage_arguments(
    original: str,
    mutation: str,
) -> None:
    current = (ROOT / "uquant/validation/production_observation.py").read_text(
        encoding="utf-8"
    )
    assert original in current
    with pytest.raises(AssertionError):
        production_observation_transport_unit_digests(
            frozen_source=_immutable_source("scripts/production_observation.py"),
            current_source=current.replace(original, mutation, 1),
            current_cli_source=(ROOT / "scripts/production_observation.py").read_text(
                encoding="utf-8"
            ),
        )


def test_task10_governed_cli_units_are_bidirectionally_preserved_in_owned_layers() -> None:
    payload = load_inventory()
    initial = _initial_unit_counts(payload["governed_cli_scripts"])
    baseline = payload["cli_owned_layer_unit_multiplicity"]
    assert isinstance(baseline, Mapping)
    current_paths = (
        tuple((ROOT / "scripts").rglob("*.py"))
        + tuple((ROOT / "research").rglob("*.py"))
        + tuple((ROOT / "uquant" / "validation").rglob("*.py"))
    )
    current = current_semantic_unit_counts(current_paths)
    current.update(
        production_observation_transport_unit_digests(
            frozen_source=_immutable_source("scripts/production_observation.py"),
            current_source=(
                ROOT / "uquant/validation/production_observation.py"
            ).read_text(encoding="utf-8"),
            current_cli_source=(ROOT / "scripts/production_observation.py").read_text(
                encoding="utf-8"
            ),
        )
    )
    current.update(
        phase2_ablation_public_owner_transport_unit_digests(
            frozen_source=_immutable_source("scripts/run_phase2_ablation.py"),
            current_source=(ROOT / "research/phase2_ablation_cli.py").read_text(
                encoding="utf-8"
            ),
        )
    )
    current.update(
        current_heads_adapter_transport_unit_digests(
            frozen_source=_immutable_source(
                "scripts/run_current_heads_competitor_matrix.py"
            ),
            current_source=(
                ROOT / "research/current_heads_competitor_matrix.py"
            ).read_text(encoding="utf-8"),
            current_adapter_source=(
                ROOT / "research/window_competitor_adapter.py"
            ).read_text(encoding="utf-8"),
        )
    )
    for frozen_path, current_path, projections in (
        (
            "scripts/run_phase2_ablation.py",
            "research/phase2_ablation_cli.py",
            (("_baseline_config_sha256", {"probe_checkout": "_probe_checkout"}),),
        ),
        (
            "scripts/run_risk_differential.py",
            "research/risk_differential_cli.py",
            (("preregister", {"checkout_identity": "_derive_checkout_identity"}),),
        ),
        (
            "scripts/future_holdout.py",
            "research/future_holdout_cli.py",
            (
                (
                    "_compute_risk_differential_payload",
                    {
                        "future_holdout_trade_replay": "run_trade_cell",
                        "future_holdout_uquant_replay": "run_uquant_cell",
                    },
                ),
            ),
        ),
        (
            "scripts/run_five_window_outperformance.py",
            "research/five_window_outperformance.py",
            (("main", {"outperformance_build": "build"}),),
        ),
        (
            "scripts/backfill_tencent_history.py",
            "research/tencent_history_adapter.py",
            (
                (
                    "main",
                    {
                        "backfill_symbol": "_backfill_one",
                        "prepend_tech_history": "_prepend_tech_proxy",
                    },
                ),
            ),
        ),
        (
            "scripts/run_phase1_diagnostic.py",
            "research/phase1_diagnostic.py",
            (
                ("_source_provenance", {"diagnostic_git": "_git"}),
                ("_runner_provenance", {"diagnostic_git": "_git"}),
                (
                    "_run_trace",
                    {"diagnostic_runner_provenance": "_runner_provenance"},
                ),
                (
                    "_compare",
                    {"diagnostic_runner_provenance": "_runner_provenance"},
                ),
            ),
        ),
        (
            "scripts/run_window_outperformance.py",
            "research/window_outperformance.py",
            (("main", {"outperformance_build": "build"}),),
        ),
    ):
        current.update(
            public_cli_seam_transport_unit_digests(
                frozen_source=_immutable_source(frozen_path),
                current_source=(ROOT / current_path).read_text(encoding="utf-8"),
                projections=projections,
            )
        )
    assert {digest: current[digest] for digest in initial} == {
        digest: int(baseline[digest]) for digest in initial
    }


def test_task10_phase2_ablation_transport_rejects_unknown_public_owner() -> None:
    current = (ROOT / "research/phase2_ablation_cli.py").read_text(encoding="utf-8")
    original = (
        "from uquant.validation.promotion import "
        "compact_promotion_payload as _compact"
    )
    assert original in current
    with pytest.raises(AssertionError):
        phase2_ablation_public_owner_transport_unit_digests(
            frozen_source=_immutable_source("scripts/run_phase2_ablation.py"),
            current_source=current.replace(
                original,
                "from uquant.validation.promotion import "
                "candidate_promotion_payload as _compact",
                1,
            ),
        )


def test_task10_analyzer_mutations_expose_unknown_debt_instead_of_filtering_it() -> None:
    assert FINAL_BUDGETS == {
        "max_module_lines": 1000,
        "max_function_lines": 120,
        "max_function_branch_points": 20,
        "max_cross_module_private_imports": 0,
        "max_mutable_module_globals": 0,
        "max_production_type_ignores": 0,
        "max_duplicate_private_helper_groups": 0,
        "max_internal_scc_size": 1,
    }
    sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    sources["uquant/reference.py"] += (
        "\n_TASK10_UNKNOWN_MUTABLE = []\n"
        "def _task10_unknown_long():\n" + "    value = 0\n" * 121 + "    return value\n"
    )
    sources["uquant/report.py"] += (
        "\ndef _task10_unknown_branchy(value: int) -> int:\n"
        + "".join(f"    if value == {index}:\n        return {index}\n" for index in range(21))
        + "    return -1\n"
    )
    mutation = measured_debt(architecture_snapshot(source_texts=sources))
    assert any("_task10_unknown_long" in str(row["id"]) for row in mutation["long_functions"])
    assert any("_task10_unknown_branchy" in str(row["id"]) for row in mutation["branchy_functions"])
    assert any("_TASK10_UNKNOWN_MUTABLE" in str(row["id"]) for row in mutation["mutable_module_globals"])


def test_task10_cross_private_debt_is_zero_without_renamed_exemptions() -> None:
    current = measured_debt(architecture_snapshot())
    assert current["cross_module_private_imports"] == []


def test_task10_mutable_global_debt_is_genuinely_immutable() -> None:
    current = measured_debt(architecture_snapshot())
    assert current["mutable_module_globals"] == []

    from uquant.leader import FACTOR_PROFILES
    from uquant.types import Opportunity
    from uquant.validation.promotion import AI_ERA_POLICY, PROTECTED_INTERVALS

    factor_profile = cast(dict[str, float], FACTOR_PROFILES[Opportunity.TREND.value])
    with pytest.raises(TypeError):
        factor_profile["momentum60"] = 99.0
    protected_interval = cast(dict[str, str], PROTECTED_INTERVALS["year_2023"])
    with pytest.raises(TypeError):
        protected_interval["start"] = "2099-01-01"
    official_policy = cast(
        dict[str, object], cast(Mapping[str, object], AI_ERA_POLICY["official"])["h1_2023"]
    )
    with pytest.raises(TypeError):
        official_policy["min_final_wealth"] = -1.0


def test_task10_production_type_ignore_debt_is_zero() -> None:
    current = measured_debt(architecture_snapshot())
    assert current["production_type_ignores"] == []


def test_task10_duplicate_private_helper_debt_is_zero_without_generic_utils() -> None:
    current = measured_debt(architecture_snapshot())
    assert current["duplicate_private_helper_groups"] == []


def test_task10_sentinel_legacy_projection_matches_immutable_start() -> None:
    from uquant.risk_sentinel.legacy_surface import immutable_legacy_surface

    projected = dict(immutable_legacy_surface())
    paths = subprocess.check_output(
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            TASK10_START_COMMIT,
            "uquant/risk_sentinel",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    expected = tuple(sorted(path for path in paths if path.endswith(".py")))
    assert tuple(projected) == expected
    for path in expected:
        assert projected[path] == subprocess.check_output(
            ["git", "show", f"{TASK10_START_COMMIT}:{path}"],
            cwd=ROOT,
        )


def test_task10_portfolio_pipeline_delegates_to_real_allocation_stages() -> None:
    owners = {
        "uquant/portfolio/allocation_opening.py": "prepare_allocation",
        "uquant/portfolio/allocation_tactical.py": "allocate_tactical",
        "uquant/portfolio/allocation_protected.py": "restore_protected_allocation",
        "uquant/portfolio/allocation_recovery.py": "allocate_recovery",
        "uquant/portfolio/allocation_closure.py": "close_allocation",
        "uquant/portfolio/recovery/tactical_admission.py": "tactical_admission_targets",
        "uquant/portfolio/recovery/cohort_admission.py": "cohort_admission_targets",
    }
    for relative, function_name in owners.items():
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        assert any(isinstance(node, ast.FunctionDef) and node.name == function_name for node in tree.body)
    pipeline = ast.parse((ROOT / "uquant/portfolio/pipeline.py").read_text(encoding="utf-8"))
    owner = next(
        node
        for node in pipeline.body
        if isinstance(node, ast.FunctionDef) and node.name == "_allocate_strategy"
    )
    observed = [
        node.func.id
        for node in ast.walk(owner)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    expected = list(owners.values())[:5]
    assert [name for name in observed if name in expected] == expected
    admission = ast.parse((ROOT / "uquant/portfolio/recovery/admission.py").read_text(encoding="utf-8"))
    admission_calls = [
        node.func.id
        for node in ast.walk(admission)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    expected_admission = list(owners.values())[5:]
    assert [name for name in admission_calls if name in expected_admission] == expected_admission


def test_task10_portfolio_transport_expands_exact_immutable_statement_order() -> None:
    expanded = expand_task10_portfolio_pipeline(root=ROOT, candidate=None)
    immutable_source = subprocess.check_output(
        ["git", "show", f"{TASK10_START_COMMIT}:uquant/portfolio/pipeline.py"],
        cwd=ROOT,
        text=True,
    )
    immutable = next(
        node
        for node in ast.parse(immutable_source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_allocate_strategy"
    )
    assert ast.dump(expanded, include_attributes=False) == ast.dump(
        immutable, include_attributes=False
    )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "uquant/portfolio/pipeline.py",
            "risk=risk,\n        user_panel=user_panel,",
            "risk=account,\n        user_panel=user_panel,",
        ),
        (
            "uquant/portfolio/allocation_opening.py",
            "and risk.votes <= 1",
            "and risk.votes <= 2",
        ),
    ),
)
def test_task10_portfolio_transport_rejects_argument_and_body_mutations(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    reviewed_sources = task10_task8_reviewed_sources(root=ROOT)
    source = reviewed_sources[relative]
    assert original in source
    mutated = source.replace(original, mutation, 1)
    pipeline_source = (
        mutated
        if relative == "uquant/portfolio/pipeline.py"
        else reviewed_sources["uquant/portfolio/pipeline.py"]
    )
    candidate = next(
        node
        for node in ast.parse(pipeline_source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_allocate_strategy"
    )
    with pytest.raises(AssertionError):
        expand_task10_portfolio_pipeline(
            root=ROOT,
            candidate=candidate,
            overrides={relative: mutated},
        )


def test_task10_risk_market_transport_rejects_delegation_argument_mutation() -> None:
    relative = "uquant/risk/assessment.py"
    source = (ROOT / relative).read_text(encoding="utf-8")
    original = "reference_returns=reference_returns,"
    assert original in source
    mutated = source.replace(original, "reference_returns=None,", 1)
    wrapper = next(
        node
        for node in ast.parse(mutated).body
        if isinstance(node, ast.FunctionDef) and node.name == "_assess_market_and_book_evidence"
    )
    with pytest.raises(AssertionError):
        expand_task10_risk_market_stage(
            root=ROOT,
            wrapper=wrapper,
            overrides={relative: mutated},
        )


def test_task10_risk_assessment_transport_rejects_stage_argument_mutation() -> None:
    relative = "uquant/risk/assessment.py"
    source = (ROOT / relative).read_text(encoding="utf-8")
    original = "market=market, recovery=recovery"
    assert original in source
    mutated = source.replace(original, "market=recovery, recovery=recovery", 1)
    candidate = next(
        node
        for node in ast.parse(mutated).body
        if isinstance(node, ast.FunctionDef) and node.name == "_assess_base_risk"
    )
    with pytest.raises(AssertionError):
        expand_task10_risk_assessment(
            root=ROOT,
            candidate=candidate,
            overrides={relative: mutated},
        )


@pytest.mark.parametrize(
    ("relative", "name", "original", "mutation"),
    (
        (
            "uquant/portfolio/allocator.py",
            "allocate",
            "risk=strategy_risk,",
            "risk=risk,",
        ),
        (
            "uquant/portfolio/risk_reduction.py",
            "_risk_lifecycle_rank",
            "max(0.0, retained[5]),",
            "max(0.0, retained[4]),",
        ),
        (
            "uquant/portfolio/risk_reduction.py",
            "_sparse_risk_reduce",
            "remaining_gross -= retained_by_bucket[index]",
            "remaining_gross += retained_by_bucket[index]",
        ),
        (
            "uquant/portfolio/freeze.py",
            "_commit_frozen_exit_state",
            'key.startswith("hard_risk_winner_trail:")',
            'key.endswith("hard_risk_winner_trail:")',
        ),
    ),
)
def test_task10_checkpoint1_transport_rejects_owner_mutations(
    relative: str,
    name: str,
    original: str,
    mutation: str,
) -> None:
    source = task10_task8_reviewed_sources(root=ROOT)[relative]
    assert original in source
    mutated = source.replace(original, mutation, 1)
    candidate = next(
        node
        for node in ast.parse(mutated).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    with pytest.raises(AssertionError):
        expand_task10_checkpoint1_method(
            root=ROOT,
            relative=relative,
            name=name,
            candidate=candidate,
            overrides={relative: mutated},
        )


@pytest.mark.parametrize(
    ("relative", "name", "original", "mutation"),
    (
        (
            "uquant/portfolio/leaders/admission.py",
            "_dynamic_k",
            "Opportunity.STRONG_TREND: 4",
            "Opportunity.STRONG_TREND: 5",
        ),
        (
            "uquant/portfolio/leaders/lifecycle.py",
            "_update_leader_cycle_arm",
            "impulse = _leader_cycle_impulse(",
            "impulse = _leader_cycle_impulse_unknown(",
        ),
        (
            "uquant/portfolio/leaders/targets.py",
            "_leader_targets",
            "ctx = _leader_target_context(",
            "ctx = _leader_target_context_unknown(",
        ),
        (
            "uquant/portfolio/strategic/discovery.py",
            "_initialize_strategic_cohort",
            "if not _strategic_discovery_open(",
            "if not _strategic_discovery_open_unknown(",
        ),
        (
            "uquant/portfolio/strategic/lifecycle.py",
            "_strategic_cohort_targets",
            "ctx = _strategic_lifecycle_context(",
            "ctx = _strategic_lifecycle_context_unknown(",
        ),
        (
            "uquant/portfolio/recovery/substitution.py",
            "_recovery_anchor_substitution",
            "return True, _pending_recovery_substitution_targets(",
            "return True, _pending_recovery_substitution_targets_unknown(",
        ),
        (
            "uquant/portfolio/recovery/admission.py",
            "_recovery_admission_targets",
            "targets = tactical_admission_targets(",
            "targets = tactical_admission_targets_unknown(",
        ),
    ),
)
def test_task10_reviewed_owner_transport_rejects_unknown_mutations(
    relative: str,
    name: str,
    original: str,
    mutation: str,
) -> None:
    source = reviewed_task10_owner_source(ROOT, relative, name)
    assert original in source
    mutated = source.replace(original, mutation, 1)
    candidate = next(
        node
        for node in ast.parse(mutated).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    with pytest.raises(AssertionError):
        expand_reviewed_task10_owner(
            root=ROOT,
            relative=relative,
            name=name,
            candidate=candidate,
            overrides={relative: mutated},
        )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "uquant/attribution/concentration.py",
            "group_lot_pnl = _group_lot_pnl",
            "group_lot_pnl = _holding_summary",
        ),
        (
            "uquant/attribution/validation_artifact.py",
            "from .concentration import group_lot_pnl as _group_lot_pnl",
            "from .concentration import holding_summary as _group_lot_pnl",
        ),
        (
            "uquant/attribution/builder.py",
            'by_symbol = _group_lot_pnl(lots, "symbol")',
            'by_symbol = _group_lot_pnl(lots, "symbol_unknown")',
        ),
        (
            "uquant/attribution/concentration.py",
            "bucket = grouped.setdefault(name, _empty_pnl_bucket())",
            "bucket = grouped.setdefault(name, _unknown_pnl_bucket())",
        ),
    ),
)
def test_task10_task5_private_transport_rejects_identity_callee_and_argument_mutations(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    source = task5_reviewed_source(ROOT, relative)
    assert original in source
    mutated = source.replace(original, mutation, 1)
    with pytest.raises(AssertionError):
        validate_task5_private_transport(
            root=ROOT,
            source_overrides={relative: mutated},
        )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "uquant/application/decision.py",
            "return attach_target_attribution(",
            "return attach_target_attribution_unknown(",
        ),
        (
            "uquant/application/target_attribution.py",
            "from ..types import PendingOrder, Side, Target, derive_attribution_event_id",
            "from ..types import AccountState, PendingOrder, Side, Target, derive_attribution_event_id",
        ),
    ),
)
def test_task10_task6_decision_owner_transport_rejects_unknown_mutations(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    source = task6_reviewed_source(ROOT, relative)
    assert original in source
    mutated = source.replace(original, mutation, 1)
    with pytest.raises(AssertionError):
        validate_task6_decision_owner_transport(
            root=ROOT,
            source_overrides={relative: mutated},
        )


def test_task10_task6_execution_debt_transport_rejects_unknown_stage_arguments() -> None:
    relative = "uquant/execution/order_planning.py"
    source = task6_reviewed_source(ROOT, relative)
    original = "cancel_pending_buy_symbols=cancel_pending_buy_symbols,"
    mutation = "cancel_pending_buy_symbols=set(),"
    assert original in source
    mutated = source.replace(original, mutation, 1)
    candidate = next(
        node
        for node in ast.parse(mutated).body
        if isinstance(node, ast.FunctionDef) and node.name == "plan_orders"
    )
    with pytest.raises(AssertionError):
        reviewed_task6_debt_definition(
            root=ROOT,
            relative=relative,
            name="plan_orders",
            candidate=candidate,
            frozen=copy.deepcopy(candidate),
            source_overrides={relative: mutated},
        )


def test_task10_final_live_blockers_match_empty_acceptance_allowlist(
    request: pytest.FixtureRequest,
) -> None:
    snapshot = architecture_snapshot()
    current = measured_debt(snapshot)
    baseline = json.loads(
        (ROOT / "artifacts/architecture_refactor/baseline_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    debt = baseline["architecture_debt"]
    assert isinstance(debt, Mapping)
    expected = debt["final_acceptance_allowlist"]
    assert isinstance(expected, Mapping)
    assert set(current) == set(expected)
    signal_categories = {
        "oversized_modules",
        "long_functions",
        "branchy_functions",
    }
    request.node.user_properties.append(
        (
            "task10_complexity_signals",
            json.dumps(
                {category: current[category] for category in sorted(signal_categories)},
                sort_keys=True,
            ),
        )
    )
    blocking_categories = set(current) - signal_categories
    assert {category: current[category] for category in blocking_categories} == {
        category: expected[category] for category in blocking_categories
    }


def test_task10_final_physical_size_signals_are_recorded(
    request: pytest.FixtureRequest,
) -> None:
    oversized_production = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "uquant").rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > 800
    )
    oversized_tests = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > 1000
    )
    oversized_scripts = sorted(
        relative
        for relative in GOVERNED_SCRIPTS
        if len((ROOT / relative).read_text(encoding="utf-8").splitlines()) >= 300
    )
    request.node.user_properties.append(
        (
            "task10_physical_size_signals",
            json.dumps(
                {
                    "production_over_800": oversized_production,
                    "scripts_at_least_300": oversized_scripts,
                    "tests_over_1000": oversized_tests,
                },
                sort_keys=True,
            ),
        )
    )
