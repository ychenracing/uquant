from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

import pytest

import uquant.account as account_api
from uquant.account import account_from_dict, save_account
from uquant.cli import _uquant_cli_parser
from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.engine import ProductionEngine
from uquant.types import ACCOUNT_SCHEMA_VERSION, AccountState

ROOT = Path(__file__).resolve().parents[1]
REMOVED_CONFIG_FIELDS = {
    "hierarchical_industry_shrinkage_enabled",
    "group_balanced_reference_enabled",
    "same_day_leader_pipeline_enabled",
    "evidence_family_voting_enabled",
}








def test_current_config_excludes_removed_switches() -> None:
    field_names = {field.name for field in fields(SystemConfig)}

    assert REMOVED_CONFIG_FIELDS.isdisjoint(field_names)
    assert REMOVED_CONFIG_FIELDS.isdisjoint(DEFAULT_CONFIG.to_dict())


def test_account_decoder_accepts_only_the_current_schema() -> None:
    error_type = getattr(account_api, "UnsupportedAccountSchemaError", None)

    assert error_type is not None
    with pytest.raises(
        error_type,
        match=rf"unsupported account schema {ACCOUNT_SCHEMA_VERSION - 1}; expected {ACCOUNT_SCHEMA_VERSION}",
    ):
        account_from_dict(
            {"schema_version": ACCOUNT_SCHEMA_VERSION - 1},
            require_hashes=False,
        )


def test_production_account_inputs_reject_non_current_schema(tmp_path: Path) -> None:
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.schema_version = ACCOUNT_SCHEMA_VERSION - 1
    error_type = account_api.UnsupportedAccountSchemaError
    message = rf"unsupported account schema {ACCOUNT_SCHEMA_VERSION - 1}; expected {ACCOUNT_SCHEMA_VERSION}"

    with pytest.raises(error_type, match=message):
        save_account(account, tmp_path / "account.json")
    with pytest.raises(error_type, match=message):
        ProductionEngine(ROOT / "data" / "frozen").decide(
            symbols=("sz300308",),
            as_of="2023-01-03",
            account=account,
        )


def test_account_api_exposes_only_code_identity_rebinding() -> None:
    assert "allow_legacy_schema" not in inspect.signature(account_from_dict).parameters
    assert "allow_legacy_schema" not in inspect.signature(account_api.load_account).parameters
    assert not hasattr(account_api, "migrate_account")
    assert hasattr(account_api, "migrate_code_identity")
    with pytest.raises(SystemExit):
        _uquant_cli_parser().parse_args(["account-migrate", "--account", "account.json"])


def test_portfolio_introspection_exposes_the_real_optional_inputs() -> None:
    import inspect

    from uquant.portfolio import PortfolioAllocator
    from uquant.portfolio_strategic import StrategicPortfolioPolicy

    for owner, name in (
        (PortfolioAllocator, "allocate"),
        (PortfolioAllocator, "_allocate_strategy"),
        (StrategicPortfolioPolicy, "_initialize_strategic_cohort"),
        (StrategicPortfolioPolicy, "_strategic_cohort_targets"),
    ):
        method = getattr(owner, name)
        signature = inspect.signature(method)
        assert signature == inspect.signature(method, follow_wrapped=False)
        assert {"qualification_panel", "qualification_leaders", "strategic_universe"} <= set(signature.parameters)
