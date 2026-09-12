# ruff: noqa: RUF001
from __future__ import annotations

import pytest

from uquant.report import render_daily_report
from uquant.types import AccountState, Decision, Opportunity, Risk


def _summary(
    *,
    coverage: str,
    base_freeze: bool = False,
    sentinel_freeze: bool = False,
    observed_level: str = "CAUTION",
) -> dict[str, object]:
    return {
        "freeze_new_risk": base_freeze or sentinel_freeze,
        "base_freeze_new_risk": base_freeze,
        "sentinel_freeze_new_risk": sentinel_freeze,
        "sentinel_mode": "FREEZE_ONLY",
        "sentinel_causal_coverage_status": coverage,
        "sentinel_causal_confidence": 0.91 if coverage == "READY" else 0.0,
        "sentinel_causal_observed_level": observed_level,
        "sentinel_causal_active_families": [
            "breadth_structure",
            "market_velocity",
        ],
        "sentinel_causal_weakest_subindustries": ["design", "optical"],
        "sentinel_causal_reasons": ["market breadth weakened"],
        "sentinel_causal_confirmation_history_trusted": coverage == "READY",
        "sentinel_causal_confirmation_days": 2,
        "sentinel_causal_repair_days": 0,
        "sentinel_causal_effective_level": observed_level,
        "sentinel_causal_incremental_families": ["breadth_structure"],
        "sentinel_causal_earlier_families": [],
        "base_first_family_dates": {"market_velocity": "2026-01-02"},
        "sentinel_first_family_dates": {
            "breadth_structure": "2026-01-02",
            "market_velocity": "2026-01-02",
        },
    }


def _report(summary: dict[str, object]) -> str:
    decision = Decision(
        date="2026-08-05",
        opportunity=Opportunity.TREND,
        risk=Risk.CAUTION,
        target_gross=0.7,
        target_k=2,
        targets=(),
        pending_orders=(),
        risk_summary=summary,
        decision_digest="digest",
    )
    return render_daily_report(decision, AccountState.empty(100_000.0))


@pytest.mark.parametrize(
    ("coverage", "base_freeze", "sentinel_freeze", "owner"),
    (
        ("NOT_READY", False, False, "DATA_NOT_READY"),
        ("READY", False, False, "NONE"),
        ("READY", True, False, "BASE_RISK"),
        ("READY", False, True, "SENTINEL"),
        ("READY", True, True, "BOTH"),
    ),
)
def test_daily_report_distinguishes_every_sentinel_owner(
    coverage: str,
    base_freeze: bool,
    sentinel_freeze: bool,
    owner: str,
) -> None:
    report = _report(
        _summary(
            coverage=coverage,
            base_freeze=base_freeze,
            sentinel_freeze=sentinel_freeze,
        )
    )

    labels = {"DATA_NOT_READY": "资料不足，不能判断完整限制来源", "NONE": "本次两项检查均未触发新增冻结",
              "BASE_RISK": "基础风险限制", "SENTINEL": "独立风险观察限制", "BOTH": "基础风险与独立风险观察共同限制"}
    assert labels[owner] in report


def test_daily_report_contains_the_compact_sentinel_operating_fields() -> None:
    report = _report(_summary(coverage="READY"))
    section = report.split("## 风险及其实际影响", 1)[1].split("## 详细依据", 1)[0]
    assert "独立观察资料覆盖：已就绪" in section
    assert "本次两项检查均未触发新增冻结" in section
    assert "多数股票与价格结构转弱" in section
    assert "市场价格变化加速" in section
    assert "芯片设计、光通信" in section
    assert "不代表要求清仓" in section
    assert '"sentinel_causal_confidence": 0.91' in report


def test_daily_report_limits_sentinel_conclusions_to_safe_manual_actions() -> None:
    data_report = _report(_summary(coverage="NOT_READY"))
    freeze_report = _report(
        _summary(coverage="READY", sentinel_freeze=True)
    )

    assert "必要资料不足或未取得，不能推断安全" in data_report
    assert "系统暂不允许增加持仓" in freeze_report


def test_daily_report_handles_absent_sentinel_summary_as_data_not_ready() -> None:
    report = _report({})

    assert "资料不足，不能判断完整限制来源" in report
    assert "独立观察风险明细：未取得" in report
    assert "不能推断安全" in report
