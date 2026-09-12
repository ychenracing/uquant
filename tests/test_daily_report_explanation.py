# ruff: noqa: RUF001
from __future__ import annotations

from copy import deepcopy

import pytest

from uquant.report import render_daily_report
from uquant.types import (
    AccountOrder,
    AccountState,
    Decision,
    Opportunity,
    PendingOrder,
    Position,
    Risk,
    Target,
)


def _decision(*, summary: dict[str, object]) -> Decision:
    return Decision(
        date="2025-01-06",
        opportunity=Opportunity.TREND,
        risk=Risk.NORMAL,
        target_gross=0.2,
        target_k=1,
        targets=(Target("new", 0.2, "CORE", 0.8, 1.0, "recorded admission reason"),),
        pending_orders=(
            PendingOrder("2025-01-06", "new", "BUY", 0.2, "recorded order reason", "CORE", order_id="O000000002"),
        ),
        risk_summary=summary,
        decision_digest="recorded-digest",
    )


def test_daily_report_separates_account_balances_from_targets_and_capital_room() -> None:
    """Targets and cash must not be displayed as settled holdings or free admission room."""
    account = AccountState.empty(100_000.0)
    account.cash = 40_000.0
    account.broker_as_of = "2025-01-06"
    account.positions = {
        "held": Position("held", shares=100, avg_cost=12.5),
        "closed": Position("closed", shares=0, avg_cost=15.0),
    }
    account.order_ledger = [
        AccountOrder(
            "O000000001", "2025-01-03", "2025-01-06", "blocked", "BUY", 0.1,
            "cancel requested", "CORE", status="CANCEL_REQUESTED",
        )
    ]
    decision = _decision(summary={
        "target_gross_cap": 0.6,
        "system_gross_cap": 0.9,
        "reasons": ["recorded account risk reason"],
    })
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")

    holdings = report.split("## 逐只股票：held", 1)[1].split("## ", 1)[0]
    assert "持有 100 股，平均成本 12.50 元" in holdings
    assert "逐只股票：new" not in holdings
    assert "逐只股票：closed" not in report
    assert "现金余额：40,000.00 元" in report
    assert "账户同步时点：2025-01-06" in report
    assert "总仓上限：90.00%" in report
    assert "风险允许的总仓上限：60.00%" in report
    assert "不等于可用买入资金" in report
    assert "O000000001" in report
    assert "recorded account risk reason" in report
    assert "recorded admission reason" in report
    assert "O000000002" in report
    assert "recorded order reason" in report
    assert (decision, account) == before


def test_daily_report_scopes_recorded_block_to_its_candidate_and_admits_missing_reasons() -> None:
    """A primary-candidate block must not become a guessed explanation for every ranked name."""
    account = AccountState.empty(100_000.0)
    account.strategic_qualification.candidate_symbol = "stale-account-candidate"
    decision = _decision(summary={
        "leader_ranking": [{"symbol": "unknown"}, {"symbol": "blocked"}, {"symbol": "new"}],
        "strategic_qualification": {
            "candidate_symbol": "blocked",
            "qualification_route": "established",
            "qualification_ready": True,
            "qualification_streak": 3,
            "qualification_last_observed_session": "2025-01-06",
            "deployment_blocked": True,
            "deployment_block_reason": "reference_coverage_or_confirmation",
            "unavailable_reference_symbols": ["reference"],
        },
    })
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")

    candidates = report.split("## 逐只股票：blocked", 1)[1].split("## ", 1)[0]
    assert "长期候选资格：条件通过" in candidates
    assert "资格确认：3 个交易日；记录时点：2025-01-06" in candidates
    assert "参考证据覆盖或连续确认条件不足" in candidates
    assert "reference_coverage_or_confirmation" in report
    unknown = report.split("## 逐只股票：unknown", 1)[1].split("## ", 1)[0]
    assert "reference_coverage_or_confirmation" not in unknown
    assert "未评估" in unknown
    assert "stale-account-candidate" not in report.split("## 详细依据", 1)[0]
    assert "单个限制解除不保证买入" in candidates
    assert (decision, account) == before


def test_daily_report_does_not_infer_no_trade_bands_or_zero_limits_from_missing_evidence() -> None:
    """No order can mean many things; missing evidence must not be rendered as a known cause."""
    decision = Decision(
        date="2025-01-06", opportunity=Opportunity.CHOPPY, risk=Risk.NORMAL,
        target_gross=0.0, target_k=0, targets=(), pending_orders=(),
        risk_summary={}, decision_digest="empty-evidence",
    )

    report = render_daily_report(decision, AccountState.empty(100_000.0))

    assert "当前持有 0 只" in report
    assert "总仓上限：未取得" in report
    assert "本次有评分记录的股票：未取得" in report
    assert "没有记录买卖意图" in report
    assert "remain inside no-trade bands" not in report


def test_report_uses_final_orders_and_recorded_limits_when_a_provisional_buy_is_frozen() -> None:
    account = AccountState.empty(100_000.0)
    trace = {
        "as_of": "2025-01-06", "scope": "FINAL_DECISION",
        "planning_scope": "SENTINEL_PLANNING_ONLY", "final_freeze_new_risk": True,
        "symbols": {"blocked": {
            "held_weight": 0.0, "proposal_weight": .2, "final_target_weight": 0.0,
            "final_target_reason": "NO_TARGET", "orders": [],
            "allocation_reason": "CORE_ADMISSION", "rank_score": .9,
            "entry": {"block": "READY", "confirmations": {"established": 5},
                      "required_confirmation": 5},
            "budget_checks": [{"cash_room": .4, "gross_room": .4, "symbol_room": .6,
                               "industry_room": .15, "correlation_room": .15,
                               "funded_increment": .15, "minimum_increment": .2}],
            "order_planning": {"block": "NO_TARGET"},
        }},
    }
    decision = _decision(summary={"core_allocation": trace})
    before = deepcopy((decision, account))
    report = render_daily_report(decision, account).replace("\\", "")
    blocked = report.split("## 逐只股票：blocked", 1)[1].split("## ", 1)[0]
    assert "没有买卖意图" in blocked
    assert "系统暂不允许增加持仓" in blocked
    assert "要求：5 个交易日" in blocked
    assert "行业空间 15.00%" in blocked
    assert "共同波动组空间 15.00%" in blocked
    assert "获配增量：15.00%；最低参与增量：20.00%" in blocked
    assert (decision, account) == before


def _core_allocation_decision(row: dict[str, object], *, frozen: bool = False) -> Decision:
    return _decision(summary={"core_allocation": {
        "as_of": "2025-01-06", "scope": "FINAL_DECISION",
        "planning_scope": "SENTINEL_PLANNING_ONLY" if frozen else "STRATEGY",
        "final_freeze_new_risk": frozen,
        "symbols": {"partial": {
            "held_weight": .1, "final_target_weight": .1,
            "final_target_reason": "retained holding", "orders": [],
            "entry": {"block": "CONFIRMATION_INCOMPLETE", "confirmations": {"established": 1},
                      "required_confirmation": 5},
            **row,
        }},
    }})


def test_report_explains_pending_current_quality_without_repeating_fresh_confirmation() -> None:
    decision = _core_allocation_decision({
        "final_target_weight": .2, "final_target_reason": "pending core buy",
        "orders": [{"side": "BUY", "order_id": "O000000003"}],
        "allocation_reason": "PENDING_CORE_BUY",
        "restore_block": "PENDING_CORE_BUY_ALREADY_EVALUATED",
        "pending_entry": {"block": "READY", "confirmations": {"established": 1},
                          "required_confirmation": 1},
    })
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")
    row = report.split("## 逐只股票：partial", 1)[1].split("## ", 1)[0]

    assert "没有买卖意图" in row  # Trace-only orders are not final Decision orders.
    assert "原买单延续评估：已满足本项候选条件" in row
    assert "要求：1 个交易日" in row
    assert "要求：5 个交易日" not in row
    assert (decision, account) == before


@pytest.mark.parametrize("block", ["CONFIRMATION_INCOMPLETE", "NOT_MATURE"])
def test_report_does_not_hide_pending_quality_rejection_behind_restoration_routing(block: str) -> None:
    pending_entry: dict[str, object] = {"block": block, "required_confirmation": 1}
    if block == "CONFIRMATION_INCOMPLETE":
        pending_entry["confirmations"] = {"established": 0}
    decision = _core_allocation_decision({
        "pending_entry": pending_entry,
        "restore_block": "PENDING_CORE_BUY_ALREADY_EVALUATED",
    })
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")
    row = report.split("## 逐只股票：partial", 1)[1].split("## ", 1)[0]

    expected = "连续交易日确认尚未完成" if block == "CONFIRMATION_INCOMPLETE" else "龙头成熟条件未满足"
    assert expected in row
    assert "要求：5 个交易日" not in row
    assert (decision, account) == before


@pytest.mark.parametrize("gate", ["COMMON_TREND_NOT_CONFIRMED", "CASH_REPAIR_PERMISSION_CLOSED"])
def test_report_exposes_recorded_pending_market_refusal(gate: str) -> None:
    decision = _core_allocation_decision({
        "pending_entry": {"block": "READY"}, "pending_market_open": False,
        "entry_gate": gate, "restore_block": "PENDING_CORE_BUY_ALREADY_EVALUATED",
    })
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))
    row = render_daily_report(decision, account).replace("\\", "").split("## 逐只股票：partial", 1)[1].split("## ", 1)[0]
    expected = "共同趋势证据尚未确认" if gate == "COMMON_TREND_NOT_CONFIRMED" else "本次账户恢复买入权限未开放"
    assert expected in row
    assert "原买单延续评估：已满足本项候选条件" in row
    assert (decision, account) == before


def test_report_explains_current_maturity_needed_for_new_ordinary_admission() -> None:
    decision = _core_allocation_decision({"entry_gate": "ORDINARY_CORE_NOT_MATURE"})
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))
    report = render_daily_report(decision, account).replace("\\", "")
    assert "普通建仓所需的成熟条件不足" in report
    assert (decision, account) == before


@pytest.mark.parametrize("frozen", [False, True])
def test_report_preserves_final_freeze_and_capital_limit_over_pending_quality(frozen: bool) -> None:
    decision = _core_allocation_decision({
        "pending_entry": {"block": "READY", "confirmations": {"established": 1},
                          "required_confirmation": 1},
        "restore_block": "PENDING_CORE_BUY_ALREADY_EVALUATED",
        "allocation_reason": "CAPITAL_LIMIT",
        "budget_checks": [{"cash_room": .0, "gross_room": .4, "symbol_room": .5,
                           "industry_room": .3, "correlation_room": .3,
                           "funded_increment": .0, "minimum_increment": .05}],
    }, frozen=frozen)
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")
    row = report.split("## 逐只股票：partial", 1)[1].split("## ", 1)[0]

    assert "可部署资金或仓位额度不足" in row
    assert "要求：1 个交易日" in row
    assert "现金支持空间 0.00%" in row
    if frozen:
        assert "系统暂不允许增加持仓" in row
    assert (decision, account) == before


def test_report_explains_missing_link_between_restoration_episode_and_current_holding() -> None:
    decision = _core_allocation_decision({
        "restore_block": "RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING",
    })
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")
    row = report.split("## 逐只股票：partial", 1)[1].split("## ", 1)[0]

    assert "该持仓与本次风险恢复记录未建立有效关联" in row
    assert (decision, account) == before


@pytest.mark.parametrize(("transfer", "expected"), [
    ({"block": "TRANSFER_BELOW_TRADE_MINIMUM", "released_weight": .04, "required_weight": .2},
     "TRANSFER_BELOW_TRADE_MINIMUM, released weight 4.0%, required admission 20.0%"),
    ({"block": "TRANSFER_CANNOT_FUND_ADMISSION", "released_weight": .3, "required_weight": .2,
      "funded_increment": .0, "cash_room": .4, "gross_room": .4, "symbol_room": .6,
      "industry_room": .45, "correlation_block": "INSUFFICIENT_CORRELATION_HISTORY"},
     "TRANSFER_CANNOT_FUND_ADMISSION, released weight 30.0%, required admission 20.0%, "
     "fundable increment 0.0%, cash room 40.0%, gross room 40.0%, name room 60.0%, "
     "industry room 45.0%, INSUFFICIENT_CORRELATION_HISTORY"),
    ({"block": "FEASIBLE_AFTER_SETTLEMENT", "released_weight": .3, "required_weight": .2,
      "funded_increment": .2, "cash_room": .4, "gross_room": .4, "symbol_room": .6,
      "industry_room": .45, "correlation_room": .45},
     "FEASIBLE_AFTER_SETTLEMENT, released weight 30.0%, required admission 20.0%, "
     "fundable increment 20.0%, cash room 40.0%, gross room 40.0%, name room 60.0%, "
     "industry room 45.0%, correlation room 45.0%"),
], ids=["below_trade_minimum", "unfundable", "feasible"])
@pytest.mark.parametrize("frozen", [False, True])
def test_report_labels_recorded_transfer_projection_without_overriding_final_constraints(
    transfer: dict[str, object], expected: str, frozen: bool,
) -> None:
    decision = _core_allocation_decision({
        "held_weight": .0, "final_target_weight": .0, "final_target_reason": "NO_TARGET",
        "entry": {"block": "READY", "confirmations": {"established": 5}, "required_confirmation": 5},
        "allocation_reason": "CAPITAL_LIMIT", "transfer_budget": transfer,
        "budget_checks": [{"cash_room": .1, "funded_increment": .1, "minimum_increment": .2}],
    }, frozen=frozen)
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")
    row = report.split("## 逐只股票：partial", 1)[1].split("## ", 1)[0]

    assert "可部署资金或仓位额度不足" in row
    assert "现金支持空间 10.00%" in row
    assert "结算后预算估算（不是现金或成交）" in row
    assert f"拟释放仓位：{transfer['released_weight']:.2%}" in row
    assert f"要求参与仓位：{transfer['required_weight']:.2%}" in row
    if "funded_increment" not in transfer:
        assert "估算可部署增量" not in row
        assert "估算共同波动组空间" not in row
    else:
        assert f"估算可部署增量：{transfer['funded_increment']:.2%}" in row
    if frozen:
        assert "系统暂不允许增加持仓" in row
    assert (decision, account) == before


def test_report_does_not_infer_transfer_feasibility_from_an_empty_record() -> None:
    decision = _core_allocation_decision({"allocation_reason": "CAPITAL_LIMIT", "transfer_budget": {}})
    account = AccountState.empty(100_000.0)
    before = deepcopy((decision, account))

    report = render_daily_report(decision, account).replace("\\", "")
    row = report.split("## 逐只股票：partial", 1)[1].split("## ", 1)[0]

    assert "可部署资金或仓位额度不足" in row
    assert "结算后预算估算" not in row
    assert "拟释放仓位" not in row
    assert (decision, account) == before
