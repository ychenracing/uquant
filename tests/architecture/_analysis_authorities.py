"""Architecture authority and pre-portfolio-relocation declarations."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PUBLIC_API_PATH = ROOT / "benchmarks" / "public_api_contract.json"

HISTORICAL_PUBLIC_API_PATH = ROOT / "benchmarks" / "architecture_refactor_public_api.json"

INVENTORY_PATH = ROOT / "artifacts" / "architecture_refactor" / "baseline_inventory.json"

FINAL_BUDGETS = {
    "max_module_lines": 1000,
    "max_function_lines": 120,
    "max_function_branch_points": 20,
    "max_cross_module_private_imports": 0,
    "max_mutable_module_globals": 0,
    "max_production_type_ignores": 0,
    "max_duplicate_private_helper_groups": 0,
    "max_internal_scc_size": 1,
}

MODULE_AUTHORITIES = {
    "uquant": "production_safe",
    "uquant.__main__": "cli_runner",
    "uquant.account": "production_safe",
    "uquant.account.codec": "production_safe",
    "uquant.account.economic_identity": "production_safe",
    "uquant.account.migrations": "production_safe",
    "uquant.account.store": "production_safe",
    "uquant.account.validation_attribution": "production_safe",
    "uquant.account.validation_common": "production_safe",
    "uquant.account.validation_orders": "production_safe",
    "uquant.account.validation_positions": "production_safe",
    "uquant.account.validation_strategy": "production_safe",
    "uquant.application": "production_safe",
    "uquant.application.backtest": "production_safe",
    "uquant.application.decision": "production_safe",
    "uquant.application.metrics": "production_safe",
    "uquant.application.risk_timeline_cache": "production_safe",
    "uquant.application.target_attribution": "production_safe",
    "uquant.atomic_io": "production_safe",
    "uquant.attribution": "production_safe",
    "uquant.attribution.builder": "production_safe",
    "uquant.attribution.concentration": "production_safe",
    "uquant.attribution.diagnostics": "production_safe",
    "uquant.attribution.ledger": "production_safe",
    "uquant.attribution.replay_evidence": "production_safe",
    "uquant.attribution.validation": "production_safe",
    "uquant.attribution.validation_artifact": "production_safe",
    "uquant.attribution.validation_lots": "production_safe",
    "uquant.broker": "production_safe",
    "uquant.broker_contract": "production_safe",
    "uquant.cli": "cli_runner",
    "uquant.config": "production_safe",
    "uquant.config.model": "production_safe",
    "uquant.config.validation": "production_safe",
    "uquant.config.validation.execution": "production_safe",
    "uquant.config.validation.market": "production_safe",
    "uquant.config.validation.portfolio": "production_safe",
    "uquant.config.validation.recovery": "production_safe",
    "uquant.config.validation.risk": "production_safe",
    "uquant.config.validation.sentinel": "production_safe",
    "uquant.config.validation.strategic": "production_safe",
    "uquant.config.views": "production_safe",
    "uquant.config_governance": "production_safe",
    "uquant.contracts": "production_safe",
    "uquant.contracts.json": "production_safe",
    "uquant.contracts.runtime_identity": "production_safe",
    "uquant.contracts.source_surfaces": "production_safe",
    "uquant.contracts.strict_json": "production_safe",
    "uquant.contracts.universe": "production_safe",
    "uquant.data": "production_safe",
    "uquant.engine": "production_safe",
    "uquant.execution": "production_safe",
    "uquant.execution.fees": "production_safe",
    "uquant.execution.market_constraints": "production_safe",
    "uquant.execution.open_execution": "production_safe",
    "uquant.execution.order_planning": "production_safe",
    "uquant.execution.pending": "production_safe",
    "uquant.execution.reconciliation": "production_safe",
    "uquant.execution.tranches": "production_safe",
    "uquant.execution_journal": "production_safe",
    "uquant.features": "production_safe",
    "uquant.industry": "production_safe",
    "uquant.infrastructure": "production_safe",
    "uquant.infrastructure.atomic_files": "production_safe",
    "uquant.infrastructure.atomic_io": "production_safe",
    "uquant.infrastructure.file_lock": "production_safe",
    "uquant.infrastructure.git_source": "production_safe",
    "uquant.leader": "production_safe",
    "uquant.market": "production_safe",
    "uquant.market.replay": "production_safe",
    "uquant.market.workspace": "production_safe",
    "uquant.market_risk": "production_safe",
    "uquant.models": "production_safe",
    "uquant.models.account": "production_safe",
    "uquant.models.decision": "production_safe",
    "uquant.models.enums": "production_safe",
    "uquant.models.strategic_grant": "production_safe",
    "uquant.models.trading": "production_safe",
    "uquant.observation": "production_safe",
    "uquant.observation.execution_journal": "production_safe",
    "uquant.observation.execution_journal.checkpoint": "production_safe",
    "uquant.observation.execution_journal.codec_v1": "production_safe",
    "uquant.observation.execution_journal.codec_v2": "production_safe",
    "uquant.observation.execution_journal.lifecycle": "production_safe",
    "uquant.observation.execution_journal.models": "production_safe",
    "uquant.observation.execution_journal.rendering": "production_safe",
    "uquant.observation.execution_journal.store": "production_safe",
    "uquant.opportunity": "production_safe",
    "uquant.portfolio": "production_safe",
    "uquant.portfolio.allocation_closure": "production_safe",
    "uquant.portfolio.allocation_opening": "production_safe",
    "uquant.portfolio.allocation_protected": "production_safe",
    "uquant.portfolio.allocation_recovery": "production_safe",
    "uquant.portfolio.allocation_tactical": "production_safe",
    "uquant.portfolio.allocator": "production_safe",
    "uquant.portfolio.context": "production_safe",
    "uquant.portfolio.freeze": "production_safe",
    "uquant.portfolio.leaders": "production_safe",
    "uquant.portfolio.leaders.admission": "production_safe",
    "uquant.portfolio.leaders.extensions": "production_safe",
    "uquant.portfolio.leaders.lifecycle": "production_safe",
    "uquant.portfolio.leaders.targets": "production_safe",
    "uquant.portfolio.pipeline": "production_safe",
    "uquant.portfolio.recovery": "production_safe",
    "uquant.portfolio.recovery.admission": "production_safe",
    "uquant.portfolio.recovery.cohort_admission": "production_safe",
    "uquant.portfolio.recovery.substitution": "production_safe",
    "uquant.portfolio.recovery.tactical_admission": "production_safe",
    "uquant.portfolio.recovery.targets": "production_safe",
    "uquant.portfolio.risk_reduction": "production_safe",
    "uquant.portfolio.strategic": "production_safe",
    "uquant.portfolio.strategic.discovery": "production_safe",
    "uquant.portfolio.strategic.lifecycle": "production_safe",
    "uquant.portfolio.strategic.qualification_candidates": "production_safe",
    "uquant.portfolio.strategic.targets": "production_safe",
    "uquant.portfolio_core": "production_safe",
    "uquant.portfolio_leaders": "production_safe",
    "uquant.portfolio_recovery": "production_safe",
    "uquant.portfolio_strategic": "production_safe",
    "uquant.provenance": "production_safe",
    "uquant.provenance.fingerprints": "production_safe",
    "uquant.provenance.source_surfaces": "production_safe",
    "uquant.provenance.surfaces": "production_safe",
    "uquant.reference": "production_safe",
    "uquant.reference_registry": "production_safe",
    "uquant.report": "production_safe",
    "uquant.risk": "production_safe",
    "uquant.risk.anchors": "production_safe",
    "uquant.risk.assessment": "production_safe",
    "uquant.risk.capital": "production_safe",
    "uquant.risk.confirmed_break": "production_safe",
    "uquant.risk.market_book": "production_safe",
    "uquant.risk.protected_recovery": "production_safe",
    "uquant.risk.recovery_state": "production_safe",
    "uquant.risk.strategic_guard": "production_safe",
    "uquant.risk.transition_resolution": "production_safe",
    "uquant.risk.transitions": "production_safe",
    "uquant.risk_sector": "production_safe",
    "uquant.risk_sentinel": "production_safe",
    "uquant.risk_sentinel.__main__": "cli_runner",
    "uquant.risk_sentinel.calibration": "validation_runner",
    "uquant.risk_sentinel.cli": "cli_runner",
    "uquant.risk_sentinel.coverage": "production_safe",
    "uquant.risk_sentinel.evidence": "production_safe",
    "uquant.risk_sentinel.history": "production_safe",
    "uquant.risk_sentinel.history_cache": "production_safe",
    "uquant.risk_sentinel.integration": "production_safe",
    "uquant.risk_sentinel.source_identity_archive": "production_safe",
    "uquant.risk_sentinel.models": "production_safe",
    "uquant.risk_sentinel.opinion": "production_safe",
    "uquant.risk_sentinel.provenance": "production_safe",
    "uquant.risk_sentinel.service": "production_safe",
    "uquant.risk_sentinel.validation": "validation_runner",
    "uquant.types": "production_safe",
    "uquant.validation": "validation_runner",
    "uquant.validation.__main__": "cli_runner",
    "uquant.validation.ai_era": "production_safe",
    "uquant.validation.ci_artifacts": "cli_runner",
    "uquant.validation.cli": "cli_runner",
    "uquant.validation.competitor": "validation_runner",
    "uquant.validation.competitor_reference": "validation_runner",
    "uquant.validation.control_plane": "validation_runner",
    "uquant.validation.equivalence": "validation_runner",
    "uquant.validation.execution_journal": "validation_runner",
    "uquant.validation.generalization": "validation_runner",
    "uquant.validation.generalization.baseline": "validation_runner",
    "uquant.validation.generalization.gates": "validation_runner",
    "uquant.validation.generalization.metrics": "validation_runner",
    "uquant.validation.generalization.models": "validation_runner",
    "uquant.validation.generalization.provenance": "validation_runner",
    "uquant.validation.generalization.runner": "validation_runner",
    "uquant.validation.generalization.scenarios": "validation_runner",
    "uquant.validation.generalization_contract": "validation_runner",
    "uquant.validation.generalization_matrix": "validation_runner",
    "uquant.validation.generalization_matrix_evidence": "validation_runner",
    "uquant.validation.generalization_matrix_validation": "validation_runner",
    "uquant.validation.generalization_policy": "validation_runner",
    "uquant.validation.generalization_policy.cell_policy": "validation_runner",
    "uquant.validation.generalization_policy.cells": "validation_runner",
    "uquant.validation.generalization_policy.evaluation_stages": "validation_runner",
    "uquant.validation.generalization_policy.evaluator": "validation_runner",
    "uquant.validation.generalization_policy.projection": "validation_runner",
    "uquant.validation.generalization_policy.schema": "validation_runner",
    "uquant.validation.generalization_policy.tail_evaluation": "validation_runner",
    "uquant.validation.generalization_reference": "validation_runner",
    "uquant.validation.holdout": "validation_runner",
    "uquant.validation.holdout.artifact_transaction": "validation_runner",
    "uquant.validation.holdout.capabilities": "validation_runner",
    "uquant.validation.holdout.checkpoints": "validation_runner",
    "uquant.validation.holdout.cli_operations": "validation_runner",
    "uquant.validation.holdout.contract": "validation_runner",
    "uquant.validation.holdout.lanes": "validation_runner",
    "uquant.validation.holdout.manifest": "validation_runner",
    "uquant.validation.holdout.replay": "validation_runner",
    "uquant.validation.holdout.service": "validation_runner",
    "uquant.validation.holdout.snapshots": "validation_runner",
    "uquant.validation.holdout.source_identity": "validation_runner",
    "uquant.validation.holdout_lanes": "validation_runner",
    "uquant.validation.holdout_runtime": "validation_runner",
    "uquant.validation.manifest": "validation_runner",
    "uquant.validation.promotion": "validation_runner",
    "uquant.validation.promotion_contract": "validation_runner",
    "uquant.validation.production_observation": "validation_runner",
    "uquant.validation.production_observation_contract": "validation_runner",
    "uquant.validation.replay_evidence": "validation_runner",
    "uquant.validation.universe": "production_safe",
}

_MODULE_AUTHORITY_VALUES = {"production_safe", "validation_runner", "cli_runner"}

_NONPRODUCTION_IMPORT_AUTHORITIES = {"operator_script", "research", "test"}

_RUNNER_AUTHORITIES = {"cli_runner", "validation_runner"}

_CONTRACT_RELOCATIONS = {
    "uquant.contracts.runtime_identity": "uquant.validation.ai_era",
    "uquant.contracts.universe": "uquant.validation.universe",
    "uquant.models.trading": "uquant.types",
    "uquant.models.strategic_grant": "uquant.types",
    "uquant.portfolio.leaders.admission": "uquant.portfolio_leaders",
    "uquant.portfolio.recovery.admission": "uquant.portfolio_recovery",
    "uquant.portfolio.strategic.discovery": "uquant.portfolio_strategic",
}

_DEBT_RELOCATIONS = {
    **_CONTRACT_RELOCATIONS,
    **{
        module: "uquant.account"
        for module in (
            "uquant.account.codec",
            "uquant.account.economic_identity",
            "uquant.account.migrations",
            "uquant.account.store",
            "uquant.account.validation_attribution",
            "uquant.account.validation_common",
            "uquant.account.validation_orders",
            "uquant.account.validation_positions",
            "uquant.account.validation_strategy",
        )
    },
    **{
        module: "uquant.attribution"
        for module in (
            "uquant.attribution.builder",
            "uquant.attribution.concentration",
            "uquant.attribution.diagnostics",
            "uquant.attribution.ledger",
            "uquant.attribution.replay_evidence",
            "uquant.attribution.validation",
            "uquant.attribution.validation_artifact",
            "uquant.attribution.validation_lots",
        )
    },
    **{
        module: "uquant.validation.execution_journal"
        for module in (
            "uquant.observation.execution_journal",
            "uquant.observation.execution_journal.checkpoint",
            "uquant.observation.execution_journal.codec_v1",
            "uquant.observation.execution_journal.codec_v2",
            "uquant.observation.execution_journal.lifecycle",
            "uquant.observation.execution_journal.models",
            "uquant.observation.execution_journal.rendering",
            "uquant.observation.execution_journal.store",
        )
    },
    **{
        module: "uquant.risk"
        for module in (
            "uquant.risk.anchors",
            "uquant.risk.assessment",
            "uquant.risk.capital",
            "uquant.risk.confirmed_break",
            "uquant.risk.market_book",
            "uquant.risk.protected_recovery",
            "uquant.risk.recovery_state",
            "uquant.risk.strategic_guard",
            "uquant.risk.transition_resolution",
            "uquant.risk.transitions",
        )
    },
    **{
        module: "uquant.portfolio"
        for module in (
            "uquant.portfolio.allocator",
            "uquant.portfolio.context",
            "uquant.portfolio.freeze",
            "uquant.portfolio.pipeline",
            "uquant.portfolio.risk_reduction",
        )
    },
    **{
        module: "uquant.portfolio_leaders"
        for module in (
            "uquant.portfolio.leaders",
            "uquant.portfolio.leaders.admission",
            "uquant.portfolio.leaders.extensions",
            "uquant.portfolio.leaders.lifecycle",
            "uquant.portfolio.leaders.targets",
        )
    },
    **{
        module: "uquant.portfolio_strategic"
        for module in (
            "uquant.portfolio.strategic",
            "uquant.portfolio.strategic.discovery",
            "uquant.portfolio.strategic.lifecycle",
            "uquant.portfolio.strategic.qualification_candidates",
            "uquant.portfolio.strategic.targets",
        )
    },
    **{
        module: "uquant.portfolio_recovery"
        for module in (
            "uquant.portfolio.recovery",
            "uquant.portfolio.recovery.admission",
            "uquant.portfolio.recovery.substitution",
            "uquant.portfolio.recovery.targets",
        )
    },
}

_CONFIG_RELOCATED_PRIVATE_IMPORT_GROUPS = (
    (
        "uquant.account",
        "uquant.account.migrations",
        (
            "_legacy_attribution_owner",
            "_legacy_industry",
            "_migrate_v4_attribution_event_ids",
            "_populate_legacy_attribution",
        ),
    ),
    (
        "uquant.account",
        "uquant.account.validation_common",
        (
            "_EVENT_ID",
            "_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION",
            "_LEGACY_INDUSTRY",
            "_LEGACY_MANIFEST_SHA256",
            "_ORDER_ID",
            "_SHOCK_SEVERITIES",
            "_SHOCK_STATES",
            "_UNLINKED_LEGACY_IDENTITY_FIELDS",
            "_UNLINKED_NATIVE_IDENTITY_FIELDS",
        ),
    ),
    (
        "uquant.account",
        "uquant.account.validation_orders",
        (
            "_derive_v4_attribution_event_id",
            "_validate_attribution_identity",
            "_validate_fill",
            "_validate_lot_origin_chains",
            "_validate_order_intent",
            "_validate_order_state",
        ),
    ),
    (
        "uquant.account",
        "uquant.account.validation_positions",
        ("_position", "_tranche", "_validate_position_state"),
    ),
    (
        "uquant.account",
        "uquant.account.validation_strategy",
        ("_validate_audit_events", "_validate_risk_streaks", "_validate_strategy_risk_state"),
    ),
    (
        "uquant.account.codec",
        "uquant.account.validation_common",
        ("_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION", "_finite_number", "_reject_nonstandard_json_constant"),
    ),
    (
        "uquant.account.codec",
        "uquant.account.validation_orders",
        ("_validate_lot_origin_chains", "_validate_order_state"),
    ),
    (
        "uquant.account.codec",
        "uquant.account.validation_positions",
        ("_position", "_validate_position_state"),
    ),
    ("uquant.account.codec", "uquant.account.validation_strategy", ("_validate_strategy_risk_state",)),
    ("uquant.account.migrations", "uquant.account.codec", ("_read_account_payload",)),
    (
        "uquant.account.migrations",
        "uquant.account.validation_common",
        (
            "_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION",
            "_LEGACY_INDUSTRY",
            "_LEGACY_MANIFEST_SHA256",
            "_unlinked_fill_matches_order",
        ),
    ),
    (
        "uquant.account.migrations",
        "uquant.account.validation_orders",
        ("_derive_v4_attribution_event_id", "_order_sequence"),
    ),
    (
        "uquant.account.store",
        "uquant.account.validation_orders",
        ("_validate_lot_origin_chains", "_validate_order_state"),
    ),
    ("uquant.account.store", "uquant.account.validation_positions", ("_validate_position_state",)),
    ("uquant.account.store", "uquant.account.validation_strategy", ("_validate_strategy_risk_state",)),
    (
        "uquant.account.validation_orders",
        "uquant.account.validation_common",
        (
            "_EVENT_ID",
            "_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION",
            "_LEGACY_INDUSTRY",
            "_LEGACY_MANIFEST_SHA256",
            "_ORDER_ID",
            "_finite_number",
            "_nonnegative_integer",
            "_required_iso_date",
            "_required_text",
            "_unlinked_fill_matches_order",
        ),
    ),
    (
        "uquant.account.validation_positions",
        "uquant.account.validation_common",
        (
            "_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION",
            "_finite_number",
            "_nonnegative_integer",
            "_optional_iso_date",
            "_required_iso_date",
            "_required_text",
        ),
    ),
    (
        "uquant.account.validation_positions",
        "uquant.account.validation_orders",
        ("_validate_attribution_identity",),
    ),
    (
        "uquant.account.validation_strategy",
        "uquant.account.validation_common",
        (
            "_SHOCK_SEVERITIES",
            "_SHOCK_STATES",
            "_finite_number",
            "_nonnegative_integer",
            "_optional_finite_event_number",
            "_optional_iso_date",
            "_required_iso_date",
            "_required_text",
            "_validate_event_array",
            "_validate_nonnegative_integer_map",
            "_validate_symbol_list",
            "_validate_weight_map",
        ),
    ),
    ("uquant.attribution", "uquant.attribution.replay_evidence", ("_DAILY_REPLAY_FIELDS", "_LEDGER_FIELDS")),
    (
        "uquant.attribution",
        "uquant.attribution.validation",
        (
            "_ACCOUNTING_FIELDS",
            "_ATTRIBUTION_FIELDS",
            "_COST_FIELDS",
            "_GROUP_FIELDS",
            "_LOT_COST_FIELDS",
            "_LOT_FIELDS",
        ),
    ),
    (
        "uquant.attribution.builder",
        "uquant.attribution.concentration",
        ("_empty_pnl_bucket", "_finite", "_group_lot_pnl", "_holding_summary"),
    ),
    ("uquant.attribution.builder", "uquant.attribution.replay_evidence", ("_positive_integer",)),
    ("uquant.attribution.builder", "uquant.attribution.validation", ("_economic_sessions",)),
    ("uquant.attribution.diagnostics", "uquant.attribution.concentration", ("_finite",)),
    ("uquant.attribution.ledger", "uquant.attribution.concentration", ("_finite",)),
    ("uquant.attribution.replay_evidence", "uquant.attribution.concentration", ("_finite",)),
    (
        "uquant.attribution.validation",
        "uquant.attribution.concentration",
        ("_finite", "_group_lot_pnl", "_holding_summary"),
    ),
    (
        "uquant.attribution.validation",
        "uquant.attribution.replay_evidence",
        (
            "_LEDGER_FIELDS",
            "_close",
            "_positive_integer",
            "_require_exact_fields",
            "_validate_daily_replay_evidence",
        ),
    ),
    (
        "uquant.observation.execution_journal",
        "uquant.observation.execution_journal.models",
        ("_BROKER_ORDER_ID", "_PLAN_ID", "_SHA256", "_SYMBOL", "_V1_FIELDS", "_V2_FIELDS", "_ZERO_HASH"),
    ),
    (
        "uquant.observation.execution_journal.checkpoint",
        "uquant.observation.execution_journal.models",
        ("_ZERO_HASH",),
    ),
    (
        "uquant.observation.execution_journal.codec_v1",
        "uquant.observation.execution_journal.lifecycle",
        ("_timestamp", "_validate_record"),
    ),
    (
        "uquant.observation.execution_journal.codec_v1",
        "uquant.observation.execution_journal.models",
        ("_SHA256", "_V1_FIELDS"),
    ),
    (
        "uquant.observation.execution_journal.codec_v2",
        "uquant.observation.execution_journal.lifecycle",
        ("_validate_record",),
    ),
    (
        "uquant.observation.execution_journal.codec_v2",
        "uquant.observation.execution_journal.models",
        ("_SHA256", "_V1_FIELDS", "_V2_FIELDS"),
    ),
    (
        "uquant.observation.execution_journal.lifecycle",
        "uquant.observation.execution_journal.models",
        ("_BROKER_ORDER_ID", "_PLAN_ID", "_SYMBOL"),
    ),
    (
        "uquant.observation.execution_journal.store",
        "uquant.observation.execution_journal.lifecycle",
        ("_positive_number", "_positive_shares", "_timestamp"),
    ),
    (
        "uquant.observation.execution_journal.store",
        "uquant.observation.execution_journal.models",
        ("_ZERO_HASH",),
    ),
    (
        "uquant.validation.execution_journal",
        "uquant.observation.execution_journal",
        ("_BROKER_ORDER_ID", "_PLAN_ID", "_SHA256", "_SYMBOL", "_V1_FIELDS", "_V2_FIELDS", "_ZERO_HASH"),
    ),
)

_CONFIG_RELOCATED_PRIVATE_IMPORTS = frozenset(
    f"{importer}:{imported_from}:{name}"
    for importer, imported_from, names in _CONFIG_RELOCATED_PRIVATE_IMPORT_GROUPS
    for name in names
)

_EXECUTION_RELOCATED_PRIVATE_IMPORT_GROUPS = (
    (
        "uquant.application",
        "uquant.application.decision",
        ("_attach_target_attribution", "_decision_config_for_universe", "_mark_account_positions"),
    ),
    ("uquant.application", "uquant.application.metrics", ("_drawdown_stats",)),
    (
        "uquant.application",
        "uquant.application.risk_timeline_cache",
        (
            "_canonical_json",
            "_causal_risk_timeline",
            "_load_risk_timeline_disk_cache",
            "_risk_timeline_disk_path",
            "_write_risk_timeline_disk_cache",
        ),
    ),
    ("uquant.execution.open_execution", "uquant.execution.market_constraints", ("_blocked",)),
    (
        "uquant.execution.open_execution",
        "uquant.execution.reconciliation",
        ("_active_order_status", "_register_account_order"),
    ),
    (
        "uquant.execution.open_execution",
        "uquant.execution.tranches",
        ("_allocate_sell_costs", "_consume_sell_tranches", "_rebuild_position_from_tranches"),
    ),
    (
        "uquant.execution",
        "uquant.execution.tranches",
        ("_RISK_LIFECYCLE_PRIORITY", "_allocate_sell_costs"),
    ),
)

_EXECUTION_RELOCATED_PRIVATE_IMPORTS = frozenset(
    f"{importer}:{imported_from}:{name}"
    for importer, imported_from, names in _EXECUTION_RELOCATED_PRIVATE_IMPORT_GROUPS
    for name in names
)

_RISK_RELOCATED_PRIVATE_IMPORT_GROUPS = (
    ("uquant.risk.assessment", "uquant.risk.anchors", ("_assess_dynamic_anchors",)),
    (
        "uquant.risk.assessment",
        "uquant.risk.capital",
        ("_apply_capital_overlays", "_observe_capital_budget", "_portfolio_drawdowns"),
    ),
    (
        "uquant.risk.assessment",
        "uquant.risk.recovery_state",
        (
            "_assess_protected_recovery",
            "_assess_recovery_state",
            "_reset_recovery_owner_rearm",
        ),
    ),
    (
        "uquant.risk.assessment",
        "uquant.risk.strategic_guard",
        ("_update_strategic_damage_guard",),
    ),
    (
        "uquant.risk.assessment",
        "uquant.risk.transitions",
        (
            "_assess_acute_and_cooldown",
            "_assess_break_conditions",
            "_assess_confirmed_concentrated_break",
            "_resolve_risk_transition",
        ),
    ),
    (
        "uquant.risk.capital",
        "uquant.risk.strategic_guard",
        ("_strategic_grace_supported", "_strategic_guard_level2_overlay_required"),
    ),
    (
        "uquant.risk.transitions",
        "uquant.risk.recovery_state",
        ("_persistent_crisis_cap", "_reset_recovery_owner_rearm"),
    ),
    (
        "uquant.risk.transitions",
        "uquant.risk.strategic_guard",
        ("_strategic_crisis_severity",),
    ),
    (
        "uquant.risk",
        "uquant.risk.anchors",
        ("_dynamic_anchor_candidate", "_update_dynamic_anchors"),
    ),
    (
        "uquant.risk",
        "uquant.risk.assessment",
        ("_assess_base_risk", "_risk_runtime_seam"),
    ),
    (
        "uquant.risk",
        "uquant.risk.capital",
        (
            "_capital_budget_repair_drawdown_confirmed",
            "_portfolio_drawdowns",
            "_update_capital_budget_ladder",
        ),
    ),
    (
        "uquant.risk",
        "uquant.risk.recovery_state",
        ("_persistent_crisis_cap", "_reset_recovery_owner_rearm"),
    ),
    (
        "uquant.risk",
        "uquant.risk.strategic_guard",
        (
            "_strategic_crisis_severity",
            "_strategic_damage_guard_active",
            "_strategic_damage_guard_persists",
            "_strategic_damage_guard_required",
            "_strategic_grace_supported",
            "_strategic_guard_level2_overlay_required",
        ),
    ),
    (
        "uquant.risk",
        "uquant.risk.transitions",
        ("_acute_sector_evacuation_required",),
    ),
)

_RISK_RELOCATED_PRIVATE_IMPORTS = frozenset(
    f"{importer}:{imported_from}:{name}"
    for importer, imported_from, names in _RISK_RELOCATED_PRIVATE_IMPORT_GROUPS
    for name in names
)

_RISK_RELOCATED_FUNCTION_DEBT = {
    identifier: "uquant.risk:_assess_base_risk"
    for identifier in (
        "uquant.risk.anchors:_assess_dynamic_anchors",
        "uquant.risk.assessment:_assess_base_risk",
        "uquant.risk.assessment:_assess_market_and_book_evidence",
        "uquant.risk.capital:_observe_capital_budget",
        "uquant.risk.recovery_state:_assess_protected_recovery",
        "uquant.risk.recovery_state:_assess_recovery_state",
        "uquant.risk.transitions:_assess_acute_and_cooldown",
        "uquant.risk.transitions:_assess_break_conditions",
        "uquant.risk.transitions:_assess_confirmed_concentrated_break",
        "uquant.risk.transitions:_resolve_risk_transition",
    )
}
