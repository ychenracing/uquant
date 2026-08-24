"""Immutable future-holdout boundary and exact post-checkout evidence."""

# ruff: noqa: F401, RUF022 - frozen compatibility exports and seams

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..ai_era import AI_ERA_WINDOWS
from .capabilities import (
    HoldoutFacadeCapabilities,
    holdout_facade_scope,
    scoped_capability_wrapper,
)
from .contract import (
    ACCOUNT_EXECUTION_FIELDS as _ACCOUNT_EXECUTION_FIELDS,
)
from .contract import (
    CHECKPOINT_RELATIVE as _CHECKPOINT_RELATIVE,
)
from .contract import (
    CLI_OPERATIONAL_COMMANDS as _CLI_OPERATIONAL_COMMANDS,
)
from .contract import (
    COMMIT_PATTERN as _COMMIT,
)
from .contract import (
    CONTRACT_FIELDS as _CONTRACT_FIELDS,
)
from .contract import (
    HOLDOUT_DATA_DIRECTORY,
    HOLDOUT_START,
    LAST_IN_SAMPLE_DATE,
    PRIOR_CLOSE_ACCOUNT_SHA256,
    REQUIRED_FUTURE_HOLDOUT_SHA256,
    REVIEW_CALENDAR_SOURCE,
    REVIEW_MILESTONES,
    REVIEW_SESSIONS,
    REVIEWED_PHASE1_WINDOWS,
    SCORE_FIELDS,
    STRATEGY_ACCOUNT_CODE_SHA256,
    STRATEGY_ANCHOR_COMMIT,
    STRATEGY_CLI_SHA256,
    STRATEGY_CONFIG_SHA256,
    STRATEGY_SOURCE_SHA256,
    FutureHoldoutContract,
    holdout_data_identity,
    load_future_holdout_contract,
    maximum_observed_market_date,
    validate_holdout_layout,
)
from .contract import (
    MANIFEST_FIELDS as _MANIFEST_FIELDS,
)
from .contract import (
    SHA256_PATTERN as _SHA256,
)
from .contract import (
    STRATEGY_FIXED_RELATIVES as _STRATEGY_FIXED_RELATIVES,
)
from .contract import (
    STRATEGY_OPERATIONAL_RELATIVES as _STRATEGY_OPERATIONAL_RELATIVES,
)
from .contract import (
    canonical_bytes as _canonical_bytes,
)
from .contract import (
    canonical_sha256 as _canonical_sha256,
)
from .contract import (
    closed_csv_files as _closed_csv_files,
)
from .contract import (
    csv_dates as _csv_dates,
)
from .contract import (
    csv_dates_from_text as _csv_dates_from_text,
)
from .contract import (
    git_executable as _git_executable,
)
from .contract import (
    read_json as _read_json,
)
from .contract import (
    read_json_snapshot as _read_json_snapshot,
)
from .contract import (
    reject_duplicate_keys as _reject_duplicate_keys,
)
from .contract import (
    reject_nonstandard_constant as _reject_nonstandard_constant,
)
from .contract import (
    repository_root as _repository_root,
)
from .contract import (
    session_dates as _session_dates,
)
from .manifest import (
    assemble_future_holdout_manifest as _assemble_future_holdout_manifest,
)
from .manifest import (
    binding_payload as _binding_payload,
)
from .manifest import (
    normalized_scores as _normalized_scores,
)
from .manifest import (
    validate_future_holdout_manifest_payload as _validate_future_holdout_manifest_payload,
)
from .manifest import (
    validated_score_values as _validated_score_values,
)
from .service import (
    append_holdout_snapshot,
    build_future_holdout_manifest,
    generate_future_holdout_manifest,
    generate_future_holdout_replay,
    validate_future_holdout_manifest,
)
from .service import (
    generate_future_holdout_replay_locked as _generate_future_holdout_replay_locked,
)
from .service import (
    manifest_repository_root as _manifest_repository_root,
)
from .service import (
    observation_metrics as _observation_metrics,
)
from .source_identity import (
    HoldoutBinding,
    current_holdout_binding,
    holdout_source_sha256,
    validate_prior_close_account,
)
from .source_identity import (
    adds_operational_parser as _adds_operational_parser,
)
from .source_identity import (
    assigned_names as _assigned_names,
)
from .source_identity import (
    cli_strategy_ast as _cli_strategy_ast,
)
from .source_identity import (
    command_guard as _command_guard,
)
from .source_identity import (
    git_strategy_relatives as _git_strategy_relatives,
)
from .source_identity import (
    industry_sha256 as _industry_sha256,
)
from .source_identity import (
    is_strategy_relative as _is_strategy_relative,
)
from .source_identity import (
    loaded_names as _loaded_names,
)
from .source_identity import (
    parser_strategy_body as _parser_strategy_body,
)
from .source_identity import (
    safe_operational_parser_statement as _safe_operational_parser_statement,
)
from .source_identity import (
    safe_parser_value as _safe_parser_value,
)
from .source_identity import (
    source_paths as _source_paths,
)
from .source_identity import (
    source_sha256 as _source_sha256,
)
from .source_identity import (
    state_hashes as _state_hashes,
)
from .source_identity import (
    strategy_account_code_sha256 as _strategy_account_code_sha256,
)
from .source_identity import (
    strategy_cli_sha256 as _strategy_cli_sha256,
)
from .source_identity import (
    strategy_source_paths as _strategy_source_paths,
)
from .source_identity import (
    strategy_source_sha256 as _strategy_source_sha256,
)
from .source_identity import (
    validated_strategy_cli_sha256 as _validated_strategy_cli_sha256,
)
from .source_identity import (
    validated_strategy_source_sha256 as _validated_strategy_source_sha256,
)

_owner_build_future_holdout_manifest = build_future_holdout_manifest
_owner_current_holdout_binding = current_holdout_binding
_owner_generate_future_holdout_manifest = generate_future_holdout_manifest
_owner_holdout_data_identity = holdout_data_identity
_owner_holdout_source_sha256 = holdout_source_sha256
_owner_load_future_holdout_contract = load_future_holdout_contract
_owner_maximum_observed_market_date = maximum_observed_market_date
_owner_strategy_account_code_sha256 = _strategy_account_code_sha256
_owner_validate_future_holdout_manifest = validate_future_holdout_manifest
_owner_validate_holdout_layout = validate_holdout_layout
_owner_validate_prior_close_account = validate_prior_close_account
_owner_validated_strategy_cli_sha256 = _validated_strategy_cli_sha256
_owner_validated_strategy_source_sha256 = _validated_strategy_source_sha256


def current_facade_capabilities() -> HoldoutFacadeCapabilities:
    return HoldoutFacadeCapabilities(
        ai_era_windows=AI_ERA_WINDOWS,
        required_future_holdout_sha256=REQUIRED_FUTURE_HOLDOUT_SHA256,
        strategy_account_code_sha256=STRATEGY_ACCOUNT_CODE_SHA256,
        prior_close_account_sha256=PRIOR_CLOSE_ACCOUNT_SHA256,
        strategy_source_paths=_strategy_source_paths,
        source_sha256=_source_sha256,
        git_strategy_relatives=_git_strategy_relatives,
        strategy_cli_sha256=_strategy_cli_sha256,
        repository_root=_repository_root,
        validate_prior_close_account=validate_prior_close_account,
        current_holdout_binding=current_holdout_binding,
    )


build_future_holdout_manifest = scoped_capability_wrapper(
    _owner_build_future_holdout_manifest,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
current_holdout_binding = scoped_capability_wrapper(
    _owner_current_holdout_binding,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
generate_future_holdout_manifest = scoped_capability_wrapper(
    _owner_generate_future_holdout_manifest,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
holdout_data_identity = scoped_capability_wrapper(
    _owner_holdout_data_identity,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
holdout_source_sha256 = scoped_capability_wrapper(
    _owner_holdout_source_sha256,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
load_future_holdout_contract = scoped_capability_wrapper(
    _owner_load_future_holdout_contract,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
maximum_observed_market_date = scoped_capability_wrapper(
    _owner_maximum_observed_market_date,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
validate_future_holdout_manifest = scoped_capability_wrapper(
    _owner_validate_future_holdout_manifest,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
validate_holdout_layout = scoped_capability_wrapper(
    _owner_validate_holdout_layout,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
validate_prior_close_account = scoped_capability_wrapper(
    _owner_validate_prior_close_account,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
_strategy_account_code_sha256 = scoped_capability_wrapper(
    _owner_strategy_account_code_sha256,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
_validated_strategy_cli_sha256 = scoped_capability_wrapper(
    _owner_validated_strategy_cli_sha256,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)
_validated_strategy_source_sha256 = scoped_capability_wrapper(
    _owner_validated_strategy_source_sha256,
    capabilities=current_facade_capabilities,
    scope=holdout_facade_scope,
)

# Preserve callers that derive the repository root from the historical facade path.
__file__ = str(Path(__file__).resolve().parent.parent / "holdout.py")

__all__ = (
    "FutureHoldoutContract",
    "HOLDOUT_DATA_DIRECTORY",
    "HOLDOUT_START",
    "HoldoutBinding",
    "LAST_IN_SAMPLE_DATE",
    "PRIOR_CLOSE_ACCOUNT_SHA256",
    "REQUIRED_FUTURE_HOLDOUT_SHA256",
    "REVIEWED_PHASE1_WINDOWS",
    "REVIEW_CALENDAR_SOURCE",
    "REVIEW_MILESTONES",
    "REVIEW_SESSIONS",
    "SCORE_FIELDS",
    "STRATEGY_ACCOUNT_CODE_SHA256",
    "STRATEGY_ANCHOR_COMMIT",
    "STRATEGY_CLI_SHA256",
    "STRATEGY_CONFIG_SHA256",
    "STRATEGY_SOURCE_SHA256",
    "_ACCOUNT_EXECUTION_FIELDS",
    "_CLI_OPERATIONAL_COMMANDS",
    "_COMMIT",
    "_CONTRACT_FIELDS",
    "_MANIFEST_FIELDS",
    "_SHA256",
    "_STRATEGY_FIXED_RELATIVES",
    "_STRATEGY_OPERATIONAL_RELATIVES",
    "build_future_holdout_manifest",
    "current_holdout_binding",
    "generate_future_holdout_manifest",
    "holdout_data_identity",
    "holdout_source_sha256",
    "load_future_holdout_contract",
    "maximum_observed_market_date",
    "validate_future_holdout_manifest",
    "validate_holdout_layout",
    "validate_prior_close_account",
)

for _name, _value in (
    ("FutureHoldoutContract", FutureHoldoutContract),
    ("HoldoutBinding", HoldoutBinding),
    ("build_future_holdout_manifest", build_future_holdout_manifest),
    ("current_holdout_binding", current_holdout_binding),
    ("generate_future_holdout_manifest", generate_future_holdout_manifest),
    ("holdout_data_identity", holdout_data_identity),
    ("holdout_source_sha256", holdout_source_sha256),
    ("load_future_holdout_contract", load_future_holdout_contract),
    ("maximum_observed_market_date", maximum_observed_market_date),
    ("validate_future_holdout_manifest", validate_future_holdout_manifest),
    ("validate_holdout_layout", validate_holdout_layout),
    ("validate_prior_close_account", validate_prior_close_account),
):
    _value.__module__ = __name__
    _value.__qualname__ = _name
