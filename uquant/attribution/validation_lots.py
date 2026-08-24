"""Economic attribution lot schema validation stages."""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

from ..types import Lifecycle, Side, validate_attribution_compatibility
from .concentration import finite_attribution_number as _finite
from .replay_evidence import close_attribution_values as _close
from .replay_evidence import require_exact_attribution_fields as _require_exact_fields

_LOT_FIELDS = frozenset(
    {
        "economic_status",
        "symbol",
        "tranche_id",
        "shares",
        "entry_date",
        "exit_date",
        "final_date",
        "realized_pnl",
        "open_pnl",
        "total_pnl",
        "origin_event_id",
        "origin_subsystem",
        "origin_mechanism",
        "origin_lifecycle",
        "current_lifecycle",
        "exit_subsystem",
        "exit_mechanism",
        "replaces_symbol",
        "industry_at_entry",
        "entry_gross_value",
        "exit_gross_value",
        "costs",
        "gross_transaction_value",
        "holding_sessions",
    }
)


_LOT_COST_FIELDS = frozenset(
    {
        "entry_commission",
        "entry_stamp_duty",
        "entry_transfer_fee",
        "entry_slippage",
        "exit_commission",
        "exit_stamp_duty",
        "exit_transfer_fee",
        "exit_slippage",
        "cash_fees",
        "slippage",
        "all_in",
    }
)


LOT_COST_FIELDS = _LOT_COST_FIELDS
LOT_FIELDS = _LOT_FIELDS


def _validate_lot_required_text(lot: dict[str, Any]) -> None:
    for field in (
        "symbol",
        "tranche_id",
        "entry_date",
        "origin_event_id",
        "origin_subsystem",
        "origin_mechanism",
        "origin_lifecycle",
        "current_lifecycle",
        "industry_at_entry",
    ):
        if not isinstance(lot[field], str) or not lot[field]:
            raise ValueError(f"economic lot {field} is missing")


def _validate_lot_economic_values(lot: dict[str, Any], *, status: Any) -> None:
    shares = lot["shares"]
    holding = lot["holding_sessions"]
    if (
        isinstance(shares, bool)
        or not isinstance(shares, int)
        or shares <= 0
        or isinstance(holding, bool)
        or not isinstance(holding, int)
        or holding < 0
    ):
        raise ValueError("economic lot shares/holding sessions are invalid")
    for field in (
        "realized_pnl",
        "open_pnl",
        "total_pnl",
        "entry_gross_value",
        "exit_gross_value",
        "gross_transaction_value",
    ):
        lot[field] = _finite(lot[field], label=f"economic lot {field}")
    if lot["entry_gross_value"] < 0.0 or lot["exit_gross_value"] < 0.0:
        raise ValueError("economic lot transaction value cannot be negative")
    if status == "REALIZED" and lot["open_pnl"] != 0.0:
        raise ValueError("realized economic lot contains open PnL")
    if status == "OPEN" and lot["realized_pnl"] != 0.0:
        raise ValueError("open economic lot contains realized PnL")
    _close(lot["realized_pnl"] + lot["open_pnl"], lot["total_pnl"], label="economic lot PnL")
    _close(
        lot["entry_gross_value"] + lot["exit_gross_value"],
        lot["gross_transaction_value"],
        label="economic lot transaction value",
    )


def _validate_lot_dates_and_values(
    lot: dict[str, Any],
    *,
    status: Any,
    economic_start: str,
    economic_end: str,
) -> str:
    _validate_lot_required_text(lot)
    try:
        entry = date_type.fromisoformat(lot["entry_date"])
        start = date_type.fromisoformat(economic_start)
        end = date_type.fromisoformat(economic_end)
    except ValueError as exc:
        raise ValueError("economic lot dates must be canonical ISO dates") from exc
    terminal_name = "exit_date" if status == "REALIZED" else "final_date"
    other_terminal = "final_date" if status == "REALIZED" else "exit_date"
    terminal = lot[terminal_name]
    if not isinstance(terminal, str) or lot[other_terminal] is not None:
        raise ValueError("economic lot terminal-date status is inconsistent")
    try:
        terminal_date = date_type.fromisoformat(terminal)
    except ValueError as exc:
        raise ValueError("economic lot terminal date must be canonical ISO") from exc
    if entry < start or entry > terminal_date or terminal_date > end:
        raise ValueError("economic lot date lies outside the exact economic interval")
    _validate_lot_economic_values(lot, status=status)
    return terminal


def _validate_lot_origins(lot: dict[str, Any], *, status: Any) -> None:
    try:
        Lifecycle(lot["origin_lifecycle"])
        Lifecycle(lot["current_lifecycle"])
        validate_attribution_compatibility(
            origin_subsystem=lot["origin_subsystem"],
            mechanism=lot["origin_mechanism"],
        )
    except ValueError as exc:
        raise ValueError("economic lot structured origin identity is invalid") from exc
    exit_subsystem = lot["exit_subsystem"]
    exit_mechanism = lot["exit_mechanism"]
    if status == "REALIZED":
        if not isinstance(exit_subsystem, str) or not isinstance(exit_mechanism, str):
            raise ValueError("realized economic lot exit identity is missing")
        try:
            validate_attribution_compatibility(
                origin_subsystem=exit_subsystem,
                mechanism=exit_mechanism,
                side=Side.SELL.value,
            )
        except ValueError as exc:
            raise ValueError("realized economic lot exit identity is invalid") from exc
    elif exit_subsystem is not None or exit_mechanism is not None:
        raise ValueError("open economic lot cannot contain exit identity")
    replaces_symbol = lot["replaces_symbol"]
    if replaces_symbol is not None and (not isinstance(replaces_symbol, str) or not replaces_symbol):
        raise ValueError("economic lot replacement identity is invalid")


def _validated_lot_costs(lot: dict[str, Any], *, index: int) -> None:
    costs = dict(_require_exact_fields(lot["costs"], _LOT_COST_FIELDS, label=f"economic lot {index} costs"))
    for field in _LOT_COST_FIELDS:
        costs[field] = _finite(costs[field], label=f"economic lot cost {field}", minimum=0.0)
    _close(
        costs["entry_commission"]
        + costs["entry_stamp_duty"]
        + costs["entry_transfer_fee"]
        + costs["exit_commission"]
        + costs["exit_stamp_duty"]
        + costs["exit_transfer_fee"],
        costs["cash_fees"],
        label="economic lot cash fees",
    )
    _close(
        costs["entry_slippage"] + costs["exit_slippage"],
        costs["slippage"],
        label="economic lot slippage",
    )
    _close(costs["cash_fees"] + costs["slippage"], costs["all_in"], label="economic lot all-in costs")
    lot["costs"] = costs


def validated_economic_lots(
    lots_value: Any,
    *,
    economic_start: str,
    economic_end: str,
) -> list[dict[str, Any]]:
    if not isinstance(lots_value, list):
        raise ValueError("economic attribution lots must be a list")
    lots: list[dict[str, Any]] = []
    seen_lots: set[tuple[str, str, str, str]] = set()
    for index, raw_lot in enumerate(lots_value):
        lot = dict(_require_exact_fields(raw_lot, _LOT_FIELDS, label=f"economic lot {index}"))
        status = lot["economic_status"]
        if status not in {"REALIZED", "OPEN"}:
            raise ValueError("economic lot status is invalid")
        terminal = _validate_lot_dates_and_values(
            lot,
            status=status,
            economic_start=economic_start,
            economic_end=economic_end,
        )
        _validate_lot_origins(lot, status=status)
        _validated_lot_costs(lot, index=index)
        identity = (lot["symbol"], lot["tranche_id"], status, terminal)
        if identity in seen_lots:
            raise ValueError("economic attribution contains a duplicate lot record")
        seen_lots.add(identity)
        lots.append(lot)
    return lots
