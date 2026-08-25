from __future__ import annotations

import dataclasses
import json
import sys
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

_RISK_REFERENCE_COMMIT = "36bc6968ee61eb578a8f19ee132aecb9b03fe7ca"
_RISK_REFERENCE_TREE = "3cc640cf565e116aa524466485dc7d9e1b511538"


@dataclasses.dataclass(frozen=True, slots=True)
class _TraceSpec:
    name: str
    start: str
    end: str
    symbols: tuple[str, ...]


_OFFICIAL_TRACE_SPECS = (
    _TraceSpec(
        name="early_ai_entry",
        start="2023-01-03",
        end="2023-01-20",
        symbols=("sz300308", "sz300502", "sz300394"),
    ),
    _TraceSpec(
        name="late_2024_rotation",
        start="2024-08-01",
        end="2024-09-02",
        symbols=("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    ),
    _TraceSpec(
        name="recent_shock",
        start="2026-06-30",
        end="2026-07-30",
        symbols=("sz300308", "sz300502", "sz300394", "sh688008", "sh603986"),
    ),
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


def official_risk_trace(root: Path) -> dict[str, Any]:
    """Build the complete risk oracle from one supplied source snapshot."""

    scenarios: list[dict[str, Any]] = []
    for spec in _OFFICIAL_TRACE_SPECS:
        observed = risk_trace_replay(
            name=spec.name,
            start=spec.start,
            end=spec.end,
            symbols=spec.symbols,
            root=root,
        )
        records = [
            {
                "date": record["date"],
                "ordered_checkpoint_sha256": record["ordered_checkpoint_sha256"],
            }
            for record in observed["records"]
        ]
        scenarios.append(
            {
                "name": spec.name,
                "requested_start": spec.start,
                "requested_end": spec.end,
                "symbols": list(spec.symbols),
                "record_count": len(records),
                "records": records,
                "records_sha256": canonical_json_sha256(records),
            }
        )
    payload: dict[str, Any] = {
        "baseline_commit": _RISK_REFERENCE_COMMIT,
        "baseline_tree": _RISK_REFERENCE_TREE,
        "contract": "uquant-task7-daily-risk-trace-v1",
        "projection": (
            "ordered account-before -> complete RiskAssessment/Sentinel result -> account-after"
        ),
        "risk_account_fields": list(_RISK_ACCOUNT_FIELDS),
        "scenarios": scenarios,
        "schema_version": 1,
    }
    payload["payload_sha256"] = canonical_json_sha256(payload)
    return payload


def _assert_snapshot_modules(root: Path) -> None:
    expected = root.resolve()
    for name, module in sys.modules.items():
        if name != "uquant" and not name.startswith("uquant."):
            continue
        source = getattr(module, "__file__", None)
        if source is not None and not Path(source).resolve().is_relative_to(expected):
            raise RuntimeError(f"trace imported uquant outside immutable snapshot: {name}")


def _main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("immutable trace runner requires one snapshot root")
    root = Path(sys.argv[1]).resolve()
    _assert_snapshot_modules(root)
    payload = official_risk_trace(root)
    _assert_snapshot_modules(root)
    print(json.dumps(payload, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ("official_risk_trace", "risk_trace_replay")
