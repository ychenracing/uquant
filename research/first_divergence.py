"""Causal production-backtest observations for offline divergence research."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

import pandas as pd

from uquant.engine import ProductionEngine
from uquant.types import AccountState, Decision, Fill

_ECONOMIC_FIELDS = (
    "opportunity",
    "risk",
    "targets",
    "strategic_targets",
    "target_gross",
    "actual_gross",
    "new_fills",
    "pending_orders",
)


def _trace_row(
    *,
    engine: ProductionEngine,
    decision: Decision,
    account: AccountState,
    new_fills: Sequence[Fill],
) -> dict[str, Any]:
    """Capture state already produced by the sole production decision path."""
    risk_summary = decision.risk_summary
    raw_family_votes = risk_summary.get("family_votes", {})
    family_votes = (
        {str(name): bool(active) for name, active in sorted(raw_family_votes.items())}
        if isinstance(raw_family_votes, Mapping)
        else {}
    )
    raw_leaders = risk_summary.get("leader_ranking", ())
    ranked_leaders = tuple(
        dict(item) for item in raw_leaders if isinstance(item, Mapping)
    ) if isinstance(raw_leaders, Sequence) else ()
    equity = engine.equity(account, pd.Timestamp(decision.date))
    actual_gross = (equity - account.cash) / equity if equity > 1e-12 else 0.0
    risk_evidence = {
        str(name): value
        for name, value in sorted(risk_summary.items())
        if name != "leader_ranking"
    }

    return {
        "date": decision.date,
        "opportunity": decision.opportunity.value,
        "risk": decision.risk.value,
        "family_votes": family_votes,
        "sector_guard_active": bool(risk_summary.get("sector_guard_active", False)),
        "capital_damage": bool(family_votes.get("capital_damage", False)),
        "capital_drawdown": float(risk_summary.get("capital_drawdown", 0.0)),
        "capital_budget_level": int(risk_summary.get("capital_budget_level", 0)),
        "reference_evidence": {
            name: value for name, value in risk_evidence.items() if name.startswith("reference_")
        },
        "risk_evidence": risk_evidence,
        "ranked_leaders": ranked_leaders,
        "strategic_targets": dict(sorted(account.strategic_cohort_targets.items())),
        "targets": tuple(asdict(target) for target in decision.targets),
        "target_gross": decision.target_gross,
        "actual_gross": actual_gross,
        "equity": equity,
        "new_fills": tuple(asdict(fill) for fill in new_fills),
        "pending_orders": tuple(asdict(order) for order in decision.pending_orders),
    }


def trace_backtest(
    engine: ProductionEngine,
    *,
    symbols: Iterable[str],
    start: str,
    end: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Run ``engine.backtest`` unchanged while observing its real daily decisions."""
    traces: list[dict[str, Any]] = []
    fill_cursor = 0
    original_decide = engine.decide

    def observed_decide(
        *,
        symbols: Iterable[str],
        as_of: str,
        account: AccountState,
    ) -> Decision:
        """Delegate to production and append the resulting economic trace row."""

        nonlocal fill_cursor
        new_fills = tuple(account.fills[fill_cursor:])
        fill_cursor = len(account.fills)
        decision = original_decide(symbols=symbols, as_of=as_of, account=account)
        traces.append(
            _trace_row(
                engine=engine,
                decision=decision,
                account=account,
                new_fills=new_fills,
            )
        )
        return decision

    try:
        object.__setattr__(engine, "decide", observed_decide)
        result = engine.backtest(symbols=symbols, start=start, end=end)
    finally:
        object.__setattr__(engine, "decide", original_decide)
    return result, tuple(traces)


def first_economic_divergence(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the first aligned executable decision change, excluding outcome metrics."""
    left_dates = tuple(str(item.get("date", "")) for item in left)
    right_dates = tuple(str(item.get("date", "")) for item in right)
    if left_dates != right_dates:
        raise ValueError("decision traces require aligned dates")
    if not all(left_dates) or left_dates != tuple(sorted(left_dates)):
        raise ValueError("decision traces require non-empty sorted dates")
    for left_item, right_item in zip(left, right, strict=True):
        changed_fields = tuple(
            field for field in _ECONOMIC_FIELDS if left_item.get(field) != right_item.get(field)
        )
        if changed_fields:
            return {
                "date": left_item["date"],
                "changed_fields": changed_fields,
                "left": dict(left_item),
                "right": dict(right_item),
            }
    return None
