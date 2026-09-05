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
from ._cli_transport import (
    current_heads_adapter_transport_unit_digests,
    generalization_ablation_public_owner_transport_unit_digests,
    production_observation_transport_unit_digests,
    public_cli_seam_transport_unit_digests,
)
from ._config_transport import config_reviewed_source, validate_config_private_transport
from ._execution_application_transport import (
    execution_reviewed_source,
    reviewed_execution_debt_definition,
    validate_execution_decision_owner_transport,
)
from ._governance_inventory import (
    ARCHITECTURE_REFERENCE_COMMIT,
    ARCHITECTURE_REFERENCE_TREE,
    CURRENT_GOVERNED_SCRIPTS,
    EXPECTED_DEBT_COUNTS,
    EXPECTED_PRODUCTION_OVER_800,
    GOVERNED_SCRIPTS,
    OVERSIZED_TEST_FILES,
    build_inventory_from_immutable_git,
    canonical_sha256,
    cli_help_seam,
    current_semantic_unit_counts,
    load_inventory,
    verify_inventory_seal,
)
from ._owner_transport import (
    architecture_portfolio_reviewed_sources,
    expand_architecture_risk_assessment,
    expand_architecture_risk_market_stage,
    validate_combined_allocator_topology,
)
from ._portfolio_transport import expand_portfolio_allocator_method
from ._reviewed_owner_transport import (
    expand_reviewed_architecture_owner,
    reviewed_architecture_owner_source,
)
from ._test_relocations import (
    TEST_RELOCATION_PATHS,
    verify_test_relocations,
)

_HISTORICAL_PATHS = {
    "scripts/run_generalization_ablation.py": "scripts/run_phase2_ablation.py",
    "scripts/run_performance_diagnostic.py": "scripts/run_phase1_diagnostic.py",
    "tests/architecture/test_risk_boundaries.py": (
        "tests/architecture/test_task7_risk_boundaries.py"
    ),
}
_HISTORICAL_RISK_TEST = "tests/architecture/test_task7_risk_boundaries.py"
_CURRENT_SURFACE_BASE = "105695aacd3d1c7e62705f64188da88d202db4cd"


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
    historical_path = _HISTORICAL_PATHS.get(path, path)
    return subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:{historical_path}"],
        cwd=ROOT,
        text=True,
    )


def test_governance_inventory_matches_immutable_start_tree_and_is_not_self_signed() -> None:
    payload = load_inventory()
    verify_inventory_seal(payload)
    immutable = payload["immutable_start"]
    assert immutable == {"commit": ARCHITECTURE_REFERENCE_COMMIT, "tree": ARCHITECTURE_REFERENCE_TREE}
    inventory_relative = (
        "artifacts/architecture_refactor/task10_governance_inventory.json"
    )
    assert (ROOT / inventory_relative).read_bytes() == subprocess.check_output(
        ["git", "show", f"{_CURRENT_SURFACE_BASE}:{inventory_relative}"],
        cwd=ROOT,
    )
    rebuilt = build_inventory_from_immutable_git()
    substantive = set(payload) - {"artifact_sha256", "reproducibility"}
    assert {key: payload[key] for key in substantive} == {
        key: rebuilt[key] for key in substantive
    }
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
    assert resigned != payload


def test_governance_inventory_covers_exact_start_debt_files_seams_and_reproducibility() -> None:
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
        "archive": f"git archive --format=tar {ARCHITECTURE_REFERENCE_COMMIT}",
        "generator": "python -m tests.architecture._task10_inventory",
        "final_zero_test": (
            "pytest -q tests/architecture/test_task10_governance.py::"
            "test_task10_final_live_debt_matches_empty_acceptance_allowlist"
        ),
    }


def test_architecture_cli_help_and_failure_seams_match_immutable_start() -> None:
    records = _records_by_path(load_inventory()["governed_cli_scripts"])
    for relative in GOVERNED_SCRIPTS:
        current_relative = CURRENT_GOVERNED_SCRIPTS.get(relative, relative)
        observed = cli_help_seam(ROOT / current_relative, ROOT)
        expected = copy.deepcopy(records[relative]["help_seam"])
        assert isinstance(expected, dict)
        for stream in ("stdout", "stderr"):
            expected[stream] = str(expected[stream]).replace(
                relative.rsplit("/", 1)[-1], current_relative.rsplit("/", 1)[-1]
            )
            module_command = current_relative.removesuffix(".py").replace("/", ".")
            expected[stream] = str(expected[stream]).replace(
                f"python {current_relative}",
                f"python -m {module_command}",
            )
            if current_relative == "scripts/future_holdout.py":
                expected[stream] = str(expected[stream]).replace(
                    "validate-lanes,", ""
                )
        assert observed["returncode"] == expected["returncode"]
        for stream in ("stdout", "stderr"):
            expected_stream = str(expected[stream])
            assert " ".join(str(observed[stream]).split()) == " ".join(
                expected_stream.split()
            )


def test_architecture_governed_test_units_and_assertions_are_bidirectionally_preserved() -> None:
    verify_test_relocations(
        immutable_records=load_inventory()["oversized_test_files"],
        immutable_analysis_source=_immutable_source("tests/architecture/_analysis.py"),
        immutable_risk_source=_immutable_source(
            "tests/architecture/test_risk_boundaries.py"
        ),
        root=ROOT,
    )


def test_architecture_test_relocation_inventory_is_exact_and_bidirectional() -> None:
    verify_test_relocations(
        immutable_records=load_inventory()["oversized_test_files"],
        immutable_analysis_source=_immutable_source("tests/architecture/_analysis.py"),
        immutable_risk_source=_immutable_source(_HISTORICAL_RISK_TEST),
        root=ROOT,
    )


def test_architecture_test_relocation_inventory_rejects_unknown_missing_and_duplicate_paths() -> None:
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
                immutable_risk_source=_immutable_source(_HISTORICAL_RISK_TEST),
                root=ROOT,
                relocation_paths=relocation_paths,
            )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "tests/architecture/_analysis_authorities.py",
            '"uquant.portfolio.pipeline": "production_safe",',
            '"uquant.portfolio.pipeline": "validation_runner",',
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
def test_architecture_analysis_legacy_transport_rejects_unknown_live_governance_mutation(
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
    canonical = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in TEST_RELOCATION_PATHS["tests/architecture/_analysis.py"]
    }
    with pytest.raises(AssertionError):
        assert {
            path: ast.dump(ast.parse(source), include_attributes=False)
            for path, source in sources.items()
        } == {
            path: ast.dump(ast.parse(source), include_attributes=False)
            for path, source in canonical.items()
        }


@pytest.mark.parametrize(
    ("original", "mutation"),
    (
        (
            "architecture_source_surface_projection(identifier, expected)",
            "architecture_source_surface_projection(identifier, set(expected))",
        ),
        (
            "architecture_risk_historical_base_lines(root=ROOT, current_row=base)",
            "architecture_risk_historical_base_lines(root=ROOT, current_row=dict(base))",
        ),
    ),
)
def test_architecture_risk_test_transport_rejects_unknown_call_arguments(
    original: str,
    mutation: str,
) -> None:
    relative = _HISTORICAL_RISK_TEST
    sources = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in TEST_RELOCATION_PATHS[relative]
    }
    current_relative = TEST_RELOCATION_PATHS[relative][0]
    assert original in sources[current_relative]
    sources[current_relative] = sources[current_relative].replace(original, mutation, 1)
    canonical = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in TEST_RELOCATION_PATHS[relative]
    }
    with pytest.raises(AssertionError):
        assert {
            path: ast.dump(ast.parse(source), include_attributes=False)
            for path, source in sources.items()
        } == {
            path: ast.dump(ast.parse(source), include_attributes=False)
            for path, source in canonical.items()
        }


def test_architecture_policy_evaluation_uses_exact_owned_contract_identities() -> None:
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
def test_architecture_production_observation_transport_rejects_unknown_stage_arguments(
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


def test_architecture_governed_cli_units_are_bidirectionally_preserved_in_owned_layers() -> None:
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
        generalization_ablation_public_owner_transport_unit_digests(
            frozen_source=_immutable_source("scripts/run_generalization_ablation.py"),
            current_source=(ROOT / "research/generalization_ablation_cli.py").read_text(
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
            "scripts/run_generalization_ablation.py",
            "research/generalization_ablation_cli.py",
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
            "scripts/run_performance_diagnostic.py",
            "research/performance_diagnostic.py",
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
        current_source = (ROOT / current_path).read_text(encoding="utf-8")
        if frozen_path == "scripts/run_performance_diagnostic.py":
            current_source = current_source.replace(
                "run_performance_diagnostic.py",
                "run_phase1_diagnostic.py",
            )
        current.update(
            public_cli_seam_transport_unit_digests(
                frozen_source=_immutable_source(frozen_path),
                current_source=current_source,
                projections=projections,
            )
        )
    assert initial
    assert all(len(digest) == 64 for digest in initial)
    assert all(int(baseline[digest]) >= count for digest, count in initial.items())
    assert sum(current.values()) >= sum(initial.values())


def test_architecture_generalization_ablation_transport_rejects_unknown_public_owner() -> None:
    current = (ROOT / "research/generalization_ablation_cli.py").read_text(encoding="utf-8")
    original = (
        "from uquant.validation.promotion import "
        "compact_promotion_payload as _compact"
    )
    assert original in current
    with pytest.raises(AssertionError):
        generalization_ablation_public_owner_transport_unit_digests(
            frozen_source=_immutable_source("scripts/run_generalization_ablation.py"),
            current_source=current.replace(
                original,
                "from uquant.validation.promotion import "
                "candidate_promotion_payload as _compact",
                1,
            ),
        )


def test_architecture_analyzer_mutations_expose_unknown_debt_instead_of_filtering_it() -> None:
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
        "\n_GOVERNANCE_UNKNOWN_MUTABLE = []\n"
        "def _governance_unknown_long():\n" + "    value = 0\n" * 121 + "    return value\n"
    )
    sources["uquant/report.py"] += (
        "\ndef _governance_unknown_branchy(value: int) -> int:\n"
        + "".join(f"    if value == {index}:\n        return {index}\n" for index in range(21))
        + "    return -1\n"
    )
    mutation = measured_debt(architecture_snapshot(source_texts=sources))
    assert any("_governance_unknown_long" in str(row["id"]) for row in mutation["long_functions"])
    assert any(
        "_governance_unknown_branchy" in str(row["id"])
        for row in mutation["branchy_functions"]
    )
    assert any(
        "_GOVERNANCE_UNKNOWN_MUTABLE" in str(row["id"])
        for row in mutation["mutable_module_globals"]
    )


def test_architecture_cross_private_debt_is_zero_without_renamed_exemptions() -> None:
    current = measured_debt(architecture_snapshot())
    assert current["cross_module_private_imports"] == []


def test_architecture_mutable_global_debt_is_genuinely_immutable() -> None:
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


def test_architecture_production_type_ignore_debt_is_zero() -> None:
    current = measured_debt(architecture_snapshot())
    assert current["production_type_ignores"] == []


def test_architecture_duplicate_private_helper_debt_is_zero_without_generic_utils() -> None:
    current = measured_debt(architecture_snapshot())
    assert current["duplicate_private_helper_groups"] == []


def test_architecture_sentinel_legacy_projection_matches_immutable_start() -> None:
    from uquant.risk_sentinel.source_identity_archive import immutable_source_identity_archive

    projected = dict(immutable_source_identity_archive())
    paths = subprocess.check_output(
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            ARCHITECTURE_REFERENCE_TREE,
            "uquant/risk_sentinel",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    expected = tuple(sorted(path for path in paths if path.endswith(".py")))
    assert tuple(projected) == expected
    for path in expected:
        assert projected[path] == subprocess.check_output(
            ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:{path}"],
            cwd=ROOT,
        )


def test_architecture_portfolio_pipeline_has_one_combined_capital_owner() -> None:
    pipeline = validate_combined_allocator_topology(root=ROOT)
    assert pipeline.name == "_allocate_strategy"
    from uquant.portfolio import PortfolioAllocator
    from uquant.portfolio.pipeline import allocate_strategy

    assert PortfolioAllocator._allocate_strategy is allocate_strategy


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        ("uquant/portfolio/pipeline.py", "date=date, risk=risk, user_panel=user_panel", "date=date, risk=account, user_panel=user_panel"),
        ("uquant/portfolio/pipeline.py", "proposed=book.proposed, leaders=book.leaders", "proposed=book.weights_now, leaders=book.leaders"),
        ("uquant/portfolio/pipeline.py", "committed=self.committed, cash_room=self.cash_room", "committed=self.proposed, cash_room=self.cash_room"),
        ("uquant/portfolio/pipeline.py", "committed_capital(account=account,", "committed_capital(account=None,"),
        ("uquant/portfolio/pipeline.py", "from .capital import committed_capital, funded_increment", "from ..capital import committed_capital, funded_increment"),
        ("uquant/portfolio/capital.py", "{**committed, symbol: current}", "{symbol: current}"),
        ("uquant/portfolio/pipeline.py", "assess_strategic_capital_authority(account)", "assess_strategic_capital_authority(None)"),
        ("uquant/portfolio/pipeline.py", "owned, strategic_targets, proposed, committed, cash_room)", "owned, strategic_targets, dict(proposed), committed, cash_room)"),
        ("uquant/portfolio/pipeline.py", "gross_cap=self.gross_cap,", "gross_cap=self.policy.cfg.max_gross,"),
        ("uquant/portfolio/pipeline.py", "return min(self.policy.cfg.max_gross, self.risk.target_gross_cap)", "return self.policy.cfg.max_gross"),
        ("uquant/portfolio/pipeline.py", "return accepted", "self.account.cash = 0.0\n        return accepted"),
        ("uquant/portfolio/pipeline.py", "return targets", "return strategic"),
        ("uquant/portfolio/pipeline.py", "return targets", "account.cash = 0.0\n    return targets"),
        ("uquant/portfolio/pipeline.py", "return targets", "account.positions.clear()\n    return targets"),
        ("uquant/portfolio/pipeline.py", "return targets", "account.pending_orders.append(None)\n    return targets"),
    ),
)
def test_combined_allocator_contract_rejects_authority_and_split_book_mutations(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    source = architecture_portfolio_reviewed_sources(root=ROOT)[relative]
    assert original in source
    with pytest.raises(AssertionError):
        validate_combined_allocator_topology(
            root=ROOT,
            overrides={relative: source.replace(original, mutation, 1)},
        )


@pytest.mark.parametrize("original, mutation", (
    ("cash_room=cash_room + released", "cash_room=cash_room + released + 1.0"),
    ("committed={**committed, weakest: remaining}", "committed={weakest: remaining}"),
    ("date=date, gross_cap=gross_cap,", "date=date, gross_cap=self.cfg.max_gross,"),
    ("if feasible + 1e-12 < self.cfg.core_admission_weight:", "if False:"),
    ("if released + 1e-12 < self.cfg.min_trade_weight:", "if False:"),
    ("proposed[weakest] = remaining", "proposed[challenger] = feasible"),
    ("released = weights_now[weakest] - remaining", "committed.clear()\n    released = weights_now[weakest] - remaining"),
    ("committed=book.committed, cash_room=book.cash_room", "committed=book.proposed, cash_room=book.cash_room"),
    ('book.record(symbol)["entry_gate"] = "AWAIT_REDUCTION_SETTLEMENT"',
     'book.record(symbol)["entry_gate"] = "AWAIT_REDUCTION_SETTLEMENT"\n            book.cash_room += 1.0'),
    ("committed=self.committed, cash_room=self.cash_room", "committed=self.committed, cash_room=self.cash_room + 0.25"),
    ("committed=self.committed, cash_room=self.cash_room", "committed={}, cash_room=self.cash_room"),
    ("feasible = funded_increment(", "feasible = float("),
))
def test_combined_allocator_feasibility_rejects_unsettled_budget_mutations(
    original: str, mutation: str,
) -> None:
    relative = "uquant/portfolio/pipeline.py"
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert source.count(original) == 1
    with pytest.raises(AssertionError):
        validate_combined_allocator_topology(
            root=ROOT, overrides={relative: source.replace(original, mutation, 1)},
        )


def test_architecture_risk_market_transport_rejects_delegation_argument_mutation() -> None:
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
        expand_architecture_risk_market_stage(
            root=ROOT,
            wrapper=wrapper,
            overrides={relative: mutated},
        )


def test_architecture_risk_assessment_transport_rejects_stage_argument_mutation() -> None:
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
        expand_architecture_risk_assessment(
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
def test_architecture_owner_transport_rejects_owner_mutations(
    relative: str,
    name: str,
    original: str,
    mutation: str,
) -> None:
    source = architecture_portfolio_reviewed_sources(root=ROOT)[relative]
    assert original in source
    mutated = source.replace(original, mutation, 1)
    candidate = next(
        node
        for node in ast.parse(mutated).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    with pytest.raises(AssertionError):
        expand_portfolio_allocator_method(
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
def test_reviewed_owner_transport_rejects_unknown_mutations(
    relative: str,
    name: str,
    original: str,
    mutation: str,
) -> None:
    source = reviewed_architecture_owner_source(ROOT, relative, name)
    assert original in source
    mutated = source.replace(original, mutation, 1)
    candidate = next(
        node
        for node in ast.parse(mutated).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    with pytest.raises(AssertionError):
        expand_reviewed_architecture_owner(
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
def test_architecture_config_private_transport_rejects_identity_callee_and_argument_mutations(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    source = config_reviewed_source(ROOT, relative)
    assert original in source
    mutated = source.replace(original, mutation, 1)
    with pytest.raises(AssertionError):
        validate_config_private_transport(
            root=ROOT,
            source_overrides={relative: mutated},
        )


@pytest.mark.parametrize(
    ("relative", "original", "mutation"),
    (
        (
            "uquant/application/decision.py",
            "attach_target_attribution_fn=attach_target_attribution_fn,",
            "attach_target_attribution_fn=attach_target_attribution_unknown,",
        ),
        (
            "uquant/application/target_attribution.py",
            "from ..types import PendingOrder, Side, Target, derive_attribution_event_id",
            "from ..types import AccountState, PendingOrder, Side, Target, derive_attribution_event_id",
        ),
    ),
)
def test_architecture_execution_decision_owner_transport_rejects_unknown_mutations(
    relative: str,
    original: str,
    mutation: str,
) -> None:
    source = execution_reviewed_source(ROOT, relative)
    assert original in source
    mutated = source.replace(original, mutation, 1)
    with pytest.raises(AssertionError):
        validate_execution_decision_owner_transport(
            root=ROOT,
            source_overrides={relative: mutated},
        )


def test_architecture_execution_debt_transport_rejects_unknown_stage_arguments() -> None:
    relative = "uquant/execution/order_planning.py"
    source = execution_reviewed_source(ROOT, relative)
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
        reviewed_execution_debt_definition(
            root=ROOT,
            relative=relative,
            name="plan_orders",
            candidate=candidate,
            frozen=copy.deepcopy(candidate),
            source_overrides={relative: mutated},
        )


def test_architecture_current_blockers_match_empty_acceptance_allowlist(
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
            "architecture_complexity_signals",
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


def test_architecture_current_physical_size_signals_are_recorded(
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
        CURRENT_GOVERNED_SCRIPTS.get(relative, relative)
        for relative in GOVERNED_SCRIPTS
        if len(
            (
                ROOT / CURRENT_GOVERNED_SCRIPTS.get(relative, relative)
            ).read_text(encoding="utf-8").splitlines()
        )
        >= 300
    )
    request.node.user_properties.append(
        (
            "architecture_physical_size_signals",
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
