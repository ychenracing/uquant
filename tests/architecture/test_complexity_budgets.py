from __future__ import annotations

from collections.abc import Mapping

import pytest

from ._analysis import FINAL_BUDGETS, measured_debt


def _by_id(rows: object) -> dict[str, Mapping[str, object]]:
    assert isinstance(rows, list)
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        assert isinstance(row, Mapping)
        identifier = str(row["id"])
        assert identifier not in result
        result[identifier] = row
    return result


def _assert_exact_monotonic_debt(
    *,
    category: str,
    metric: str | None,
    baseline_inventory: dict[str, object],
    current: dict[str, list[dict[str, object]]],
) -> None:
    debt = baseline_inventory["architecture_debt"]
    assert isinstance(debt, Mapping)
    initial = _by_id(debt["initial"][category])
    allowlist = set(debt["temporary_allowlist"][category])
    observed = _by_id(current[category])
    assert allowlist == set(initial), f"{category} Task 1 allowlist drifted from initial debt"
    assert set(observed) <= allowlist, f"{category} introduced non-baseline debt"
    if metric is not None:
        for identifier, row in observed.items():
            current_value = row[metric]
            initial_value = initial[identifier][metric]
            assert isinstance(current_value, int)
            assert isinstance(initial_value, int)
            assert current_value <= initial_value, (
                f"{identifier} worsened from {initial_value} to {current_value}"
            )


def test_final_budgets_are_strict_and_not_weakened(
    baseline_inventory: dict[str, object],
) -> None:
    debt = baseline_inventory["architecture_debt"]
    assert isinstance(debt, Mapping)
    assert debt["final_budgets"] == FINAL_BUDGETS
    final_allowlist = debt["final_acceptance_allowlist"]
    assert isinstance(final_allowlist, Mapping)
    assert set(final_allowlist) == set(debt["temporary_allowlist"])
    assert all(value == [] for value in final_allowlist.values())


def test_module_and_function_debt_is_exact_non_growing_and_monotonic(
    baseline_inventory: dict[str, object], current_architecture: dict[str, object]
) -> None:
    current = measured_debt(current_architecture)
    _assert_exact_monotonic_debt(
        category="oversized_modules",
        metric="measured_lines",
        baseline_inventory=baseline_inventory,
        current=current,
    )
    _assert_exact_monotonic_debt(
        category="long_functions",
        metric="measured_lines",
        baseline_inventory=baseline_inventory,
        current=current,
    )
    _assert_exact_monotonic_debt(
        category="branchy_functions",
        metric="measured_branch_points",
        baseline_inventory=baseline_inventory,
        current=current,
    )


def test_global_type_ignore_and_duplicate_helper_debt_is_exact_and_monotonic(
    baseline_inventory: dict[str, object], current_architecture: dict[str, object]
) -> None:
    current = measured_debt(current_architecture)
    for category in (
        "mutable_module_globals",
        "production_type_ignores",
        "duplicate_private_helper_groups",
    ):
        _assert_exact_monotonic_debt(
            category=category,
            metric=None,
            baseline_inventory=baseline_inventory,
            current=current,
        )


def test_live_debt_can_shrink_without_rewriting_the_task_1_baseline(
    baseline_inventory: dict[str, object], current_architecture: dict[str, object]
) -> None:
    current = measured_debt(current_architecture)
    current["oversized_modules"] = current["oversized_modules"][1:]
    _assert_exact_monotonic_debt(
        category="oversized_modules",
        metric="measured_lines",
        baseline_inventory=baseline_inventory,
        current=current,
    )


def test_live_debt_cannot_exceed_its_initial_per_identity_severity(
    baseline_inventory: dict[str, object], current_architecture: dict[str, object]
) -> None:
    current = measured_debt(current_architecture)
    debt = baseline_inventory["architecture_debt"]
    assert isinstance(debt, Mapping)
    initial = _by_id(debt["initial"]["oversized_modules"])
    identifier, initial_row = next(iter(initial.items()))
    first = dict(initial_row)
    measured_lines = initial[str(first["id"])]["measured_lines"]
    assert isinstance(measured_lines, int)
    first["measured_lines"] = measured_lines + 1
    assert str(first["id"]) == identifier
    current["oversized_modules"] = [first]
    with pytest.raises(AssertionError, match="worsened from"):
        _assert_exact_monotonic_debt(
            category="oversized_modules",
            metric="measured_lines",
            baseline_inventory=baseline_inventory,
            current=current,
        )
