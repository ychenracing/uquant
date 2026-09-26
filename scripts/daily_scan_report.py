"""Lossless production-output projection and previous-session comparisons."""
from __future__ import annotations

# Chinese punctuation is intentional in user-facing reports.
# ruff: noqa: RUF001

import json
import math
from typing import Any

from scripts.daily_scan_market import WATCHLIST

RISK_FIELDS = ("freeze_new_risk", "base_freeze_new_risk", "sentinel_freeze_new_risk",
               "target_gross_cap", "system_gross_cap", "severity", "reduction_level",
               "sentinel_causal_coverage_status", "sentinel_causal_active_families", "sector_guard_active")
ENTRY_FIELDS = ("entry", "pending_entry", "pullback_entry", "repair_entry", "entry_gate")
LIMIT_FIELDS = ("increase_block", "restore_block", "allocation_reason", "order_planning")


def json_safe(value: Any) -> Any:
    """Represent unavailable diagnostics as null, never as invented finite evidence."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "value"):
        return json_safe(value.value)
    if hasattr(value, "item"):
        return json_safe(value.item())
    return value


def project_signals(decision: dict[str, Any], quotes: dict[str, Any]) -> dict[str, Any]:
    summary = decision["risk_summary"]
    market = {"opportunity": decision["opportunity"], "risk": decision["risk"],
              **{key: summary.get(key) for key in RISK_FIELDS}}
    trace = summary.get("core_allocation", {})
    final = trace.get("scope") == "FINAL_DECISION"
    rows = trace.get("symbols", {}) if final else {}
    leaders = {row["symbol"]: row for row in summary.get("leader_ranking", [])}
    targets = {row["symbol"]: row for row in decision["targets"]}
    result = []
    for symbol, name in WATCHLIST:
        row = rows.get(symbol, {})
        qualification = {key: row[key] for key in ENTRY_FIELDS if key in row}
        strategic = summary.get("strategic_qualification", {})
        if strategic.get("candidate_symbol") == symbol:
            qualification["strategic"] = strategic
        result.append({"symbol": symbol, "name": name, "quote": quotes.get(symbol),
                       "leader": leaders.get(symbol), "qualification": qualification or None,
                       "risk_limits": {key: row[key] for key in LIMIT_FIELDS if key in row} or None,
                       "allocation_trace_scope": trace.get("scope"), "target": targets.get(symbol),
                       "orders": [order for order in decision["pending_orders"] if order["symbol"] == symbol]})
    return json_safe({"market": market, "stocks": result})


def comparison(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None or previous.get("status") not in ("COMPLETE", "PARTIAL"):
        return {"status": "INCOMPARABLE", "reason": "没有可核验的前一交易日结构化结果", "changes": []}
    for key in ("observer_id", "config_sha256", "economic_code_hash"):
        if previous.get(key) != current.get(key):
            return {"status": "INCOMPARABLE", "reason": f"比较口径变化：{key}", "changes": []}
    if previous.get("target_date") != current["previous_session"]:
        return {"status": "INCOMPARABLE", "reason": "结果不是前一交易日", "changes": []}
    changes = []
    unavailable = []
    before, after = previous["signals"], current["signals"]
    for key, value in after["market"].items():
        if before["market"].get(key) is None or value is None:
            unavailable.append("市场/" + key)
        elif before["market"][key] != value:
            changes.append({"subject": "市场", "field": key, "before": before["market"][key], "after": value, "comparable": True})
    old = {row["symbol"]: row for row in before["stocks"]}
    if set(old) != {symbol for symbol, _ in WATCHLIST}:
        return {"status": "INCOMPARABLE", "reason": "前一交易日股票池不完整", "changes": []}
    for row in after["stocks"]:
        former = old[row["symbol"]]
        # Compare actual action/weight/eligibility, not changing order IDs or dates.
        fields = {"action": sorted((o["side"], o.get("target_weight")) for o in row["orders"]),
                  "target_weight": (row["target"] or {}).get("weight"),
                  "qualification": row["qualification"], "risk_limits": row["risk_limits"]}
        old_fields = {"action": sorted((o["side"], o.get("target_weight")) for o in former["orders"]),
                      "target_weight": (former["target"] or {}).get("weight"),
                      "qualification": former["qualification"], "risk_limits": former["risk_limits"]}
        for key, value in fields.items():
            if value is None or old_fields[key] is None:
                unavailable.append(row["name"] + "/" + key)
            elif value != old_fields[key]:
                changes.append({"subject": row["name"], "field": key, "before": old_fields[key], "after": value, "comparable": True})
    return {"status": "COMPARABLE", "unavailable": unavailable, "source_changed": previous.get("source_sha") != current.get("source_sha"), "changes": changes}


def display(value: Any) -> str:
    if value is None:
        return "未提供"
    text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
    return text.replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def render_report(payload: dict[str, Any], production_report: str) -> str:
    comp = payload["comparison"]
    lines = [f"# Uquant 13标的盘后日报 — {payload['target_date']}", "",
             f"状态：{payload['status']}；实际行情日：{payload['target_date']}；前一交易日：{payload['previous_session']}",
             f"源码：`{payload['source_sha']}`；[Actions Run]({payload['run_url']})", "",
             f"扫描启动：{payload['started_at']}；计算完成：{payload['computed_at']}（不冒充整个 Actions Job 完成时间）",
             f"观察起点：{payload['observer_start']}；初始模拟现金：{payload['initial_cash']} 元。",
             "口径：连续观察账户，不是用户实盘；不假设或模拟成交，未执行意图会继续保留。",
             "提示：" + "；".join(payload.get("warnings", [])),
             "## 重点变化", ""]
    if comp["status"] != "COMPARABLE":
        lines.append("**不可比较：" + comp["reason"] + "**")
    elif not comp["changes"]:
        lines.append("可比较且已核验的字段无变化；不涵盖下面列出的缺失字段。")
    else:
        for change in comp["changes"]:
            lines.append(f"- **{change['subject']} / {change['field']}：{display(change['before'])} → {display(change['after'])}**")
    if comp.get("unavailable"):
        lines.append("不可比较的缺失字段：" + "、".join(comp["unavailable"]))
    if comp.get("source_changed"):
        lines.append("源码提交有变化；经济代码与配置指纹一致。本比较不是新版本历史回算。")
    lines.extend(["", "## 市场与风险", "", "字段 | 生产输出", "--- | ---"])
    lines.extend(f"{key} | {display(value)}" for key, value in payload["signals"]["market"].items())
    lines.extend(["", "## 全部13只股票", "", "代码及名称 | 收盘价 / 涨跌幅(%) | 机会/趋势证据 | 资格 | 风险限制 | 行动/订单意图 | 目标仓位", "--- | --- | --- | --- | --- | --- | ---"])
    for row in payload["signals"]["stocks"]:
        quote = row["quote"] or {}
        actions = [{"side": o["side"], "signal_date": o.get("signal_date"), "target_weight": o.get("target_weight")} for o in row["orders"]]
        values = [row["symbol"][2:] + " " + row["name"], f"{display(quote.get('close'))} / {display(quote.get('change_pct'))}",
                  display(row["leader"]), display(row["qualification"]), display(row["risk_limits"]),
                  display(actions) if actions else "未生成订单意图（不等于持有或允许买入）", display((row["target"] or {}).get("weight"))]
        lines.append(" | ".join(values))
    lines.extend(["", "目标权重以0—1表示；订单只是下一可交易日人工核对的意图，不是成交。", ""])
    if production_report:
        lines.extend(["## 生产系统完整原始日报", "", production_report])
    return "\n".join(lines) + "\n"
