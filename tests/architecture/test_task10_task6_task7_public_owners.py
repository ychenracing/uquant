from __future__ import annotations

import ast
import importlib
import subprocess
from collections.abc import Mapping

import pytest

from ._analysis import ROOT, architecture_snapshot
from ._task10_private_imports import (
    current_governed_sources,
    scan_governed_private_edges,
    scan_sealed_governed_private_edges,
)

_TASK6_TASK7_PUBLIC_OWNERS_COMMIT = "6ee2575f63b4e1e5ffccb8d84b4f63f6ce301964"
_TASK6_TASK7_PUBLIC_OWNERS_TREE = "39634dcfa3b47ba9578fb0e12448f9fe997afc34"
_TASK7_REMOVED_RUNTIME_ROUTE = (
    "uquant.risk.assessment",
    "_risk_runtime_seam",
    "uquant.risk.assessment",
    "risk_runtime_seam",
)

TASK_PUBLIC_ROUTES = {
    6: {
        ("uquant.application.decision", "_attach_target_attribution"): (
            "uquant.application.target_attribution",
            "attach_target_attribution",
        ),
        ("uquant.application.decision", "_decision_config_for_universe"): (
            "uquant.application.decision",
            "decision_config_for_universe",
        ),
        ("uquant.application.decision", "_mark_account_positions"): (
            "uquant.application.decision",
            "mark_account_positions",
        ),
        ("uquant.application.metrics", "_drawdown_stats"): (
            "uquant.application.metrics",
            "equity_drawdown_stats",
        ),
        ("uquant.application.risk_timeline_cache", "_canonical_json"): (
            "uquant.application.risk_timeline_cache",
            "canonical_risk_timeline_json",
        ),
        ("uquant.application.risk_timeline_cache", "_causal_risk_timeline"): (
            "uquant.application.risk_timeline_cache",
            "causal_risk_timeline",
        ),
        ("uquant.application.risk_timeline_cache", "_load_risk_timeline_disk_cache"): (
            "uquant.application.risk_timeline_cache",
            "load_risk_timeline_disk_cache",
        ),
        ("uquant.application.risk_timeline_cache", "_risk_timeline_disk_path"): (
            "uquant.application.risk_timeline_cache",
            "risk_timeline_disk_path",
        ),
        ("uquant.application.risk_timeline_cache", "_write_risk_timeline_disk_cache"): (
            "uquant.application.risk_timeline_cache",
            "write_risk_timeline_disk_cache",
        ),
        ("uquant.execution.market_constraints", "_blocked"): (
            "uquant.execution.market_constraints",
            "market_execution_blocked",
        ),
        ("uquant.execution.reconciliation", "_active_order_status"): (
            "uquant.execution.reconciliation",
            "active_order_status",
        ),
        ("uquant.execution.reconciliation", "_register_account_order"): (
            "uquant.execution.reconciliation",
            "register_account_order",
        ),
        ("uquant.execution.tranches", "_RISK_LIFECYCLE_PRIORITY"): (
            "uquant.execution.tranches",
            "RISK_LIFECYCLE_PRIORITY",
        ),
        ("uquant.execution.tranches", "_allocate_sell_costs"): (
            "uquant.execution.tranches",
            "allocate_sell_costs",
        ),
        ("uquant.execution.tranches", "_consume_sell_tranches"): (
            "uquant.execution.tranches",
            "consume_sell_tranches",
        ),
        ("uquant.execution.tranches", "_rebuild_position_from_tranches"): (
            "uquant.execution.tranches",
            "rebuild_position_from_tranches",
        ),
    },
    7: {
        ("uquant.risk.anchors", "_assess_dynamic_anchors"): (
            "uquant.risk.anchors",
            "assess_dynamic_anchors",
        ),
        ("uquant.risk.anchors", "_dynamic_anchor_candidate"): (
            "uquant.risk.anchors",
            "dynamic_anchor_candidate",
        ),
        ("uquant.risk.anchors", "_update_dynamic_anchors"): (
            "uquant.risk.anchors",
            "update_dynamic_anchors",
        ),
        ("uquant.risk.assessment", "_assess_base_risk"): (
            "uquant.risk.assessment",
            "assess_base_risk",
        ),
        ("uquant.risk.assessment", "_risk_runtime_seam"): (
            "uquant.risk.assessment",
            "risk_runtime_seam",
        ),
        ("uquant.risk.capital", "_apply_capital_overlays"): (
            "uquant.risk.capital",
            "apply_capital_overlays",
        ),
        ("uquant.risk.capital", "_capital_budget_repair_drawdown_confirmed"): (
            "uquant.risk.capital",
            "capital_budget_repair_drawdown_confirmed",
        ),
        ("uquant.risk.capital", "_observe_capital_budget"): (
            "uquant.risk.capital",
            "observe_capital_budget",
        ),
        ("uquant.risk.capital", "_portfolio_drawdowns"): (
            "uquant.risk.capital",
            "portfolio_drawdowns",
        ),
        ("uquant.risk.capital", "_update_capital_budget_ladder"): (
            "uquant.risk.capital",
            "update_capital_budget_ladder",
        ),
        ("uquant.risk.recovery_state", "_assess_protected_recovery"): (
            "uquant.risk.protected_recovery",
            "assess_protected_recovery",
        ),
        ("uquant.risk.recovery_state", "_assess_recovery_state"): (
            "uquant.risk.recovery_state",
            "assess_recovery_state",
        ),
        ("uquant.risk.recovery_state", "_persistent_crisis_cap"): (
            "uquant.risk.recovery_state",
            "persistent_crisis_cap",
        ),
        ("uquant.risk.recovery_state", "_reset_recovery_owner_rearm"): (
            "uquant.risk.recovery_state",
            "reset_recovery_owner_rearm",
        ),
        ("uquant.risk.strategic_guard", "_strategic_crisis_severity"): (
            "uquant.risk.strategic_guard",
            "strategic_crisis_severity",
        ),
        ("uquant.risk.strategic_guard", "_strategic_damage_guard_active"): (
            "uquant.risk.strategic_guard",
            "strategic_damage_guard_active",
        ),
        ("uquant.risk.strategic_guard", "_strategic_damage_guard_persists"): (
            "uquant.risk.strategic_guard",
            "strategic_damage_guard_persists",
        ),
        ("uquant.risk.strategic_guard", "_strategic_damage_guard_required"): (
            "uquant.risk.strategic_guard",
            "strategic_damage_guard_required",
        ),
        ("uquant.risk.strategic_guard", "_strategic_grace_supported"): (
            "uquant.risk.strategic_guard",
            "strategic_grace_supported",
        ),
        ("uquant.risk.strategic_guard", "_strategic_guard_level2_overlay_required"): (
            "uquant.risk.strategic_guard",
            "strategic_guard_level2_overlay_required",
        ),
        ("uquant.risk.strategic_guard", "_update_strategic_damage_guard"): (
            "uquant.risk.strategic_guard",
            "update_strategic_damage_guard",
        ),
        ("uquant.risk.transitions", "_acute_sector_evacuation_required"): (
            "uquant.risk.transitions",
            "acute_sector_evacuation_required",
        ),
        ("uquant.risk.transitions", "_assess_acute_and_cooldown"): (
            "uquant.risk.transitions",
            "assess_acute_and_cooldown",
        ),
        ("uquant.risk.transitions", "_assess_break_conditions"): (
            "uquant.risk.transitions",
            "assess_break_conditions",
        ),
        ("uquant.risk.transitions", "_assess_confirmed_concentrated_break"): (
            "uquant.risk.confirmed_break",
            "assess_confirmed_concentrated_break",
        ),
        ("uquant.risk.transitions", "_resolve_risk_transition"): (
            "uquant.risk.transition_resolution",
            "resolve_risk_transition",
        ),
    },
}

TASK_EDGE_COUNTS = {6: 17, 7: 29}
TASK_IMPORTER_LOCAL_NAMES = {
    ("uquant.application", "uquant.application.decision", "_attach_target_attribution"): (
        "attach_target_attribution"
    ),
    ("uquant.application", "uquant.application.decision", "_decision_config_for_universe"): (
        "decision_config_for_universe"
    ),
    ("uquant.application", "uquant.application.decision", "_mark_account_positions"): (
        "mark_account_positions"
    ),
    ("uquant.application", "uquant.application.metrics", "_drawdown_stats"): "drawdown_stats",
    ("uquant.application", "uquant.application.risk_timeline_cache", "_canonical_json"): (
        "canonical_risk_json"
    ),
    ("uquant.application", "uquant.application.risk_timeline_cache", "_causal_risk_timeline"): (
        "causal_risk_timeline"
    ),
    (
        "uquant.application",
        "uquant.application.risk_timeline_cache",
        "_load_risk_timeline_disk_cache",
    ): "load_risk_timeline_disk_cache",
    ("uquant.application", "uquant.application.risk_timeline_cache", "_risk_timeline_disk_path"): (
        "risk_timeline_disk_path"
    ),
    (
        "uquant.application",
        "uquant.application.risk_timeline_cache",
        "_write_risk_timeline_disk_cache",
    ): "write_risk_timeline_disk_cache",
}


def _task_rows(task: int) -> list[object]:
    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, Mapping)
    rows = graph[f"task{task}_relocated_private_imports"]
    assert isinstance(rows, list)
    return rows


def _sealed_source(relative: str) -> str:
    tree = subprocess.check_output(
        ["git", "rev-parse", f"{_TASK6_TASK7_PUBLIC_OWNERS_COMMIT}^{{tree}}"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert tree == _TASK6_TASK7_PUBLIC_OWNERS_TREE
    return subprocess.check_output(
        [
            "git",
            "show",
            f"{_TASK6_TASK7_PUBLIC_OWNERS_COMMIT}:{relative}",
        ],
        cwd=ROOT,
        text=True,
    )


def _assert_task7_removed_runtime_route_projection() -> None:
    assessment = ast.parse(
        _sealed_source("uquant/risk/assessment.py"),
        type_comments=True,
    )
    private_definitions = [
        node
        for node in assessment.body
        if isinstance(node, ast.FunctionDef) and node.name == "_risk_runtime_seam"
    ]
    aliases = [
        node
        for node in assessment.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "risk_runtime_seam"
            for target in node.targets
        )
    ]
    assert len(private_definitions) == len(aliases) == 1
    assert isinstance(aliases[0].value, ast.Name)
    assert aliases[0].value.id == "_risk_runtime_seam"

    facade = ast.parse(_sealed_source("uquant/risk/__init__.py"), type_comments=True)
    imports = [
        alias
        for node in facade.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "assessment"
        for alias in node.names
        if alias.name == "risk_runtime_seam"
    ]
    assert len(imports) == 1 and imports[0].asname == "_risk_runtime_seam"

    current_facade = importlib.import_module("uquant.risk")
    current_owner = importlib.import_module("uquant.risk.assessment")
    assert "_risk_runtime_seam" not in vars(current_facade)
    assert "risk_runtime_seam" not in vars(current_owner)
    assert current_facade.base_risk_assessor() is current_facade._assess_base_risk
    assert current_facade.dynamic_anchor_updater() is current_facade._update_dynamic_anchors


@pytest.mark.parametrize("task", (6, 7))  # type: ignore[untyped-decorator]
def test_task10_task6_task7_public_routes_preserve_exact_owner_identity(task: int) -> None:
    rows = _task_rows(task)
    pairs = {
        (str(row["imported_from"]), str(row["name"]))
        for row in rows
        if isinstance(row, Mapping)
    }
    routes = TASK_PUBLIC_ROUTES[task]
    assert len(rows) == TASK_EDGE_COUNTS[task]
    assert pairs == set(routes)
    for (legacy_owner, private_name), (public_owner, public_name) in sorted(
        routes.items()
    ):
        if (
            legacy_owner,
            private_name,
            public_owner,
            public_name,
        ) == _TASK7_REMOVED_RUNTIME_ROUTE:
            _assert_task7_removed_runtime_route_projection()
            continue
        legacy_module = importlib.import_module(legacy_owner)
        public_module = importlib.import_module(public_owner)
        assert getattr(legacy_module, private_name) is getattr(public_module, public_name)


@pytest.mark.parametrize("task", (6, 7))  # type: ignore[untyped-decorator]
def test_task10_task6_task7_importers_keep_exact_local_legacy_bindings(task: int) -> None:
    routes = TASK_PUBLIC_ROUTES[task]
    for row in _task_rows(task):
        assert isinstance(row, Mapping)
        legacy_owner = str(row["imported_from"])
        private_name = str(row["name"])
        public_owner, public_name = routes[(legacy_owner, private_name)]
        importer_name = str(row["importer"])
        local_name = TASK_IMPORTER_LOCAL_NAMES.get(
            (importer_name, legacy_owner, private_name),
            private_name,
        )
        if (
            legacy_owner,
            private_name,
            public_owner,
            public_name,
        ) == _TASK7_REMOVED_RUNTIME_ROUTE:
            assert importer_name == "uquant.risk"
            assert local_name == "_risk_runtime_seam"
            _assert_task7_removed_runtime_route_projection()
            continue
        importer = importlib.import_module(importer_name)
        owner = importlib.import_module(public_owner)
        assert getattr(importer, local_name) is getattr(owner, public_name)


@pytest.mark.parametrize("task", (6, 7))  # type: ignore[untyped-decorator]
def test_task10_task6_task7_current_private_edges_are_closed(task: int) -> None:
    historical_ids = {
        str(row["id"])
        for row in _task_rows(task)
        if isinstance(row, Mapping)
    }
    observed = scan_governed_private_edges(current_governed_sources())
    current_ids = {
        str(row["id"])
        for row in observed["direct"]
        if isinstance(row, Mapping)
    }
    assert not current_ids & historical_ids


def test_task10_task6_task7_raw_count_progression_is_exact() -> None:
    checkpoint = scan_sealed_governed_private_edges(
        ROOT,
        commit=_TASK6_TASK7_PUBLIC_OWNERS_COMMIT,
        tree=_TASK6_TASK7_PUBLIC_OWNERS_TREE,
    )
    assert len(checkpoint["direct"]) == 224
    assert len(checkpoint["qualified"]) == 19
    assert scan_governed_private_edges(current_governed_sources()) == {
        "direct": [],
        "qualified": [],
        "dynamic": [],
    }
