"""Reviewed point-in-time membership for the production reference universe."""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import pandas as pd

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "reference_registry.json"


@dataclass(frozen=True, slots=True)
class ReferenceMembership:
    """One reviewed symbol-membership interval with an exclusive end date."""

    symbol: str
    effective_from: pd.Timestamp
    effective_to: pd.Timestamp | None
    source: str
    review_status: str

    def active(self, as_of: pd.Timestamp) -> bool:
        """Return whether this interval is visible at `as_of`."""

        return self.effective_from <= as_of and (
            self.effective_to is None or as_of < self.effective_to
        )


def load_reference_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> tuple[ReferenceMembership, ...]:
    """Load and fail closed on malformed, overlapping, or unreviewed membership."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("memberships"), list):
        raise ValueError("reference registry schema_version 1 and memberships are required")
    entries: list[ReferenceMembership] = []
    for raw in payload["memberships"]:
        required = {"symbol", "effective_from", "effective_to", "source", "review_status"}
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise ValueError("reference registry membership is incomplete")
        if raw["review_status"] != "approved" or not str(raw["source"]).strip():
            raise ValueError("reference registry membership must be sourced and approved")
        start = pd.Timestamp(str(raw["effective_from"])).normalize()
        end = (
            pd.Timestamp(str(raw["effective_to"])).normalize()
            if raw["effective_to"] is not None
            else None
        )
        if end is not None and end <= start:
            raise ValueError("reference registry effective_to must follow effective_from")
        entries.append(
            ReferenceMembership(
                symbol=str(raw["symbol"]),
                effective_from=start,
                effective_to=end,
                source=str(raw["source"]),
                review_status=str(raw["review_status"]),
            )
        )
    by_symbol: dict[str, list[ReferenceMembership]] = {}
    for entry in entries:
        by_symbol.setdefault(entry.symbol, []).append(entry)
    for symbol, periods in by_symbol.items():
        ordered = sorted(periods, key=lambda item: item.effective_from)
        for left, right in pairwise(ordered):
            if left.effective_to is None or right.effective_from < left.effective_to:
                raise ValueError(f"overlapping reference membership for {symbol}")
    return tuple(sorted(entries, key=lambda item: (item.symbol, item.effective_from)))


def resolve_reference_symbols(
    as_of: str | pd.Timestamp,
    *,
    registry: tuple[ReferenceMembership, ...] | None = None,
) -> tuple[str, ...]:
    """Resolve only memberships already effective at the decision date."""
    date = pd.Timestamp(as_of).normalize()
    entries = registry if registry is not None else load_reference_registry()
    return tuple(sorted(entry.symbol for entry in entries if entry.active(date)))
