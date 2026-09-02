from __future__ import annotations

import inspect
from dataclasses import fields
from pathlib import Path

import pytest

import uquant.account as account_api
from research.post_generalization_trust_closure_checkpoint_c import (
    normalize_source_derived_identities,
)
from uquant.account import account_from_dict, save_account
from uquant.cli import _parser
from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.engine import ProductionEngine
from uquant.provenance.surfaces import load_source_surface_registry
from uquant.types import ACCOUNT_SCHEMA_VERSION, AccountState

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = "research/post_generalization_trust_closure_checkpoint_c.py"
REMOVED_CONFIG_FIELDS = {
    "hierarchical_industry_shrinkage_enabled",
    "group_balanced_reference_enabled",
    "same_day_leader_pipeline_enabled",
    "evidence_family_voting_enabled",
}


def _payload(*, suffix: str, shares: int = 100) -> dict[str, object]:
    return {
        "account_identity": f"account-{suffix}",
        "code_hash": suffix * 64,
        "order_ledger": [
            {
                "order_id": f"order-{suffix}",
                "event_id": f"event-{suffix}",
                "grant_id": f"grant-{suffix}",
                "epoch_id": f"epoch-{suffix}",
                "shares": shares,
            }
        ],
        "fills": [
            {
                "fill_id": f"fill-{suffix}",
                "order_id": f"order-{suffix}",
                "event_id": f"event-{suffix}",
                "grant_id": f"grant-{suffix}",
                "epoch_id": f"epoch-{suffix}",
                "shares": shares,
            }
        ],
        "strategic_grant": {
            "grant_id": f"grant-{suffix}",
            "epoch_id": f"epoch-{suffix}",
            "submitted_order_ids": [f"order-{suffix}"],
        },
    }


def test_cleanup_projection_normalizes_only_source_derived_identity() -> None:
    left = normalize_source_derived_identities(_payload(suffix="a"))
    right = normalize_source_derived_identities(_payload(suffix="b"))

    assert left == right
    assert left != normalize_source_derived_identities(_payload(suffix="b", shares=99))


def test_cleanup_runner_changes_invalidate_validation_and_package_identity() -> None:
    registry = load_source_surface_registry(ROOT)

    assert RUNNER_PATH in registry.surface("validation_runner_v1").source_paths
    assert RUNNER_PATH in registry.surface("full_package_v1").source_paths


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
        _parser().parse_args(["account-migrate", "--account", "account.json"])
