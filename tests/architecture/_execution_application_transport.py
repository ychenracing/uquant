"""Exact owner transport for the frozen execution decision fan-out."""

from __future__ import annotations

import ast
import copy
import hashlib
import inspect
from collections.abc import Mapping, Set
from pathlib import Path
from typing import Any

_APPLICATION_STAGES = "7d98ce7ea30faa7871021217948de0cfd526c05c"
_ATTRIBUTION_STAGES = "b00b89d482b51df38b251db4302c5924cf05e93e"
_DECISION_OWNER = "eb6c7321b620fbba2f1abae4af538033fce10a16"
_ARCHITECTURE_CLOSURE = "c0cde6c60bbf234d08e836f84981aa1b3231279b"
_DECISION_PATH = "uquant/application/decision.py"
_EXTRACTED_PATH = "uquant/application/target_attribution.py"
_RISK_TIMELINE_PATH = "uquant/application/risk_timeline_cache.py"
_DECISION_CHAIN = (_APPLICATION_STAGES, _ATTRIBUTION_STAGES, _DECISION_OWNER)
_DECISION_FAN_OUT = frozenset(
    {
        "uquant.account.codec",
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
ARCHITECTURE_CURRENT_ENGINE_DOCSTRINGS = {
    "_decision_config_for_universe": (
        "Return one production policy regardless of unrelated universe members.\n\n"
        "Universe size is retained only as diagnostic provenance. It must never select\n"
        "a different strategy configuration: an otherwise irrelevant symbol cannot\n"
        "change the decision path merely by crossing a pool-size threshold."
    ),
    "_load": "Load symbols through the market workspace owner.",
    "_price": "Read one point-in-time price through the market workspace owner.",
}
_REVIEWED_SOURCE_CHAINS: Mapping[str, tuple[str, ...]] = {
    "uquant/application/backtest.py": (_APPLICATION_STAGES,),
    _DECISION_PATH: _DECISION_CHAIN,
    "uquant/application/metrics.py": (
        _APPLICATION_STAGES,
        _ATTRIBUTION_STAGES,
    ),
    _RISK_TIMELINE_PATH: (_ARCHITECTURE_CLOSURE,),
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
        ("uquant/execution/reconciliation.py", "_register_account_order"),
        (
            "uquant/execution/reconciliation.py",
            "_reconcile_account_orders_mutating",
        ),
        ("uquant/execution/reconciliation.py", "reconcile_account_orders"),
        ("uquant/execution/tranches.py", "_consume_sell_tranches"),
        ("uquant/execution/tranches.py", "_rebuild_position_from_tranches"),
        ("uquant/execution/open_execution.py", "ExecutionPlanner"),
        ("uquant/application/decision.py", "_attach_target_attribution"),
        ("uquant/application/decision.py", "_decision_config_for_universe"),
        ("uquant/application/decision.py", "decide"),
        ("uquant/application/decision.py", "deterministic_decision"),
        (_RISK_TIMELINE_PATH, "_causal_risk_timeline"),
        ("uquant/application/backtest.py", "backtest"),
        ("uquant/application/metrics.py", "performance_metrics"),
    }
)


def execution_reviewed_source(root: Path, relative: str) -> str:
    """Read one execution proof input from its immutable reviewed commit."""

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
    """Bind one changed execution definition through its exact reviewed owner."""
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


def validate_engine_descriptor_transport(
    *,
    name: str,
    observed: Any,
    expected: Any,
) -> None:
    """Project the exact read-only universe declaration back to the frozen facade."""

    observed_signature = inspect.signature(observed)
    expected_signature = inspect.signature(expected)
    observed_annotations = dict(observed.__annotations__)
    expected_annotations = dict(expected.__annotations__)
    current_docstring = ARCHITECTURE_CURRENT_ENGINE_DOCSTRINGS.get(name)
    if current_docstring is not None:
        assert inspect.cleandoc(observed.__doc__ or "") == current_docstring
    if name == "_decision_config_for_universe":
        assert observed_signature == expected_signature
        assert observed_annotations == expected_annotations
        return
    if name == "_causal_risk_timeline":
        parameter = observed_signature.parameters["role_absent_symbols"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default == ()
        assert parameter.annotation == "tuple[str, ...]"
        projected = observed_signature.replace(
            parameters=[
                value
                for key, value in observed_signature.parameters.items()
                if key != "role_absent_symbols"
            ]
        )
        assert projected == expected_signature
        assert observed_annotations.pop("role_absent_symbols") == "tuple[str, ...]"
        assert observed_annotations == expected_annotations
        return
    read_only_parameter = "strategic_universe_declaration"
    if name not in {"decide", "deterministic_decision"}:
        assert observed_signature == expected_signature
        assert observed_annotations == expected_annotations
        return
    parameter = observed_signature.parameters[read_only_parameter]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None
    assert parameter.annotation == "StrategicUniverseDeclaration | None"
    projected = observed_signature.replace(
        parameters=[
            value
            for key, value in observed_signature.parameters.items()
            if key != read_only_parameter
        ]
    )
    assert projected == expected_signature
    assert observed_annotations.pop(read_only_parameter) == (
        "StrategicUniverseDeclaration | None"
    )
    assert observed_annotations == expected_annotations


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
    """Separate live-zero acceptance from exact frozen execution-debt identity."""
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
