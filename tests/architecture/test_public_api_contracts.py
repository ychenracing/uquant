from __future__ import annotations

from collections.abc import Mapping

from ._analysis import canonical_sha256, public_api_snapshot


def test_public_names_signatures_dataclasses_enums_and_runtime_contracts_are_frozen(
    public_api_baseline: dict[str, object],
) -> None:
    expected = public_api_baseline["contract"]
    assert isinstance(expected, Mapping)
    assert public_api_baseline["contract_sha256"] == canonical_sha256(expected)
    modules = expected["modules"]
    assert isinstance(modules, Mapping)
    assert public_api_snapshot(modules=modules) == expected


def test_public_api_contract_is_bound_to_the_task_1_baseline(
    public_api_baseline: dict[str, object], baseline_inventory: dict[str, object]
) -> None:
    baseline = baseline_inventory["baseline"]
    assert isinstance(baseline, Mapping)
    assert public_api_baseline["schema_version"] == 1
    assert public_api_baseline["baseline_commit"] == baseline["commit"]
