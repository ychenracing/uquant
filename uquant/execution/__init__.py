"""Stable execution facade over responsibility-focused mechanical owners."""

from __future__ import annotations

from .fees import fee_components
from .open_execution import ExecutionPlanner
from .order_planning import plan_orders
from .pending import merge_pending_orders
from .reconciliation import reconcile_account_orders
from .tranches import (
    RISK_LIFECYCLE_PRIORITY as _RISK_LIFECYCLE_PRIORITY,
)
from .tranches import (
    allocate_sell_costs as _allocate_sell_costs,
)
from .tranches import (
    risk_priority_tranche_key,
)

allocate_sell_costs = _allocate_sell_costs

__all__ = (  # noqa: RUF022 - immutable legacy export order
    "ExecutionPlanner",
    "_RISK_LIFECYCLE_PRIORITY",
    "fee_components",
    "merge_pending_orders",
    "plan_orders",
    "reconcile_account_orders",
    "risk_priority_tranche_key",
)

for _legacy_object in (
    ExecutionPlanner,
    fee_components,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
    risk_priority_tranche_key,
    _allocate_sell_costs,
):
    _legacy_object.__module__ = __name__
ExecutionPlanner.__init__.__module__ = __name__
ExecutionPlanner.execute_open.__module__ = __name__
del _legacy_object
