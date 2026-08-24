"""Immutable structural values for the production-promotion contract."""

from __future__ import annotations

import operator
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final, Never, cast


class _ImmutablePolicyList(list[Any]):
    """List-compatible immutable sequence for compiled policy values."""

    @staticmethod
    def _reject_mutation() -> Never:
        raise TypeError("promotion policy values are immutable")

    def __setitem__(self, key: object, value: object, /) -> None:
        del key, value
        self._reject_mutation()

    def __delitem__(self, key: object, /) -> None:
        del key
        self._reject_mutation()

    def append(self, value: Any, /) -> None:
        del value
        self._reject_mutation()

    def extend(self, values: object, /) -> None:
        del values
        self._reject_mutation()

    def insert(self, index: object, value: Any, /) -> None:
        del index, value
        self._reject_mutation()

    def pop(self, index: object = -1, /) -> Never:
        del index
        self._reject_mutation()

    def remove(self, value: Any, /) -> None:
        del value
        self._reject_mutation()

    def clear(self) -> None:
        self._reject_mutation()

    def reverse(self) -> None:
        self._reject_mutation()

    def sort(self, *, key: Any = None, reverse: bool = False) -> None:
        del key, reverse
        self._reject_mutation()

    def __iadd__(self, value: object, /) -> Never:
        del value
        self._reject_mutation()

    def __add__(self, value: object, /) -> Any:
        if not isinstance(value, list):
            return NotImplemented
        return list(self) + value

    def __imul__(self, value: object, /) -> Never:
        del value
        self._reject_mutation()

    def __mul__(self, value: object, /) -> Any:
        try:
            count = operator.index(cast(Any, value))
        except TypeError:
            return NotImplemented
        return list(self) * count

    def __deepcopy__(self, memo: dict[int, object]) -> list[Any]:
        del memo
        return list(self)


def freeze_promotion_policy(value: Any) -> Any:
    """Recursively freeze the compile-anchored promotion policy."""
    if isinstance(value, dict):
        return MappingProxyType({key: freeze_promotion_policy(item) for key, item in value.items()})
    if isinstance(value, list):
        return _ImmutablePolicyList(freeze_promotion_policy(item) for item in value)
    return value


EXECUTION_CONTRACT: Final[Mapping[str, Any]] = MappingProxyType(
    {
        "engine": "uquant.engine.ProductionEngine",
        "initial_cash": 2_000_000.0,
        "market": "A-share AI supply chain",
        "positioning": "cash-only long",
        "decision": "daily close t",
        "execution": "next tradable open",
        "intraday_exit": False,
        "automation": "human-assisted, no broker submission",
        "prelisting": "invisible until first observable row",
    }
)

# These intervals retain the report's explicit regression protections. They
# overlap the six official windows, but are evaluated independently so a good
# half-year aggregate cannot hide the reported 2023, 2024, or bull regression.
PROTECTED_INTERVALS: Final[Mapping[str, Mapping[str, str]]] = MappingProxyType(
    {
        "year_2023": MappingProxyType({"start": "2023-01-03", "end": "2023-12-29"}),
        "year_2024": MappingProxyType({"start": "2024-01-02", "end": "2024-12-31"}),
        "bull": MappingProxyType({"start": "2025-04-01", "end": "2026-06-30"}),
    }
)

TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema_version",
        "validation_fingerprint",
        "contract",
        "pools",
        "policy",
        "champion",
        "provenance",
    }
)
METRIC_FIELDS: Final = frozenset(
    {
        "final_wealth",
        "cagr",
        "max_drawdown",
        "sharpe",
        "calmar",
        "account_orders",
        "annual_turnover",
        "gross_turnover",
        "acute_return",
    }
)
