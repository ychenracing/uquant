"""Atomic persistence for the single real-account state."""

from __future__ import annotations

from pathlib import Path

from ..infrastructure.atomic_files import atomic_write_json_with_mode
from ..types import ACCOUNT_SCHEMA_VERSION, AccountState
from .validation_attribution import validate_lot_origin_chains as _validate_lot_origin_chains
from .validation_orders import validate_order_state as _validate_order_state
from .validation_positions import validate_position_state as _validate_position_state
from .validation_strategy import validate_strategy_risk_state as _validate_strategy_risk_state


def save_account(state: AccountState, path: str | Path) -> None:
    """Atomically persist an account state after flushing it to stable storage."""
    if state.schema_version != ACCOUNT_SCHEMA_VERSION:
        raise RuntimeError(f"account schema {state.schema_version} requires explicit migration before save")
    _validate_position_state(state, validate_attribution=True)
    _validate_order_state(
        state,
        sequence_was_explicit=True,
        validate_attribution=True,
    )
    _validate_strategy_risk_state(state)
    _validate_lot_origin_chains(state)
    atomic_write_json_with_mode(
        path,
        lambda: state.to_dict(),
        mode=0o600,
    )
