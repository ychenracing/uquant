"""Stable Markdown rendering for execution-journal records."""

from __future__ import annotations

from .models import JournalRecord, JournalStatus


def render_compact_execution_journal(records: tuple[JournalRecord, ...]) -> str:
    """Render the frozen compact production-report journal format."""

    lines = [
        "# Manual Execution Journal",
        "",
        "| Seq | Plan | Status | Symbol | Side | Planned | Next open | Actual | Shares | Slippage | Note |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    plans: dict[str, JournalRecord] = {}

    def markdown_cell(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r\n", "<br>")
            .replace("\n", "<br>")
            .replace("\r", "<br>")
        )

    for item in records:
        if item.status.value == JournalStatus.PLANNED.value:
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
                    markdown_cell(item.manual_skip or ""),
                )
            )
            + " |"
        )
    if not records:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)


def render_execution_journal(records: tuple[JournalRecord, ...]) -> str:
    """Render v1/v2 observational events without deriving strategy intent."""

    lines = [
        "# Future Holdout Manual Execution Journal",
        "",
        "| Seq | Decision | Plan | Status | Symbol | Side | Weight | Planned | Next open | Actual | Shares | Slippage | Broker/Note |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    plans: dict[str, JournalRecord] = {}

    def markdown_cell(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r\n", "<br>")
            .replace("\n", "<br>")
            .replace("\r", "<br>")
        )

    for item in records:
        if item.status.value == JournalStatus.PLANNED.value:
            plans[item.plan_id] = item
        plan = plans.get(item.plan_id)
        slippage = "" if item.slippage_bps is None else f"{item.slippage_bps:.4f} bps"
        note = item.manual_skip or item.broker_order_id or ""
        lines.append(
            "| "
            + " | ".join(
                (
                    str(item.sequence),
                    item.decision_date,
                    item.plan_id,
                    item.status.value,
                    "" if plan is None or plan.symbol is None else plan.symbol,
                    "" if plan is None or plan.side is None else plan.side,
                    "" if plan is None or plan.planned_weight is None else f"{plan.planned_weight:.4f}",
                    "" if plan is None or plan.planned_price is None else f"{plan.planned_price:.4f}",
                    "" if item.next_open is None else f"{item.next_open:.4f}",
                    "" if item.actual_price is None else f"{item.actual_price:.4f}",
                    "" if item.actual_shares is None else str(item.actual_shares),
                    slippage,
                    markdown_cell(note),
                )
            )
            + " |"
        )
    if not records:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)
