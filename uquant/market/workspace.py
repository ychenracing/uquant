"""Owned point-in-time market frames and cost-only data caches."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from ..config import SystemConfig
from ..data import DataManifest, DataStore, normalize_symbol
from ..features import compute_features
from .replay import ReplayUniverse


class MarketWorkspace:
    """Own loaded market bytes, derived frames, manifests, and lookups."""

    def __init__(self, data_dir: str | Path, cfg: SystemConfig) -> None:
        self.cfg = cfg
        self.data = DataStore(data_dir)
        self._raw: dict[str, pd.DataFrame] = {}
        self._features: dict[str, pd.DataFrame] = {}
        self._manifest_cache: dict[tuple[tuple[str, ...], str], DataManifest] = {}
        self._reference_returns: pd.DataFrame | None = None
        self._universe: ReplayUniverse | None = None

    @classmethod
    def production(
        cls,
        data_dir: str | Path,
        cfg: SystemConfig,
        *,
        reference_symbols: Iterable[str],
        index_symbols: Iterable[str],
    ) -> MarketWorkspace:
        """Create the lazy fixed-universe workspace used by production."""

        workspace = cls(data_dir, cfg)
        workspace.bind(
            ReplayUniverse.from_symbols(
                tradable_symbols=(),
                reference_symbols=reference_symbols,
                index_symbols=index_symbols,
            )
        )
        return workspace

    @property
    def universe(self) -> ReplayUniverse:
        if self._universe is None:
            raise RuntimeError("market workspace has no prepared replay universe")
        return self._universe

    def bind(self, universe: ReplayUniverse) -> None:
        """Bind identity without eagerly loading data."""

        if self._universe is None or self._universe.identity_sha256 != universe.identity_sha256:
            self._universe = universe
            self._reference_returns = None

    def bind_tradable(self, symbols: Iterable[str]) -> ReplayUniverse:
        """Replace only tradable symbols while preserving the bound reference policy."""

        current = self.universe
        universe = ReplayUniverse.from_symbols(
            tradable_symbols=symbols,
            reference_symbols=current.reference_symbols,
            index_symbols=current.index_symbols,
        )
        self.bind(universe)
        return universe

    def filter_reference_symbols(self, symbols: Iterable[str]) -> tuple[str, ...]:
        """Retain candidate references in their supplied point-in-time order."""

        allowed = set(self.universe.reference_symbols)
        return tuple(symbol for symbol in symbols if symbol in allowed)

    @property
    def loaded_symbols(self) -> tuple[str, ...]:
        return tuple(self._raw)

    def replace_data_store(self, data: DataStore) -> None:
        """Replace the data authority and invalidate every derived cost cache."""

        if self.data is data:
            return
        self.data = data
        self._raw.clear()
        self._features.clear()
        self._manifest_cache.clear()
        self._reference_returns = None

    def prepare(self, universe: ReplayUniverse) -> None:
        """Bind one explicit universe and load each required frame once."""

        self.bind(universe)
        self.load(universe.all_symbols)

    def load(self, symbols: Iterable[str]) -> None:
        """Load and feature-build normalized symbols in stable sorted order."""

        for symbol in sorted({normalize_symbol(item) for item in symbols}):
            if symbol not in self._raw:
                raw = self.data.load(symbol)
                self._raw[symbol] = raw
                self._features[symbol] = compute_features(raw, self.cfg)
        references = self._universe.reference_symbols if self._universe is not None else ()
        if (
            self._reference_returns is None
            and references
            and set(references).issubset(self._raw)
        ):
            self._reference_returns = pd.DataFrame(
                {
                    symbol: self._raw[symbol]["close"].pct_change(fill_method=None)
                    for symbol in references
                }
            )

    def raw_frame(self, symbol: str) -> pd.DataFrame:
        """Return a mutation-isolated copy of one owned raw frame."""

        return self._owned_raw_frame(symbol).copy(deep=True)

    def feature_frame(self, symbol: str) -> pd.DataFrame:
        """Return a mutation-isolated copy of one owned feature frame."""

        return self._owned_feature_frame(symbol).copy(deep=True)

    def reference_returns(self) -> pd.DataFrame:
        """Return mutation-isolated reference returns in universe order."""

        return self._owned_reference_returns().copy(deep=True)

    def price(
        self,
        symbol: str,
        as_of: str | pd.Timestamp,
        field: str = "close",
    ) -> float:
        """Return the latest visible field using the historical failure contract."""

        frame = self._raw[symbol]
        date = pd.Timestamp(as_of)
        visible = frame.loc[:date]
        if visible.empty:
            raise RuntimeError(f"{symbol} has no mark price at {date.date()}")
        return float(visible.iloc[-1][field])

    def common_sessions(self, left: str, right: str) -> pd.DatetimeIndex:
        """Return the ordered intersection of two already loaded frames."""

        left_frame = self._raw[normalize_symbol(left)]
        right_frame = self._raw[normalize_symbol(right)]
        return pd.DatetimeIndex(left_frame.index.intersection(right_frame.index))

    def manifest(
        self,
        symbols: Iterable[str],
        *,
        as_of: str | pd.Timestamp,
    ) -> DataManifest:
        """Return one cached manifest for an exact symbol/date prefix."""

        normalized = tuple(sorted({normalize_symbol(symbol) for symbol in symbols}))
        date = str(pd.Timestamp(as_of).normalize().date())
        key = (normalized, date)
        if key not in self._manifest_cache:
            self._manifest_cache[key] = self.data.manifest(normalized, as_of=date)
        return self._manifest_cache[key]

    def visible_symbols(
        self,
        symbols: Iterable[str],
        *,
        as_of: str | pd.Timestamp,
    ) -> tuple[str, ...]:
        """Return loaded symbols with at least one physically visible row."""

        date = pd.Timestamp(as_of)
        return tuple(
            symbol
            for symbol in sorted({normalize_symbol(item) for item in symbols})
            if symbol in self._raw and not self._raw[symbol].loc[:date].empty
        )

    def clear_caches(self) -> None:
        """Clear cost-only manifest state without changing owned market frames."""

        self._manifest_cache.clear()

    def _owned_raw_frame(self, symbol: str) -> pd.DataFrame:
        return self._raw[normalize_symbol(symbol)]

    def _owned_feature_frame(self, symbol: str) -> pd.DataFrame:
        return self._features[normalize_symbol(symbol)]

    def _owned_reference_returns(self) -> pd.DataFrame:
        if self._reference_returns is None:
            raise RuntimeError("market workspace reference returns are not prepared")
        return self._reference_returns
