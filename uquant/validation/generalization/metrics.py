"""Deterministic universe-generalization diagnostics and frozen references.

The production engine remains the only strategy implementation.  This module
only constructs causal universe perturbations, replays them through a supplied
runner, and aggregates dependency evidence.  It deliberately has no API that
writes a baseline file: reference updates must remain an explicit, reviewed
repository change.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

from ..statistics import linear_quantile as _linear_quantile
from .models import GeneralizationObservation, GeneralizationScenario


def _accumulate_symbol_profit(
    *,
    final_prices: Mapping[str, float],
    result: Mapping[str, Any],
) -> tuple[Mapping[Any, Any], dict[str, float]]:
    account = result.get("final_account")
    if not isinstance(account, Mapping):
        raise ValueError("backtest result is missing final_account")
    raw_fills = account.get("fills", [])
    raw_positions = account.get("positions", {})
    if not isinstance(raw_fills, list) or not isinstance(raw_positions, Mapping):
        raise ValueError("backtest final_account has invalid fills or positions")
    pnl: dict[str, float] = {}
    for raw_fill in raw_fills:
        if not isinstance(raw_fill, Mapping):
            raise ValueError("backtest result contains an invalid fill")
        symbol = str(raw_fill.get("symbol", ""))
        side = str(raw_fill.get("side", ""))
        gross = float(raw_fill.get("gross_value", 0.0))
        fee_values = tuple(
            float(raw_fill.get(field, 0.0)) for field in ("commission", "stamp_duty", "transfer_fee")
        )
        fees = sum(fee_values)
        if (
            not symbol
            or not math.isfinite(gross)
            or gross < 0
            or any(not math.isfinite(value) or value < 0 for value in fee_values)
            or side not in {"BUY", "SELL"}
        ):
            raise ValueError("backtest result contains an invalid fill cash flow")
        cash_flow = -(gross + fees) if side == "BUY" else gross - fees
        pnl[symbol] = pnl.get(symbol, 0.0) + cash_flow
    for symbol, raw_position in raw_positions.items():
        if not isinstance(raw_position, Mapping):
            raise ValueError("backtest result contains an invalid final position")
        shares = int(raw_position.get("shares", 0))
        mark = float(final_prices.get(str(symbol), float("nan")))
        if shares < 0 or (shares > 0 and (not math.isfinite(mark) or mark <= 0)):
            raise ValueError(f"final mark is missing or invalid: {symbol}")
        pnl[str(symbol)] = pnl.get(str(symbol), 0.0) + shares * mark
    return account, pnl


def symbol_pnl_from_result(
    result: Mapping[str, Any],
    final_prices: Mapping[str, float],
) -> dict[str, float]:
    """Attribute total portfolio profit by transaction cash flow and final marks."""
    account, pnl = _accumulate_symbol_profit(
        final_prices=final_prices,
        result=result,
    )

    if "final_equity" in result and "initial_cash" in account:
        expected = float(result["final_equity"]) - float(account["initial_cash"])
        observed = sum(pnl.values())
        tolerance = max(1e-6, abs(expected) * 1e-10)
        if abs(observed - expected) > tolerance:
            raise ValueError(
                "symbol PnL does not reconcile to portfolio profit: "
                f"observed={observed:.8f}, expected={expected:.8f}"
            )
    return dict(sorted(pnl.items()))


def symbol_pnl_concentration(symbol_pnl: Mapping[str, float]) -> dict[str, float]:
    """Measure Top-1, Top-3, and HHI from exact absolute symbol PnL.

    Absolute contributions avoid signed cancellation.  A portfolio with no
    non-zero symbol PnL has no contribution concentration, represented by
    exact zeros rather than a fabricated or non-finite ratio.
    """
    if any(
        not isinstance(symbol, str) or not symbol or not math.isfinite(float(value))
        for symbol, value in symbol_pnl.items()
    ):
        raise ValueError("invalid symbol PnL for concentration")
    absolute = sorted((abs(float(value)) for value in symbol_pnl.values() if value != 0.0), reverse=True)
    denominator = sum(absolute)
    if denominator == 0.0:
        return {
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        }
    weights = [value / denominator for value in absolute]
    return {
        "top1_concentration": weights[0],
        "top3_concentration": sum(weights[:3]),
        "pnl_hhi": sum(weight * weight for weight in weights),
    }


def _deployment_from_result(result: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Extract every actually filled lifecycle, including strategic attribution."""
    explicit = result.get("deployed_exposure")
    if explicit is not None:
        if not isinstance(explicit, list):
            raise ValueError("scenario result deployed_exposure must be a list")
        deployed: set[tuple[str, str]] = set()
        for item in explicit:
            if not isinstance(item, Mapping) or set(item) != {"symbol", "lifecycle"}:
                raise ValueError("scenario result has invalid deployed_exposure item")
            symbol = item["symbol"]
            lifecycle = item["lifecycle"]
            if not isinstance(symbol, str) or not isinstance(lifecycle, str):
                raise ValueError("scenario result has invalid deployed_exposure item")
            deployed.add((symbol, lifecycle))
        return tuple(sorted(deployed))

    account = result.get("final_account")
    if not isinstance(account, Mapping):
        return ()
    fills = account.get("fills", [])
    if not isinstance(fills, list):
        raise ValueError("scenario result final_account.fills must be a list")
    deployed = set()
    for fill in fills:
        if not isinstance(fill, Mapping):
            raise ValueError("scenario result contains an invalid fill")
        side = fill.get("side")
        shares = fill.get("shares", 0)
        if side != "BUY" or isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
            continue
        symbol = fill.get("symbol")
        lifecycle = fill.get("lifecycle")
        if not isinstance(symbol, str) or not isinstance(lifecycle, str):
            raise ValueError("scenario result contains an invalid BUY deployment")
        deployed.add((symbol, lifecycle))
        if fill.get("reason_code") == "strategic_cohort":
            deployed.add((symbol, "STRATEGIC"))
    return tuple(sorted(deployed))


def observation_from_result(
    case: GeneralizationScenario,
    result: Mapping[str, Any],
    *,
    symbol_pnl: Mapping[str, float] | None = None,
) -> GeneralizationObservation:
    """Validate one engine/runner result and bind it to its scenario."""
    pnl_source = symbol_pnl if symbol_pnl is not None else result.get("symbol_pnl", {})
    if not isinstance(pnl_source, Mapping):
        raise ValueError(f"scenario result has invalid symbol_pnl: {case.name}")
    try:
        wealth = float(result["final_wealth"])
        drawdown = float(result["max_drawdown"])
        raw_orders = result["account_orders"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"scenario result is missing required metrics: {case.name}") from exc
    if isinstance(raw_orders, bool) or not isinstance(raw_orders, int):
        raise ValueError(f"scenario result has a non-integer order count: {case.name}")
    pnl_items = tuple(sorted((str(symbol), float(value)) for symbol, value in pnl_source.items()))
    unexpected_pnl = sorted({symbol for symbol, _ in pnl_items} - set(case.symbols))
    if unexpected_pnl:
        raise ValueError(f"scenario result attributes PnL outside its universe: {case.name} {unexpected_pnl}")
    deployed = _deployment_from_result(result)
    unexpected_deployment = sorted({symbol for symbol, _ in deployed} - set(case.symbols))
    if unexpected_deployment:
        raise ValueError(f"scenario result deploys outside its universe: {case.name} {unexpected_deployment}")
    return GeneralizationObservation(
        name=case.name,
        family=case.family,
        final_wealth=wealth,
        max_drawdown=drawdown,
        account_orders=raw_orders,
        symbol_pnl=pnl_items,
        deployed_exposure=deployed,
    )


def _quantile(values: Sequence[float], probability: float) -> float:
    return _linear_quantile(values, probability)


def aggregate_metrics(
    observations: Sequence[GeneralizationObservation],
) -> dict[str, float]:
    """Aggregate robust lower-tail wealth, upper-tail risk, and order burden."""
    if not observations:
        raise ValueError("generalization aggregation requires observations")
    wealth = [item.final_wealth for item in observations]
    drawdown = [item.max_drawdown for item in observations]
    orders = [float(item.account_orders) for item in observations]
    return {
        "p10_wealth": _quantile(wealth, 0.10),
        "median_wealth": float(median(wealth)),
        "p90_drawdown": _quantile(drawdown, 0.90),
        "worst_drawdown": max(drawdown),
        "median_orders": float(median(orders)),
        "p90_orders": _quantile(orders, 0.90),
    }


def prior_dependence(
    observations: Sequence[GeneralizationObservation],
) -> dict[str, float | str]:
    """Calculate PDI_1 and PDI_3 against the current full-universe wealth."""
    base = next((item for item in observations if item.family == "baseline"), None)
    remove_one = [item for item in observations if item.family == "remove_one"]
    remove_all = [item for item in observations if item.family == "remove_all"]
    if base is None or not remove_one or len(remove_all) != 1:
        raise ValueError("PDI requires one base, remove-one cases, and one remove-all case")
    weakest_one = min(remove_one, key=lambda item: (item.final_wealth, item.name))
    pdi_1 = max(0.0, 1.0 - weakest_one.final_wealth / base.final_wealth)
    pdi_3 = max(0.0, 1.0 - remove_all[0].final_wealth / base.final_wealth)
    return {
        "PDI_1": pdi_1,
        "PDI_3": pdi_3,
        "PDI_1_worst_case": weakest_one.name,
        "PDI_3_case": remove_all[0].name,
    }


def industry_pnl_shares(
    observation: GeneralizationObservation,
    industries: Mapping[str, str],
) -> dict[str, dict[str, float]]:
    """Return signed industry contribution as a share of net portfolio profit."""
    grouped: dict[str, float] = {}
    for symbol, value in observation.symbol_pnl:
        industry = industries.get(symbol, "unknown")
        grouped[industry] = grouped.get(industry, 0.0) + value
    net = sum(grouped.values())
    if abs(net) <= 1e-12:
        raise ValueError("industry PnL share is undefined when net PnL is zero")
    return {
        industry: {"pnl": pnl, "share_of_net_pnl": pnl / net} for industry, pnl in sorted(grouped.items())
    }


deployment_from_result = _deployment_from_result
quantile = _quantile
