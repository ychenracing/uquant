"""One-page daily report; no second decision path exists here."""

from __future__ import annotations

from .types import AccountState, Decision


def render_daily_report(decision: Decision, account: AccountState) -> str:
    """Render the already-computed decision without changing portfolio intent."""
    current = {symbol: position for symbol, position in account.positions.items() if position.shares > 0}
    lines = [
        f"# Daily Report — {decision.date}",
        "",
        f"Opportunity: **{decision.opportunity.value}**  ",
        f"Risk: **{decision.risk.value}**  ",
        f"Target Gross: **{decision.target_gross:.1%}**  ",
        f"Target K: **{decision.target_k}**",
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
            f"- Sector breadth declining: {decision.risk_summary.get('declining_ratio', 0.0):.1%}",
            f"- Below MA20: {decision.risk_summary.get('below_ma20_ratio', 0.0):.1%}",
            f"- Correlation: {decision.risk_summary.get('median_correlation', 0.0):.2f}",
            f"- Operating DD: {decision.risk_summary.get('operating_drawdown', 0.0):.1%}",
            f"- Capital DD: {decision.risk_summary.get('capital_drawdown', 0.0):.1%}",
            "",
            "## Tomorrow",
            "",
        ]
    )
    if not decision.pending_orders:
        lines.append("1. No executable account order; remain inside no-trade bands.")
    for index, order in enumerate(decision.pending_orders, start=1):
        lines.append(
            f"{index}. {order.side} {order.symbol} toward {order.target_weight:.1%} at the next tradable open; {order.reason}."
        )
    lines.extend(["", f"Decision digest: `{decision.decision_digest}`", ""])
    return "\n".join(lines)
