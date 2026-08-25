from __future__ import annotations

import importlib
from collections.abc import Mapping

from ._analysis import ROOT, architecture_snapshot
from ._private_imports import (
    current_governed_sources,
    scan_governed_private_edges,
    scan_sealed_governed_private_edges,
)

_PORTFOLIO_PUBLIC_OWNERS_COMMIT = "a2a81c2729d0f487e4816006beb4967dfd169cb2"
_PORTFOLIO_PUBLIC_OWNERS_TREE = "08eb399120d76626cca3c9618cbb76b564d6a652"

PORTFOLIO_PUBLIC_ROUTES = {
    ("uquant.portfolio.leaders.admission", "_admission_utility"): (
        "uquant.portfolio.leaders.admission",
        "admission_utility",
    ),
    ("uquant.portfolio.leaders.admission", "_conviction_evidence_qualified"): (
        "uquant.portfolio.leaders.admission",
        "conviction_evidence_qualified",
    ),
    ("uquant.portfolio.leaders.admission", "_conviction_shares"): (
        "uquant.portfolio.leaders.admission",
        "conviction_shares",
    ),
    ("uquant.portfolio.leaders.admission", "_correlations"): (
        "uquant.portfolio.leaders.admission",
        "leader_correlations",
    ),
    ("uquant.portfolio.leaders.admission", "_dynamic_k"): (
        "uquant.portfolio.leaders.admission",
        "dynamic_leader_count",
    ),
    ("uquant.portfolio.leaders.lifecycle", "_industry_handoff"): (
        "uquant.portfolio.leaders.lifecycle",
        "industry_handoff",
    ),
    ("uquant.portfolio.leaders.lifecycle", "_leader_lifecycle_exit_confirmed"): (
        "uquant.portfolio.leaders.lifecycle",
        "leader_lifecycle_exit_confirmed",
    ),
    ("uquant.portfolio.leaders.lifecycle", "_leader_session_distance"): (
        "uquant.portfolio.leaders.lifecycle",
        "leader_session_distance",
    ),
    ("uquant.portfolio.leaders.lifecycle", "_retention_score"): (
        "uquant.portfolio.leaders.lifecycle",
        "leader_retention_score",
    ),
    ("uquant.portfolio.leaders.lifecycle", "_rotation_allowed"): (
        "uquant.portfolio.leaders.lifecycle",
        "leader_rotation_allowed",
    ),
    ("uquant.portfolio.leaders.lifecycle", "_session_clock"): (
        "uquant.portfolio.leaders.lifecycle",
        "leader_session_clock",
    ),
    ("uquant.portfolio.leaders.lifecycle", "_update_leader_cycle_arm"): (
        "uquant.portfolio.leaders.lifecycle",
        "update_leader_cycle_arm",
    ),
    ("uquant.portfolio.leaders.targets", "_cap_opportunity_gross"): (
        "uquant.portfolio.leaders.targets",
        "cap_opportunity_gross",
    ),
    ("uquant.portfolio.leaders.targets", "_leader_targets"): (
        "uquant.portfolio.leaders.targets",
        "leader_targets",
    ),
    ("uquant.portfolio.recovery.targets", "_confirmed_recovery_substitution_targets"): (
        "uquant.portfolio.recovery.targets",
        "confirmed_recovery_substitution_targets",
    ),
    ("uquant.portfolio.recovery.targets", "_pending_recovery_substitution_targets"): (
        "uquant.portfolio.recovery.targets",
        "pending_recovery_substitution_targets",
    ),
    ("uquant.portfolio.recovery.substitution", "_recovery_anchor_substitution"): (
        "uquant.portfolio.recovery.substitution",
        "recovery_anchor_substitution",
    ),
    ("uquant.portfolio.strategic.targets", "_strategic_active_targets"): (
        "uquant.portfolio.strategic.targets",
        "strategic_active_targets",
    ),
    ("uquant.portfolio.strategic.targets", "_strategic_completed_exit_targets"): (
        "uquant.portfolio.strategic.targets",
        "strategic_completed_exit_targets",
    ),
    ("uquant.portfolio.strategic.discovery", "_initialize_strategic_cohort"): (
        "uquant.portfolio.strategic.discovery",
        "initialize_strategic_cohort",
    ),
    ("uquant.portfolio.strategic.lifecycle", "_bounded_strategic_restore_risk_open"): (
        "uquant.portfolio.strategic.lifecycle",
        "bounded_strategic_restore_risk_open",
    ),
    ("uquant.portfolio.strategic.lifecycle", "_retire_strategic_member"): (
        "uquant.portfolio.strategic.lifecycle",
        "retire_strategic_member",
    ),
    ("uquant.portfolio.strategic.lifecycle", "_strategic_cohort_targets"): (
        "uquant.portfolio.strategic.lifecycle",
        "strategic_cohort_targets",
    ),
    ("uquant.portfolio.allocator", "_confirmed_recovery_gross"): (
        "uquant.portfolio.allocator",
        "confirmed_recovery_gross",
    ),
    ("uquant.portfolio.freeze", "_commit_frozen_exit_state"): (
        "uquant.portfolio.freeze",
        "commit_frozen_exit_state",
    ),
    ("uquant.portfolio.freeze", "_frozen_existing_targets"): (
        "uquant.portfolio.freeze",
        "frozen_existing_targets",
    ),
    ("uquant.portfolio.pipeline", "_allocate_strategy"): (
        "uquant.portfolio.pipeline",
        "allocate_strategy",
    ),
    ("uquant.portfolio.risk_reduction", "_risk_attribution_mechanism"): (
        "uquant.portfolio.risk_reduction",
        "risk_attribution_mechanism",
    ),
    ("uquant.portfolio.risk_reduction", "_risk_lifecycle_rank"): (
        "uquant.portfolio.risk_reduction",
        "risk_lifecycle_rank",
    ),
    ("uquant.portfolio.risk_reduction", "_risk_reduction_metadata"): (
        "uquant.portfolio.risk_reduction",
        "risk_reduction_metadata",
    ),
    ("uquant.portfolio.risk_reduction", "_risk_retention_score"): (
        "uquant.portfolio.risk_reduction",
        "risk_retention_score",
    ),
    ("uquant.portfolio.risk_reduction", "_risk_retention_vector"): (
        "uquant.portfolio.risk_reduction",
        "risk_retention_vector",
    ),
    ("uquant.portfolio.risk_reduction", "_sparse_risk_reduce"): (
        "uquant.portfolio.risk_reduction",
        "sparse_risk_reduce",
    ),
    ("uquant.portfolio.risk_reduction", "_subset_retention_vector"): (
        "uquant.portfolio.risk_reduction",
        "subset_retention_vector",
    ),
    ("uquant.portfolio.risk_reduction", "_turnover_aware_sector_cap"): (
        "uquant.portfolio.risk_reduction",
        "turnover_aware_sector_cap",
    ),
}


def _portfolio_rows() -> list[Mapping[str, object]]:
    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, Mapping)
    rows = graph["task8_relocated_private_imports"]
    assert isinstance(rows, list)
    assert all(isinstance(row, Mapping) for row in rows)
    return rows


def test_architecture_portfolio_public_routes_preserve_exact_owner_identity() -> None:
    rows = _portfolio_rows()
    pairs = {(str(row["imported_from"]), str(row["name"])) for row in rows}
    assert len(rows) == 35
    assert pairs == set(PORTFOLIO_PUBLIC_ROUTES)
    for (legacy_owner, private_name), (public_owner, public_name) in sorted(
        PORTFOLIO_PUBLIC_ROUTES.items()
    ):
        legacy_module = importlib.import_module(legacy_owner)
        public_module = importlib.import_module(public_owner)
        assert getattr(legacy_module, private_name) is getattr(public_module, public_name)


def test_architecture_portfolio_importers_keep_exact_local_legacy_bindings() -> None:
    for row in _portfolio_rows():
        legacy_owner = str(row["imported_from"])
        private_name = str(row["name"])
        public_owner, public_name = PORTFOLIO_PUBLIC_ROUTES[(legacy_owner, private_name)]
        importer_name = str(row["importer"])
        importer = importlib.import_module(importer_name)
        binding_owner = importer.PortfolioAllocator if importer_name == "uquant.portfolio" else importer
        owner = importlib.import_module(public_owner)
        assert getattr(binding_owner, private_name) is getattr(owner, public_name)


def test_architecture_portfolio_current_private_edges_are_closed() -> None:
    historical_ids = {str(row["id"]) for row in _portfolio_rows()}
    observed = scan_governed_private_edges(current_governed_sources())
    current_ids = {
        str(row["id"])
        for row in observed["direct"]
        if isinstance(row, Mapping)
    }
    assert not current_ids & historical_ids


def test_architecture_portfolio_raw_count_progression_is_exact() -> None:
    checkpoint = scan_sealed_governed_private_edges(
        ROOT,
        commit=_PORTFOLIO_PUBLIC_OWNERS_COMMIT,
        tree=_PORTFOLIO_PUBLIC_OWNERS_TREE,
    )
    assert len(checkpoint["direct"]) == 189
    assert len(checkpoint["qualified"]) == 19
    assert scan_governed_private_edges(current_governed_sources()) == {
        "direct": [],
        "qualified": [],
        "dynamic": [],
    }
