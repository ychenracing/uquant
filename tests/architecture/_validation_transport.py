"""Exact transport from frozen validation evidence to current domain owners."""

from __future__ import annotations

import ast
import copy
import hashlib
from collections.abc import Mapping, Sequence, Set
from pathlib import Path

_REVIEWED_OWNERS = frozenset(
    {
        "uquant/validation/generalization/models.py",
        "uquant/validation/generalization/scenarios.py",
        "uquant/validation/generalization/provenance.py",
        "uquant/validation/generalization/baseline.py",
        "uquant/validation/generalization/metrics.py",
        "uquant/validation/generalization/gates.py",
        "uquant/validation/generalization/runner.py",
        "uquant/validation/generalization_policy/schema.py",
        "uquant/validation/generalization_policy/cells.py",
        "uquant/validation/generalization_policy/projection.py",
        "uquant/validation/generalization_policy/evaluator.py",
        "uquant/validation/holdout/contract.py",
        "uquant/validation/holdout/manifest.py",
        "uquant/validation/holdout/source_identity.py",
        "uquant/validation/holdout/lanes.py",
        "uquant/validation/holdout/snapshots.py",
        "uquant/validation/holdout/replay.py",
        "uquant/validation/holdout/checkpoints.py",
        "uquant/validation/holdout/artifact_transaction.py",
        "uquant/validation/holdout/service.py",
    }
)

_SCHEMA_ALIASES = {
    "ARTIFACT_FIELDS_V1": "_ARTIFACT_FIELDS_V1",
    "ARTIFACT_FIELDS_V2": "_ARTIFACT_FIELDS_V2",
    "ATTRIBUTION_DEFINITION": "_ATTRIBUTION_DEFINITION",
    "CELL_FIELDS_V1": "_CELL_FIELDS_V1",
    "CELL_FIELDS_V2": "_CELL_FIELDS_V2",
    "EVIDENCE_FIELDS": "_EVIDENCE_FIELDS",
    "ROOT": "_ROOT",
    "artifact_equality_sha256": "_artifact_equality_sha256",
    "metric_payload": "_metric_payload",
    "metrics_reconciled_from_raw": "_metrics_reconciled_from_raw",
    "provenance_schema_failures": "_provenance_schema_failures",
    "replay_error": "_replay_error",
    "schema_failures": "_schema_failures",
}
_PROJECTION_ALIASES = {
    "attribution_neutral_equality_sha256": "_attribution_neutral_equality_sha256",
    "candidate_contract_sha256": "_candidate_contract_sha256",
}
_STAGE_CALL_COUNTS = {
    **{("schema", name): 1 for name in _SCHEMA_ALIASES},
    **{("projection", name): 1 for name in _PROJECTION_ALIASES},
    ("schema", "schema_failures"): 3,
}
_POLICY_PRIVATE_IDS = frozenset(
    {
        f"uquant.validation.generalization_policy.evaluator:"
        f"uquant.validation.generalization_policy.{module}:{private}"
        for module, aliases in (
            ("schema", _SCHEMA_ALIASES),
            ("projection", _PROJECTION_ALIASES),
        )
        for private in aliases.values()
    }
)

_PUBLIC_OWNER_ALIASES: Mapping[str, Mapping[str, str]] = {
    "uquant/validation/generalization/models.py": {
        "BASELINE_SCHEMA_VERSION": "_BASELINE_SCHEMA_VERSION",
        "COMMIT_PATTERN": "_COMMIT",
        "COMPETITOR_BEST_FIELDS": "_COMPETITOR_BEST_FIELDS",
        "COMPETITOR_PROVENANCE_FIELDS": "_COMPETITOR_PROVENANCE_FIELDS",
        "EXECUTION_CONTRACT": "_EXECUTION_CONTRACT",
        "FIXED_PRODUCTION_PATHS": "_FIXED_PRODUCTION_PATHS",
        "POLICY_FIELDS": "_POLICY_FIELDS",
        "PROVENANCE_SECTIONS": "_PROVENANCE_SECTIONS",
        "REFERENCE_FIELDS": "_REFERENCE_FIELDS",
        "SHA256_PATTERN": "_SHA256",
    },
    "uquant/validation/generalization/scenarios.py": {
        "canonical_symbols": "_canonical_symbols",
        "derived_seed": "_derived_seed",
        "slug": "_slug",
        "unique_integers": "_unique_integers",
        "validate_industry_coverage": "_validate_industry_coverage",
    },
    "uquant/validation/generalization/provenance.py": {
        "exact_fields": "_exact_fields",
        "fingerprint": "_fingerprint",
        "git_executable": "_git_executable",
        "git_stdout": "_git_stdout",
        "immutable_validation_inputs": "_immutable_validation_inputs",
        "nonempty_text": "_nonempty_text",
        "production_commit": "_production_commit",
        "production_source_fingerprint": "_production_source_fingerprint",
        "validated_competitor_best": "_validated_competitor_best",
        "validated_provenance": "_validated_provenance",
        "validation_fingerprint": "_validation_fingerprint",
    },
    "uquant/validation/generalization/baseline.py": {
        "parse_policy": "_parse_policy",
        "policy_number": "_policy_number",
        "read_generalization_baseline": "_read_generalization_baseline",
        "reject_duplicate_keys": "_reject_duplicate_keys",
        "reject_nonstandard_constant": "_reject_nonstandard_constant",
        "validate_baseline_envelope": "_validate_baseline_envelope",
    },
    "uquant/validation/generalization/metrics.py": {
        "deployment_from_result": "_deployment_from_result",
        "quantile": "_quantile",
    },
    "uquant/validation/generalization/gates.py": {
        "aggregate_gate_results": "_aggregate_gate_results",
        "reference_aggregate": "_reference_aggregate",
        "relative_change": "_relative_change",
    },
    "uquant/validation/generalization_policy/schema.py": {
        "ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS": "_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS",
        "BASELINE_CELL_FIELDS": "_BASELINE_CELL_FIELDS",
        "COMMIT_PATTERN": "_COMMIT",
        "DATA_FIELDS": "_DATA_FIELDS",
        "DEPRECATED_V1_ATTRIBUTION_TOKEN": "_DEPRECATED_V1_ATTRIBUTION_TOKEN",
        "METRIC_FIELDS": "_METRIC_FIELDS",
        "PROVENANCE_FIELDS": "_PROVENANCE_FIELDS",
        "REPOSITORY_ROOT": "_ROOT",
        "REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256": (
            "_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256"
        ),
        "RUNTIME_FIELDS": "_RUNTIME_FIELDS",
        "SHA256_PATTERN": "_SHA256",
        "canonical_sha256": "_canonical_sha256",
        "derived_seed": "_derived_seed",
        "hash_json": "_hash_json",
        "read_json": "_read_json",
        "reject_duplicate_keys": "_reject_duplicate_keys",
        "reject_nonstandard_constant": "_reject_nonstandard_constant",
        "require_exact_seal": "_require_exact_seal",
        "require_sha256": "_require_sha256",
    },
    "uquant/validation/generalization_policy/cells.py": {
        "load_baseline_cells": "_load_baseline_cells",
    },
    "uquant/validation/generalization_policy/projection.py": {
        "project_raw_evidence_for_frozen_v1": "_project_raw_evidence_for_frozen_v1",
        "v2_economic_projection": "_v2_economic_projection",
    },
    "uquant/validation/generalization_policy/evaluator.py": {
        "evaluate_recovered_against_group_envelope": (
            "_evaluate_recovered_against_group_envelope"
        ),
        "quantile": "_quantile",
        "random_tail_statistics": "_random_tail_statistics",
        "violates_effective_floor": "_violates_effective_floor",
    },
    "uquant/validation/holdout/artifact_transaction.py": {
        "AUTHORITATIVE_REPOSITORY_RELATIVES": "_AUTHORITATIVE_REPOSITORY_RELATIVES",
        "ArtifactSnapshot": "_ArtifactSnapshot",
        "artifact_bundle_lock": "_artifact_bundle_lock",
        "artifact_bundle_lock_path": "_artifact_bundle_lock_path",
        "artifact_bundle_lock_paths": "_artifact_bundle_lock_paths",
        "artifact_snapshots": "_artifact_snapshots",
        "canonical_carrier_path": "_canonical_carrier_path",
        "git_metadata_paths": "_git_metadata_paths",
        "link_bytes_if_absent": "_link_bytes_if_absent",
        "paths_overlap": "_paths_overlap",
        "read_protected_artifact": "_read_protected_artifact",
        "reject_authoritative_output_paths": "_reject_authoritative_output_paths",
        "reject_output_in_protected_data": "_reject_output_in_protected_data",
        "resolved_path_text": "_resolved_path_text",
        "restore_artifact_snapshots": "_restore_artifact_snapshots",
        "restore_owned_artifact": "_restore_owned_artifact",
        "tracked_repository_paths": "_tracked_repository_paths",
    },
    "uquant/validation/holdout/checkpoints.py": {
        "CHECKPOINT_FIELDS": "_CHECKPOINT_FIELDS",
        "checkpoint_payload": "_checkpoint_payload",
        "read_checkpoint_carrier": "_read_checkpoint_carrier",
        "validate_daily_replay_continuity": "_validate_daily_replay_continuity",
        "verify_checkpoint_artifacts": "_verify_checkpoint_artifacts",
    },
    "uquant/validation/holdout/contract.py": {
        "ACCOUNT_EXECUTION_FIELDS": "_ACCOUNT_EXECUTION_FIELDS",
        "CHECKPOINT_RELATIVE": "_CHECKPOINT_RELATIVE",
        "CLI_OPERATIONAL_COMMANDS": "_CLI_OPERATIONAL_COMMANDS",
        "COMMIT_PATTERN": "_COMMIT",
        "CONTRACT_FIELDS": "_CONTRACT_FIELDS",
        "MANIFEST_FIELDS": "_MANIFEST_FIELDS",
        "SHA256_PATTERN": "_SHA256",
        "STRATEGY_FIXED_RELATIVES": "_STRATEGY_FIXED_RELATIVES",
        "STRATEGY_OPERATIONAL_RELATIVES": "_STRATEGY_OPERATIONAL_RELATIVES",
        "canonical_sha256": "_canonical_sha256",
        "canonical_bytes": "_canonical_bytes",
        "closed_csv_files": "_closed_csv_files",
        "csv_dates": "_csv_dates",
        "csv_dates_from_text": "_csv_dates_from_text",
        "git_executable": "_git_executable",
        "read_json": "_read_json",
        "read_json_snapshot": "_read_json_snapshot",
        "reject_duplicate_keys": "_reject_duplicate_keys",
        "reject_nonstandard_constant": "_reject_nonstandard_constant",
        "repository_root": "_repository_root",
        "session_dates": "_session_dates",
    },
    "uquant/validation/holdout/lanes.py": {
        "BEHAVIORS": "_BEHAVIORS",
        "COMMIT_PATTERN": "_COMMIT",
        "LANE_FIELDS": "_LANE_FIELDS",
        "LANE_ID_PATTERN": "_LANE_ID",
        "LEGACY_LANE_ID": "_LEGACY_LANE_ID",
        "REGISTRY_FIELDS": "_REGISTRY_FIELDS",
        "RUNTIME_FIELDS": "_RUNTIME_FIELDS",
        "SHA256_PATTERN": "_SHA256",
        "STATUSES": "_STATUSES",
        "canonical_bytes": "_canonical_bytes",
        "canonical_sha256": "_canonical_sha256",
        "decode_lane": "_decode_lane",
        "identity": "_identity",
        "reject_duplicate_keys": "_reject_duplicate_keys",
        "reject_nonstandard_constant": "_reject_nonstandard_constant",
        "validate_hash": "_validate_hash",
    },
    "uquant/validation/holdout/manifest.py": {
        "assemble_future_holdout_manifest": "_assemble_future_holdout_manifest",
        "binding_payload": "_binding_payload",
        "normalized_scores": "_normalized_scores",
        "validate_future_holdout_manifest_payload": (
            "_validate_future_holdout_manifest_payload"
        ),
        "validated_score_values": "_validated_score_values",
    },
    "uquant/validation/holdout/replay.py": {
        "DAILY_DECISION_FIELDS": "_DAILY_DECISION_FIELDS",
        "REPLAY_FIELDS": "_REPLAY_FIELDS",
        "daily_decision_payload": "_daily_decision_payload",
        "decision_payload": "_decision_payload",
        "decision_payload_sha256": "_decision_payload_sha256",
        "drawdown": "_drawdown",
        "period_symbol_pnl": "_period_symbol_pnl",
    },
    "uquant/validation/holdout/snapshots.py": {
        "HoldoutDataSnapshot": "_HoldoutDataSnapshot",
        "capture_holdout_data": "_capture_holdout_data",
        "csv_inventory": "_csv_inventory",
        "materialize_overlay": "_materialize_overlay",
        "merged_csv_text": "_merged_csv_text",
        "one_snapshot_row": "_one_snapshot_row",
        "snapshot_files_sha256": "_snapshot_files_sha256",
        "validated_snapshot_prefix_sha256": "_validated_snapshot_prefix_sha256",
    },
    "uquant/validation/holdout/source_identity.py": {
        "adds_operational_parser": "_adds_operational_parser",
        "assigned_names": "_assigned_names",
        "cli_strategy_ast": "_cli_strategy_ast",
        "command_guard": "_command_guard",
        "git_strategy_relatives": "_git_strategy_relatives",
        "industry_sha256": "_industry_sha256",
        "is_strategy_relative": "_is_strategy_relative",
        "loaded_names": "_loaded_names",
        "parser_strategy_body": "_parser_strategy_body",
        "safe_operational_parser_statement": "_safe_operational_parser_statement",
        "safe_parser_value": "_safe_parser_value",
        "source_sha256": "_source_sha256",
        "source_paths": "_source_paths",
        "state_hashes": "_state_hashes",
        "strategy_account_code_sha256": "_strategy_account_code_sha256",
        "strategy_cli_sha256": "_strategy_cli_sha256",
        "strategy_source_sha256": "_strategy_source_sha256",
        "strategy_source_paths": "_strategy_source_paths",
        "validated_strategy_cli_sha256": "_validated_strategy_cli_sha256",
        "validated_strategy_source_sha256": "_validated_strategy_source_sha256",
    },
    "uquant/validation/holdout/service.py": {
        "generate_future_holdout_replay_locked": "_generate_future_holdout_replay_locked",
        "manifest_repository_root": "_manifest_repository_root",
        "observation_metrics": "_observation_metrics",
    },
}
_EVALUATOR_OWNER_NAMES = {
    "_evaluate_recovered_group_envelope_owner": (
        "evaluate_recovered_against_group_envelope"
    ),
    "_policy_quantile_owner": "policy_quantile",
    "_random_tail_statistics_owner": "random_tail_statistics",
    "_violates_effective_floor_owner": "violates_effective_floor",
}


def _source(
    root: Path,
    relative: str,
    overrides: Mapping[str, str] | None,
) -> str:
    if overrides is not None and relative in overrides:
        return overrides[relative]
    return (root / relative).read_text(encoding="utf-8")


def _alias_nodes_are_exact(
    tree: ast.Module,
    aliases: Mapping[str, str],
) -> set[ast.stmt]:
    matched: set[ast.stmt] = set()
    for public, private in aliases.items():
        nodes = [
            node
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == public
                for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
            )
        ]
        assert len(nodes) == 1
        value = nodes[0].value
        assert isinstance(value, ast.Name) and value.id == private
        matched.add(nodes[0])
    return matched


def _public_import_names() -> dict[str, str]:
    result = {
        **_SCHEMA_ALIASES,
        **_PROJECTION_ALIASES,
        "head_and_source": "_head_and_source",
    }
    for aliases in _PUBLIC_OWNER_ALIASES.values():
        for public, private in aliases.items():
            assert public not in result or result[public] == private
            result[public] = private
    return result


class _PublicOwnerProjection(ast.NodeTransformer):
    def __init__(self) -> None:
        self._imports = _public_import_names()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        node = copy.deepcopy(node)
        normalized: list[ast.alias] = []
        for alias in node.names:
            private = self._imports.get(alias.name)
            if private is not None and alias.asname == private:
                normalized.append(ast.alias(name=private, asname=None))
            elif alias.name == "RandomTailStatistics" and alias.asname == alias.name:
                normalized.append(ast.alias(name=alias.name, asname=None))
            elif alias.asname in _EVALUATOR_OWNER_NAMES:
                assert alias.name == _EVALUATOR_OWNER_NAMES[alias.asname]
                normalized.append(ast.alias(name=alias.name, asname=None))
            else:
                normalized.append(alias)
        node.names = normalized
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node = copy.deepcopy(node)
        node.id = _EVALUATOR_OWNER_NAMES.get(node.id, node.id)
        return node


def _merge_adjacent_imports(tree: ast.Module) -> ast.Module:
    merged: list[ast.stmt] = []
    for node in tree.body:
        if (
            merged
            and isinstance(node, ast.ImportFrom)
            and isinstance(merged[-1], ast.ImportFrom)
            and node.level == merged[-1].level
            and node.module == merged[-1].module
        ):
            merged[-1].names.extend(node.names)
        else:
            merged.append(node)
    for node in merged:
        if isinstance(node, ast.ImportFrom):
            node.names.sort(key=lambda alias: (alias.name, alias.asname or ""))
    tree.body = merged
    return tree


def _project_public_owner_source(
    root: Path,
    *,
    owner: str,
    candidate: str,
) -> str:
    """Prove that a candidate preserves the current public owner exactly."""

    def projected_tree(source: str) -> ast.Module:
        tree = ast.parse(source, type_comments=True)
        aliases = _PUBLIC_OWNER_ALIASES.get(owner, {})
        public_nodes = _alias_nodes_are_exact(tree, aliases) if aliases else set()
        tree.body = [node for node in tree.body if node not in public_nodes]
        return _merge_adjacent_imports(
            ast.fix_missing_locations(_PublicOwnerProjection().visit(tree))
        )

    projected = projected_tree(candidate)
    reviewed = projected_tree((root / owner).read_text(encoding="utf-8"))
    assert ast.dump(projected, include_attributes=False) == ast.dump(
        reviewed, include_attributes=False
    )
    return candidate


def _assert_projected_policy_helpers(
    root: Path,
    overrides: Mapping[str, str] | None,
) -> None:
    for owner in (
        "uquant/validation/generalization_policy/schema.py",
        "uquant/validation/generalization_policy/projection.py",
        "uquant/validation/generalization_policy/evaluation_stages.py",
        "uquant/validation/generalization_policy/cell_policy.py",
        "uquant/validation/generalization_policy/tail_evaluation.py",
    ):
        _project_public_owner_source(
            root,
            owner=owner,
            candidate=_source(root, owner, overrides),
        )


def reviewed_validation_owner_source(
    root: Path,
    *,
    owner: str,
    candidate_sources: Mapping[str, str] | None = None,
) -> str:
    """Return a current owner after its exact candidate projection closes."""

    assert owner in _REVIEWED_OWNERS
    candidate = _source(root, owner, candidate_sources)
    _project_public_owner_source(root, owner=owner, candidate=candidate)
    if owner.endswith("/evaluator.py"):
        _assert_projected_policy_helpers(root, candidate_sources)
    return candidate


def assert_reviewed_validation_owner_transport(root: Path) -> None:
    for owner in _REVIEWED_OWNERS:
        reviewed_validation_owner_source(root, owner=owner)


def _importer_module(relative: str) -> tuple[str, bool]:
    parts = list(Path(relative).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _resolved_import(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = module.split(".") if is_package else module.split(".")[:-1]
    ascend = node.level - 1
    base = package[: len(package) - ascend] if ascend else package
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


class _ScopedImports(ast.NodeVisitor):
    def __init__(self, module: str, is_package: bool) -> None:
        self.module = module
        self.is_package = is_package
        self.scope: list[str] = []
        self.rows: list[tuple[str, str, str, tuple[str, ...], int]] = []

    def _visit_scope(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        owner = _resolved_import(self.module, self.is_package, node)
        self.rows.extend(
            (owner, alias.name, alias.asname or alias.name, tuple(self.scope), node.lineno)
            for alias in node.names
        )
        self.generic_visit(node)


def _scoped_imports(source: str, relative: str) -> _ScopedImports:
    module, is_package = _importer_module(relative)
    visitor = _ScopedImports(module, is_package)
    visitor.visit(ast.parse(source, filename=relative, type_comments=True))
    return visitor


def _facade_module_aliases(tree: ast.Module, module: str, is_package: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _resolved_import(module, is_package, node)
        for alias in node.names:
            owner = f"{base}.{alias.name}".strip(".")
            result[owner] = alias.asname or alias.name
    return result


def assert_validation_importer_public_transports(
    *,
    root: Path,
    rows: Sequence[Mapping[str, object]],
    routes: Mapping[tuple[str, str], tuple[str, str]],
    candidate_sources: Mapping[str, str] | None = None,
) -> None:
    """Project all 189 sealed direct import bindings onto explicit public routes."""

    assert len(rows) == 189
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["path"]), []).append(row)
    direct_count = 0
    facade_count = 0
    facade_path = "uquant/validation/generalization_reference.py"
    current_paths = {
        "research/phase2_ablation_cli.py": "research/generalization_ablation_cli.py",
    }
    local_names = {
        ("research.first_divergence", "_CAUSAL_STAGES"): "TRACE_STAGES",
    }
    for relative, importer_rows in sorted(grouped.items()):
        current_relative = current_paths.get(relative, relative)
        module, is_package = _importer_module(current_relative)
        current_source = _source(root, current_relative, candidate_sources)
        current = _scoped_imports(current_source, current_relative)
        current_tree = ast.parse(
            current_source,
            filename=current_relative,
            type_comments=True,
        )
        module_aliases = _facade_module_aliases(current_tree, module, is_package)
        for row in importer_rows:
            owner = str(row["imported_from"])
            private = str(row["name"])
            local = local_names.get((owner, private), private)
            public_owner, public = routes[(owner, private)]
            if relative != facade_path:
                matches = [
                    item
                    for item in current.rows
                    if item[:3] == (public_owner, public, local)
                ]
                assert len(matches) == 1
                direct_count += 1
                continue
            assert public_owner in module_aliases
            module_alias = module_aliases[public_owner]
            assignments = [
                node
                for node in current_tree.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == local
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == module_alias
                and node.value.attr == public
            ]
            assert len(assignments) == 1
            facade_count += 1
    assert (direct_count, facade_count) == (148, 41)


def validation_private_relocation_projection(
    *,
    root: Path,
    observed: Set[str],
    expected: Set[str],
    overrides: Mapping[str, str] | None = None,
) -> set[str]:
    """Project only the 15 exact public policy edges onto historical IDs."""

    missing = set(expected) - set(observed)
    assert not (set(observed) - set(expected)) and missing == _POLICY_PRIVATE_IDS
    _assert_projected_policy_helpers(root, overrides)
    return set(observed) | missing


def validation_historical_debt_projection(
    *,
    root: Path,
    current_functions: Set[str],
    historical_functions: Set[str],
    current_globals: Set[str],
    historical_globals: Set[str],
) -> tuple[set[str], set[str]]:
    """Keep live-zero acceptance distinct from frozen Task-9 debt identity."""

    assert not current_functions and not current_globals
    function_digest = hashlib.sha256("\n".join(sorted(historical_functions)).encode()).hexdigest()
    global_digest = hashlib.sha256("\n".join(sorted(historical_globals)).encode()).hexdigest()
    assert (len(historical_functions), function_digest) == (
        20,
        "2bb780cac9bfe1badae5df2a27ce64ae8fe006f652d36028a6fd2474ff496a7d",
    )
    assert (len(historical_globals), global_digest) == (
        32,
        "20adf1dcdf7e14e67eec7e20892b9cbbdd3d67d828122d91e4400c25ba6a0fc4",
    )
    assert_reviewed_validation_owner_transport(root)
    return set(historical_functions), set(historical_globals)
