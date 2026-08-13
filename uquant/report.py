"""One-page daily report; no second decision path exists here."""

from __future__ import annotations

from .types import AccountState, Decision


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
