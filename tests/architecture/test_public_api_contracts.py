from __future__ import annotations

import tomllib
from collections.abc import Mapping

from ._analysis import ROOT, canonical_sha256, cli_help_snapshot, public_api_snapshot


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
    from ._baseline import BASELINE_COMMIT

    baseline = baseline_inventory["baseline"]
    assert isinstance(baseline, Mapping)
    assert public_api_baseline["schema_version"] == 1
    assert public_api_baseline["baseline_commit"] == BASELINE_COMMIT
    assert baseline["commit"] == BASELINE_COMMIT


def test_cli_help_covers_every_declared_script_and_nested_parser() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared_scripts = set(project["scripts"])
    help_surfaces = cli_help_snapshot()
    assert declared_scripts <= set(help_surfaces)
    assert "usage: uquant-sentinel" in help_surfaces["uquant-sentinel"]
    assert "uquant execution-journal planned" in help_surfaces
