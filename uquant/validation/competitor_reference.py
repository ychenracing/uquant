"""Immutable repository identities for the reviewed competitor adapters."""

from __future__ import annotations

from typing import Final

METRIC_FIELDS: Final = frozenset({"final_wealth", "max_drawdown", "account_orders"})
POLICY_FIELDS: Final = frozenset(
    {
        "wealth_floor_ratio",
        "drawdown_tolerance",
        "absolute_max_drawdown",
        "order_tolerance",
        "order_ceiling_ratio",
    }
)

LOCKED_REPOSITORY_IDENTITIES: Final[tuple[tuple[str, str, str, str], ...]] = (
    (
        "aquant",
        "ychenracing/aquant",
        "3c38fbbf679a0fb1b4ee8f3d47b6931d3eb8fdbd",
        "0fdc39c40239e51b5c91024507bef1bed222cd83575e4d9f870b8ada2f73a50a",
    ),
    (
        "qwenquant",
        "ychenracing/qwenquant",
        "0b3681e10b75425ad8600e75835677a6a125ed13",
        "66fc531989e294990d40dae5f0c0ff867fe4e144ab2bae81863b42e7113c46c0",
    ),
    (
        "trade",
        "ychenracing/trade",
        "cee1620f40af3af8f839e15db188a9e388a78dd0",
        "03e33e1396ca31d61e724bcd9cf58971ae656134740eb8929313167aa8ed8597",
    ),
)
