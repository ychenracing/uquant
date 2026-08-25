"""Exact Task-10 owner transport for the frozen Task-6 decision fan-out."""

from __future__ import annotations

import ast
import copy
import hashlib
import subprocess
from collections.abc import Mapping, Set
from pathlib import Path

from ._task10_inventory import TASK10_START_COMMIT

_APPLICATION_STAGES = "7d98ce7ea30faa7871021217948de0cfd526c05c"
_ATTRIBUTION_STAGES = "b00b89d482b51df38b251db4302c5924cf05e93e"
_DECISION_OWNER = "eb6c7321b620fbba2f1abae4af538033fce10a16"
_TASK10_CLOSURE = "c0cde6c60bbf234d08e836f84981aa1b3231279b"
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
TASK10_TASK6_REVIEWED_DEFINITIONS = frozenset(
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


def _git_source(root: Path, revision: str, relative: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{revision}:{relative}"],
        cwd=root,
        text=True,
    )


def _reviewed_revision(relative: str) -> str:
    return _TASK10_CLOSURE if relative == _DECISION_PATH else _REVIEWED_SOURCE_CHAINS[relative][-1]


def task6_reviewed_source(root: Path, relative: str) -> str:
    """Read one Task-6 proof input from its immutable reviewed commit."""

    assert relative in _REVIEWED_SOURCE_CHAINS
    return _git_source(root, _reviewed_revision(relative), relative)


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


def validate_task6_decision_owner_transport(
    *,
    root: Path,
    source_overrides: Mapping[str, str] | None = None,
) -> None:
    """Require the reviewed move and exact public wrapper call."""
    if source_overrides is not None:
        assert set(source_overrides) <= {_DECISION_PATH, _EXTRACTED_PATH}
    decision_log = subprocess.check_output(
        [
            "git",
            "log",
            "--format=%H",
            f"{TASK10_START_COMMIT}..{_DECISION_OWNER}",
            "--",
            _DECISION_PATH,
        ],
        cwd=root,
        text=True,
    ).splitlines()
    assert tuple(reversed(decision_log)) == _DECISION_CHAIN
    extracted_log = subprocess.check_output(
        [
            "git",
            "log",
            "--format=%H",
            f"{TASK10_START_COMMIT}..{_DECISION_OWNER}",
            "--",
            _EXTRACTED_PATH,
        ],
        cwd=root,
        text=True,
    ).splitlines()
    assert extracted_log == [_DECISION_OWNER]
    closure_log = subprocess.check_output(
        [
            "git",
            "log",
            "--format=%H",
            f"{_DECISION_OWNER}..{_TASK10_CLOSURE}",
            "--",
            _DECISION_PATH,
        ],
        cwd=root,
        text=True,
    ).splitlines()
    assert closure_log == [_TASK10_CLOSURE]
    reviewed_sources = {
        relative: task6_reviewed_source(root, relative)
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

    parent = _function(
        _git_source(root, f"{_DECISION_OWNER}^", _DECISION_PATH),
        "_attach_target_attribution",
    )
    owner = copy.deepcopy(_function(extracted, "attach_target_attribution"))
    owner.name = "_attach_target_attribution"
    assert ast.dump(owner, include_attributes=False) == ast.dump(
        parent,
        include_attributes=False,
    )
    wrapper = _function(decision, "_attach_target_attribution")
    returns = [node for node in wrapper.body if isinstance(node, ast.Return)]
    assert len(returns) == 1
    call = returns[0].value
    assert isinstance(call, ast.Call)
    assert isinstance(call.func, ast.Name) and call.func.id == "attach_target_attribution"
    assert tuple(ast.unparse(argument) for argument in call.args) == (
        "legacy_industry",
        "legacy_manifest_sha256",
    )
    assert tuple(
        (keyword.arg, ast.unparse(keyword.value)) for keyword in call.keywords
    ) == (
        ("signal_date", "signal_date"),
        ("targets", "targets"),
        ("retained_orders", "retained_orders"),
        ("cfg", "cfg"),
    )


def task10_task6_decision_fanout(
    *,
    root: Path,
    decision_fan_out: Set[str],
    extracted_owner_fan_out: Set[str],
) -> int:
    """Collapse only the exact extracted owner back into its frozen caller."""
    validate_task6_decision_owner_transport(root=root)
    decision = frozenset(decision_fan_out)
    extracted = frozenset(extracted_owner_fan_out)
    assert decision == _DECISION_FAN_OUT
    assert extracted == _EXTRACTED_FAN_OUT
    assert extracted <= decision - {"uquant.application.target_attribution"}
    return len(decision - {"uquant.application.target_attribution"} | extracted)


def _assert_reviewed_task6_sources(root: Path) -> None:
    for relative, chain in _REVIEWED_SOURCE_CHAINS.items():
        reviewed = chain[-1]
        log = subprocess.check_output(
            [
                "git",
                "log",
                "--format=%H",
                f"{TASK10_START_COMMIT}..{reviewed}",
                "--",
                relative,
            ],
            cwd=root,
            text=True,
        ).splitlines()
        assert tuple(reversed(log)) == chain
        source = task6_reviewed_source(root, relative)
        ast.parse(source, filename=relative, type_comments=True)


def reviewed_task6_debt_definition(
    *,
    root: Path,
    relative: str,
    name: str,
    candidate: ast.FunctionDef | ast.ClassDef | None,
    frozen: ast.FunctionDef | ast.ClassDef,
    source_overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef | ast.ClassDef:
    """Bind one changed Task-6 definition through its exact reviewed Task-10 owner."""
    assert (relative, name) in TASK10_TASK6_REVIEWED_DEFINITIONS
    if source_overrides is not None:
        assert set(source_overrides) == {relative}
    chain = _REVIEWED_SOURCE_CHAINS[relative]
    reviewed = chain[-1]
    log = subprocess.check_output(
        [
            "git",
            "log",
            "--format=%H",
            f"{TASK10_START_COMMIT}..{reviewed}",
            "--",
            relative,
        ],
        cwd=root,
        text=True,
    ).splitlines()
    assert tuple(reversed(log)) == chain
    reviewed_source = task6_reviewed_source(root, relative)
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
    assert len(reviewed_matches) == 1
    if candidate is not None:
        assert ast.dump(candidate, include_attributes=False) == ast.dump(
            reviewed_matches[0],
            include_attributes=False,
        )
    assert reviewed_matches[0].name == frozen.name == name
    return copy.deepcopy(frozen)


def task10_task6_historical_debt_projection(
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
    from ._analysis import MODULE_AUTHORITIES, architecture_snapshot, git_python_sources

    immutable_sources = git_python_sources(root, _TASK10_CLOSURE)
    immutable_modules = {
        path.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".")
        for path in immutable_sources
    }
    stale_authorities = set(MODULE_AUTHORITIES) - immutable_modules
    assert stale_authorities == {
        "uquant.validation.holdout.capabilities",
        "uquant.validation.production_observation_contract",
    }
    immutable = architecture_snapshot(
        source_texts=immutable_sources,
        module_authorities={
            module: authority
            for module, authority in MODULE_AUTHORITIES.items()
            if module in immutable_modules
        },
    )
    immutable_function_rows = immutable["functions"]
    immutable_global_rows = immutable["module_globals"]
    assert isinstance(immutable_function_rows, list)
    assert isinstance(immutable_global_rows, list)
    functions = {str(row["id"]): row for row in immutable_function_rows}
    for identifier in historical_functions:
        row = functions[identifier]
        assert int(row["lines"]) <= 120
        assert int(row["branch_points"]) <= 20
    globals_by_id = {str(row["id"]): row for row in immutable_global_rows}
    for identifier in historical_globals:
        row = globals_by_id[identifier]
        assert not bool(row["mutable_initializer"])
        assert not bool(row["mutation_sites"])
    _assert_reviewed_task6_sources(root)
    validate_task6_decision_owner_transport(root=root)
    return set(historical_functions), set(historical_globals)
