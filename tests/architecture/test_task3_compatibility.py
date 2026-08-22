from __future__ import annotations

import ast
import copy
import json
import types
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import cast

import pytest

import uquant.config.model as config_model
import uquant.config.validation.execution as execution_validation
import uquant.config.validation.market as market_validation
import uquant.config.validation.strategic as strategic_validation
from uquant.config import DEFAULT_CONFIG

from ._analysis import ROOT
from ._task3_baseline import (
    BASELINE_COMMIT,
    ISOLATED_VALIDATION_CASE_COUNT,
    METHOD_IDS,
    REACHABLE_WITNESS_CASE_COUNT,
    REACHABLE_WITNESS_START_INDEX,
    TOTAL_VALIDATION_CASE_COUNT,
    UNKNOWN_KEYWORD_CASE_INDEX,
    baseline_config_module,
    baseline_load_method_pickles,
    baseline_method_contract,
    baseline_validation_clause_dumps,
    candidate_validation_clause_dumps,
    capture_validation_contract,
    current_load_method_pickles,
    current_method_contract,
    exception_observation,
    validation_fixture_metadata,
)

VALIDATION_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "task3_config_validation_contract.json"

REACHABLE_ADJACENT_SWAPS = (
    (
        "leader-cycle-market-range-before-impulse-relation",
        "uquant/config/validation/strategic.py",
        "_validate_strategic_admission",
        4,
        "uquant.config.validation.strategic",
    ),
    (
        "strategic-transition-max-range-before-inverted-range",
        "uquant/config/validation/strategic.py",
        "_validate_strategic_transition_bounds",
        3,
        "uquant.config.validation.strategic",
    ),
)

STRUCTURAL_ONLY_ADJACENT_SWAPS = (
    (
        "transition-range-before-repair-relation",
        "uquant/config/validation/risk.py",
        "_validate_risk_anchors_and_capital",
        3,
        "uquant.config.validation.risk",
    ),
    (
        "transition-repair-relation-before-chronic-window",
        "uquant/config/validation/risk.py",
        "_validate_risk_anchors_and_capital",
        4,
        "uquant.config.validation.risk",
    ),
)

ALL_AUDITED_ADJACENT_SWAPS = (
    *REACHABLE_ADJACENT_SWAPS,
    *STRUCTURAL_ONLY_ADJACENT_SWAPS,
)


def _validation_fixture() -> dict[str, object]:
    value = json.loads(VALIDATION_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _mutated_source_with_adjacent_function_clauses_swapped(
    relative_path: str,
    function_name: str,
    left_clause_index: int,
) -> str:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    offset = int(
        bool(function.body)
        and isinstance(function.body[0], ast.Expr)
        and isinstance(function.body[0].value, ast.Constant)
        and isinstance(function.body[0].value.value, str)
    )
    left = offset + left_clause_index
    function.body[left], function.body[left + 1] = (
        function.body[left + 1],
        function.body[left],
    )
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _compiled_mutated_function(
    relative_path: str,
    function_name: str,
    left_clause_index: int,
    module: types.ModuleType,
) -> object:
    source = _mutated_source_with_adjacent_function_clauses_swapped(
        relative_path,
        function_name,
        left_clause_index,
    )
    tree = ast.parse(source, filename=relative_path)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[function], type_ignores=[]), relative_path, "exec"), namespace)
    return namespace[function_name]


def _mutated_model_source_with_adjacent_validator_calls_swapped(
    left_call_index: int,
) -> str:
    relative_path = "uquant/config/model.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=relative_path)
    config_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SystemConfig"
    )
    post_init = next(
        node
        for node in config_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__post_init__"
    )
    offset = int(
        isinstance(post_init.body[0], ast.Expr)
        and isinstance(post_init.body[0].value, ast.Constant)
        and isinstance(post_init.body[0].value.value, str)
    )
    left = offset + left_call_index
    post_init.body[left], post_init.body[left + 1] = (
        post_init.body[left + 1],
        post_init.body[left],
    )
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _market_wrapper_rebind_source() -> str:
    relative_path = "uquant/config/validation/market.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    return source + """

_original_validate_market = validate_market

def _wrapped_validate_market(config: Any) -> None:
    _original_validate_market(config)
    if config.initial_cash == 12345:
        raise ValueError("rebound validator changed valid behavior")

validate_market = _wrapped_validate_market
"""


def test_validation_fixture_is_reproducible_from_immutable_baseline_behavior() -> None:
    fixture = _validation_fixture()
    metadata = fixture["baseline"]
    assert isinstance(metadata, Mapping)
    assert metadata["baseline_commit"] == BASELINE_COMMIT

    assert capture_validation_contract(fixture) == fixture


@pytest.mark.parametrize(
    "malformation",
    (
        "shortened",
        "repartitioned",
        "duplicate",
        "isolated-marker",
        "invalid-order-probe",
        "synthetic-marker",
        "unknown-keyword-moved",
    ),
)
def test_validation_capture_rejects_malformed_fixture_shapes(malformation: str) -> None:
    fixture = copy.deepcopy(_validation_fixture())
    cases = cast(list[dict[str, object]], fixture["cases"])
    metadata = cast(dict[str, object], fixture["baseline"])

    if malformation == "shortened":
        cases.pop()
    elif malformation == "repartitioned":
        metadata["isolated_case_count"] = ISOLATED_VALIDATION_CASE_COUNT - 1
    elif malformation == "duplicate":
        cases[1] = copy.deepcopy(cases[0])
    elif malformation == "isolated-marker":
        cases[0]["witness_id"] = "not-isolated"
    elif malformation == "invalid-order-probe":
        cases[metadata["order_probe_start_index"]] = {
            "changes": {"initial_cash": -1},
            "exception_type": "ValueError",
            "message": "initial_cash must be positive",
        }
    elif malformation == "synthetic-marker":
        cases[REACHABLE_WITNESS_START_INDEX]["changes"] = {
            "leader_cycle_min_market_ret120": {
                "__task3_comparison_probe__": "removed"
            }
        }
    else:
        cases[UNKNOWN_KEYWORD_CASE_INDEX], cases[UNKNOWN_KEYWORD_CASE_INDEX - 1] = (
            cases[UNKNOWN_KEYWORD_CASE_INDEX - 1],
            cases[UNKNOWN_KEYWORD_CASE_INDEX],
        )

    with pytest.raises(AssertionError):
        capture_validation_contract(fixture)


def test_validation_fixture_partitions_are_exact_and_fail_closed() -> None:
    fixture = _validation_fixture()
    cases = cast(Sequence[Mapping[str, object]], fixture["cases"])
    metadata = cast(Mapping[str, object], fixture["baseline"])

    assert len(cases) == TOTAL_VALIDATION_CASE_COUNT
    assert metadata["total_case_count"] == TOTAL_VALIDATION_CASE_COUNT
    assert metadata["reachable_witness_start_index"] == REACHABLE_WITNESS_START_INDEX
    assert metadata["reachable_witness_case_count"] == REACHABLE_WITNESS_CASE_COUNT
    assert metadata["unknown_keyword_case_index"] == UNKNOWN_KEYWORD_CASE_INDEX
    assert cases[UNKNOWN_KEYWORD_CASE_INDEX]["changes"] == {
        "not_a_governed_parameter": True
    }


def test_validation_capture_rejects_replaced_stimulus_with_regenerated_metadata() -> None:
    fixture = copy.deepcopy(_validation_fixture())
    cases = cast(list[dict[str, object]], fixture["cases"])
    cases[15] = {
        "changes": {"min_trade_weight": -1},
        "exception_type": "ValueError",
        "message": (
            "protected_restore_min_trade_weight/restoration_min_trade_weight must be "
            "positive, ordered, and no greater than min_trade_weight"
        ),
    }
    fixture["baseline"] = validation_fixture_metadata(cases)

    with pytest.raises(AssertionError):
        capture_validation_contract(fixture)


def test_split_validators_preserve_all_159_baseline_clauses_in_exact_ast_order() -> None:
    baseline = baseline_validation_clause_dumps()
    candidate = candidate_validation_clause_dumps()

    assert len(baseline) == 159
    assert len(candidate) == 159
    assert candidate == baseline
    assert all(left != right for left, right in pairwise(candidate))


def test_semantic_gate_rejects_wrapper_and_runtime_rebinding_bypass() -> None:
    relative_path = "uquant/config/validation/market.py"
    mutated_source = _market_wrapper_rebind_source()
    namespace: dict[str, object] = {}
    exec(compile(mutated_source, relative_path, "exec"), namespace)
    valid_config = DEFAULT_CONFIG.override(initial_cash=12345)
    rebound = namespace["validate_market"]
    assert callable(rebound)
    with pytest.raises(ValueError, match="rebound validator changed valid behavior"):
        rebound(valid_config)

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps({relative_path: mutated_source})


@pytest.mark.parametrize(
    "mutation",
    (
        "decorator",
        "alias",
        "conditional-rebind",
        "conditional-delete",
    ),
)
def test_semantic_gate_rejects_validator_binding_shadow_variants(
    mutation: str,
) -> None:
    relative_path = "uquant/config/validation/market.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    if mutation == "decorator":
        source = source.replace(
            "def validate_market(config: Any) -> None:",
            "@staticmethod\ndef validate_market(config: Any) -> None:",
            1,
        )
    elif mutation == "alias":
        source += "\n_validate_market_alias = validate_market\n"
    elif mutation == "conditional-rebind":
        source += "\nif False:\n    validate_market = lambda config: None\n"
    else:
        source += "\nif False:\n    del validate_market\n"

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps({relative_path: source})


@pytest.mark.parametrize(
    ("relative_path", "suffix"),
    (
        (
            "uquant/config/model.py",
            "\nvalidate_market = validate_execution\n",
        ),
        (
            "uquant/config/validation/strategic.py",
            "\nif False:\n    _validate_strategic_admission = lambda config: None\n",
        ),
    ),
)
def test_semantic_gate_rejects_model_and_helper_source_rebinding(
    relative_path: str,
    suffix: str,
) -> None:
    source = (ROOT / relative_path).read_text(encoding="utf-8") + suffix

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps({relative_path: source})


def test_semantic_gate_verifies_live_validator_and_helper_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_market = market_validation.validate_market

    def rebound_market(config: object) -> None:
        original_market(config)

    rebound_market.__module__ = original_market.__module__
    rebound_market.__qualname__ = original_market.__qualname__
    monkeypatch.setattr(market_validation, "validate_market", rebound_market)
    monkeypatch.setattr(config_model, "validate_market", rebound_market)
    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps()

    monkeypatch.undo()
    original_helper = strategic_validation._validate_strategic_admission

    def rebound_helper(config: object) -> None:
        original_helper(config)

    rebound_helper.__module__ = original_helper.__module__
    rebound_helper.__qualname__ = original_helper.__qualname__
    monkeypatch.setattr(
        strategic_validation,
        "_validate_strategic_admission",
        rebound_helper,
    )
    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps()


def test_semantic_gate_rejects_source_builtin_shadow_that_changes_validation() -> None:
    relative_path = "uquant/config/validation/execution.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8") + """

getattr = lambda config, name: -1
"""
    namespace: dict[str, object] = {}
    exec(compile(source, relative_path, "exec"), namespace)
    mutated = namespace["validate_execution"]
    assert callable(mutated)
    with pytest.raises(ValueError, match="commission_rate cannot be negative"):
        mutated(DEFAULT_CONFIG)

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps({relative_path: source})


def test_semantic_gate_rejects_source_imported_global_retargeting() -> None:
    relative_path = "uquant/config/validation/sentinel.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    mutated_source = source.replace("import math", "import types as math", 1)
    namespace: dict[str, object] = {}
    exec(compile(mutated_source, relative_path, "exec"), namespace)
    mutated = namespace["validate_sentinel"]
    assert callable(mutated)
    with pytest.raises(AttributeError, match="isfinite"):
        mutated(DEFAULT_CONFIG)

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps({relative_path: mutated_source})


def test_semantic_gate_rejects_live_builtin_shadow_that_changes_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def shadow_getattr(config: object, name: str) -> int:
        return -1

    monkeypatch.setattr(execution_validation, "getattr", shadow_getattr, raising=False)
    with pytest.raises(ValueError, match="commission_rate cannot be negative"):
        execution_validation.validate_execution(DEFAULT_CONFIG)

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps()


def test_semantic_gate_rejects_source_system_config_dispatch_rebinding() -> None:
    relative_path = "uquant/config/model.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8") + """

def _rebound_getattribute(self: object, name: str) -> object:
    if name == "__post_init__":
        return lambda: None
    return object.__getattribute__(self, name)

SystemConfig.__getattribute__ = _rebound_getattribute
"""

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps({relative_path: source})


def test_semantic_gate_rejects_live_system_config_dispatch_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_type = type(DEFAULT_CONFIG)

    def rebound_getattribute(self: object, name: str) -> object:
        if name == "__post_init__":

            def changed_validation() -> None:
                raise ValueError("rebound SystemConfig dispatch changed valid behavior")

            return changed_validation
        return object.__getattribute__(self, name)

    monkeypatch.setattr(config_type, "__getattribute__", rebound_getattribute)
    with pytest.raises(ValueError, match="rebound SystemConfig dispatch"):
        DEFAULT_CONFIG.override(initial_cash=12345)

    with pytest.raises(AssertionError):
        candidate_validation_clause_dumps()


@pytest.mark.parametrize("left_call_index", range(9))
def test_semantic_ast_gate_detects_every_adjacent_validator_block_swap(
    left_call_index: int,
) -> None:
    relative_path = "uquant/config/model.py"
    mutated_source = _mutated_model_source_with_adjacent_validator_calls_swapped(
        left_call_index
    )

    assert candidate_validation_clause_dumps({relative_path: mutated_source}) != (
        baseline_validation_clause_dumps()
    )


@pytest.mark.parametrize(
    ("original", "replacement"),
    (
        (
            "config.leader_cycle_confirm_days < 1",
            "config.leader_cycle_confirm_days < 2",
        ),
        (
            '"leader_cycle_confirm_days must be positive"',
            '"leader_cycle_confirm_days changed"',
        ),
    ),
)
def test_semantic_ast_gate_detects_predicate_and_raise_changes(
    original: str,
    replacement: str,
) -> None:
    relative_path = "uquant/config/validation/strategic.py"
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    assert source.count(original) == 1
    mutated_source = source.replace(original, replacement, 1)

    assert candidate_validation_clause_dumps({relative_path: mutated_source}) != (
        baseline_validation_clause_dumps()
    )


@pytest.mark.parametrize(
    ("_witness_id", "relative_path", "function_name", "left_clause_index", "_module_name"),
    ALL_AUDITED_ADJACENT_SWAPS,
)
def test_semantic_ast_gate_detects_every_audited_adjacent_swap(
    _witness_id: str,
    relative_path: str,
    function_name: str,
    left_clause_index: int,
    _module_name: str,
) -> None:
    mutated_source = _mutated_source_with_adjacent_function_clauses_swapped(
        relative_path,
        function_name,
        left_clause_index,
    )

    assert candidate_validation_clause_dumps({relative_path: mutated_source}) != (
        baseline_validation_clause_dumps()
    )


@pytest.mark.parametrize(
    ("witness_id", "relative_path", "function_name", "left_clause_index", "module_name"),
    REACHABLE_ADJACENT_SWAPS,
)
def test_reachable_witnesses_detect_every_behavioral_adjacent_swap(
    monkeypatch: pytest.MonkeyPatch,
    witness_id: str,
    relative_path: str,
    function_name: str,
    left_clause_index: int,
    module_name: str,
) -> None:
    fixture = _validation_fixture()
    cases = cast(Sequence[Mapping[str, object]], fixture["cases"])
    witness = next(
        case
        for case in cases[
            REACHABLE_WITNESS_START_INDEX : REACHABLE_WITNESS_START_INDEX
            + REACHABLE_WITNESS_CASE_COUNT
        ]
        if case["witness_id"] == witness_id
    )
    changes = cast(Mapping[str, object], witness["changes"])
    expected = {
        "exception_type": witness["exception_type"],
        "message": witness["message"],
    }
    assert exception_observation(baseline_config_module().DEFAULT_CONFIG, changes) == expected
    assert exception_observation(DEFAULT_CONFIG, changes) == expected

    module = __import__(module_name, fromlist=[function_name])
    mutated = _compiled_mutated_function(
        relative_path,
        function_name,
        left_clause_index,
        module,
    )
    monkeypatch.setattr(module, function_name, mutated)

    assert exception_observation(DEFAULT_CONFIG, changes) != expected


def test_transition_relation_swaps_are_structural_only_for_numeric_config() -> None:
    fixture = _validation_fixture()
    metadata = cast(Mapping[str, object], fixture["baseline"])
    assert metadata["structural_only_adjacent_swaps"] == [
        {
            "baseline_clause_indexes": [140, 141],
            "swap_id": "transition-range-before-repair-relation",
        },
        {
            "baseline_clause_indexes": [141, 142],
            "swap_id": "transition-repair-relation-before-chronic-window",
        },
    ]

    expected = {
        "exception_type": "ValueError",
        "message": "invalid strategic damage guard transition",
    }
    for changes in (
        {
            "transition_damage_freeze": -0.1,
            "transition_damage_repair": 0.0,
        },
        {
            "chronic_confirm_days": 2,
            "transition_damage_freeze": 0.3,
            "transition_damage_repair": 0.4,
        },
    ):
        assert (
            exception_observation(baseline_config_module().DEFAULT_CONFIG, changes)
            == expected
        )
        assert exception_observation(DEFAULT_CONFIG, changes) == expected


def test_every_pair_of_isolated_invalid_stimuli_preserves_first_failure_order() -> None:
    fixture = _validation_fixture()
    cases = cast(Sequence[Mapping[str, object]], fixture["cases"])
    isolated = cases[:ISOLATED_VALIDATION_CASE_COUNT]
    baseline_default = baseline_config_module().DEFAULT_CONFIG
    comparisons = 0
    mismatches: list[dict[str, object]] = []

    for index, left in enumerate(isolated):
        left_changes = cast(Mapping[str, object], left["changes"])
        for right in isolated[index + 1 :]:
            right_changes = cast(Mapping[str, object], right["changes"])
            if not set(left_changes).isdisjoint(right_changes):
                continue
            changes = {**left_changes, **right_changes}
            comparisons += 1
            expected = exception_observation(baseline_default, changes)
            observed = exception_observation(DEFAULT_CONFIG, changes)
            if observed != expected and len(mismatches) < 20:
                mismatches.append(
                    {
                        "changes": changes,
                        "expected": expected,
                        "observed": observed,
                    }
                )

    metadata = cast(Mapping[str, object], fixture["baseline"])
    assert comparisons == metadata["pair_case_count"]
    assert mismatches == []


@pytest.mark.parametrize(
    ("left", "right", "changes"),
    (
        (
            "validate_market",
            "validate_execution",
            {"initial_cash": 0, "commission_rate": -0.1},
        ),
        (
            "validate_strategic_discovery",
            "validate_strategic_transition",
            {
                "strategic_cohort_size": 0,
                "strategic_long_cycle_min_ret20": -1.0,
            },
        ),
        (
            "validate_strategic_transition",
            "validate_strategic_lifecycle",
            {
                "strategic_long_cycle_min_ret20": -1.0,
                "strategic_dominant_max_weight": 1.01,
            },
        ),
        (
            "validate_strategic_lifecycle",
            "validate_risk",
            {
                "strategic_dominant_max_weight": 1.01,
                "risk_anchor_count": 0,
            },
        ),
    ),
)
def test_pairwise_guard_detects_demonstrated_validator_block_swaps(
    monkeypatch: pytest.MonkeyPatch,
    left: str,
    right: str,
    changes: dict[str, object],
) -> None:
    baseline_default = baseline_config_module().DEFAULT_CONFIG
    expected = exception_observation(baseline_default, changes)
    assert exception_observation(DEFAULT_CONFIG, changes) == expected
    left_validator = getattr(config_model, left)
    right_validator = getattr(config_model, right)

    monkeypatch.setattr(config_model, left, right_validator)
    monkeypatch.setattr(config_model, right, left_validator)

    assert exception_observation(DEFAULT_CONFIG, changes) != expected


def test_all_authored_public_methods_retain_legacy_attribution() -> None:
    baseline = baseline_method_contract()
    current = current_method_contract()

    assert tuple(baseline) == METHOD_IDS
    assert tuple(current) == METHOD_IDS
    assert {
        method_id: (record["module"], record["qualname"])
        for method_id, record in current.items()
    } == {
        method_id: (record["module"], record["qualname"])
        for method_id, record in baseline.items()
    }


def test_authored_public_method_pickles_load_in_both_directions() -> None:
    baseline = baseline_method_contract()
    current = current_method_contract()
    baseline_pickles = {
        method_id: cast(str, record["pickle_b64"])
        for method_id, record in baseline.items()
    }
    current_pickles = {
        method_id: cast(str, record["pickle_b64"])
        for method_id, record in current.items()
    }

    assert current_pickles == baseline_pickles
    assert all(
        cast(bool, result["ok"])
        for result in current_load_method_pickles(baseline_pickles).values()
    )
    assert all(
        cast(bool, result["ok"])
        for result in baseline_load_method_pickles(current_pickles).values()
    )
