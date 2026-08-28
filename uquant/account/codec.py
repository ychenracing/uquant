"""Strict account payload decoding and loading."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..models.strategic_epoch import strategic_epoch_from_payload
from ..models.strategic_grant import (
    strategic_grant_from_payload,
    strategic_qualification_from_payload,
)
from ..models.strategic_rearm import (
    flat_book_capital_repair_from_payload,
    strategic_cash_rearm_from_payload,
)
from ..types import (
    ACCOUNT_SCHEMA_VERSION,
    AccountOrder,
    AccountState,
    Fill,
    PendingOrder,
)
from .validation_attribution import validate_lot_origin_chains as _validate_lot_origin_chains
from .validation_common import (
    HISTORICAL_ATTRIBUTION_SCHEMA_VERSION as _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION,
)
from .validation_common import (
    finite_number as _finite_number,
)
from .validation_common import (
    reject_nonstandard_account_json_constant as _reject_nonstandard_json_constant,
)
from .validation_orders import validate_order_state as _validate_order_state
from .validation_positions import (
    position_from_payload as _position,
)
from .validation_positions import (
    validate_position_state as _validate_position_state,
)
from .validation_strategy import validate_strategy_risk_state as _validate_strategy_risk_state


def load_account(
    path: str | Path,
    *,
    require_hashes: bool = True,
    allow_legacy_schema: bool = False,
) -> AccountState:
    """Load and validate the durable account state from a JSON file.

    Validation rejects malformed order lifecycles, duplicate identifiers,
    negative balances, and missing provenance hashes when fail-closed operation
    is expected.
    """
    payload = _read_account_payload(path)
    return account_from_dict(
        payload,
        require_hashes=require_hashes,
        allow_legacy_schema=allow_legacy_schema,
    )


def _read_account_payload(path: str | Path) -> dict[str, Any]:
    """Read one account JSON object with the strict parser used by migration."""

    source = Path(path)
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"account state is missing or corrupt: {source}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("account state must be a JSON object")
    return dict(payload)


read_account_payload = _read_account_payload


def _validate_decoded_account(
    *,
    allow_legacy_schema: Any,
    require_hashes: Any,
    schema_version: Any,
    sequence_was_explicit: Any,
    state: Any,
) -> None:
    initial_cash = _finite_number(
        state.initial_cash,
        field="account state initial_cash",
        minimum=0.0,
    )
    cash = _finite_number(state.cash, field="account state cash", minimum=-1e-6)
    if initial_cash == 0.0 or cash < -1e-6:
        raise RuntimeError("account state violates cash invariants")
    validate_attribution = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    _validate_position_state(
        state,
        validate_attribution=validate_attribution,
    )
    _validate_order_state(
        state,
        sequence_was_explicit=sequence_was_explicit,
        allow_schema_v2_missing_sell_attribution=(allow_legacy_schema and schema_version == 2),
        validate_attribution=validate_attribution,
        event_schema_version=schema_version,
    )
    _validate_strategy_risk_state(state)
    if validate_attribution:
        _validate_lot_origin_chains(state, schema_version=schema_version)
    if require_hashes and (not state.data_hash or not state.code_hash):
        raise RuntimeError("account state missing validation hashes")


def _resolve_account_schema_context(
    *,
    allow_legacy_schema: Any,
    payload: Any,
    schema_version: Any,
) -> tuple[Any, Any, Any, Any]:
    if schema_version > ACCOUNT_SCHEMA_VERSION or schema_version < 1:
        raise RuntimeError(f"unsupported account schema {schema_version}; expected {ACCOUNT_SCHEMA_VERSION}")
    if schema_version != ACCOUNT_SCHEMA_VERSION and not allow_legacy_schema:
        raise RuntimeError(
            f"account schema {schema_version} requires explicit migration; "
            "run `uquant account-migrate --help`"
        )
    native_schema = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    sequence_was_explicit = "next_order_sequence" in payload
    if schema_version == ACCOUNT_SCHEMA_VERSION and not sequence_was_explicit:
        raise RuntimeError("current account schema requires next_order_sequence")
    operating_peak = payload.get("operating_peak")
    capital_peak = payload.get("capital_peak")
    if operating_peak is None:
        operating_peak = payload["initial_cash"]
    if capital_peak is None:
        capital_peak = payload["initial_cash"]
    return capital_peak, native_schema, operating_peak, sequence_was_explicit


def _decode_account_core_fields(
    *,
    capital_peak: Any,
    native_schema: bool,
    operating_peak: Any,
    payload: Mapping[str, Any],
    schema_version: int,
) -> dict[str, Any]:
    return {
        "initial_cash": payload["initial_cash"] if native_schema else float(payload["initial_cash"]),
        "cash": payload["cash"] if native_schema else float(payload["cash"]),
        "schema_version": schema_version,
        "positions": {
            symbol: _position(item, schema_version=schema_version)
            for symbol, item in payload.get("positions", {}).items()
        },
        "pending_orders": [PendingOrder(**item) for item in payload.get("pending_orders", [])],
        "order_ledger": [AccountOrder(**item) for item in payload.get("order_ledger", [])],
        "next_order_sequence": payload.get("next_order_sequence", 1)
        if native_schema
        else int(payload.get("next_order_sequence", 1)),
        "fills": [Fill(**item) for item in payload.get("fills", [])],
        "broker_as_of": payload.get("broker_as_of", ""),
        "opportunity": payload.get("opportunity", "CHOPPY")
        if native_schema
        else str(payload.get("opportunity", "CHOPPY")),
        "risk": payload.get("risk", "NORMAL") if native_schema else str(payload.get("risk", "NORMAL")),
        "shock_state": payload.get("shock_state", "NONE")
        if native_schema
        else str(payload.get("shock_state", "NONE")),
        "sector_shock_dates": payload.get("sector_shock_dates", []),
        "sector_guard_active": payload.get("sector_guard_active", False),
        "sector_guard_started": payload.get("sector_guard_started", ""),
        "sector_guard_symbols": payload.get("sector_guard_symbols", [])
        if native_schema
        else [str(item) for item in payload.get("sector_guard_symbols", [])],
        "sector_recovery_streak": payload.get("sector_recovery_streak", 0),
        "cooldown_until": payload.get("cooldown_until", ""),
        "operating_peak": operating_peak,
        "capital_peak": capital_peak,
    }


def _decode_account_lifecycle_fields(
    *,
    native_schema: bool,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "leader_tenure": {str(k): v for k, v in payload.get("leader_tenure", {}).items()},
        "candidate_tenure": {str(k): v for k, v in payload.get("candidate_tenure", {}).items()},
        "replacement_tenure": {str(k): v for k, v in payload.get("replacement_tenure", {}).items()},
        "active_leaders": payload.get("active_leaders", [])
        if native_schema
        else [str(item) for item in payload.get("active_leaders", [])],
        "dynamic_k": payload.get("dynamic_k", 0),
        "last_k_change_date": payload.get("last_k_change_date", ""),
        "satellite_entry_dates": {str(k): v for k, v in payload.get("satellite_entry_dates", {}).items()},
        "risk_streaks": {str(k): v for k, v in payload.get("risk_streaks", {}).items()},
        "rotation_dates": payload.get("rotation_dates", []),
        "replacement_events": payload.get("replacement_events", [])
        if native_schema
        else list(payload.get("replacement_events", [])),
        "lifecycle_events": payload.get("lifecycle_events", [])
        if native_schema
        else list(payload.get("lifecycle_events", [])),
        "risk_events": payload.get("risk_events", [])
        if native_schema
        else list(payload.get("risk_events", [])),
        "account_migrations": list(payload.get("account_migrations", [])),
        "anchor_weights": {str(k): v for k, v in payload.get("anchor_weights", {}).items()},
        "recovery_anchor_date": payload.get("recovery_anchor_date", ""),
        "recovery_conviction_symbol": payload.get("recovery_conviction_symbol", "")
        if native_schema
        else str(payload.get("recovery_conviction_symbol", "")),
        "tactical_anchor_symbol": payload.get("tactical_anchor_symbol", "")
        if native_schema
        else str(payload.get("tactical_anchor_symbol", "")),
        "protected_weights": {str(k): v for k, v in payload.get("protected_weights", {}).items()},
        "strategic_cohort_symbols": payload.get("strategic_cohort_symbols", []),
        "strategic_cohort_targets": {
            str(k): v for k, v in payload.get("strategic_cohort_targets", {}).items()
        },
        "strategic_exit_bands": {
            str(k): list(values) for k, values in payload.get("strategic_exit_bands", {}).items()
        },
        "strategic_active_bands": {
            str(k): list(values) for k, values in payload.get("strategic_active_bands", {}).items()
        },
        "strategic_restore_weights": {
            str(k): v for k, v in payload.get("strategic_restore_weights", {}).items()
        },
        "strategic_epoch": payload.get("strategic_epoch", 0),
    }


def _decode_account_strategy_fields(
    *,
    capital_peak: Any,
    native_schema: bool,
    operating_peak: Any,
    payload: Mapping[str, Any],
    schema_version: int,
) -> dict[str, Any]:
    if schema_version == ACCOUNT_SCHEMA_VERSION:
        if "flat_book_capital_repair" not in payload:
            raise RuntimeError(
                "current account schema requires flat_book_capital_repair"
            )
        if "strategic_cash_rearm" not in payload:
            raise RuntimeError("current account schema requires strategic_cash_rearm")
    legacy_candidate_rearm = schema_version < ACCOUNT_SCHEMA_VERSION
    return {
        "strategic_epochs_completed": payload.get(
            "strategic_epochs_completed",
            payload.get("candidate_tenure", {}).get("strategic_cohort_completed", 0),
        ),
        "strategic_last_exit_date": payload.get("strategic_last_exit_date", ""),
        "strategic_rearm_date": payload.get("strategic_rearm_date", ""),
        "strategic_candidate_signature": payload.get("strategic_candidate_signature", "")
        if native_schema
        else str(payload.get("strategic_candidate_signature", "")),
        "strategic_previous_symbols": payload.get("strategic_previous_symbols", []),
        "account_identity": payload.get("account_identity", ""),
        "strategic_qualification": strategic_qualification_from_payload(
            payload.get("strategic_qualification")
        ),
        "strategic_successor_qualification": strategic_qualification_from_payload(
            payload.get("strategic_successor_qualification")
        ),
        "strategic_grant": strategic_grant_from_payload(payload.get("strategic_grant")),
        "flat_book_capital_repair": flat_book_capital_repair_from_payload(
            None if legacy_candidate_rearm else payload.get("flat_book_capital_repair")
        ),
        "strategic_cash_rearm": strategic_cash_rearm_from_payload(
            None if legacy_candidate_rearm else payload.get("strategic_cash_rearm")
        ),
        "strategic_epochs": [
            strategic_epoch_from_payload(dict(item))
            for item in payload.get("strategic_epochs", [])
        ],
        "active_strategic_epoch_id": payload.get("active_strategic_epoch_id", ""),
        "protected_weight_epoch_ids": {
            str(k): str(v) for k, v in payload.get("protected_weight_epoch_ids", {}).items()
        },
        "strategic_restore_epoch_ids": {
            str(k): str(v) for k, v in payload.get("strategic_restore_epoch_ids", {}).items()
        },
        "recovery_owner_epoch_id": payload.get("recovery_owner_epoch_id", ""),
        "strategic_tradable_universe_identity": payload.get(
            "strategic_tradable_universe_identity", ""
        ),
        "strategic_qualification_universe_identity": payload.get(
            "strategic_qualification_universe_identity", ""
        ),
        "strategic_risk_universe_identity": payload.get(
            "strategic_risk_universe_identity", ""
        ),
        "risk_anchor_symbols": payload.get("risk_anchor_symbols", []),
        "risk_anchor_signature": payload.get("risk_anchor_signature", "")
        if native_schema
        else str(payload.get("risk_anchor_signature", "")),
        "risk_anchor_candidate_signature": payload.get("risk_anchor_candidate_signature", "")
        if native_schema
        else str(payload.get("risk_anchor_candidate_signature", "")),
        "risk_anchor_candidate_streak": payload.get("risk_anchor_candidate_streak", 0),
        "risk_signal_state": {str(k): v for k, v in payload.get("risk_signal_state", {}).items()},
        "capital_budget_level": payload.get("capital_budget_level", 0),
        "capital_budget_repair_streak": payload.get("capital_budget_repair_streak", 0),
        "chronic_level": payload.get("chronic_level", 0),
        "chronic_streak": payload.get("chronic_streak", 0),
        "chronic_repair_streak": payload.get("chronic_repair_streak", 0),
        "scout_signature": payload.get("scout_signature", "")
        if native_schema
        else str(payload.get("scout_signature", "")),
        "scout_entry_date": payload.get("scout_entry_date", ""),
        "reconciliation_events": payload.get("reconciliation_events", [])
        if native_schema
        else list(payload.get("reconciliation_events", [])),
        "shock_start_date": payload.get("shock_start_date", ""),
        "shock_severity": payload.get("shock_severity", "NORMAL")
        if native_schema
        else str(payload.get("shock_severity", "NORMAL")),
        "last_shock_date": payload.get("last_shock_date", ""),
        "last_successful_run": payload.get("last_successful_run", ""),
        "data_hash": payload.get("data_hash", "") if native_schema else str(payload.get("data_hash", "")),
        "data_hash_as_of": payload.get("data_hash_as_of", ""),
        "data_hash_symbols": payload.get("data_hash_symbols", [])
        if native_schema
        else [str(item) for item in payload.get("data_hash_symbols", [])],
        "code_hash": payload.get("code_hash", "") if native_schema else str(payload.get("code_hash", "")),
    }


def account_from_dict(
    value: Mapping[str, Any],
    *,
    require_hashes: bool = True,
    allow_legacy_schema: bool = False,
) -> AccountState:
    """Decode and fully validate an in-memory durable account payload."""

    payload = dict(value)
    raw_schema_version = payload.get("schema_version", 1)
    if isinstance(raw_schema_version, bool):
        raise RuntimeError("account state has an invalid schema version")
    if isinstance(raw_schema_version, int):
        schema_version = raw_schema_version
    else:
        try:
            schema_version = int(raw_schema_version)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("account state has an invalid schema version") from exc
        if not allow_legacy_schema or schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION:
            raise RuntimeError("native account schema_version must be an integer")
    capital_peak, native_schema, operating_peak, sequence_was_explicit = _resolve_account_schema_context(
        allow_legacy_schema=allow_legacy_schema,
        payload=payload,
        schema_version=schema_version,
    )
    try:
        state = AccountState(
            **_decode_account_core_fields(
                capital_peak=capital_peak,
                native_schema=native_schema,
                operating_peak=operating_peak,
                payload=payload,
                schema_version=schema_version,
            ),
            **_decode_account_lifecycle_fields(
                native_schema=native_schema,
                payload=payload,
            ),
            **_decode_account_strategy_fields(
                capital_peak=capital_peak,
                native_schema=native_schema,
                operating_peak=operating_peak,
                payload=payload,
                schema_version=schema_version,
            ),
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("account state violates schema") from exc
    _validate_decoded_account(
        allow_legacy_schema=allow_legacy_schema,
        require_hashes=require_hashes,
        schema_version=schema_version,
        sequence_was_explicit=sequence_was_explicit,
        state=state,
    )
    return state
