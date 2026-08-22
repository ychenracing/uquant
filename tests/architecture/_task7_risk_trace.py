from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from uquant.contracts.strict_json import canonical_json_sha256
from uquant.models.account import AccountState
from uquant.models.decision import RiskAssessment

_RISK_ACCOUNT_FIELDS = (
    "risk",
    "shock_state",
    "sector_shock_dates",
    "sector_guard_active",
    "sector_guard_started",
    "sector_guard_symbols",
    "sector_recovery_streak",
    "cooldown_until",
    "operating_peak",
    "capital_peak",
    "candidate_tenure",
    "risk_streaks",
    "risk_events",
    "anchor_weights",
    "recovery_anchor_date",
    "recovery_conviction_symbol",
    "tactical_anchor_symbol",
    "protected_weights",
    "strategic_cohort_symbols",
    "strategic_cohort_targets",
    "strategic_exit_bands",
    "strategic_active_bands",
    "strategic_restore_weights",
    "strategic_epoch",
    "strategic_epochs_completed",
    "strategic_last_exit_date",
    "strategic_rearm_date",
    "strategic_candidate_signature",
    "strategic_previous_symbols",
    "risk_anchor_symbols",
    "risk_anchor_signature",
    "risk_anchor_candidate_signature",
    "risk_anchor_candidate_streak",
    "risk_signal_state",
    "capital_budget_level",
    "capital_budget_repair_streak",
    "chronic_level",
    "chronic_streak",
    "chronic_repair_streak",
    "shock_start_date",
    "shock_severity",
    "last_shock_date",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _account_projection(account: AccountState) -> dict[str, Any]:
    payload = account.to_dict()
    return {field: _jsonable(payload[field]) for field in _RISK_ACCOUNT_FIELDS}


def _assessment_payload(assessment: RiskAssessment) -> dict[str, Any]:
    return _jsonable(assessment)


def risk_trace_replay(
    *,
    name: str,
    start: str,
    end: str,
    symbols: Sequence[str],
    root: Path,
) -> dict[str, Any]:
    import uquant.engine as engine_module

    original = engine_module.assess_risk
    records: list[dict[str, Any]] = []

    def traced_assess_risk(**kwargs: Any) -> RiskAssessment:
        account = kwargs["account"]
        if not isinstance(account, AccountState):
            raise AssertionError("risk trace requires the real AccountState")
        before = _account_projection(account)
        assessment = original(**kwargs)
        after = _account_projection(account)
        assessment_payload = _assessment_payload(assessment)
        control = {
            "state": assessment.state.value,
            "target_gross_cap": assessment.target_gross_cap,
            "votes": assessment.votes,
            "reasons": list(assessment.reasons),
            "shock_state": assessment.shock_state,
            "freeze_new_risk": assessment.freeze_new_risk,
            "reduction_level": assessment.reduction_level,
            "severity": assessment.severity,
        }
        record = {
            "date": str(kwargs["date"].date()),
            "control": control,
            "account_before_sha256": canonical_json_sha256(before),
            "assessment_sha256": canonical_json_sha256(assessment_payload),
            "account_after_sha256": canonical_json_sha256(after),
        }
        record["ordered_checkpoint_sha256"] = canonical_json_sha256(
            {
                "account_before": before,
                "assessment": assessment_payload,
                "account_after": after,
            }
        )
        records.append(record)
        return assessment

    engine_module.assess_risk = traced_assess_risk
    try:
        engine_module.ProductionEngine(root / "data" / "frozen").backtest(
            symbols=symbols,
            start=start,
            end=end,
        )
    finally:
        engine_module.assess_risk = original

    return {
        "name": name,
        "requested_start": start,
        "requested_end": end,
        "symbols": list(symbols),
        "risk_account_fields": list(_RISK_ACCOUNT_FIELDS),
        "records": records,
        "records_sha256": canonical_json_sha256(records),
    }


__all__ = ("risk_trace_replay",)
