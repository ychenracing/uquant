"""One-page daily report; no second decision path exists here."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .attribution import validate_economic_attribution
from .execution_journal import JournalRecord, JournalStatus
from .types import AccountState, Decision


def render_execution_journal(records: tuple[JournalRecord, ...]) -> str:
    """Render observational execution events without deriving strategy intent."""

    lines = [
        "# Manual Execution Journal",
        "",
        "| Seq | Plan | Status | Symbol | Side | Planned | Next open | Actual | Shares | Slippage | Note |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    plans: dict[str, JournalRecord] = {}
    for item in records:
        if item.status is JournalStatus.PLANNED:
            plans[item.plan_id] = item
        plan = plans.get(item.plan_id)
        symbol = plan.symbol if plan is not None else None
        side = plan.side if plan is not None else None
        planned_price = plan.planned_price if plan is not None else None
        slippage = "" if item.slippage_bps is None else f"{item.slippage_bps:.4f} bps"
        lines.append(
            "| "
            + " | ".join(
                (
                    str(item.sequence),
                    item.plan_id,
                    item.status.value,
                    symbol or "",
                    side or "",
                    "" if planned_price is None else f"{planned_price:.4f}",
                    "" if item.next_open is None else f"{item.next_open:.4f}",
                    "" if item.actual_price is None else f"{item.actual_price:.4f}",
                    "" if item.actual_shares is None else str(item.actual_shares),
                    slippage,
                    item.manual_skip or "",
                )
            )
            + " |"
        )
    if not records:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)


def render_economic_attribution_report(attribution: Mapping[str, Any]) -> str:
    """Render reconciled accounting separately from explicitly diagnostic effects."""

    interval_value = attribution.get("interval")
    if not isinstance(interval_value, Mapping):
        raise ValueError("economic attribution report requires validated canonical evidence")
    canonical = validate_economic_attribution(
        attribution,
        economic_start=str(interval_value.get("economic_start")),
        economic_end=str(interval_value.get("economic_end")),
    )
    interval = canonical["interval"]
    accounting = canonical["accounting"]
    costs = canonical["costs"]
    concentration = canonical["symbol_concentration"]
    diagnostics = canonical["diagnostics"]
    positive = concentration["positive"]
    top1 = positive.get("top1")
    top3 = positive.get("top3")
    hhi = positive.get("hhi")

    def percentage(value: Any) -> str:
        return "N/A" if value is None else f"{float(value):.2%}"

    cash_drag = diagnostics["cash_drag"]
    avoidance = diagnostics["risk_avoidance"]
    avoidance_line = (
        f"Risk avoidance (paired counterfactual, not accounting PnL): {float(avoidance['value']):.6f}"
        if avoidance.get("status") == "PAIRED_COUNTERFACTUAL"
        else "Risk avoidance: N/A — requires an exact paired counterfactual"
    )
    industry_rows = [
        f"{name} | {float(bucket['total_pnl']):.6f}"
        for name, bucket in canonical["by_industry"].items()
    ] or ["N/A | 0.000000"]
    mechanism_rows = [
        f"{name} | {float(bucket['total_pnl']):.6f}"
        for name, bucket in canonical["by_mechanism"].items()
        if any(float(bucket[field]) != 0.0 for field in ("total_pnl", "all_in_costs"))
    ] or ["N/A | 0.000000"]
    exit_mechanism_rows = [
        f"{name} | {float(bucket['total_pnl']):.6f}"
        for name, bucket in canonical["by_exit_mechanism"].items()
        if any(float(bucket[field]) != 0.0 for field in ("total_pnl", "all_in_costs"))
    ] or ["N/A | 0.000000"]
    origin_lifecycle_rows = [
        f"{name} | {float(bucket['total_pnl']):.6f}"
        for name, bucket in canonical["by_origin_lifecycle"].items()
        if any(float(bucket[field]) != 0.0 for field in ("total_pnl", "all_in_costs"))
    ] or ["N/A | 0.000000"]
    current_lifecycle_rows = [
        f"{name} | {float(bucket['total_pnl']):.6f}"
        for name, bucket in canonical["by_current_lifecycle"].items()
        if any(float(bucket[field]) != 0.0 for field in ("total_pnl", "all_in_costs"))
    ] or ["N/A | 0.000000"]
    industry_hhi = canonical["industry_concentration"]["positive"].get("hhi")
    holding = canonical["holding_period_sessions"]
    turnover = canonical["turnover"]
    replacements = canonical["replacements"]
    lines = [
        "# Economic Attribution — "
        f"{interval['economic_start']} to {interval['economic_end']}",
        "",
        "## Reconciled Accounting PnL",
        "",
        "Reconciled: **"
        + ("YES" if accounting["reconciled"] else "NO")
        + f"** (error {float(accounting['reconciliation_error']):.6f}; "
        f"tolerance {float(accounting['tolerance']):.6f})",
        f"Realized PnL: {float(accounting['realized_pnl']):.6f}",
        f"Open PnL: {float(accounting['open_pnl']):.6f}",
        f"Total PnL: {float(accounting['total_pnl']):.6f}",
        "",
        "## Contribution Concentration",
        "",
        f"Top-1 positive contribution: {percentage(top1)}",
        f"Top-3 positive contribution: {percentage(top3)}",
        f"Positive-contribution HHI: {'N/A' if hhi is None else f'{float(hhi):.6f}'}",
        "",
        "## Industry-at-entry Contribution",
        "",
        "Industry | Total PnL",
        "--- | ---:",
        *industry_rows,
        "Positive industry HHI: "
        + ("N/A" if industry_hhi is None else f"{float(industry_hhi):.6f}"),
        "",
        "## Origin Mechanism Contribution",
        "",
        "Mechanism | Total PnL",
        "--- | ---:",
        *mechanism_rows,
        "",
        "Exit mechanism | Realized PnL",
        "--- | ---:",
        *exit_mechanism_rows,
        "",
        "## Lifecycle Contribution",
        "",
        "Origin lifecycle | Total PnL",
        "--- | ---:",
        *origin_lifecycle_rows,
        "",
        "Current lifecycle | Total PnL",
        "--- | ---:",
        *current_lifecycle_rows,
        "",
        "## Turnover, Holding, and Replacements",
        "",
        f"Gross turnover: {float(turnover['gross_turnover']):.6%}",
        "Share-weighted holding sessions: "
        + (
            "N/A"
            if holding["all"]["weighted_average"] is None
            else f"{float(holding['all']['weighted_average']):.6f}"
        ),
        f"Replacement-linked lot count: {int(replacements['linked_lot_count'])}",
        f"Replacement-linked total PnL: {float(replacements['total_pnl']):.6f}",
        "",
        "## Costs",
        "",
        f"Cash fees: {float(costs['cash_fees']):.6f}",
        f"Slippage: {float(costs['slippage']):.6f}",
        f"All-in cost: {float(costs['all_in']):.6f}",
        f"All-in cost drag / initial cash: {float(costs['all_in_cost_drag_initial_cash']):.6%}",
        "",
        "## Diagnostics — Not Accounting PnL",
        "",
        f"Cash drag (diagnostic, not accounting PnL): {float(cash_drag['value']):.6f}",
        avoidance_line,
        "",
    ]
    return "\n".join(lines)


def render_daily_report(decision: Decision, account: AccountState) -> str:
    """Render the already-computed decision without changing portfolio intent."""
    current = {symbol: position for symbol, position in account.positions.items() if position.shares > 0}
    sector_return = decision.risk_summary.get("sector_guard_equal_return")
    sector_return_text = "N/A" if sector_return is None else f"{float(sector_return):.1%}"
    lines = [
        f"# Daily Report — {decision.date}",
        "",
        f"Opportunity: **{decision.opportunity.value}**  ",
        f"Risk: **{decision.risk.value}**  ",
        f"Target Gross: **{decision.target_gross:.1%}**  ",
        f"Target K: **{decision.target_k}**",
        f"Factor Profile: **{decision.risk_summary.get('factor_profile', 'CHOPPY')}**  ",
        f"Strategic Epoch: **{decision.risk_summary.get('strategic_epoch', 0)}**",
        "",
        "## Targets",
        "",
        "| Symbol | Action | Target | Lifecycle | Reason |",
        "|---|---|---:|---|---|",
    ]
    for target in decision.targets:
        held = target.symbol in current
        if target.weight <= 0:
            action = "SELL" if held else "BLOCKED"
        elif held:
            action = "HOLD/ADJUST"
        else:
            action = "BUY"
        lines.append(
            f"| {target.symbol} | {action} | {target.weight:.1%} | {target.lifecycle} | {target.reason} |"
        )
    lines.extend(
        [
            "",
            "## Risk",
            "",
            f"- Shock: {decision.risk_summary.get('shock_state', 'NONE')}",
            "- Deployed-sector guard: "
            + (
                "ACTIVE"
                if decision.risk_summary.get(
                    "sector_guard_active",
                    account.sector_guard_active,
                )
                else "INACTIVE"
            ),
            f"- Deployed-sector daily return: {sector_return_text}",
            f"- Sector-shock confirmations: {decision.risk_summary.get('sector_guard_shock_count', len(account.sector_shock_dates))}",
            f"- Sector breadth declining: {decision.risk_summary.get('declining_ratio', 0.0):.1%}",
            f"- Below MA20: {decision.risk_summary.get('below_ma20_ratio', 0.0):.1%}",
            f"- Correlation: {decision.risk_summary.get('median_correlation', 0.0):.2f}",
            f"- Operating DD: {decision.risk_summary.get('operating_drawdown', 0.0):.1%}",
            f"- Capital DD: {decision.risk_summary.get('capital_drawdown', 0.0):.1%}",
            f"- Trend health: {decision.risk_summary.get('trend_health', 0.0):.1%}",
            f"- Transition damage: {decision.risk_summary.get('transition_damage', 0.0):.1%}",
            "- Freeze new risk: " + ("YES" if decision.risk_summary.get("freeze_new_risk") else "NO"),
            f"- Reduction level: {decision.risk_summary.get('reduction_level', 0)}",
            f"- Severity: {decision.risk_summary.get('severity', 'NORMAL')}",
            f"- Capital budget rung: {decision.risk_summary.get('capital_budget_level', 0)}",
            f"- Chronic deterioration: {decision.risk_summary.get('chronic_level', 0)}",
            "- Dynamic anchors: " + ", ".join(decision.risk_summary.get("risk_anchor_symbols", [])),
            "",
            "## Tomorrow",
            "",
        ]
    )
    if not decision.pending_orders:
        lines.append("1. No executable account order; remain inside no-trade bands.")
    for index, order in enumerate(decision.pending_orders, start=1):
        lines.append(
            f"{index}. {order.side} {order.symbol} toward {order.target_weight:.1%} "
            f"at the next tradable open; {order.reason} "
            f"[{order.reason_code}/{order.exit_kind}/{order.reduction_policy}]."
        )
    lines.extend(
        [
            "",
            f"Decision digest: `{decision.decision_digest}`",
            "Effective config: "
            f"`{decision.risk_summary.get('effective_config_sha256', 'UNAVAILABLE')}`",
            "",
        ]
    )
    return "\n".join(lines)
