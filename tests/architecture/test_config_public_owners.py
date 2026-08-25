from __future__ import annotations

import importlib
from collections.abc import Mapping

from ._analysis import ROOT, architecture_snapshot
from ._private_imports import current_governed_sources, scan_governed_private_edges
from ._config_transport import config_post_checkpoint_private_edges

CONFIG_PUBLIC_ROUTES = {
    ("uquant.account.codec", "_read_account_payload"): (
        "uquant.account.codec",
        "read_account_payload",
    ),
    ("uquant.account.migrations", "_legacy_attribution_owner"): (
        "uquant.account.migrations",
        "legacy_attribution_owner",
    ),
    ("uquant.account.migrations", "_legacy_industry"): (
        "uquant.account.migrations",
        "legacy_industry",
    ),
    ("uquant.account.migrations", "_migrate_v4_attribution_event_ids"): (
        "uquant.account.migrations",
        "migrate_v4_attribution_event_ids",
    ),
    ("uquant.account.migrations", "_populate_legacy_attribution"): (
        "uquant.account.migrations",
        "populate_legacy_attribution",
    ),
    ("uquant.account.validation_common", "_EVENT_ID"): (
        "uquant.account.validation_common",
        "EVENT_ID_PATTERN",
    ),
    ("uquant.account.validation_common", "_HISTORICAL_ATTRIBUTION_SCHEMA_VERSION"): (
        "uquant.account.validation_common",
        "HISTORICAL_ATTRIBUTION_SCHEMA_VERSION",
    ),
    ("uquant.account.validation_common", "_LEGACY_INDUSTRY"): (
        "uquant.account.validation_common",
        "LEGACY_INDUSTRY",
    ),
    ("uquant.account.validation_common", "_LEGACY_MANIFEST_SHA256"): (
        "uquant.account.validation_common",
        "LEGACY_MANIFEST_SHA256",
    ),
    ("uquant.account.validation_common", "_ORDER_ID"): (
        "uquant.account.validation_common",
        "ORDER_ID_PATTERN",
    ),
    ("uquant.account.validation_common", "_SHOCK_SEVERITIES"): (
        "uquant.account.validation_common",
        "SHOCK_SEVERITIES",
    ),
    ("uquant.account.validation_common", "_SHOCK_STATES"): (
        "uquant.account.validation_common",
        "SHOCK_STATES",
    ),
    ("uquant.account.validation_common", "_UNLINKED_LEGACY_IDENTITY_FIELDS"): (
        "uquant.account.validation_common",
        "UNLINKED_LEGACY_IDENTITY_FIELDS",
    ),
    ("uquant.account.validation_common", "_UNLINKED_NATIVE_IDENTITY_FIELDS"): (
        "uquant.account.validation_common",
        "UNLINKED_NATIVE_IDENTITY_FIELDS",
    ),
    ("uquant.account.validation_common", "_finite_number"): (
        "uquant.account.validation_common",
        "finite_number",
    ),
    ("uquant.account.validation_common", "_nonnegative_integer"): (
        "uquant.account.validation_common",
        "nonnegative_integer",
    ),
    ("uquant.account.validation_common", "_optional_finite_event_number"): (
        "uquant.account.validation_common",
        "optional_finite_event_number",
    ),
    ("uquant.account.validation_common", "_optional_iso_date"): (
        "uquant.account.validation_common",
        "optional_iso_date",
    ),
    ("uquant.account.validation_common", "_reject_nonstandard_json_constant"): (
        "uquant.account.validation_common",
        "reject_nonstandard_account_json_constant",
    ),
    ("uquant.account.validation_common", "_required_iso_date"): (
        "uquant.account.validation_common",
        "required_iso_date",
    ),
    ("uquant.account.validation_common", "_required_text"): (
        "uquant.account.validation_common",
        "required_text",
    ),
    ("uquant.account.validation_common", "_unlinked_fill_matches_order"): (
        "uquant.account.validation_common",
        "unlinked_fill_matches_order",
    ),
    ("uquant.account.validation_common", "_validate_event_array"): (
        "uquant.account.validation_common",
        "validate_account_event_array",
    ),
    ("uquant.account.validation_common", "_validate_nonnegative_integer_map"): (
        "uquant.account.validation_common",
        "validate_nonnegative_account_integer_map",
    ),
    ("uquant.account.validation_common", "_validate_symbol_list"): (
        "uquant.account.validation_common",
        "validate_account_symbol_list",
    ),
    ("uquant.account.validation_common", "_validate_weight_map"): (
        "uquant.account.validation_common",
        "validate_account_weight_map",
    ),
    ("uquant.account.validation_orders", "_derive_v4_attribution_event_id"): (
        "uquant.account.validation_attribution",
        "derive_v4_attribution_event_id",
    ),
    ("uquant.account.validation_orders", "_order_sequence"): (
        "uquant.account.validation_orders",
        "order_sequence",
    ),
    ("uquant.account.validation_orders", "_validate_attribution_identity"): (
        "uquant.account.validation_attribution",
        "validate_attribution_identity",
    ),
    ("uquant.account.validation_orders", "_validate_fill"): (
        "uquant.account.validation_orders",
        "validate_fill",
    ),
    ("uquant.account.validation_orders", "_validate_lot_origin_chains"): (
        "uquant.account.validation_attribution",
        "validate_lot_origin_chains",
    ),
    ("uquant.account.validation_orders", "_validate_order_intent"): (
        "uquant.account.validation_attribution",
        "validate_order_intent",
    ),
    ("uquant.account.validation_orders", "_validate_order_state"): (
        "uquant.account.validation_orders",
        "validate_order_state",
    ),
    ("uquant.account.validation_positions", "_position"): (
        "uquant.account.validation_positions",
        "position_from_payload",
    ),
    ("uquant.account.validation_positions", "_tranche"): (
        "uquant.account.validation_positions",
        "tranche_from_payload",
    ),
    ("uquant.account.validation_positions", "_validate_position_state"): (
        "uquant.account.validation_positions",
        "validate_position_state",
    ),
    ("uquant.account.validation_strategy", "_validate_audit_events"): (
        "uquant.account.validation_strategy",
        "validate_audit_events",
    ),
    ("uquant.account.validation_strategy", "_validate_risk_streaks"): (
        "uquant.account.validation_strategy",
        "validate_risk_streaks",
    ),
    ("uquant.account.validation_strategy", "_validate_strategy_risk_state"): (
        "uquant.account.validation_strategy",
        "validate_strategy_risk_state",
    ),
    ("uquant.attribution.concentration", "_finite"): (
        "uquant.attribution.concentration",
        "finite_attribution_number",
    ),
    ("uquant.attribution.concentration", "_group_lot_pnl"): (
        "uquant.attribution.concentration",
        "group_lot_pnl",
    ),
    ("uquant.attribution.concentration", "_holding_summary"): (
        "uquant.attribution.concentration",
        "holding_summary",
    ),
    ("uquant.attribution.replay_evidence", "_DAILY_REPLAY_FIELDS"): (
        "uquant.attribution.replay_evidence",
        "DAILY_REPLAY_FIELDS",
    ),
    ("uquant.attribution.replay_evidence", "_LEDGER_FIELDS"): (
        "uquant.attribution.replay_evidence",
        "LEDGER_FIELDS",
    ),
    ("uquant.attribution.replay_evidence", "_close"): (
        "uquant.attribution.replay_evidence",
        "close_attribution_values",
    ),
    ("uquant.attribution.replay_evidence", "_positive_integer"): (
        "uquant.attribution.replay_evidence",
        "positive_attribution_integer",
    ),
    ("uquant.attribution.replay_evidence", "_validate_daily_replay_evidence"): (
        "uquant.attribution.replay_evidence",
        "validate_daily_replay_evidence",
    ),
    ("uquant.attribution.validation", "_ACCOUNTING_FIELDS"): (
        "uquant.attribution.validation_artifact",
        "ACCOUNTING_FIELDS",
    ),
    ("uquant.attribution.validation", "_ATTRIBUTION_FIELDS"): (
        "uquant.attribution.validation_artifact",
        "ATTRIBUTION_FIELDS",
    ),
    ("uquant.attribution.validation", "_COST_FIELDS"): (
        "uquant.attribution.validation_artifact",
        "COST_FIELDS",
    ),
    ("uquant.attribution.validation", "_GROUP_FIELDS"): (
        "uquant.attribution.validation_artifact",
        "GROUP_FIELDS",
    ),
    ("uquant.attribution.validation", "_LOT_COST_FIELDS"): (
        "uquant.attribution.validation_lots",
        "LOT_COST_FIELDS",
    ),
    ("uquant.attribution.validation", "_LOT_FIELDS"): (
        "uquant.attribution.validation_lots",
        "LOT_FIELDS",
    ),
    ("uquant.attribution.validation", "_economic_sessions"): (
        "uquant.attribution.validation",
        "economic_sessions",
    ),
    **{
        ("uquant.observation.execution_journal", private): (
            "uquant.observation.execution_journal.models",
            public,
        )
        for private, public in {
            "_BROKER_ORDER_ID": "BROKER_ORDER_ID_PATTERN",
            "_PLAN_ID": "PLAN_ID_PATTERN",
            "_SHA256": "SHA256_PATTERN",
            "_SYMBOL": "SYMBOL_PATTERN",
            "_V1_FIELDS": "V1_FIELDS",
            "_V2_FIELDS": "V2_FIELDS",
            "_ZERO_HASH": "ZERO_HASH",
        }.items()
    },
    ("uquant.observation.execution_journal.lifecycle", "_positive_number"): (
        "uquant.observation.execution_journal.lifecycle",
        "positive_journal_number",
    ),
    ("uquant.observation.execution_journal.lifecycle", "_positive_shares"): (
        "uquant.observation.execution_journal.lifecycle",
        "positive_journal_shares",
    ),
    ("uquant.observation.execution_journal.lifecycle", "_timestamp"): (
        "uquant.observation.execution_journal.lifecycle",
        "journal_timestamp",
    ),
    ("uquant.observation.execution_journal.lifecycle", "_validate_record"): (
        "uquant.observation.execution_journal.lifecycle",
        "validate_journal_record",
    ),
    **{
        ("uquant.observation.execution_journal.models", private): (
            "uquant.observation.execution_journal.models",
            public,
        )
        for private, public in {
            "_BROKER_ORDER_ID": "BROKER_ORDER_ID_PATTERN",
            "_PLAN_ID": "PLAN_ID_PATTERN",
            "_SHA256": "SHA256_PATTERN",
            "_SYMBOL": "SYMBOL_PATTERN",
            "_V1_FIELDS": "V1_FIELDS",
            "_V2_FIELDS": "V2_FIELDS",
            "_ZERO_HASH": "ZERO_HASH",
        }.items()
    },
}


def test_architecture_config_public_routes_preserve_exact_owner_identity() -> None:
    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, Mapping)
    rows = graph["task5_relocated_private_imports"]
    assert isinstance(rows, list)
    original_pairs = {
        (str(row["imported_from"]), str(row["name"]))
        for row in rows
        if isinstance(row, Mapping)
    }
    assert len(rows) == 123
    assert original_pairs == set(CONFIG_PUBLIC_ROUTES)
    for (legacy_owner, private_name), (public_owner, public_name) in sorted(
        CONFIG_PUBLIC_ROUTES.items()
    ):
        legacy_module = importlib.import_module(legacy_owner)
        public_module = importlib.import_module(public_owner)
        assert getattr(legacy_module, private_name) is getattr(public_module, public_name)


def test_architecture_config_importers_keep_exact_local_legacy_bindings() -> None:
    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, Mapping)
    rows = graph["task5_relocated_private_imports"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, Mapping)
        legacy_owner = str(row["imported_from"])
        private_name = str(row["name"])
        public_owner, public_name = CONFIG_PUBLIC_ROUTES[(legacy_owner, private_name)]
        importer = importlib.import_module(str(row["importer"]))
        owner = importlib.import_module(public_owner)
        assert getattr(importer, private_name) is getattr(owner, public_name)


def test_architecture_config_raw_private_edges_are_closed() -> None:
    checkpoint = config_post_checkpoint_private_edges(ROOT)
    assert len(checkpoint["direct"]) == 270
    assert len(checkpoint["qualified"]) == 19
    live = scan_governed_private_edges(current_governed_sources())
    assert live == {"direct": [], "qualified": [], "dynamic": []}
    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, Mapping)
    historical = graph["task5_relocated_private_imports"]
    assert isinstance(historical, list)
    assert not {
        str(row["id"])
        for row in checkpoint["direct"]
        if isinstance(row, Mapping)
    } & {
        str(row["id"])
        for row in historical
        if isinstance(row, Mapping)
    }
