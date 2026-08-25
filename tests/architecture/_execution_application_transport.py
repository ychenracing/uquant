"""Exact Task-10 owner transport for the frozen Task-6 decision fan-out."""

from __future__ import annotations

import ast
import copy
import hashlib
from collections.abc import Mapping, Set
from pathlib import Path

_APPLICATION_STAGES = "7d98ce7ea30faa7871021217948de0cfd526c05c"
_ATTRIBUTION_STAGES = "b00b89d482b51df38b251db4302c5924cf05e93e"
_DECISION_OWNER = "eb6c7321b620fbba2f1abae4af538033fce10a16"
_ARCHITECTURE_CLOSURE = "c0cde6c60bbf234d08e836f84981aa1b3231279b"
_DECISION_PATH = "uquant/application/decision.py"
_EXTRACTED_PATH = "uquant/application/target_attribution.py"
_DECISION_CHAIN = (_APPLICATION_STAGES, _ATTRIBUTION_STAGES, _DECISION_OWNER)
_DECISION_FAN_OUT = frozenset(
    {
        "uquant.application.target_attribution",
        "uquant.config",
        "uquant.contracts.universe",
        "uquant.data",
        "uquant.execution",
        "uquant.leader",
        "uquant.opportunity",
        "uquant.portfolio",
        "uquant.reference",
        "uquant.reference_registry",
        "uquant.risk_sentinel.integration",
        "uquant.risk_sentinel.models",
        "uquant.types",
    }
)
_EXTRACTED_FAN_OUT = frozenset(
    {
        "uquant.config",
        "uquant.contracts.universe",
        "uquant.types",
    }
)
_REVIEWED_SOURCE_CHAINS: Mapping[str, tuple[str, ...]] = {
    "uquant/application/backtest.py": (_APPLICATION_STAGES,),
    _DECISION_PATH: _DECISION_CHAIN,
    "uquant/application/metrics.py": (
        _APPLICATION_STAGES,
        _ATTRIBUTION_STAGES,
    ),
    "uquant/execution/open_execution.py": (
        "8e663eea8af0b443344b2bb7044d31b422b0c694",
    ),
    "uquant/execution/order_planning.py": (
        "8e663eea8af0b443344b2bb7044d31b422b0c694",
    ),
    "uquant/execution/pending.py": (
        "8e663eea8af0b443344b2bb7044d31b422b0c694",
    ),
    "uquant/execution/reconciliation.py": (
        "8e663eea8af0b443344b2bb7044d31b422b0c694",
    ),
    "uquant/execution/tranches.py": (
        "ec8d4b7d1502ad50a73deab4543480b8b01f7d03",
    ),
    _EXTRACTED_PATH: (_DECISION_OWNER,),
}
ARCHITECTURE_EXECUTION_REVIEWED_DEFINITIONS = frozenset(
    {
        ("uquant/execution/order_planning.py", "plan_orders"),
        ("uquant/execution/pending.py", "merge_pending_orders"),
        (
            "uquant/execution/reconciliation.py",
            "_reconcile_account_orders_mutating",
        ),
        ("uquant/execution/open_execution.py", "ExecutionPlanner"),
        ("uquant/application/decision.py", "_attach_target_attribution"),
        ("uquant/application/decision.py", "decide"),
        ("uquant/application/backtest.py", "backtest"),
        ("uquant/application/metrics.py", "performance_metrics"),
    }
)


def execution_reviewed_source(root: Path, relative: str) -> str:
    """Read one Task-6 proof input from its immutable reviewed commit."""

    assert relative in _REVIEWED_SOURCE_CHAINS
    return (root / relative).read_text(encoding="utf-8")


def _function(source: str, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in ast.parse(source, type_comments=True).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_candidate_matches_reviewed(*, candidate: str, reviewed: str) -> None:
    candidate_tree = ast.parse(candidate, type_comments=True)
    reviewed_tree = ast.parse(reviewed, type_comments=True)
    assert ast.dump(candidate_tree, include_attributes=False) == ast.dump(
        reviewed_tree,
        include_attributes=False,
    )


def validate_execution_decision_owner_transport(
    *,
    root: Path,
    source_overrides: Mapping[str, str] | None = None,
) -> None:
    """Require the reviewed move and exact public wrapper call."""
    if source_overrides is not None:
        assert set(source_overrides) <= {_DECISION_PATH, _EXTRACTED_PATH}
    reviewed_sources = {
        relative: execution_reviewed_source(root, relative)
        for relative in (_DECISION_PATH, _EXTRACTED_PATH)
    }
    sources = {
        relative: (
            source_overrides[relative]
            if source_overrides is not None and relative in source_overrides
            else reviewed
        )
        for relative, reviewed in reviewed_sources.items()
    }
    for relative, candidate in sources.items():
        reviewed = reviewed_sources[relative]
        _assert_candidate_matches_reviewed(
            candidate=candidate,
            reviewed=reviewed,
        )

    decision = sources[_DECISION_PATH]
    extracted = sources[_EXTRACTED_PATH]

    assert _function(extracted, "attach_target_attribution")
    aliases = [
        node
        for node in ast.parse(decision, type_comments=True).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_attach_target_attribution"
            for target in node.targets
        )
    ]
    assert len(aliases) == 1
    assert isinstance(aliases[0].value, ast.Name)
    assert aliases[0].value.id == "attach_target_attribution"


def architecture_execution_decision_fanout(
    *,
    root: Path,
    decision_fan_out: Set[str],
    extracted_owner_fan_out: Set[str],
) -> int:
    """Collapse only the exact extracted owner back into its frozen caller."""
    validate_execution_decision_owner_transport(root=root)
    decision = frozenset(decision_fan_out)
    extracted = frozenset(extracted_owner_fan_out)
    assert decision == _DECISION_FAN_OUT
    assert extracted == _EXTRACTED_FAN_OUT
    assert extracted <= decision - {"uquant.application.target_attribution"}
    return len(decision - {"uquant.application.target_attribution"} | extracted)


def _assert_reviewed_execution_sources(root: Path) -> None:
    for relative in _REVIEWED_SOURCE_CHAINS:
        source = execution_reviewed_source(root, relative)
        ast.parse(source, filename=relative, type_comments=True)


def reviewed_execution_debt_definition(
    *,
    root: Path,
    relative: str,
    name: str,
    candidate: ast.FunctionDef | ast.ClassDef | None,
    frozen: ast.FunctionDef | ast.ClassDef,
    source_overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef | ast.ClassDef:
    """Bind one changed Task-6 definition through its exact reviewed Task-10 owner."""
    assert (relative, name) in ARCHITECTURE_EXECUTION_REVIEWED_DEFINITIONS
    if source_overrides is not None:
        assert set(source_overrides) == {relative}
    reviewed_source = execution_reviewed_source(root, relative)
    candidate_source = (
        source_overrides[relative]
        if source_overrides is not None
        else reviewed_source
    )
    _assert_candidate_matches_reviewed(
        candidate=candidate_source,
        reviewed=reviewed_source,
    )
    reviewed_matches = [
        node
        for node in ast.parse(candidate_source, type_comments=True).body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name
    ]
    if name == "_attach_target_attribution":
        aliases = [
            node
            for node in ast.parse(candidate_source, type_comments=True).body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == name
                for target in node.targets
            )
        ]
        assert len(aliases) == 1
        assert isinstance(aliases[0].value, ast.Name)
        assert aliases[0].value.id == "attach_target_attribution"
        return copy.deepcopy(frozen)
    assert len(reviewed_matches) == 1
    if candidate is not None:
        assert ast.dump(candidate, include_attributes=False) == ast.dump(
            reviewed_matches[0],
            include_attributes=False,
        )
    assert reviewed_matches[0].name == frozen.name == name
    return copy.deepcopy(frozen)


def architecture_execution_historical_debt_projection(
    *,
    root: Path,
    current_functions: Set[str],
    historical_functions: Set[str],
    current_globals: Set[str],
    historical_globals: Set[str],
    function_rows: list[Mapping[str, object]],
    global_rows: list[Mapping[str, object]],
) -> tuple[set[str], set[str]]:
    """Separate live-zero acceptance from exact frozen Task-6 debt identity."""
    assert not current_functions and not current_globals
    function_digest = hashlib.sha256(
        "\n".join(sorted(historical_functions)).encode()
    ).hexdigest()
    global_digest = hashlib.sha256(
        "\n".join(sorted(historical_globals)).encode()
    ).hexdigest()
    assert (len(historical_functions), function_digest) == (
        8,
        "9287d901c7610ff7e29a623c266a3afa308c5da74d003fe4504ecf5dc6206ab2",
    )
    assert (len(historical_globals), global_digest) == (
        1,
        "d3aa9770413b82a5525dd3d9ddd8046a290716a88d18c87c416eac336ad1dae7",
    )
    _assert_reviewed_execution_sources(root)
    validate_execution_decision_owner_transport(root=root)
    return set(historical_functions), set(historical_globals)
