"""Position and tranche decoding and validation for durable accounts."""

from __future__ import annotations

import math
from typing import Any

from ..types import AccountState, Lifecycle, Opportunity, Position, Tranche
from .validation_common import (
    _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION,
    _finite_number,
    _nonnegative_integer,
    _optional_iso_date,
    _required_iso_date,
    _required_text,
)
from .validation_orders import _validate_attribution_identity


def _tranche(payload: dict[str, Any], *, schema_version: int) -> Tranche:
    """Load a tranche while deriving safe current-schema economic metadata."""
    native_schema = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    if native_schema:
        avg_cost = payload.get("avg_cost", 0.0)
        highest = payload.get("highest_close", avg_cost)
        lowest = payload.get("lowest_close", avg_cost)
    else:
        avg_cost = float(payload.get("avg_cost", 0.0))
        highest = float(payload.get("highest_close", avg_cost))
        lowest = float(payload.get("lowest_close", avg_cost))
        if lowest <= 0:
            lowest = avg_cost
    convert_text = (lambda value: value) if native_schema else str
    convert_int = (lambda value: value) if native_schema else int
    convert_float = (lambda value: value) if native_schema else float
    return Tranche(
        tranche_id=convert_text(payload["tranche_id"]),
        lifecycle=convert_text(payload.get("lifecycle", "CORE")),
        shares=convert_int(payload.get("shares", 0)),
        avg_cost=avg_cost,
        entry_date=convert_text(payload.get("entry_date", "")),
        sellable_date=convert_text(payload.get("sellable_date", "")),
        highest_close=highest,
        lowest_close=lowest,
        mfe=convert_float(
            payload.get(
                "mfe",
                max(
                    0.0,
                    float(highest) / max(float(avg_cost), 1e-12) - 1.0,
                ),
            )
        ),
        mae=convert_float(
            payload.get(
                "mae",
                min(
                    0.0,
                    float(lowest) / max(float(avg_cost), 1e-12) - 1.0,
                ),
            )
        ),
        entry_score=convert_float(payload.get("entry_score", 0.0)),
        entry_confidence=convert_float(payload.get("entry_confidence", 0.0)),
        entry_regime=convert_text(payload.get("entry_regime", "CHOPPY")),
        entry_industry_strength=convert_float(payload.get("entry_industry_strength", 0.0)),
        event_id=convert_text(payload.get("event_id", "")),
        origin_subsystem=convert_text(payload.get("origin_subsystem", "")),
        mechanism=convert_text(payload.get("mechanism", "")),
        origin_lifecycle=convert_text(payload.get("origin_lifecycle", "")),
        replaces_symbol=payload.get("replaces_symbol"),
        industry_at_entry=convert_text(payload.get("industry_at_entry", "")),
        industry_manifest_sha256=convert_text(payload.get("industry_manifest_sha256", "")),
    )


def _position(payload: dict[str, Any], *, schema_version: int) -> Position:
    """Decode a position and reconcile aggregate shares with its tranche lots."""

    native_schema = schema_version >= _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION
    convert_text = (lambda value: value) if native_schema else str
    convert_int = (lambda value: value) if native_schema else int
    convert_float = (lambda value: value) if native_schema else float
    position = Position(
        symbol=convert_text(payload["symbol"]),
        shares=convert_int(payload.get("shares", 0)),
        avg_cost=convert_float(payload.get("avg_cost", 0.0)),
        entry_date=convert_text(payload.get("entry_date", "")),
        highest_close=convert_float(payload.get("highest_close", 0.0)),
        lifecycle=convert_text(payload.get("lifecycle", "CORE")),
        tranches=[_tranche(item, schema_version=schema_version) for item in payload.get("tranches", [])],
    )
    if schema_version < _HISTORICAL_ATTRIBUTION_SCHEMA_VERSION and position.shares > 0:
        known_shares = sum(item.shares for item in position.tranches)
        if known_shares > position.shares:
            raise ValueError("compatible position tranches exceed aggregate shares")
        residual = position.shares - known_shares
        if residual:
            entry_date = position.entry_date or "0001-01-01"
            highest_close = (
                position.highest_close
                if math.isfinite(position.highest_close) and position.highest_close > 0
                else position.avg_cost
            )
            position.entry_date = entry_date
            position.highest_close = highest_close
            position.tranches.append(
                Tranche(
                    tranche_id=f"legacy:{position.symbol}:{len(position.tranches) + 1}",
                    lifecycle=position.lifecycle,
                    shares=residual,
                    avg_cost=position.avg_cost,
                    entry_date=entry_date,
                    # Equality preserves "already sellable" semantics while
                    # keeping the current causal date invariant.
                    sellable_date=entry_date,
                    highest_close=highest_close,
                    lowest_close=position.avg_cost,
                )
            )
    return position


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
        if position.shares != sum(item.shares for item in position.tranches):
            raise RuntimeError("account position shares do not reconcile to tranches")
