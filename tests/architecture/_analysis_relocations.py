"""Architecture owner-relocation declarations."""

from __future__ import annotations

from ._analysis_authorities import _CONTRACT_RELOCATIONS

_PORTFOLIO_RELOCATED_PRIVATE_IMPORT_GROUPS = (
    (
        "uquant.portfolio",
        "uquant.portfolio.allocator",
        ("_confirmed_recovery_gross",),
    ),
    (
        "uquant.portfolio",
        "uquant.portfolio.freeze",
        ("_commit_frozen_exit_state", "_frozen_existing_targets"),
    ),
    (
        "uquant.portfolio",
        "uquant.portfolio.pipeline",
        ("_allocate_strategy",),
    ),
    (
        "uquant.portfolio",
        "uquant.portfolio.risk_reduction",
        (
            "_risk_attribution_mechanism",
            "_risk_lifecycle_rank",
            "_risk_reduction_metadata",
            "_risk_retention_score",
            "_risk_retention_vector",
            "_sparse_risk_reduce",
            "_subset_retention_vector",
            "_turnover_aware_sector_cap",
        ),
    ),
    (
        "uquant.portfolio.leaders",
        "uquant.portfolio.leaders.admission",
        (
            "_admission_utility",
            "_conviction_evidence_qualified",
            "_conviction_shares",
            "_correlations",
            "_dynamic_k",
        ),
    ),
    (
        "uquant.portfolio.leaders",
        "uquant.portfolio.leaders.lifecycle",
        (
            "_industry_handoff",
            "_leader_lifecycle_exit_confirmed",
            "_retention_score",
            "_rotation_allowed",
            "_session_clock",
            "_leader_session_distance",
            "_update_leader_cycle_arm",
        ),
    ),
    (
        "uquant.portfolio.leaders",
        "uquant.portfolio.leaders.targets",
        ("_cap_opportunity_gross", "_leader_targets"),
    ),
    (
        "uquant.portfolio.strategic",
        "uquant.portfolio.strategic.discovery",
        ("_initialize_strategic_cohort",),
    ),
    (
        "uquant.portfolio.strategic",
        "uquant.portfolio.strategic.lifecycle",
        (
            "_bounded_strategic_restore_risk_open",
            "_retire_strategic_member",
            "_strategic_cohort_targets",
        ),
    ),
    (
        "uquant.portfolio.strategic.lifecycle",
        "uquant.portfolio.strategic.targets",
        ("_strategic_active_targets", "_strategic_completed_exit_targets"),
    ),
    (
        "uquant.portfolio.pipeline",
        "uquant.portfolio.recovery.admission",
        ("_recovery_admission_targets",),
    ),
    (
        "uquant.portfolio.recovery",
        "uquant.portfolio.recovery.substitution",
        ("_recovery_anchor_substitution",),
    ),
    (
        "uquant.portfolio.recovery.admission",
        "uquant.portfolio.recovery.targets",
        (
            "_awaiting_recovery_cohort_targets",
            "_controlled_oversold_rebound_targets",
            "_locked_recovery_cohort_targets",
            "_overextended_pullback_targets",
            "_recovery_cohort_targets",
        ),
    ),
    (
        "uquant.portfolio.recovery.substitution",
        "uquant.portfolio.recovery.targets",
        (
            "_confirmed_recovery_substitution_targets",
            "_pending_recovery_substitution_targets",
        ),
    ),
)

_PORTFOLIO_RELOCATED_PRIVATE_IMPORTS = frozenset(
    f"{importer}:{imported_from}:{name}"
    for importer, imported_from, names in _PORTFOLIO_RELOCATED_PRIVATE_IMPORT_GROUPS
    for name in names
)

_VALIDATION_RELOCATED_PRIVATE_IMPORT_GROUPS = (
    (
        "uquant.validation.generalization",
        "uquant.validation.generalization.baseline",
        ("_parse_policy", "_policy_number", "_read_generalization_baseline", "_reject_duplicate_keys", "_reject_nonstandard_constant", "_validate_baseline_envelope"),
    ),
    ("uquant.validation.generalization", "uquant.validation.generalization.gates", ("_aggregate_gate_results", "_reference_aggregate", "_relative_change")),
    ("uquant.validation.generalization", "uquant.validation.generalization.metrics", ("_deployment_from_result", "_quantile")),
    (
        "uquant.validation.generalization",
        "uquant.validation.generalization.models",
        ("_BASELINE_SCHEMA_VERSION", "_COMMIT", "_COMPETITOR_BEST_FIELDS", "_COMPETITOR_PROVENANCE_FIELDS", "_EXECUTION_CONTRACT", "_FIXED_PRODUCTION_PATHS", "_POLICY_FIELDS", "_PROVENANCE_SECTIONS", "_REFERENCE_FIELDS", "_SHA256"),
    ),
    (
        "uquant.validation.generalization",
        "uquant.validation.generalization.provenance",
        ("_exact_fields", "_fingerprint", "_git_executable", "_git_stdout", "_immutable_validation_inputs", "_nonempty_text", "_production_commit", "_production_source_fingerprint", "_validated_competitor_best", "_validated_provenance", "_validation_fingerprint"),
    ),
    ("uquant.validation.generalization", "uquant.validation.generalization.scenarios", ("_canonical_symbols", "_derived_seed", "_slug", "_unique_integers", "_validate_industry_coverage")),
    ("uquant.validation.generalization.baseline", "uquant.validation.generalization.models", ("_BASELINE_SCHEMA_VERSION", "_POLICY_FIELDS", "_REFERENCE_FIELDS", "_SHA256")),
    ("uquant.validation.generalization.baseline", "uquant.validation.generalization.provenance", ("_validated_competitor_best", "_validated_provenance", "_validation_fingerprint")),
    ("uquant.validation.generalization.gates", "uquant.validation.generalization.metrics", ("_quantile",)),
    ("uquant.validation.generalization.provenance", "uquant.validation.generalization.models", ("_COMMIT", "_COMPETITOR_BEST_FIELDS", "_COMPETITOR_PROVENANCE_FIELDS", "_EXECUTION_CONTRACT", "_FIXED_PRODUCTION_PATHS", "_PROVENANCE_SECTIONS", "_SHA256")),
    ("uquant.validation.generalization.provenance", "uquant.validation.generalization.scenarios", ("_canonical_symbols", "_validate_industry_coverage")),
    ("uquant.validation.generalization.runner", "uquant.validation.generalization.baseline", ("_read_generalization_baseline", "_validate_baseline_envelope")),
    ("uquant.validation.generalization.runner", "uquant.validation.generalization.provenance", ("_immutable_validation_inputs", "_production_commit", "_production_source_fingerprint", "_validated_provenance")),
    ("uquant.validation.generalization.runner", "uquant.validation.generalization.scenarios", ("_canonical_symbols", "_validate_industry_coverage")),
    ("uquant.validation.generalization_policy.cells", "uquant.validation.generalization_policy.projection", ("_attribution_neutral_equality_sha256",)),
    (
        "uquant.validation.generalization_policy.cells",
        "uquant.validation.generalization_policy.schema",
        ("_ATTRIBUTION_DEFINITION", "_BASELINE_CELL_FIELDS", "_COMMIT", "_artifact_equality_sha256", "_derived_seed", "_metric_payload", "_read_json", "_reject_duplicate_keys", "_reject_nonstandard_constant", "_replay_error", "_require_exact_seal", "_require_sha256"),
    ),
    ("uquant.validation.generalization_policy.evaluator", "uquant.validation.generalization_matrix", ("_head_and_source",)),
    ("uquant.validation.generalization_policy.evaluator", "uquant.validation.generalization_policy.projection", ("_attribution_neutral_equality_sha256", "_candidate_contract_sha256")),
    (
        "uquant.validation.generalization_policy.evaluator",
        "uquant.validation.generalization_policy.schema",
        ("_ARTIFACT_FIELDS_V1", "_ARTIFACT_FIELDS_V2", "_ATTRIBUTION_DEFINITION", "_CELL_FIELDS_V1", "_CELL_FIELDS_V2", "_EVIDENCE_FIELDS", "_ROOT", "_artifact_equality_sha256", "_metric_payload", "_metrics_reconciled_from_raw", "_provenance_schema_failures", "_replay_error", "_schema_failures"),
    ),
    ("uquant.validation.generalization_policy.projection", "uquant.validation.generalization_policy.schema", ("_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS", "_DEPRECATED_V1_ATTRIBUTION_TOKEN", "_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256", "_artifact_equality_sha256", "_hash_json")),
    ("uquant.validation.generalization_reference", "uquant.validation.generalization_policy.cells", ("_load_baseline_cells",)),
    ("uquant.validation.generalization_reference", "uquant.validation.generalization_policy.evaluator", ("_RandomTailStatistics", "_evaluate_recovered_against_group_envelope", "_quantile", "_random_tail_statistics", "_violates_effective_floor")),
    ("uquant.validation.generalization_reference", "uquant.validation.generalization_policy.projection", ("_attribution_neutral_equality_sha256", "_candidate_contract_sha256", "_project_raw_evidence_for_frozen_v1", "_v2_economic_projection")),
    (
        "uquant.validation.generalization_reference",
        "uquant.validation.generalization_policy.schema",
        ("_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS", "_ARTIFACT_FIELDS_V1", "_ARTIFACT_FIELDS_V2", "_ATTRIBUTION_DEFINITION", "_BASELINE_CELL_FIELDS", "_CELL_FIELDS_V1", "_CELL_FIELDS_V2", "_COMMIT", "_DATA_FIELDS", "_DEPRECATED_V1_ATTRIBUTION_TOKEN", "_EVIDENCE_FIELDS", "_METRIC_FIELDS", "_PROVENANCE_FIELDS", "_REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256", "_ROOT", "_RUNTIME_FIELDS", "_SHA256", "_artifact_equality_sha256", "_canonical_sha256", "_derived_seed", "_hash_json", "_metric_payload", "_metrics_reconciled_from_raw", "_provenance_schema_failures", "_read_json", "_reject_duplicate_keys", "_reject_nonstandard_constant", "_replay_error", "_require_exact_seal", "_require_sha256", "_schema_failures"),
    ),
    ("uquant.validation.holdout.checkpoints", "uquant.validation.holdout.artifact_transaction", ("_read_protected_artifact", "_resolved_path_text")),
    ("uquant.validation.holdout.checkpoints", "uquant.validation.holdout.contract", ("_canonical_sha256", "_read_json", "_session_dates")),
    ("uquant.validation.holdout.manifest", "uquant.validation.holdout.contract", ("_MANIFEST_FIELDS", "_SHA256", "_canonical_sha256", "_session_dates")),
    ("uquant.validation.holdout.manifest", "uquant.validation.holdout.source_identity", ("_state_hashes",)),
    ("uquant.validation.holdout.replay", "uquant.validation.holdout.contract", ("_canonical_sha256", "_read_json", "_session_dates")),
    ("uquant.validation.holdout.replay", "uquant.validation.holdout.manifest", ("_normalized_scores", "_validated_score_values")),
    ("uquant.validation.holdout.replay", "uquant.validation.holdout.snapshots", ("_capture_holdout_data", "_materialize_overlay")),
    ("uquant.validation.holdout.service", "uquant.validation.holdout.artifact_transaction", ("_artifact_bundle_lock", "_artifact_bundle_lock_path", "_artifact_bundle_lock_paths", "_artifact_snapshots", "_canonical_carrier_path", "_read_protected_artifact", "_reject_authoritative_output_paths", "_reject_output_in_protected_data", "_resolved_path_text", "_restore_artifact_snapshots")),
    ("uquant.validation.holdout.service", "uquant.validation.holdout.checkpoints", ("_checkpoint_payload", "_read_checkpoint_carrier", "_validate_daily_replay_continuity", "_verify_checkpoint_artifacts")),
    ("uquant.validation.holdout.service", "uquant.validation.holdout.contract", ("_CHECKPOINT_RELATIVE", "_closed_csv_files", "_git_executable", "_read_json", "_repository_root")),
    ("uquant.validation.holdout.service", "uquant.validation.holdout.manifest", ("_assemble_future_holdout_manifest", "_normalized_scores", "_validate_future_holdout_manifest_payload")),
    ("uquant.validation.holdout.service", "uquant.validation.holdout.replay", ("_daily_decision_payload",)),
    ("uquant.validation.holdout.service", "uquant.validation.holdout.snapshots", ("_capture_holdout_data", "_validated_snapshot_prefix_sha256")),
    ("uquant.validation.holdout.snapshots", "uquant.validation.holdout.contract", ("_CHECKPOINT_RELATIVE", "_closed_csv_files", "_csv_dates_from_text", "_session_dates")),
    ("uquant.validation.holdout.source_identity", "uquant.validation.holdout.contract", ("_ACCOUNT_EXECUTION_FIELDS", "_CLI_OPERATIONAL_COMMANDS", "_COMMIT", "_SHA256", "_STRATEGY_FIXED_RELATIVES", "_STRATEGY_OPERATIONAL_RELATIVES", "_canonical_sha256", "_git_executable", "_repository_root")),
    ("uquant.validation.holdout_runtime", "uquant.validation.holdout.contract", ("_CHECKPOINT_RELATIVE",)),
)

_VALIDATION_RELOCATED_PRIVATE_IMPORTS = frozenset(
    f"{importer}:{imported_from}:{name}"
    for importer, imported_from, names in _VALIDATION_RELOCATED_PRIVATE_IMPORT_GROUPS
    for name in names
)

_VALIDATION_RELOCATED_FUNCTION_DEBT = {
    **{
        f"{owner}:{name}": (f"uquant.validation.generalization:{name}", overhead)
        for owner, names, overhead in (
            ("uquant.validation.generalization.baseline", ("_parse_policy",), 0),
            ("uquant.validation.generalization.gates", ("evaluate_generalization",), 0),
            ("uquant.validation.generalization.metrics", ("symbol_pnl_from_result",), 0),
            ("uquant.validation.generalization.provenance", ("_validated_provenance",), 0),
            ("uquant.validation.generalization.runner", ("run_generalization",), 4),
            ("uquant.validation.generalization.scenarios", ("build_generalization_scenarios",), 0),
        )
        for name in names
    },
    **{
        f"{owner}:{name}": (f"uquant.validation.generalization_reference:{name}", 0)
        for owner, names in (
            ("uquant.validation.generalization_policy.cells", ("_load_baseline_cells", "load_generalization_baseline", "load_generalization_policy")),
            ("uquant.validation.generalization_policy.evaluator", ("evaluate_generalization_policy_artifact",)),
            ("uquant.validation.generalization_policy.projection", ("_project_raw_evidence_for_frozen_v1",)),
            ("uquant.validation.generalization_policy.schema", ("_provenance_schema_failures",)),
        )
        for name in names
    },
    **{
        f"uquant.validation.holdout.contract:{name}": (
            f"uquant.validation.holdout:{name}",
            0,
        )
        for name in ("load_future_holdout_contract", "validate_holdout_layout")
    },
    "uquant.validation.holdout.lanes:validate_lane_registry": (
        "uquant.validation.holdout_lanes:validate_lane_registry",
        0,
    ),
    **{
        f"uquant.validation.holdout.{owner}:{name}": (
            f"uquant.validation.holdout_runtime:{name}",
            overhead,
        )
        for owner, names, overhead in (
            ("checkpoints", ("_read_checkpoint_carrier",), 0),
            ("replay", ("replay_future_holdout", "read_future_holdout_replay"), 0),
            ("service", ("_generate_future_holdout_replay_locked",), 0),
            ("snapshots", ("append_holdout_snapshot",), 2),
        )
        for name in names
    },
}

_VALIDATION_RELOCATED_GLOBAL_DEBT = {
    **{
        f"uquant.validation.generalization.models:{name}": (
            f"uquant.validation.generalization:{name}"
        )
        for name in (
            "_COMPETITOR_BEST_FIELDS",
            "_COMPETITOR_PROVENANCE_FIELDS",
            "_EXECUTION_CONTRACT",
            "_POLICY_FIELDS",
            "_PROVENANCE_SECTIONS",
            "_REFERENCE_FIELDS",
        )
    },
    **{
        f"uquant.validation.generalization_policy.schema:{name}": (
            f"uquant.validation.generalization_reference:{name}"
        )
        for name in (
            "_ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS",
            "_ARTIFACT_FIELDS_V1",
            "_ARTIFACT_FIELDS_V2",
            "_ATTRIBUTION_DEFINITION",
            "_BASELINE_CELL_FIELDS",
            "_CELL_FIELDS_V1",
            "_CELL_FIELDS_V2",
            "_DATA_FIELDS",
            "_DEPRECATED_V1_ATTRIBUTION_TOKEN",
            "_EVIDENCE_FIELDS",
            "_METRIC_FIELDS",
            "_PROVENANCE_FIELDS",
            "_RUNTIME_FIELDS",
        )
    },
    **{
        f"uquant.validation.holdout.contract:{name}": f"uquant.validation.holdout:{name}"
        for name in (
            "_ACCOUNT_EXECUTION_FIELDS",
            "_CLI_OPERATIONAL_COMMANDS",
            "_CONTRACT_FIELDS",
            "_MANIFEST_FIELDS",
            "_STRATEGY_FIXED_RELATIVES",
            "_STRATEGY_OPERATIONAL_RELATIVES",
        )
    },
    **{
        f"uquant.validation.holdout.lanes:{name}": f"uquant.validation.holdout_lanes:{name}"
        for name in ("_BEHAVIORS", "_LANE_FIELDS", "_REGISTRY_FIELDS", "_RUNTIME_FIELDS")
    },
    "uquant.validation.holdout.checkpoints:_CHECKPOINT_FIELDS": (
        "uquant.validation.holdout_runtime:_CHECKPOINT_FIELDS"
    ),
    **{
        f"uquant.validation.holdout.replay:{name}": f"uquant.validation.holdout_runtime:{name}"
        for name in ("_DAILY_DECISION_FIELDS", "_REPLAY_FIELDS")
    },
}

_PORTFOLIO_RELOCATED_FUNCTION_NAMES = {
    "_leader_session_distance": "_session_distance",
}

_PORTFOLIO_RELOCATED_FUNCTION_DEBT = {
    **{
        f"{owner}:{name}": f"uquant.portfolio:PortfolioAllocator.{name}"
        for owner, names in (
            (
                "uquant.portfolio.allocator",
                ("_confirmed_recovery_gross", "allocate"),
            ),
            (
                "uquant.portfolio.freeze",
                ("_commit_frozen_exit_state", "_frozen_existing_targets"),
            ),
            ("uquant.portfolio.pipeline", ("_allocate_strategy",)),
            (
                "uquant.portfolio.risk_reduction",
                (
                    "_risk_attribution_mechanism",
                    "_risk_lifecycle_rank",
                    "_risk_reduction_metadata",
                    "_risk_retention_score",
                    "_risk_retention_vector",
                    "_sparse_risk_reduce",
                    "_subset_retention_vector",
                    "_turnover_aware_sector_cap",
                ),
            ),
        )
        for name in names
    },
    **{
        f"{owner}:{name}": (
            "uquant.portfolio_leaders:LeaderPortfolioPolicy."
            f"{_PORTFOLIO_RELOCATED_FUNCTION_NAMES.get(name, name)}"
        )
        for owner, names in (
            (
                "uquant.portfolio.leaders.admission",
                (
                    "_admission_utility",
                    "_conviction_evidence_qualified",
                    "_conviction_shares",
                    "_correlations",
                    "_dynamic_k",
                ),
            ),
            (
                "uquant.portfolio.leaders.lifecycle",
                (
                    "_industry_handoff",
                    "_leader_lifecycle_exit_confirmed",
                    "_retention_score",
                    "_rotation_allowed",
                    "_session_clock",
                    "_leader_session_distance",
                    "_update_leader_cycle_arm",
                ),
            ),
            (
                "uquant.portfolio.leaders.targets",
                ("_cap_opportunity_gross", "_leader_targets"),
            ),
        )
        for name in names
    },
    **{
        f"{owner}:{name}": (
            f"uquant.portfolio_strategic:StrategicPortfolioPolicy.{name}"
        )
        for owner, names in (
            (
                "uquant.portfolio.strategic.discovery",
                ("_initialize_strategic_cohort",),
            ),
            (
                "uquant.portfolio.strategic.lifecycle",
                (
                    "_bounded_strategic_restore_risk_open",
                    "_retire_strategic_member",
                    "_strategic_cohort_targets",
                ),
            ),
        )
        for name in names
    },
    **{
        f"uquant.portfolio.recovery.{owner}:{name}": (
            "uquant.portfolio:PortfolioAllocator._allocate_strategy"
        )
        for owner, names in (
            ("admission", ("_recovery_admission_targets",)),
            (
                "targets",
                (
                    "_awaiting_recovery_cohort_targets",
                    "_controlled_oversold_rebound_targets",
                    "_locked_recovery_cohort_targets",
                    "_overextended_pullback_targets",
                    "_recovery_cohort_targets",
                ),
            ),
        )
        for name in names
    },
    **{
        f"uquant.portfolio.recovery.{owner}:{name}": (
            "uquant.portfolio_recovery:RecoveryPortfolioPolicy."
            "_recovery_anchor_substitution"
        )
        for owner, names in (
            ("substitution", ("_recovery_anchor_substitution",)),
            (
                "targets",
                (
                    "_confirmed_recovery_substitution_targets",
                    "_pending_recovery_substitution_targets",
                ),
            ),
        )
        for name in names
    },
}

_PORTFOLIO_ALLOCATE_STRATEGY_DEBT = frozenset(
    identifier
    for identifier, legacy in _PORTFOLIO_RELOCATED_FUNCTION_DEBT.items()
    if legacy == "uquant.portfolio:PortfolioAllocator._allocate_strategy"
)

_PORTFOLIO_RELOCATED_TYPE_IGNORES = {
    f"uquant/portfolio/risk_reduction.py:{suffix}": f"uquant/portfolio.py:{suffix}"
    for suffix in (
        "[arg-type]:self._risk_lifecycle_rank(retained_vector),  # type: ignore[arg-type]:0",
        "[return-value]:return tuple(max(0.0, value) for value in retained)  # type: ignore[return-value]:0",
        "[return-value]:return tuple(sum(vector[index] for vector in vectors) for index in range(6))  # type: ignore[return-value]:0",
    )
}

_EXECUTION_RELOCATED_FUNCTION_DEBT = {
    "uquant.application.backtest:backtest": ("uquant.engine:ProductionEngine.backtest", 0),
    "uquant.application.decision:_attach_target_attribution": (
        "uquant.engine:_attach_target_attribution",
        2,
    ),
    "uquant.application.decision:decide": ("uquant.engine:ProductionEngine.decide", 0),
    "uquant.application.metrics:performance_metrics": ("uquant.engine:performance_metrics", 0),
    "uquant.execution.open_execution:ExecutionPlanner.execute_open": (
        "uquant.execution:ExecutionPlanner.execute_open",
        0,
    ),
    "uquant.execution.order_planning:plan_orders": ("uquant.execution:plan_orders", 0),
    "uquant.execution.pending:merge_pending_orders": ("uquant.execution:merge_pending_orders", 0),
    "uquant.execution.reconciliation:_reconcile_account_orders_mutating": (
        "uquant.execution:_reconcile_account_orders_mutating",
        0,
    ),
}

_EXECUTION_RELOCATED_GLOBAL_DEBT = {
    "uquant.execution.tranches:_RISK_LIFECYCLE_PRIORITY": (
        "uquant.execution:_RISK_LIFECYCLE_PRIORITY"
    ),
}

_PUBLIC_API_IMPLEMENTATIONS = {
    legacy: current for current, legacy in _CONTRACT_RELOCATIONS.items()
}

_PUBLIC_API_FACADE_PATHS = {
    # Compatibility ownership converts the stable import path to its package owner.
    # The immutable baseline contract continues to name the historical .py facade.
    "uquant.config": "uquant/config.py",
    # Configuration ownership performs the same transition for these facades.
    "uquant.account": "uquant/account.py",
    "uquant.attribution": "uquant/attribution.py",
    # Execution ownership preserves the historical path as a same-name package facade.
    "uquant.execution": "uquant/execution.py",
    # Risk ownership preserves the public Base Risk import path through its package facade.
    "uquant.risk": "uquant/risk.py",
    # Portfolio ownership preserves the public allocator path through its package facade.
    "uquant.portfolio": "uquant/portfolio.py",
    # Validation ownership preserves generalization's public path through its package facade.
    "uquant.validation.generalization": "uquant/validation/generalization.py",
    # Validation ownership preserves Holdout's public path through its same-name package facade.
    "uquant.validation.holdout": "uquant/validation/holdout.py",
}

_MUTABLE_CALLS = {
    "collections.defaultdict",
    "defaultdict",
    "dict",
    "list",
    "set",
}

_MUTATING_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
}
