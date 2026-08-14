"""Verified, cached frozen-market lookups for replay evidence validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from ..data import DataContractError, DataStore, normalize_symbol
from .manifest import verify_data_manifest

_INDEX_SYMBOLS = ("sh000300", "sh000682")


class VerifiedMarketData:
    """Load one checksum-verified frozen snapshot once and serve causal lookups."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        expected_manifest: Mapping[str, Any],
    ) -> None:
        root = Path(data_dir)
        observed = verify_data_manifest(root)
        if dict(observed) != dict(expected_manifest):
            raise DataContractError(
                "frozen market data differs from artifact provenance"
            )
        store = DataStore(root)
        symbols = tuple(sorted(path.stem for path in root.glob("*.csv")))
        if not symbols:
            raise DataContractError("verified market data has no symbol panels")
        self._panels = {
            normalize_symbol(symbol): store.load(symbol) for symbol in symbols
        }
        missing_indices = sorted(set(_INDEX_SYMBOLS) - set(self._panels))
        if missing_indices:
            raise DataContractError(
                f"verified market data is missing session indices: {missing_indices}"
            )

    def sessions(self, start: str, end: str) -> tuple[str, ...]:
        """Return the exact common production index sessions for an interval."""

        try:
            start_date = pd.Timestamp(start).normalize()
            end_date = pd.Timestamp(end).normalize()
        except (TypeError, ValueError) as exc:
            raise DataContractError("verified market interval is invalid") from exc
        if start_date > end_date:
            raise DataContractError("verified market interval is inverted")
        sessions = self._panels[_INDEX_SYMBOLS[0]].index.intersection(
            self._panels[_INDEX_SYMBOLS[1]].index
        )
        sessions = sessions[(sessions >= start_date) & (sessions <= end_date)]
        if sessions.empty:
            raise DataContractError("verified market interval has no common sessions")
        return tuple(str(value.date()) for value in sessions)

    def close(self, symbol: str, session: str) -> float:
        """Return the latest causal close at one verified production session."""

        normalized = normalize_symbol(symbol)
        frame = self._panels.get(normalized)
        if frame is None:
            raise DataContractError(f"verified market data is missing {normalized}")
        try:
            date = pd.Timestamp(session).normalize()
        except (TypeError, ValueError) as exc:
            raise DataContractError("verified market session is invalid") from exc
        visible = frame.loc[:date]
        if visible.empty:
            raise DataContractError(
                f"verified market data has no causal close for {normalized} at {session}"
            )
        close = float(visible.iloc[-1]["close"])
        if not pd.notna(close) or close <= 0.0:
            raise DataContractError(
                f"verified market close is invalid for {normalized} at {session}"
            )
        return close
