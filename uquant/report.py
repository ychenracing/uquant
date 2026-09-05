"""One-page daily report; no second decision path exists here."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType as _MappingProxyType
from typing import Any

from .attribution import validate_economic_attribution
from .observation.execution_journal import JournalRecord
from .observation.execution_journal.rendering import (
    render_compact_execution_journal,
)
from .types import AccountState, Decision


def _sentinel_values(value: object, *, limit: int = 3) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value[:limit] if str(item))


def _risk_sentinel_section(summary: Mapping[str, Any]) -> list[str]:
    coverage = str(summary.get("sentinel_causal_coverage_status", "NOT_READY"))
    base_freeze = bool(summary.get("base_freeze_new_risk", summary.get("freeze_new_risk", False)))
    sentinel_freeze = bool(summary.get("sentinel_freeze_new_risk", False))
    if coverage != "READY":
        owner = "DATA_NOT_READY"
    elif base_freeze and sentinel_freeze:
        owner = "BOTH"
    elif base_freeze:
        owner = "BASE_RISK"
    elif sentinel_freeze:
        owner = "SENTINEL"
    else:
        owner = "NONE"
    freeze = bool(summary.get("freeze_new_risk", base_freeze or sentinel_freeze))
    assessment = summary.get("sentinel_assessment")
    observed = summary.get("sentinel_causal_observed_level", "NOT_READY")
    if isinstance(assessment, Mapping):
        observed = assessment.get("level", observed)
    families = _sentinel_values(summary.get("sentinel_causal_active_families"))
    weakest = _sentinel_values(summary.get("sentinel_causal_weakest_subindustries"))
    if owner == "DATA_NOT_READY":
        conclusion = "check market data; do not infer safety."
    elif freeze:
        conclusion = "do not add new risk."
    else:
        conclusion = "normal execution; Sentinel remains observational."
    return [
        "## Risk Sentinel",
        "",
        f"- Mode: {summary.get('sentinel_mode', 'FREEZE_ONLY')}",
        f"- Level: {observed}",
        f"- Coverage: {coverage}",
        f"- Confidence: {float(summary.get('sentinel_causal_confidence', 0.0)):.1%}",
        f"- Owner: **{owner}**",
        f"- Risk Families: {', '.join(families) or 'NONE'}",
        f"- AI Industry Risk: {', '.join(weakest) or 'NONE'}",
        f"- Conclusion: {conclusion}",
        "",
    ]


def render_execution_journal(records: tuple[JournalRecord, ...]) -> str:
    """Render observational execution events without deriving strategy intent."""

    return render_compact_execution_journal(records)


def _account_report_section(account: AccountState, summary: Mapping[str, Any]) -> list[str]:
    """Display settled account facts without estimating executable capital."""
    risk_cap = summary.get("target_gross_cap")
    system_cap = summary.get("system_gross_cap")
    risk_cap_text = "UNAVAILABLE" if risk_cap is None else f"{float(risk_cap):.1%}"
    system_cap_text = "UNAVAILABLE" if system_cap is None else f"{float(system_cap):.1%}"
    cancellations = [
        f"{order.order_id} ({order.symbol})"
        for order in account.order_ledger
        if order.side == "BUY" and order.status == "CANCEL_REQUESTED"
    ]
    reasons = summary.get("reasons")
    reasons_text = "; ".join(str(reason) for reason in reasons) if isinstance(reasons, (list, tuple)) else ""
    lines = [
        "## Holdings and capital",
        "",
        f"- Recorded cash: {account.cash:,.2f}; Broker snapshot: {account.broker_as_of or 'UNAVAILABLE'}.",
        f"- Risk gross cap: {risk_cap_text}; system gross cap: {system_cap_text}.",
        f"- Recorded risk reasons: {reasons_text or 'NONE RECORDED'}.",
        "- Cash is not unreserved buying power; pending BUY commitments reserve budget, "
        "and unfilled SELL proceeds are not cash.",
        "- BUY cancellations awaiting broker confirmation: " + (", ".join(cancellations) or "NONE") + ".",
        "",
        "| Symbol | Held shares | Average cost | Lifecycle |",
        "|---|---:|---:|---|",
    ]
    held = [position for _, position in sorted(account.positions.items()) if position.shares > 0]
    lines.extend(
        f"| {position.symbol} | {position.shares} | {position.avg_cost:.2f} | {position.lifecycle} |"
        for position in held
    )
    if not held:
        lines.extend(["", "No held shares recorded."])
    lines.extend(["", "Holdings are account facts; target weights below are intentions.", ""])
    return lines


_CORE_REVIEW_CHANGES = _MappingProxyType({
    "NOT_MATURE": "Leadership must become mature.",
    "CONFIRMATION_INCOMPLETE": "Complete the recorded consecutive-session confirmation.",
    "CONFIDENCE_BELOW_MINIMUM": "Leadership confidence must meet its threshold.",
    "INDUSTRY_NOT_VERIFIED": "Verify industry evidence.",
    "CURRENT_MARKET_DATA_UNAVAILABLE": "Restore current market data.",
    "INSUFFICIENT_HISTORY": "Accumulate the required causal history.",
    "STRUCTURE_NOT_REPAIRED": "Price structure must recover.",
    "LIQUIDITY_NOT_CONFIRMED": "Liquidity must meet its existing threshold.",
    "NEW_RISK_FROZEN": "The recorded risk freeze must clear.",
    "UNRESOLVED_LIABILITY": "Reconcile outstanding physical orders and fills.",
    "OPPORTUNITY_NOT_OPEN": "The opportunity state must permit core entry.",
    "RISK_NOT_NORMAL": "Base Risk must return to NORMAL for new core entry.",
    "EXISTING_HOLDING_OR_COMMITMENT": "Manage the existing holding or order through its own lifecycle.",
    "AWAIT_REDUCTION_SETTLEMENT": "The prior reduction must actually settle.",
    "CAPITAL_LIMIT": "Available settled capital or a binding cap must change; a sale intent supplies no cash.",
    "RESTORATION_COMPLETED_RETAIN_DRIFT": "Retain price drift until independent deterioration or a risk reduction.",
    "RESTORATION_EVIDENCE_UNAVAILABLE": "Restore the holding's market and leadership evidence.",
    "RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING": "The current uninterrupted holding must span the recorded risk episode; verify actual fills.",
    "OWNER_EVIDENCE_UNAVAILABLE": "Restore the current owner's market evidence.",
    "OWNER_DEPLOYMENT_BLOCK": "The current owner's recorded deployment block must clear.",
    "CANCELLATION_AWAITING_CONFIRMATION": "Obtain the pending cancellation's acknowledgement.",
    "NO_TRADE_BAND": "The executable difference must cross the recorded trade threshold.",
})


def _recorded_budget_limits(row: Mapping[str, Any]) -> list[str]:
    parts = []
    budgets = row.get("budget_checks")
    if isinstance(budgets, list) and budgets:
        latest = budgets[-1]
        limits = [(label, latest[key]) for label, key in (
            ("cash", "cash_room"), ("gross", "gross_room"), ("name", "symbol_room"),
            ("industry", "industry_room"), ("correlation", "correlation_room"),
        ) if key in latest]
        parts.append("room: " + ", ".join(f"{label} {float(value):.1%}" for label, value in limits))
        parts.append(f"funded increment {float(latest.get('funded_increment', 0.0)):.1%}; "
                     f"minimum {float(latest.get('minimum_increment', 0.0)):.1%}")
        if latest.get("block") or latest.get("correlation_block"):
            parts.append(str(latest.get("block") or latest["correlation_block"]))
    else:
        parts.append("capital room not evaluated on this branch")
    transfer = row.get("transfer_budget")
    if isinstance(transfer, Mapping) and transfer.get("block"):
        estimate = [str(transfer["block"])]
        estimate.extend(f"{label} {float(transfer[key]):.1%}" for label, key in (
            ("released weight", "released_weight"), ("required admission", "required_weight"),
            ("fundable increment", "funded_increment"), ("cash room", "cash_room"),
            ("gross room", "gross_room"), ("name room", "symbol_room"),
            ("industry room", "industry_room"), ("correlation room", "correlation_room"),
        ) if key in transfer)
        if transfer.get("correlation_block"):
            estimate.append(str(transfer["correlation_block"]))
        parts.append("transfer feasibility after settlement (estimate; not cash or a fill): "
                     + ", ".join(estimate))
    return parts


def _recorded_core_constraint(row: Mapping[str, Any], trace: Mapping[str, Any]) -> str:
    entry = row.get("pending_entry", row.get("entry", {}))
    entry_block = entry.get("block", "") if isinstance(entry, Mapping) else ""
    restore_block = row.get("restore_block")
    if restore_block == "PENDING_CORE_BUY_ALREADY_EVALUATED":
        restore_block = None
    block = row.get("increase_block") or restore_block or row.get("entry_gate")
    if not block and entry_block != "READY":
        block = entry_block
    if row.get("allocation_reason") == "CAPITAL_LIMIT":
        block = "CAPITAL_LIMIT"
    if trace.get("planning_scope") == "SENTINEL_PLANNING_ONLY" and trace.get("final_freeze_new_risk"):
        block = "NEW_RISK_FROZEN"
    parts = [str(block)] if block else []
    if isinstance(row.get("pending_entry"), Mapping):
        parts.append("pending current quality " + str(entry_block))
    confirmations = entry.get("confirmations") if isinstance(entry, Mapping) else None
    if isinstance(confirmations, Mapping):
        parts.append("confirmation " + ", ".join(f"{route} {streak}/{entry['required_confirmation']}"
                                                 for route, streak in confirmations.items()))
    parts.extend(_recorded_budget_limits(row))
    planning = row.get("order_planning", {})
    if isinstance(planning, Mapping) and planning.get("block") not in {None, "NONE", "NO_TARGET"}:
        parts.append("order: " + str(planning["block"]))
        if "difference_value" in planning:
            parts.append(f"difference {float(planning['difference_value']):,.2f}; "
                         f"standard threshold {float(planning['standard_trade_threshold']):,.2f}")
        if not block:
            block = str(planning["block"])
    parts.append(_CORE_REVIEW_CHANGES.get(str(block),
        "Reassess after independent deterioration, changed risk, qualification, or settled capital."))
    return "; ".join(parts)


def _core_allocation_report(trace: Mapping[str, Any]) -> list[str]:
    rows = trace["symbols"]
    lines = [
        f"- Allocation observed: {trace['as_of']}; final risk freeze: {bool(trace['final_freeze_new_risk'])}.",
        "- Each room value is recorded where that intent was evaluated; rows share one sequential budget.",
        "", "| Symbol | Held → final target | Recorded orders / final reason | Constraint / next review |",
        "|---|---:|---|---|",
    ]
    for symbol, row in sorted(rows.items(), key=lambda item: (-item[1].get("rank_score", -1.0), item[0])):
        orders = ", ".join(f"{order['side']} {order['order_id']}" for order in row.get("orders", ())) or "NO ORDER"
        reason = str(row.get("final_target_reason", "UNAVAILABLE")).replace("|", "/")
        constraint = _recorded_core_constraint(row, trace).replace("|", "/")
        lines.append(f"| {symbol} | {float(row['held_weight']):.1%} → {float(row['final_target_weight']):.1%} "
                     f"| {orders}; {reason} | {constraint} |")
    return [*lines, ""]


def _recorded_qualification_report(observation: object) -> list[str]:
    lines: list[str] = []
    if isinstance(observation, Mapping) and observation:
        symbol = observation.get("candidate_symbol") or "UNASSIGNED"
        ready = observation.get("qualification_ready")
        ready_text = "UNAVAILABLE" if ready is None else ("YES" if ready else "NO")
        block = observation.get("deployment_block_reason") or "NONE RECORDED"
        lines.extend([
            f"- Candidate observation: {symbol}; route: {observation.get('qualification_route') or 'UNAVAILABLE'}; "
            f"ready: {ready_text}.",
            "- Observed session: "
            f"{observation.get('qualification_last_observed_session') or 'UNAVAILABLE'}; "
            f"confirmation streak: {observation.get('qualification_streak', 'UNAVAILABLE')}.",
            f"- Deployment block for {symbol}: {block}.",
        ])
        if observation.get("candidate_invalidation_reason"):
            lines.append(f"- Recorded invalidation: {observation['candidate_invalidation_reason']}.")
        references = observation.get("unavailable_reference_symbols")
        if isinstance(references, (list, tuple)) and references:
            lines.append("- Unavailable references: " + ", ".join(str(item) for item in references) + ".")
    else:
        lines.append("- Candidate qualification evidence: NOT RECORDED.")
    return lines


def _candidate_report_section(decision: Decision) -> list[str]:
    """Show recorded qualification evidence, never reconstruct admission rules."""
    lines = ["## Candidate explanation", ""]
    lines.extend(_recorded_qualification_report(decision.risk_summary.get("strategic_qualification")))
    ranking = decision.risk_summary.get("leader_ranking")
    if isinstance(ranking, (list, tuple)):
        buying = {order.symbol for order in decision.pending_orders if order.side == "BUY"}
        waiting = [
            str(item["symbol"])
            for item in ranking
            if isinstance(item, Mapping) and item.get("symbol") and item["symbol"] not in buying
        ]
        lines.append("- Ranked symbols without a BUY intent: " + (", ".join(waiting) or "NONE") + ".")
    else:
        lines.append("- Candidate ranking: NOT RECORDED.")
    trace = decision.risk_summary.get("core_allocation")
    if isinstance(trace, Mapping) and trace.get("scope") == "FINAL_DECISION":
        lines.extend(_core_allocation_report(trace))
    else:
        lines.append("- Per-candidate admission reasons and remaining capital limits are not recorded for every ranked "
                     "symbol. Ranking alone does not establish qualification.")
    lines.extend([
        "- Next review: changes in recorded risk/freeze/caps, qualification/reference coverage, or reconciled "
        "fills, cancellations and cash. Only a new daily Decision can change targets; "
        "a cleared block alone does not authorize a BUY.",
        "",
    ])
    return lines


def _economic_attribution_report_lines(
    *,
    accounting: Any,
    avoidance_line: Any,
    cash_drag: Any,
    costs: Any,
    current_lifecycle_rows: Any,
    exit_mechanism_rows: Any,
    hhi: Any,
    holding: Any,
    industry_hhi: Any,
    industry_rows: Any,
    interval: Any,
    mechanism_rows: Any,
    origin_lifecycle_rows: Any,
    percentage: Any,
    replacements: Any,
    top1: Any,
    top3: Any,
    turnover: Any,
) -> Any:
    lines = [
        f"# Economic Attribution — {interval['economic_start']} to {interval['economic_end']}",
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
        "Positive industry HHI: " + ("N/A" if industry_hhi is None else f"{float(industry_hhi):.6f}"),
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
    return lines


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
        f"{name} | {float(bucket['total_pnl']):.6f}" for name, bucket in canonical["by_industry"].items()
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
    lines = _economic_attribution_report_lines(
        accounting=accounting,
        avoidance_line=avoidance_line,
        cash_drag=cash_drag,
        costs=costs,
        current_lifecycle_rows=current_lifecycle_rows,
        exit_mechanism_rows=exit_mechanism_rows,
        hhi=hhi,
        holding=holding,
        industry_hhi=industry_hhi,
        industry_rows=industry_rows,
        interval=interval,
        mechanism_rows=mechanism_rows,
        origin_lifecycle_rows=origin_lifecycle_rows,
        percentage=percentage,
        replacements=replacements,
        top1=top1,
        top3=top3,
        turnover=turnover,
    )
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
        *_account_report_section(account, decision.risk_summary),
        *_risk_sentinel_section(decision.risk_summary),
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
            *_candidate_report_section(decision),
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
        lines.append("1. No executable account order was recorded; no execution cause is inferred.")
    for index, order in enumerate(decision.pending_orders, start=1):
        lines.append(
            f"{index}. {order.side} {order.symbol} toward {order.target_weight:.1%} "
            f"at the next tradable open; {order.reason} "
            f"[{order.reason_code}/{order.exit_kind}/{order.reduction_policy}]; order_id={order.order_id or 'UNAVAILABLE'}."
        )
    lines.extend(
        [
            "",
            f"Decision digest: `{decision.decision_digest}`",
            f"Effective config: `{decision.risk_summary.get('effective_config_sha256', 'UNAVAILABLE')}`",
            "",
        ]
    )
    return "\n".join(lines)
