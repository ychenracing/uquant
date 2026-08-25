"""Immutable replay-universe identity and bounded replay orchestration."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

import pandas as pd

from ..data import normalize_symbol


class _Workspace(Protocol):
    def prepare(self, universe: ReplayUniverse) -> None: ...

    def common_sessions(self, left: str, right: str) -> pd.DatetimeIndex: ...

    def raw_frame(self, symbol: str) -> pd.DataFrame: ...


class ReplayCache[Key, Value]:
    """Finite thread-safe LRU whose entries may affect cost, never results."""

    def __init__(self, *, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("replay cache capacity must be positive")
        self._capacity = capacity
        self._values: OrderedDict[Key, Value] = OrderedDict()
        self._lock = RLock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)

    def get(self, key: Key) -> Value | None:
        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
            return value

    def get_or_build(self, key: Key, builder: Callable[[], Value]) -> Value:
        """Atomically read or build one entry and evict the least-recently used."""

        with self._lock:
            value = self._values.get(key)
            if value is not None:
                self._values.move_to_end(key)
                return value
            value = builder()
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self._capacity:
                self._values.popitem(last=False)
            return value

    def keys(self) -> tuple[Key, ...]:
        with self._lock:
            return tuple(self._values)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


def _normalize_symbol_set(symbols: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({normalize_symbol(symbol) for symbol in symbols}))


def _universe_identity(
    *,
    tradable_symbols: tuple[str, ...],
    reference_symbols: tuple[str, ...],
    index_symbols: tuple[str, ...],
) -> str:
    payload = {
        "tradable_symbols": list(tradable_symbols),
        "reference_symbols": list(reference_symbols),
        "index_symbols": list(index_symbols),
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayUniverse:
    """One canonical, immutable symbol context for a deterministic replay."""

    tradable_symbols: tuple[str, ...]
    reference_symbols: tuple[str, ...]
    index_symbols: tuple[str, ...]
    identity_sha256: str

    def __post_init__(self) -> None:
        groups = (
            self.tradable_symbols,
            self.reference_symbols,
            self.index_symbols,
        )
        if any(group != _normalize_symbol_set(group) for group in groups):
            raise ValueError("replay universe symbols must be canonical, sorted, and unique")
        expected = _universe_identity(
            tradable_symbols=self.tradable_symbols,
            reference_symbols=self.reference_symbols,
            index_symbols=self.index_symbols,
        )
        if self.identity_sha256 != expected:
            raise ValueError("replay universe identity does not match its canonical symbols")

    @classmethod
    def from_symbols(
        cls,
        *,
        tradable_symbols: Iterable[str],
        reference_symbols: Iterable[str],
        index_symbols: Iterable[str],
    ) -> ReplayUniverse:
        """Normalize symbol sets and bind their strict canonical identity."""

        tradable = _normalize_symbol_set(tradable_symbols)
        references = _normalize_symbol_set(reference_symbols)
        indexes = _normalize_symbol_set(index_symbols)
        identity = _universe_identity(
            tradable_symbols=tradable,
            reference_symbols=references,
            index_symbols=indexes,
        )
        return cls(tradable, references, indexes, identity)

    @property
    def all_symbols(self) -> tuple[str, ...]:
        """Return the canonical union used to prepare market data."""

        return tuple(sorted(set(self.tradable_symbols + self.reference_symbols + self.index_symbols)))

    def cache_keys(
        self,
        *,
        data_identity: str,
        config_identity: str,
        semantic_universe_identity: str,
        source_identity: str,
        builder_identity: object,
    ) -> tuple[tuple[object, ...], tuple[str, str, str, str]]:
        """Return complete memory and backward-shaped v1 disk cache keys."""

        combined = hashlib.sha256(
            json.dumps(
                [semantic_universe_identity, self.identity_sha256],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return (
            (
                data_identity,
                config_identity,
                self.identity_sha256,
                semantic_universe_identity,
                source_identity,
                builder_identity,
            ),
            (data_identity, config_identity, combined, source_identity),
        )


@dataclass(frozen=True, slots=True)
class ReplayHarness:
    """Prepare repeated market-only replay inputs for one explicit universe."""

    workspace: _Workspace
    universe: ReplayUniverse

    def prepare(self) -> None:
        self.workspace.prepare(self.universe)

    def sessions(self, *, start: str, end: str) -> pd.DatetimeIndex:
        self.prepare()
        if len(self.universe.index_symbols) != 2:
            raise ValueError("replay harness requires exactly two index symbols")
        sessions = self.workspace.common_sessions(*self.universe.index_symbols)
        return sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]

    def raw_panel(self, symbols: Iterable[str]) -> dict[str, pd.DataFrame]:
        self.prepare()
        return {
            symbol: self.workspace.raw_frame(symbol)
            for symbol in _normalize_symbol_set(symbols)
        }
