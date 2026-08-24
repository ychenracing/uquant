"""Economic attribution validation facade and engine-result reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date as date_type
from typing import Any, cast

from ..types import Side
from .concentration import finite_attribution_number as _finite
from .replay_evidence import close_attribution_values as _close
from .replay_evidence import positive_attribution_integer as _positive_integer
from .replay_evidence import validate_daily_replay_evidence as _validate_daily_replay_evidence
from .validation_artifact import (
    ACCOUNTING_FIELDS,
    ATTRIBUTION_FIELDS,
    COST_FIELDS,
    GROUP_FIELDS,
    validate_economic_attribution_artifact,
)
from .validation_artifact import economic_sessions as economic_sessions
from .validation_lots import LOT_COST_FIELDS, LOT_FIELDS

_ACCOUNTING_FIELDS = ACCOUNTING_FIELDS
_ATTRIBUTION_FIELDS = ATTRIBUTION_FIELDS
_COST_FIELDS = COST_FIELDS
_GROUP_FIELDS = GROUP_FIELDS
_LOT_COST_FIELDS = LOT_COST_FIELDS
_LOT_FIELDS = LOT_FIELDS
_economic_sessions = economic_sessions


def validate_economic_attribution(
    value: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    require_daily_ledger: bool = True,
) -> dict[str, Any]:
    """Validate and canonicalize the closed production attribution schema."""

    return validate_economic_attribution_artifact(
        value,
        economic_start=economic_start,
        economic_end=economic_end,
        require_daily_ledger=require_daily_ledger,
    )


type _BuyFillShares = dict[tuple[str, str, str], int]
type _SoldLot = tuple[str, str, str, str, int]


def _accumulate_engine_fill(
    raw_fill: Any,
    *,
    index: int,
    economic_start: str,
    economic_end: str,
    fill_costs: dict[str, float],
    buy_shares: _BuyFillShares,
    sold_lots: list[_SoldLot],
) -> Mapping[str, Any]:
    if not isinstance(raw_fill, Mapping):
        raise ValueError("engine result contains a malformed fill")
    side = raw_fill.get("side")
    symbol = raw_fill.get("symbol")
    fill_date = raw_fill.get("fill_date")
    shares = raw_fill.get("shares")
    if (
        side not in {Side.BUY.value, Side.SELL.value}
        or not isinstance(symbol, str)
        or not symbol
        or not isinstance(fill_date, str)
        or isinstance(shares, bool)
        or not isinstance(shares, int)
        or shares <= 0
    ):
        raise ValueError(f"engine fill {index} identity is malformed")
    try:
        fill_day = date_type.fromisoformat(fill_date)
    except ValueError as exc:
        raise ValueError("engine fill date is malformed") from exc
    if not date_type.fromisoformat(economic_start) <= fill_day <= date_type.fromisoformat(economic_end):
        raise ValueError("engine fill lies outside the exact economic interval")
    for output_name, source_name in (
        ("commission", "commission"),
        ("stamp_duty", "stamp_duty"),
        ("transfer_fee", "transfer_fee"),
        ("slippage", "slippage_cost"),
        ("gross_transaction_value", "gross_value"),
    ):
        fill_costs[output_name] += _finite(
            raw_fill.get(source_name),
            label=f"engine fill {source_name}",
            minimum=0.0,
        )
    if side == Side.BUY.value:
        event_id = raw_fill.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("engine BUY fill is missing structured origin event")
        key = (symbol, event_id, fill_date)
        if key in buy_shares:
            raise ValueError("engine BUY fill origin identity is ambiguous")
        buy_shares[key] = shares
        return raw_fill
    allocations = raw_fill.get("sold_tranches")
    if not isinstance(allocations, list):
        raise ValueError("engine SELL fill does not reconcile through sold tranches")
    allocated_shares = [
        _positive_integer(allocation.get("shares"), label="engine sold tranche shares")
        if isinstance(allocation, Mapping)
        else _malformed_sold_tranche()
        for allocation in allocations
    ]
    if sum(allocated_shares) != shares:
        raise ValueError("engine SELL fill does not reconcile through sold tranches")
    for allocation, allocation_shares in zip(allocations, allocated_shares, strict=True):
        allocation_map = cast(Mapping[str, Any], allocation)
        sold_lots.append(
            (
                symbol,
                str(allocation_map.get("tranche_id", "")),
                str(allocation_map.get("event_id", "")),
                fill_date,
                allocation_shares,
            )
        )
    return raw_fill


def _malformed_sold_tranche() -> int:
    raise ValueError("engine sold tranche is malformed")


def _validated_engine_fills(
    fills: list[Any],
    *,
    economic_start: str,
    economic_end: str,
) -> tuple[dict[str, float], _BuyFillShares, list[_SoldLot], list[Mapping[str, Any]]]:
    costs = {
        "commission": 0.0,
        "stamp_duty": 0.0,
        "transfer_fee": 0.0,
        "slippage": 0.0,
        "gross_transaction_value": 0.0,
    }
    buy_shares: _BuyFillShares = {}
    sold_lots: list[_SoldLot] = []
    normalized = [
        _accumulate_engine_fill(
            raw_fill,
            index=index,
            economic_start=economic_start,
            economic_end=economic_end,
            fill_costs=costs,
            buy_shares=buy_shares,
            sold_lots=sold_lots,
        )
        for index, raw_fill in enumerate(fills)
    ]
    return costs, buy_shares, sold_lots, normalized


def _validate_engine_fill_totals(
    *, canonical: dict[str, Any], fill_costs: dict[str, float], result: Mapping[str, Any]
) -> None:
    costs = canonical["costs"]
    for field in ("commission", "stamp_duty", "transfer_fee", "slippage"):
        _close(float(costs[field]), fill_costs[field], label=f"attribution versus fills {field}")
    turnover = canonical["turnover"]
    _close(
        float(turnover["gross_transaction_value"]),
        fill_costs["gross_transaction_value"],
        label="attribution versus fills transaction value",
    )
    _close(
        float(turnover["gross_turnover"]),
        _finite(result.get("gross_turnover"), label="engine gross turnover", minimum=0.0),
        label="attribution versus engine gross turnover",
    )


def _attributed_lot_identities(
    lots: list[dict[str, Any]],
) -> tuple[_BuyFillShares, list[_SoldLot], list[tuple[str, str, str, int]]]:
    buy_shares: _BuyFillShares = {}
    sold_lots: list[_SoldLot] = []
    open_lots: list[tuple[str, str, str, int]] = []
    for lot in lots:
        key = (str(lot["symbol"]), str(lot["origin_event_id"]), str(lot["entry_date"]))
        buy_shares[key] = buy_shares.get(key, 0) + int(lot["shares"])
        if lot["economic_status"] == "REALIZED":
            sold_lots.append(
                (
                    str(lot["symbol"]),
                    str(lot["tranche_id"]),
                    str(lot["origin_event_id"]),
                    str(lot["exit_date"]),
                    int(lot["shares"]),
                )
            )
        else:
            open_lots.append(
                (
                    str(lot["symbol"]),
                    str(lot["tranche_id"]),
                    str(lot["origin_event_id"]),
                    int(lot["shares"]),
                )
            )
    return buy_shares, sold_lots, open_lots


def _engine_open_lots(positions: Mapping[str, Any]) -> list[tuple[str, str, str, int]]:
    lots: list[tuple[str, str, str, int]] = []
    for symbol, raw_position in positions.items():
        if not isinstance(symbol, str) or not isinstance(raw_position, Mapping):
            raise ValueError("engine open position is malformed")
        tranches = raw_position.get("tranches")
        if not isinstance(tranches, list):
            raise ValueError("engine open position tranches are malformed")
        for tranche in tranches:
            if not isinstance(tranche, Mapping):
                raise ValueError("engine open tranche is malformed")
            lots.append(
                (
                    symbol,
                    str(tranche.get("tranche_id", "")),
                    str(tranche.get("event_id", "")),
                    int(tranche.get("shares", 0)),
                )
            )
    return lots


def _validate_engine_symbol_pnl(
    result: Mapping[str, Any], canonical: dict[str, Any], *, final_equity: float
) -> None:
    symbol_pnl = result.get("symbol_pnl")
    if not isinstance(symbol_pnl, Mapping):
        raise ValueError("engine result is missing symbol PnL")
    normalized = {
        str(symbol): _finite(value, label=f"engine symbol PnL {symbol}")
        for symbol, value in symbol_pnl.items()
    }
    attributed = {symbol: float(bucket["total_pnl"]) for symbol, bucket in canonical["by_symbol"].items()}
    if set(normalized) != set(attributed):
        raise ValueError("attribution symbol coverage differs from engine result")
    for symbol, pnl in attributed.items():
        _close(pnl, normalized[symbol], label=f"attribution symbol PnL {symbol}")
    if canonical["daily_ledger"]:
        _close(
            float(canonical["daily_ledger"][-1]["equity"]),
            final_equity,
            label="attribution ledger versus engine final equity",
        )


def validate_attribution_against_engine_result(
    result: Mapping[str, Any],
    *,
    economic_start: str,
    economic_end: str,
    attribution: Mapping[str, Any] | None = None,
    trusted_sessions: Sequence[str] | None = None,
    trusted_close: Callable[[str, str], float] | None = None,
    require_daily_replay_evidence: bool = False,
) -> dict[str, Any]:
    """Reconcile attribution to raw fills, sold tranches, positions, and equity."""

    attribution_value = result.get("attribution") if attribution is None else attribution
    if not isinstance(attribution_value, Mapping):
        raise ValueError("engine result is missing economic attribution")
    canonical = validate_economic_attribution(
        attribution_value,
        economic_start=economic_start,
        economic_end=economic_end,
    )
    account = result.get("final_account")
    if not isinstance(account, Mapping):
        raise ValueError("engine result is missing structured final account")
    fills = account.get("fills")
    positions = account.get("positions")
    if not isinstance(fills, list) or not isinstance(positions, Mapping):
        raise ValueError("engine result final account has malformed fills or positions")
    initial_cash = _finite(account.get("initial_cash"), label="engine initial cash", minimum=0.0)
    final_equity = _finite(result.get("final_equity"), label="engine final equity", minimum=0.0)
    if initial_cash <= 0.0:
        raise ValueError("engine initial cash must be positive")
    _close(
        float(canonical["accounting"]["expected_pnl"]),
        final_equity - initial_cash,
        label="attribution versus engine final equity",
    )
    _close(
        _finite(result.get("final_wealth"), label="engine final wealth"),
        final_equity / initial_cash,
        label="engine final wealth",
    )
    if result.get("start") != economic_start or result.get("end") != economic_end:
        raise ValueError("engine result interval differs from the exact economic interval")
    fill_costs, buy_shares, sold_lots, normalized_fills = _validated_engine_fills(
        fills,
        economic_start=economic_start,
        economic_end=economic_end,
    )
    _validate_engine_fill_totals(canonical=canonical, fill_costs=fill_costs, result=result)
    attributed_buy, attributed_sold, attributed_open = _attributed_lot_identities(canonical["lots"])
    if attributed_buy != buy_shares:
        raise ValueError("attribution lots do not exactly cover raw BUY fills")
    if sorted(attributed_sold) != sorted(sold_lots):
        raise ValueError("attribution realized lots differ from raw sold tranches")
    if sorted(attributed_open) != sorted(_engine_open_lots(positions)):
        raise ValueError("attribution open lots differ from raw account tranches")
    _validate_engine_symbol_pnl(result, canonical, final_equity=final_equity)
    if require_daily_replay_evidence or result.get("daily_replay_evidence") is not None:
        _validate_daily_replay_evidence(
            result=result,
            attribution=canonical,
            account=account,
            fills=normalized_fills,
            positions=positions,
            economic_start=economic_start,
            economic_end=economic_end,
            trusted_sessions=trusted_sessions,
            trusted_close=trusted_close,
        )
    return canonical
