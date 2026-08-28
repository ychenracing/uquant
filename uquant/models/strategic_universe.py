"""Point-in-time role separation for strategic security universes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date as date_type
from enum import Enum


class ReferenceAvailability(str, Enum):
    """Causal data availability for one role-bound security."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _symbols(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    materialized = tuple(sorted(set(values)))
    if any(not isinstance(symbol, str) or not symbol for symbol in materialized):
        raise ValueError(f"strategic {label} contains an invalid symbol")
    return materialized


@dataclass(frozen=True, slots=True)
class StrategicUniverseRoles:
    """Immutable role sets and identities visible to one close decision."""

    as_of: str
    tradable_symbols: tuple[str, ...]
    qualification_reference_symbols: tuple[str, ...]
    risk_reference_symbols: tuple[str, ...]
    available_symbols: tuple[str, ...]
    unavailable_reference_symbols: tuple[str, ...]
    point_in_time_industries: tuple[tuple[str, str], ...]
    tradable_identity: str
    qualification_reference_identity: str
    risk_reference_identity: str
    point_in_time_industry_identity: str

    def availability(self, symbol: str) -> ReferenceAvailability:
        """Return UNAVAILABLE instead of manufacturing negative evidence."""

        return (
            ReferenceAvailability.AVAILABLE
            if symbol in self.available_symbols
            else ReferenceAvailability.UNAVAILABLE
        )

    def industry_of(self, symbol: str) -> str:
        """Return the bound point-in-time industry or ``unknown``."""

        return dict(self.point_in_time_industries).get(symbol, "unknown")


def build_strategic_universe_roles(
    *,
    as_of: str,
    tradable_symbols: Iterable[str],
    qualification_reference_symbols: Iterable[str],
    risk_reference_symbols: Iterable[str],
    industries: Mapping[str, str],
    available_symbols: Iterable[str],
) -> StrategicUniverseRoles:
    """Build causal universe roles without granting reference symbols capital rights."""

    try:
        date_type.fromisoformat(as_of)
    except (TypeError, ValueError) as exc:
        raise ValueError("strategic universe as_of must be an ISO date") from exc
    tradable = _symbols(tradable_symbols, label="tradable universe")
    qualification = _symbols(
        qualification_reference_symbols,
        label="qualification reference universe",
    )
    risk = _symbols(risk_reference_symbols, label="risk reference universe")
    available = _symbols(available_symbols, label="available universe")
    point_in_time_industries = tuple(
        (symbol, str(industries.get(symbol, "unknown"))) for symbol in qualification
    )
    if any(not industry for _, industry in point_in_time_industries):
        raise ValueError("strategic point-in-time industry must be non-empty")
    unavailable = tuple(
        symbol
        for symbol in sorted(set(qualification) | set(risk))
        if symbol not in available
    )
    role_payload = {"as_of": as_of}
    return StrategicUniverseRoles(
        as_of=as_of,
        tradable_symbols=tradable,
        qualification_reference_symbols=qualification,
        risk_reference_symbols=risk,
        available_symbols=available,
        unavailable_reference_symbols=unavailable,
        point_in_time_industries=point_in_time_industries,
        tradable_identity=_canonical_sha256(
            {**role_payload, "role": "TRADABLE", "symbols": tradable}
        ),
        qualification_reference_identity=_canonical_sha256(
            {
                **role_payload,
                "role": "QUALIFICATION_REFERENCE",
                "symbols": qualification,
                "availability": [
                    (symbol, "AVAILABLE" if symbol in available else "UNAVAILABLE")
                    for symbol in qualification
                ],
            }
        ),
        risk_reference_identity=_canonical_sha256(
            {
                **role_payload,
                "role": "RISK_REFERENCE",
                "symbols": risk,
                "availability": [
                    (symbol, "AVAILABLE" if symbol in available else "UNAVAILABLE")
                    for symbol in risk
                ],
            }
        ),
        point_in_time_industry_identity=_canonical_sha256(
            {
                **role_payload,
                "industries": point_in_time_industries,
            }
        ),
    )


__all__ = (
    "ReferenceAvailability",
    "StrategicUniverseRoles",
    "build_strategic_universe_roles",
)
