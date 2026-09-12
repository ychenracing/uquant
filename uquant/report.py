"""Read-only Chinese daily reports and accounting attribution."""

# Chinese punctuation is intentional in user-facing report text.
# ruff: noqa: RUF001

from __future__ import annotations

import html
import json
import math
from collections.abc import Mapping
from dataclasses import asdict
from types import MappingProxyType
from typing import Any

from .attribution import validate_economic_attribution
from .observation.execution_journal import JournalRecord
from .observation.execution_journal.rendering import (
    render_compact_execution_journal,
)
from .types import AccountState, Decision


def render_execution_journal(records: tuple[JournalRecord, ...]) -> str:
    """Render observational execution events without deriving strategy intent."""
    return render_compact_execution_journal(records)


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


_BLOCK_TEXT = MappingProxyType({
    "READY": "已满足本项候选条件；仍需通过风险、资金和执行检查",
    "NOT_MATURE": "龙头成熟条件未满足",
    "CONFIRMATION_INCOMPLETE": "连续交易日确认尚未完成",
    "CONFIDENCE_BELOW_MINIMUM": "证据可信程度未达到要求",
    "INDUSTRY_NOT_VERIFIED": "行业归属尚未核实",
    "CURRENT_MARKET_DATA_UNAVAILABLE": "缺少当日行情",
    "INSUFFICIENT_HISTORY": "历史行情长度不足",
    "STRUCTURE_NOT_REPAIRED": "价格结构尚未恢复",
    "LIQUIDITY_NOT_CONFIRMED": "成交活跃程度尚未满足要求",
    "NEW_RISK_FROZEN": "系统暂不允许增加持仓",
    "freeze_new_risk": "系统暂不允许增加持仓",
    "UNRESOLVED_LIABILITY": "尚有订单或成交责任需要核对",
    "OPPORTUNITY_NOT_OPEN": "当前市场机会状态不允许这条建仓路径",
    "RISK_NOT_NORMAL": "当前基础风险状态不允许普通建仓",
    "COMMON_TREND_NOT_CONFIRMED": "共同趋势证据尚未确认",
    "CASH_REPAIR_PERMISSION_CLOSED": "本次账户恢复买入权限未开放",
    "PULLBACK_PERMISSION_CLOSED": "原回调买入的证据或完整预算不再满足要求",
    "PULLBACK_NOT_GRADUATED": "该持仓尚未进入成熟管理，不能恢复加仓",
    "BOUNDED_PULLBACK_AUTHORIZED": "本次仅允许已记录的有限回调买入",
    "EXISTING_HOLDING_OR_COMMITMENT": "已有持仓或买入承诺，按原持仓路径处理",
    "ORDINARY_CORE_NOT_MATURE": "普通建仓所需的成熟条件不足",
    "IMMATURE_CORE_SLOT_OCCUPIED": "未成熟持仓名额已占用，需成熟或实际退出后重评",
    "IMMATURE_CORE_LOWER_RANK": "本次未成熟候选顺序靠后，未选中",
    "AWAIT_REDUCTION_SETTLEMENT": "等待先前减仓实际完成",
    "CAPITAL_LIMIT": "可部署资金或仓位额度不足；未成交卖单不提供资金",
    "opportunity_not_deployable": "当前机会状态不允许部署这条长期候选路径",
    "qualification_not_ready": "长期候选资格尚未完成确认",
    "qualification_invalid": "原候选资格已失效，不能继续部署",
    "reference_coverage_or_confirmation": "参考证据覆盖或连续确认条件不足",
    "candidate_not_tradable": "该候选不在当前允许交易范围",
    "ordinary_trend_participation": "当前只开放原有普通趋势参与路径",
    "strategic_market_opportunity_required": "长期主线建仓还需对应市场机会确认",
    "risk_caution": "基础风险警戒限制本次长期建仓",
    "target_gross_cap": "本次风险允许的仓位额度为零",
    "risk_off": "基础风险要求降低持仓，不允许本次新增",
    "crisis": "严重风险限制本次新增",
    "capital_budget": "账户回撤预算限制本次新增",
    "chronic_damage": "持续转弱限制本次新增",
    "POSITION_COUNT_LIMIT": "剩余持仓名额不足，资格要求保持不变",
    "MISSING_BOOK_EVIDENCE": "缺少已有持仓的行情或分类资料",
    "INSUFFICIENT_CORRELATION_HISTORY": "共同波动检查所需历史不足",
    "NONFINITE_CORRELATION": "共同波动检查结果无效",
    "RESTORATION_COMPLETED_RETAIN_DRIFT": "恢复已完成，保留价格变化产生的仓位漂移",
    "RESTORATION_EVIDENCE_UNAVAILABLE": "缺少恢复持仓所需的行情或龙头证据",
    "RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING": "该持仓与本次风险恢复记录未建立有效关联",
    "OWNER_EVIDENCE_UNAVAILABLE": "缺少主导持仓的行情证据",
    "OWNER_DEPLOYMENT_BLOCK": "主导持仓的新增部署被限制",
    "CANCELLATION_AWAITING_CONFIRMATION": "买单撤销尚未收到确认，暂不能重复买入",
    "NO_TRADE_BAND": "目标与持仓的差额未达到本次交易门槛",
    "STICKY_HOLD": "保留原持仓，不因价格漂移单独调仓",
    "NO_TARGET": "没有记录目标，不据此推断买卖许可",
    "NONE": "本项检查未记录阻断",
    "PENDING_CORE_BUY_ALREADY_EVALUATED": "原买单已在延续路径评估，不重复分配",
    "FAILED_DEPLOYMENT_UNSETTLED": "先前部署仍未结清，暂缓新建仓",
    "ACCOUNT_REPAIR_AUTHORIZED": "本次有限账户恢复买入已获得原规则授权",
    "candidate_identity_already_bound": "候选已经绑定持仓或未完成订单",
    "unresolved_execution_capacity": "尚有未核清的执行责任或持仓行情",
    "insufficient_executable_capital": "实际可部署资金不足",
    "TRANSFER_BELOW_TRADE_MINIMUM": "拟转出金额未达到原有最小交易要求",
    "TRANSFER_CANNOT_FUND_ADMISSION": "即使拟减仓完成，记录的可部署预算仍不足",
    "TRANSFER_SETTLED_AWAIT_ADMISSION": "先前减仓已完成，等待独立建仓检查",
    "FEASIBLE_AFTER_SETTLEMENT": "结算后预算估算可行；目前不是可用现金或买入授权",
})

_EXECUTION_TEXT = MappingProxyType({
    "MISSING_OR_SUSPENDED": "执行当日行情缺失或停牌，未能成交",
    "INSUFFICIENT_HISTORY": "执行检查缺少所需历史行情",
    "LIMIT_BLOCKED": "执行当日涨跌停限制阻止成交",
    "POSITION_CAP_BLOCKED": "执行时持仓数量已达上限",
    "CAPACITY_OR_CASH_BLOCKED": "执行时可成交股数为零，需核对成交量、现金与可卖股份",
    "AWAITING_HANDOFF_SELL": "等待前置卖出实际完成",
    "WAITING_NEXT_OPEN": "等待原信号之后的可交易开盘",
    "CANCEL_REQUESTED": "已请求撤销，尚未确认取消",
    "FILL": "已记录成交，剩余部分仍需核对",
    "ZERO_REQUEST": "执行检查确认目标已满足，未继续建单",
    "PENDING": "订单意图待执行", "PARTIAL": "部分成交，余量未结清",
    "PARTIALLY_FILLED": "部分成交，余量未结清",
})

_TARGET_REASON_TEXT = MappingProxyType({
    "confirmed core admitted from available account capital": "候选确认已通过，使用本次实际可用资金建仓",
    "confirmed core admitted through bounded account repair": "候选确认已通过，本次有限账户恢复权限允许建仓",
    "leader lifecycle exit: confirmed structural deterioration": "持仓价格结构持续转弱并完成确认，触发退出",
    "prequalified strategic leader cohort with staged profit protection": "长期龙头组合已完成资格确认，按分步保护规则管理",
    "strategic one-shot profit lock": "长期主导持仓触发一次性盈利保护，减少部分仓位",
    "retained core holding": "保留原有核心持仓；不表示本次重新获得买入许可",
    "mature anchored leader": "保留已确认的恢复龙头，不因价格漂移调仓",
    "causal crash-recovery leader": "已记录下跌后的恢复条件，按恢复持仓规则处理",
    "core restoration after account risk repair": "账户风险恢复后，在原有资金限制内恢复核心仓位",
    "leader rotation after the prior reduction filled": "先前减仓已实际完成，本次按原有顺序转入候选",
    "leader rotation: bounded transfer after confirmed deterioration": "原持仓转弱已确认，先有限减仓；卖出未成交前不预支资金",
    "controlled oversold rebound probe": "超跌反弹条件已触发，只参与原有的有限试探仓位",
    "controlled rebound probe": "已记录有限反弹参与条件，按试探仓位处理",
    "controlled rebound exit": "反弹持仓触发原有退出条件",
    "overextended pullback cooldown": "回调前涨幅过大，进入观察等待期",
    "awaiting recovery cohort member confirmation": "恢复组合成员尚未完成连续确认，继续等待",
    "graduated recovery cohort; retain price drift": "恢复组合已转入成熟管理，保留价格漂移",
    "confirmed recovery anchor substitution": "恢复持仓替换条件已确认，按原有顺序处理",
    "strategic cohort completed staged exit": "长期组合已完成分步退出",
    "completed post-shock restoration; retain price drift": "冲击后仓位恢复已完成，不因价格漂移调仓",
    "post-shock restoration; retain winner drift": "恢复持仓保留趋势收益造成的仓位漂移",
    "ordinary long-pullback confirmed MA120 deterioration": "长期回调持仓相对120个交易日平均价格持续转弱并完成确认",
    "ordinary long-pullback disaster loss against actual average cost": "长期回调持仓相对真实平均成本触发严重亏损保护",
})


def _target_reason(reason: str, code: str) -> str:
    if reason in _TARGET_REASON_TEXT:
        return _TARGET_REASON_TEXT[reason]
    if reason.endswith("; level-1 risk freeze; retain existing exposure"):
        return "一级风险限制冻结新增仓位，按现有持有边界保留敞口；不等于强制减仓"
    return _plain_code(code, reason=True)


_REASON_TEXT = MappingProxyType({
    "strategy_target": "按本次组合目标处理，具体条件见下方记录",
    "rotation": "根据已确认的相对强弱变化调整持仓",
    "strategic_cohort": "按已确认的长期龙头持仓规则处理",
    "strategic_tail": "长期持仓触发退出保护",
    "recovery_cohort": "按风险冲击后的恢复规则处理",
    "recovery_exit": "恢复持仓触发退出规则",
    "lifecycle_exit": "持仓结构转弱，触发退出规则",
    "satellite_expiry": "短期观察持仓到期",
    "challenger_scout": "按候选观察仓位规则处理",
    "strategic_damage_guard": "主线受损保护要求控制仓位",
    "sector_guard": "持仓行业风险保护要求控制仓位",
    "risk_gross_cap": "风险规则限制组合总仓位",
    "capital_budget": "账户回撤规则限制可用仓位",
    "risk_off": "风险升高，降低持仓",
    "crisis": "严重风险触发仓位保护",
    "ordinary_pullback_entry": "已记录的有限回调建仓",
    "risk_freeze_hold": "冻结新增风险，保留符合原有持有边界的敞口",
})


def _plain_code(value: object, *, reason: bool = False) -> str:
    if value is None or value == "":
        return "未取得"
    code = str(value)
    if code.startswith("current_core_entry:"):
        return _plain_code(code.split(":", 1)[1])
    return (_REASON_TEXT if reason else _BLOCK_TEXT).get(code, f"尚无直白释义（原码：{code}）")


def _percent(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "未取得"
    return f"{value:.2%}"


def _status(value: object) -> str:
    return "条件通过" if value is True else "条件未通过" if value is False else "必要资料不足"


def _symbol_limit_lines(row: Mapping[str, Any], trace: Mapping[str, Any], final: bool) -> list[str]:
    lines: list[str] = []
    if final and trace.get("planning_scope") == "SENTINEL_PLANNING_ONLY" and trace.get("final_freeze_new_risk") is True:
        lines.append("最终生效限制：系统暂不允许增加持仓；下面的中间资金测算不代表最终买入许可")
    if row.get("allocation_reason") == "CAPITAL_LIMIT":
        lines.append("实际分配限制：" + _plain_code("CAPITAL_LIMIT"))
    transfer = row.get("transfer_budget")
    if isinstance(transfer, Mapping) and transfer:
        lines.append("结算后预算估算（不是现金或成交）：" + _plain_code(transfer.get("block")))
        for key, label in (("released_weight", "拟释放仓位"), ("required_weight", "要求参与仓位"),
                           ("funded_increment", "估算可部署增量"), ("cash_room", "估算现金空间"),
                           ("gross_room", "估算总仓空间"), ("symbol_room", "估算单票空间"),
                           ("industry_room", "估算行业空间"), ("correlation_room", "估算共同波动组空间")):
            if key in transfer:
                lines.append(label + "：" + _percent(transfer[key]))
        if transfer.get("correlation_block"):
            lines.append("估算限制：" + _plain_code(transfer["correlation_block"]))
    return lines


def _entry_lines(row: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, label in (("entry", "普通建仓"), ("pending_entry", "原买单延续"),
                       ("pullback_entry", "回调建仓"), ("repair_entry", "账户恢复建仓")):
        if key == "entry" and isinstance(row.get("pending_entry"), Mapping):
            continue
        entry = row.get(key)
        if isinstance(entry, Mapping):
            lines.append(f"{label}评估：{_plain_code(entry.get('block'))}")
            checks = entry.get("checks", {})
            for check, label in (("confidence", "龙头证据可信程度"), ("industry", "行业归属核验"),
                                  ("current_data", "当日行情"), ("history", "历史行情长度"),
                                  ("structure", "价格结构与活跃成交条件"), ("liquidity", "成交活跃程度")):
                item = checks.get(check) if isinstance(checks, Mapping) else None
                if not isinstance(item, Mapping):
                    continue
                text = f"{label}：{_status(item.get('passed'))}"
                if check == "confidence":
                    text += f"；观测值 {_percent(item.get('value'))}，要求至少 {_percent(item.get('minimum'))}（不是上涨概率）"
                elif check == "history":
                    text += f"；观测 {item.get('value', '未取得')} 个交易日，要求至少 {item.get('minimum', '未取得')} 个交易日"
                lines.append(text)
            if isinstance(entry.get("confirmations"), Mapping):
                lines.append(f"已记录连续确认次数：{entry['confirmations']}；要求：{entry.get('required_confirmation', '未取得')} 个交易日")
        elif key == "entry":
            lines.append("普通建仓条件：这条路径未评估或必要资料未取得")
    return lines


def _budget_lines(row: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for budget in row.get("budget_checks", ()):
        lines.append("本次资金检查：" + _status(budget.get("accepted")) + "；各股票共用同一账户资金，不可重复相加")
        rooms = [f"{label} {_percent(budget[key])}" for key, label in (
            ("cash_room", "现金支持空间"), ("gross_room", "总仓空间"), ("symbol_room", "单票空间"),
            ("industry_room", "行业空间"), ("correlation_room", "共同波动组空间")) if key in budget]
        if rooms:
            lines.append("；".join(rooms))
        lines.append(f"获配增量：{_percent(budget.get('funded_increment'))}；最低参与增量：{_percent(budget.get('minimum_increment'))}")
        for key in ("block", "correlation_block"):
            if budget.get(key):
                lines.append("本次限制：" + _plain_code(budget[key]))  # noqa: PERF401
    return lines


def _ledger_lines(symbol: str, account: AccountState) -> list[str]:
    lines: list[str] = []
    for record in account.order_ledger:
        if record.symbol == symbol and record.status not in {"FILLED", "CANCELLED", "REPLACED"}:
            status = {"CANCEL_REQUESTED": "撤销待确认", "PARTIAL": "部分成交", "PARTIALLY_FILLED": "部分成交", "PENDING": "尚未完成"}.get(record.status, "尚未结清")
            event = _EXECUTION_TEXT.get(record.last_event, f"尚无直白释义（原码：{record.last_event or '未取得'}）")
            lines.append(f"账户订单：{status}；原信号 {record.signal_date}；记录余量 {record.remaining_shares} 股")
            lines.append(f"最近执行记录（{record.last_update_date or '时点未取得'}）：{event}；此为已发生的检查，不预测下一开盘可否成交")
            if record.cancel_reason:
                cancel = "新增风险冻结，申请撤销原买单" if record.cancel_reason == "sentinel_freeze_new_risk" else "撤销原因尚无直白释义：" + record.cancel_reason
                lines.append("撤销核对：" + cancel + "；以实际确认回报为准")
    return lines


def _candidate_lines(symbol: str, decision: Decision, row: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    ranking = decision.risk_summary.get("leader_ranking", ())
    for item in ranking if isinstance(ranking, (list, tuple)) else ():
        if isinstance(item, Mapping) and item.get("symbol") == symbol:
            lines.append("支持关注的龙头成熟条件：" + _status(item.get("mature")) + "；仅支持候选评估，不等于交易许可")  # noqa: PERF401
    lines.extend(_entry_lines(row))
    for key, label in (("increase_block", "新增限制"), ("restore_block", "恢复限制"), ("entry_gate", "建仓权限")):
        if row.get(key):
            lines.append(f"{label}：{_plain_code(row[key])}")
    observation = decision.risk_summary.get("strategic_qualification")
    if isinstance(observation, Mapping) and observation.get("candidate_symbol") == symbol:
        lines.append("长期候选资格：" + _status(observation.get("qualification_ready")) + "；资格不等于可以买入")
        if observation.get("deployment_block_reason"):
            lines.append("长期部署限制：" + _plain_code(observation["deployment_block_reason"]))
        lines.append(f"资格确认：{observation.get('qualification_streak', '未取得')} 个交易日；记录时点：{observation.get('qualification_last_observed_session') or '未取得'}")
    lines.extend(_budget_lines(row))
    planning = row.get("order_planning")
    if isinstance(planning, Mapping):
        lines.append("订单规划：" + _plain_code(planning.get("block")))
        if "difference_value" in planning:
            lines.append(f"目标差额：{planning['difference_value']:,.2f} 元；通常交易门槛：{planning['standard_trade_threshold']:,.2f} 元")
    return lines


def _recorded_symbol_lines(symbol: str, decision: Decision, account: AccountState) -> list[str]:
    target = next((t for t in decision.targets if t.symbol == symbol), None)
    orders = [o for o in decision.pending_orders if o.symbol == symbol]
    position = account.positions.get(symbol)
    held = position is not None and position.shares > 0
    trace = decision.risk_summary.get("core_allocation", {})
    final = isinstance(trace, Mapping) and trace.get("scope") == "FINAL_DECISION"
    row = trace.get("symbols", {}).get(symbol, {}) if final else {}
    lines = [f"当前情况：持有 {position.shares} 股，平均成本 {position.avg_cost:,.2f} 元" if held and position else "当前情况：未持有"]
    if orders:
        actions = ["准备买入／增加" if o.side == "BUY" else "准备清仓" if o.target_weight == 0 else "准备减少" for o in orders]
        lines.append("最终处理：" + "、".join(actions) + "；仅为意图，尚未确认成交")
    elif target is not None and target.weight > 0:
        lines.append("最终处理：保留目标／观察，未生成买入意图；也未生成卖出意图")
    elif held:
        lines.append("最终处理：没有卖出意图记录；不能据此断言主动看多或已完成退出")
    else:
        lines.append("最终处理：没有买卖意图；仅观察，是否满足全部条件须看本次证据")
    lines.append("目标仓位：" + (_percent(target.weight) if target else "未取得"))
    if target:
        lines.append("目标依据：" + _target_reason(target.reason, target.reason_code))
    lines.extend(_symbol_limit_lines(row, trace, final))
    lines.extend(_candidate_lines(symbol, decision, row))
    lines.extend(_ledger_lines(symbol, account))
    if not final:
        lines.append("本次最终分配过程未取得；中间规划不能作为最终授权")
    lines.append("重新评估：仅核对上述未满足条件是否变化；单个限制解除不保证买入，仍需下一次完整决策")
    return lines


def _freeze_owner(summary: Mapping[str, Any]) -> str:
    base_freeze = summary.get("base_freeze_new_risk")
    sentinel_freeze = summary.get("sentinel_freeze_new_risk")
    if summary.get("sentinel_causal_coverage_status") != "READY":
        return "资料不足，不能判断完整限制来源"
    elif base_freeze is True and sentinel_freeze is True:
        return "基础风险与独立风险观察共同限制"
    elif base_freeze is True:
        return "基础风险限制"
    elif sentinel_freeze is True:
        return "独立风险观察限制"
    elif base_freeze is False and sentinel_freeze is False:
        return "本次两项检查均未触发新增冻结"
    else:
        return "限制来源未取得"


def _daily_risk_lines(decision: Decision) -> list[str]:
    summary = decision.risk_summary
    freeze = summary.get("freeze_new_risk")
    owner = _freeze_owner(summary)
    risk_lines = [
        "新增冻结来源：" + owner,
        "市场基础风险：" + {"NORMAL": "本次基础评估处于正常档；不代表市场安全", "CAUTION": "处于警戒档", "RISK_OFF": "处于降低风险档", "CRISIS": "处于严重风险档"}.get(decision.risk.value, "未取得"),
        "新增持仓权限：" + ("系统暂不允许增加持仓" if freeze is True else "本项未触发新增冻结，仍需其他检查" if freeze is False else "未取得，不能推断无风险"),
        "独立风险观察：" + ("已冻结新增风险；不代表要求清仓" if summary.get("sentinel_freeze_new_risk") is True else "未记录新增冻结；不代表要求清仓" if summary.get("sentinel_freeze_new_risk") is False else "资料未取得；不代表要求清仓"),
        "独立观察资料覆盖：" + ("已就绪" if summary.get("sentinel_causal_coverage_status") == "READY" else "必要资料不足或未取得，不能推断安全"),
        "行业风险保护：" + ("已触发" if summary.get("sector_guard_active") is True else "本次未触发" if summary.get("sector_guard_active") is False else "未取得"),
        "持仓行业当日收益：" + _percent(summary.get("sector_guard_equal_return")),
        "从账户资金高点回落的比例：" + _percent(summary.get("capital_drawdown")),
        "风险允许的总仓上限：" + _percent(summary.get("target_gross_cap")),
        "个股与账户执行限制见逐票说明；买入冻结不自动取消必要减仓，卖出意图也不保证成交",
    ]
    if summary.get("sentinel_mode") == "SHADOW":
        risk_lines.insert(0, "此样本为离线观察，不能作为生产交易权限")
    family_text = {"breadth_structure": "多数股票与价格结构转弱", "market_velocity": "市场价格变化加速", "covariance_stress": "股票共同波动压力增大",
                   "correlation": "股票共同波动增强", "volatility": "价格波动扩大", "liquidity": "成交流动性转弱"}
    families = summary.get("sentinel_causal_active_families")
    if isinstance(families, (list, tuple)):
        risk_lines.append("已记录的独立观察风险：" + ("、".join(family_text.get(str(f), f"尚无直白释义（{f}）") for f in families) or "本次未记录触发项"))
    else:
        risk_lines.append("独立观察风险明细：未取得")
    weakest = summary.get("sentinel_causal_weakest_subindustries")
    if isinstance(weakest, (list, tuple)) and weakest:
        names = {"design": "芯片设计", "optical": "光通信"}
        risk_lines.append("观察中较弱的行业：" + "、".join(names.get(str(v), str(v)) for v in weakest) + "；行业观察不等于整个市场风险")
    return risk_lines


def _action_lines(decision: Decision) -> list[str]:
    actions = []
    for index, order in enumerate(decision.pending_orders, 1):
        action = "准备买入／增加" if order.side == "BUY" else "准备清仓" if order.target_weight == 0 else "准备减少"
        carried = "本日新意图" if order.signal_date == decision.date else "延续的未完成意图"
        actions.append(f"{index}. {order.symbol}：{action}，目标 {_percent(order.target_weight)}；{carried}，原信号日期 {order.signal_date}；{_target_reason(order.reason, order.reason_code)}")
    actions.append("逐单核对券商回报、可用资金、可卖股份、停牌／涨跌停、成交量和价格；意图不是券商已接受、可立即成交或已经成交")
    if not decision.pending_orders:
        actions.insert(0, "没有记录买卖意图；不把目标仓位当作订单，具体受阻或未评估原因见逐票说明")
    return actions


def _recorded_held_weight(held: list[str], rows: Mapping[str, Any]) -> float | None:
    if not all(s in rows and isinstance(rows[s].get("held_weight"), (int, float)) for s in held):
        return None
    return float(sum(rows[s]["held_weight"] for s in held))


def _daily_sections(decision: Decision, account: AccountState) -> list[tuple[str, list[str]]]:
    summary = decision.risk_summary
    buys = sum(o.side == "BUY" for o in decision.pending_orders)
    sells = sum(o.side == "SELL" for o in decision.pending_orders)
    freeze = summary.get("freeze_new_risk")
    conclusion = ("有买入意图，等待下一可交易日核对" if buys else "需要处理减仓，等待下一可交易日核对" if sells else "本次没有生成买卖意图")
    if freeze is True:
        conclusion += "；系统暂不允许增加持仓，已记录的有限例外须逐单核对" if buys else "；系统暂不允许增加持仓"
    ranking = summary.get("leader_ranking")
    analyzed = [str(r['symbol']) for r in ranking if isinstance(r, Mapping) and r.get('symbol')] if isinstance(ranking, (list, tuple)) else []
    held = [s for s, p in account.positions.items() if p.shares > 0]
    trace = summary.get("core_allocation", {})
    rows = trace.get("symbols", {}) if isinstance(trace, Mapping) and trace.get("scope") == "FINAL_DECISION" else {}
    actual = _recorded_held_weight(held, rows)
    sections = [("今天的结论", [
        conclusion,
        f"决策日期：{decision.date}；行情截止：{summary.get('decision_input_identity', {}).get('as_of', '未取得')}",
        "本次有评分记录的股票：" + ("、".join(analyzed) or "未取得") + "；未宣称扫描全市场",
        f"账户同步时点：{account.broker_as_of or '未取得，需与券商核对'}；下一次执行：决策日之后的下一可交易日，具体日期与可成交条件须核对",
        f"实际持仓比例：{_percent(actual)}；目标比例：{_percent(decision.target_gross)}；总仓上限：{_percent(summary.get('system_gross_cap'))}（上限不是建议满仓）",
        f"当前持有 {len(held)} 只；目标持有 {decision.target_k} 只；买入意图 {buys} 项；卖出意图 {sells} 项",
        f"现金余额：{account.cash:,.2f} 元；不等于可用买入资金，未成交卖单不释放现金或名额",
    ])]
    actions = _action_lines(decision)
    sections.append(("下一可交易日需要核对的事项", actions))
    symbols = list(dict.fromkeys([*held, *(o.symbol for o in decision.pending_orders), *analyzed, *(t.symbol for t in decision.targets), *rows]))
    for symbol in symbols:
        sections.append((f"逐只股票：{symbol}", _recorded_symbol_lines(symbol, decision, account)))  # noqa: PERF401
    sections.append(("风险及其实际影响", _daily_risk_lines(decision)))
    return sections


def _audit_text(decision: Decision, account: AccountState) -> str:
    return json.dumps({"decision": asdict(decision), "account": account.to_dict()},
                      ensure_ascii=False, indent=2, allow_nan=False, sort_keys=True)


def _markdown_text(value: str) -> str:
    # Raw input cannot introduce Markdown links, HTML, tables or code blocks.
    value = html.escape(value, quote=False).replace("\n", " ").replace("\r", " ")
    for char in ('\\', '`', '*', '_', '{', '}', '[', ']', '(', ')', '#', '+', '!', '|'):
        value = value.replace(char, '\\' + char)
    return value


def render_daily_report(decision: Decision, account: AccountState) -> str:
    """Render recorded facts, retaining the complete source evidence for review."""
    lines = [f"# 盘后决策报告 — {_markdown_text(decision.date)}", ""]
    for title, paragraphs in _daily_sections(decision, account):
        lines.extend([f"## {_markdown_text(title)}", ""])
        lines.extend("- " + _markdown_text(line) for line in paragraphs)
        lines.append("")
    # Indented, HTML-escaped code cannot close a fence or activate raw HTML.
    lines.extend(["## 详细依据与核对信息", "", "原始原因、原因码、订单身份、配置指纹和账户事实如下。", ""])
    lines.extend("    " + html.escape(line, quote=False) for line in _audit_text(decision, account).splitlines())
    return "\n".join(lines) + "\n"


def render_daily_html(decision: Decision, account: AccountState) -> str:
    """Render a standalone offline document from the same read-only facts."""
    sections = []
    for title, paragraphs in _daily_sections(decision, account):
        body = ''.join(f'<li>{html.escape(line)}</li>' for line in paragraphs)
        sections.append(f'<section><h2>{html.escape(title)}</h2><ul>{body}</ul></section>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>盘后决策报告</title><style>'
            'body{font:16px/1.7 system-ui,sans-serif;margin:0;background:#eef2f5;color:#172c3c}'
            'main{max-width:1000px;margin:auto;padding:20px}section,details{background:white;padding:20px;margin:16px 0;border-radius:12px}'
            'section:first-of-type{border-top:5px solid #27657a}h1{font-size:26px}h2{font-size:20px;margin:0 0 12px}'
            'ul{padding-left:22px}li{margin:8px 0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}'
            'summary{cursor:pointer;font-weight:bold}@media(max-width:600px){main{padding:10px}section,details{padding:14px}}'
            '</style><main><h1>盘后决策报告 · ' + html.escape(decision.date) + '</h1>' + ''.join(sections)
            + '<details><summary>详细依据与核对信息</summary><pre>' + html.escape(_audit_text(decision, account))
            + '</pre></details></main></html>\n')
