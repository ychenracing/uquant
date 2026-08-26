from __future__ import annotations

import tomllib
from collections.abc import Mapping

from ._analysis import ROOT, canonical_sha256, cli_help_snapshot, public_api_snapshot


def test_public_names_signatures_dataclasses_enums_and_runtime_contracts_match_current_contract(
    public_api_contract: dict[str, object],
) -> None:
    expected = public_api_contract["contract"]
    assert isinstance(expected, Mapping)
    assert public_api_contract["contract_sha256"] == canonical_sha256(expected)
    modules = expected["modules"]
    assert isinstance(modules, Mapping)
    observed = public_api_snapshot(modules=modules)
    observed_trace = observed["decision_fill_account_trace"]
    expected_trace = expected["decision_fill_account_trace"]
    assert isinstance(observed_trace, dict)
    assert isinstance(expected_trace, Mapping)
    observed_account = observed_trace["account_after"]
    expected_account = expected_trace["account_after"]
    assert isinstance(observed_account, dict)
    assert isinstance(expected_account, Mapping)
    from uquant.engine import code_fingerprint

    assert observed_account["code_hash"] == code_fingerprint()
    observed_account["code_hash"] = expected_account["code_hash"]
    observed_trace["account_after_sha256"] = canonical_sha256(observed_account)
    assert observed == expected


def test_public_api_contract_uses_current_governance_identity(
    public_api_contract: dict[str, object], baseline_inventory: dict[str, object]
) -> None:
    from ._baseline import BASELINE_COMMIT

    baseline = baseline_inventory["baseline"]
    assert isinstance(baseline, Mapping)
    assert set(public_api_contract) == {
        "contract",
        "contract_id",
        "contract_sha256",
        "recorded_on",
        "schema_version",
    }
    assert public_api_contract["contract_id"] == "uquant-public-api-v1"
    assert public_api_contract["recorded_on"] == "2026-08-26"
    assert public_api_contract["schema_version"] == 1
    assert baseline["commit"] == BASELINE_COMMIT


def test_cli_help_covers_every_declared_script_and_nested_parser() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared_scripts = set(project["scripts"])
    help_surfaces = cli_help_snapshot()
    assert declared_scripts <= set(help_surfaces)
    assert "usage: uquant-sentinel" in help_surfaces["uquant-sentinel"]
    assert "uquant execution-journal planned" in help_surfaces
