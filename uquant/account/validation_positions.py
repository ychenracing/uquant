"""Position and tranche decoding and validation for durable accounts."""

from __future__ import annotations

from typing import Any

from ..types import AccountState, Lifecycle, Opportunity, Position, Tranche
from .validation_attribution import (
    validate_attribution_identity as _validate_attribution_identity,
)
from .validation_common import (
    finite_number as _finite_number,
)
from .validation_common import (
    nonnegative_integer as _nonnegative_integer,
)
from .validation_common import (
    optional_iso_date as _optional_iso_date,
)
from .validation_common import (
    required_iso_date as _required_iso_date,
)
from .validation_common import (
    required_text as _required_text,
)


def _tranche(payload: dict[str, Any]) -> Tranche:
    """Load one tranche from the current account schema."""
    avg_cost = payload.get("avg_cost", 0.0)
    highest = payload.get("highest_close", avg_cost)
    lowest = payload.get("lowest_close", avg_cost)
    return Tranche(
        tranche_id=payload["tranche_id"],
        lifecycle=payload.get("lifecycle", "CORE"),
        shares=payload.get("shares", 0),
        avg_cost=avg_cost,
        entry_date=payload.get("entry_date", ""),
        sellable_date=payload.get("sellable_date", ""),
        highest_close=highest,
        lowest_close=lowest,
        mfe=payload.get(
            "mfe",
            max(
                0.0,
                float(highest) / max(float(avg_cost), 1e-12) - 1.0,
            ),
        ),
        mae=payload.get(
            "mae",
            min(
                0.0,
                float(lowest) / max(float(avg_cost), 1e-12) - 1.0,
            ),
        ),
        entry_score=payload.get("entry_score", 0.0),
        entry_confidence=payload.get("entry_confidence", 0.0),
        entry_regime=payload.get("entry_regime", "CHOPPY"),
        entry_industry_strength=payload.get("entry_industry_strength", 0.0),
        event_id=payload.get("event_id", ""),
        origin_subsystem=payload.get("origin_subsystem", ""),
        mechanism=payload.get("mechanism", ""),
        origin_lifecycle=payload.get("origin_lifecycle", ""),
        replaces_symbol=payload.get("replaces_symbol"),
        industry_at_entry=payload.get("industry_at_entry", ""),
        industry_manifest_sha256=payload.get("industry_manifest_sha256", ""),
        grant_id=payload.get("grant_id", ""),
        epoch_id=payload.get("epoch_id", ""),
    )


def _position(payload: dict[str, Any]) -> Position:
    """Decode a position and reconcile aggregate shares with its tranche lots."""

    return Position(
        symbol=payload["symbol"],
        shares=payload.get("shares", 0),
        avg_cost=payload.get("avg_cost", 0.0),
        entry_date=payload.get("entry_date", ""),
        highest_close=payload.get("highest_close", 0.0),
        lifecycle=payload.get("lifecycle", "CORE"),
        tranches=[_tranche(item) for item in payload.get("tranches", [])],
        grant_id=payload.get("grant_id", ""),
        epoch_id=payload.get("epoch_id", ""),
    )


def _validate_position_tranche(
    tranche: Tranche,
    *,
    lifecycles: set[str],
    validate_attribution: bool,
) -> None:
    _nonnegative_integer(tranche.shares, field="account tranche shares", positive=True)
    tranche_cost = _finite_number(
        tranche.avg_cost,
        field="account tranche cost",
        minimum=0.0,
    )
    if tranche_cost == 0.0:
        raise RuntimeError("account tranche cost must be positive")
    tranche_high = _finite_number(
        tranche.highest_close,
        field="account tranche highest close",
        minimum=0.0,
    )
    tranche_low = _finite_number(
        tranche.lowest_close,
        field="account tranche lowest close",
        minimum=0.0,
    )
    if tranche_high == 0.0 or tranche_low == 0.0:
        raise RuntimeError("account tranche prices must be positive")
    if tranche.lifecycle not in lifecycles:
        raise RuntimeError("account tranche has invalid lifecycle")
    if not tranche.entry_date or not tranche.sellable_date:
        raise RuntimeError("account tranche requires entry and sellable dates")
    entry_date = _required_iso_date(
        tranche.entry_date,
        field="account tranche entry date",
    )
    sellable_date = _required_iso_date(
        tranche.sellable_date,
        field="account tranche sellable date",
    )
    if sellable_date < entry_date:
        raise RuntimeError("account tranche sellable date predates entry date")
    _finite_number(tranche.mfe, field="account tranche mfe", minimum=0.0)
    _finite_number(tranche.mae, field="account tranche mae", maximum=0.0)
    _finite_number(tranche.entry_score, field="account tranche entry_score")
    _finite_number(
        tranche.entry_confidence,
        field="account tranche entry_confidence",
        minimum=0.0,
        maximum=1.0,
    )
    if not isinstance(tranche.entry_regime, str) or tranche.entry_regime not in {
        item.value for item in Opportunity
    }:
        raise RuntimeError("account tranche has invalid entry_regime")
    _finite_number(
        tranche.entry_industry_strength,
        field="account tranche entry_industry_strength",
    )
    if validate_attribution:
        _validate_attribution_identity(
            tranche,
            label="account tranche",
        )


def _validate_position_state(
    state: AccountState,
    *,
    validate_attribution: bool = False,
) -> None:
    """Reject durable positions whose aggregate and lot inventories diverge."""
    lifecycles = {item.value for item in Lifecycle}
    try:
        _optional_iso_date(state.broker_as_of, field="broker_as_of")
    except RuntimeError as exc:
        raise RuntimeError("account state has invalid broker_as_of") from exc
    for key, position in state.positions.items():
        _required_text(key, field="account position key")
        _required_text(position.symbol, field="account position symbol")
        if key != position.symbol:
            raise RuntimeError("account position key differs from its symbol")
        _nonnegative_integer(position.shares, field="account position shares", positive=True)
        position_cost = _finite_number(
            position.avg_cost,
            field="account position cost",
            minimum=0.0,
        )
        if position_cost == 0.0:
            raise RuntimeError("account position cost must be positive")
        highest_close = _finite_number(
            position.highest_close,
            field="account position highest close",
            minimum=0.0,
        )
        if highest_close == 0.0:
            raise RuntimeError("account position highest close must be positive")
        if position.lifecycle not in lifecycles:
            raise RuntimeError("account position has invalid lifecycle")
        _required_iso_date(position.entry_date, field="account position entry date")
        if not position.tranches:
            raise RuntimeError("account position requires tranche inventory")
        tranche_ids = [item.tranche_id for item in position.tranches]
        for tranche_id in tranche_ids:
            _required_text(tranche_id, field="account tranche id")
        if not all(tranche_ids) or len(tranche_ids) != len(set(tranche_ids)):
            raise RuntimeError("account position has invalid tranche ids")
        for tranche in position.tranches:
            _validate_position_tranche(
                tranche,
                lifecycles=lifecycles,
                validate_attribution=validate_attribution,
            )
        tranche_strategic_identities = {
            (tranche.grant_id, tranche.epoch_id) for tranche in position.tranches
        }
        if tranche_strategic_identities != {(position.grant_id, position.epoch_id)}:
            raise RuntimeError(
                "account position strategic identity differs from tranches"
            )
        if position.shares != sum(item.shares for item in position.tranches):
            raise RuntimeError("account position shares do not reconcile to tranches")


position_from_payload = _position
validate_position_state = _validate_position_state
