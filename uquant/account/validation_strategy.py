"""Durable strategy, risk, and audit-event validation."""

from __future__ import annotations

from typing import Any

from ..models.strategic_grant import (
    validate_strategic_grant,
    validate_strategic_qualification,
)
from ..types import AccountState, Lifecycle, Opportunity, Risk
from .validation_common import (
    SHOCK_SEVERITIES as _SHOCK_SEVERITIES,
)
from .validation_common import (
    SHOCK_STATES as _SHOCK_STATES,
)
from .validation_common import (
    finite_number as _finite_number,
)
from .validation_common import (
    nonnegative_integer as _nonnegative_integer,
)
from .validation_common import (
    optional_finite_event_number as _optional_finite_event_number,
)
from .validation_common import (
    optional_iso_date as _optional_iso_date,
)
from .validation_common import (
    required_iso_date as _required_iso_date,
)
from .validation_common import (
    required_text as _required_text,
)
from .validation_common import (
    validate_account_event_array as _validate_event_array,
)
from .validation_common import (
    validate_account_symbol_list as _validate_symbol_list,
)
from .validation_common import (
    validate_account_weight_map as _validate_weight_map,
)
from .validation_common import (
    validate_nonnegative_account_integer_map as _validate_nonnegative_integer_map,
)


def _validate_risk_streaks(values: Any) -> None:
    """Validate streak counters plus the signed opportunity evidence sentinel."""

    if not isinstance(values, dict):
        raise RuntimeError("risk_streaks must be an object")
    for key, value in values.items():
        _required_text(key, field="risk_streaks key")
        if key == "opportunity_evidence":
            if isinstance(value, bool) or not isinstance(value, int) or value not in {-1, 0, 1}:
                raise RuntimeError("risk_streaks[opportunity_evidence] must be -1, 0, or 1")
            continue
        _nonnegative_integer(value, field=f"risk_streaks[{key}]")


def _validate_reconciliation_events(
    *,
    lifecycles: Any,
    state: Any,
) -> None:
    reconciliation_event_types = {
        "sell_lot_attribution_incomplete",
        "broker_share_deficit_reconciled",
        "economic_lot_degraded",
    }
    for event in _validate_event_array(
        state.reconciliation_events,
        field="reconciliation_events",
    ):
        _required_iso_date(event.get("date"), field="reconciliation event date")
        _required_text(event.get("symbol"), field="reconciliation event symbol")
        if event.get("event") not in reconciliation_event_types:
            raise RuntimeError("reconciliation event has invalid event type")
        for name in (
            "shares",
            "broker_shares",
            "attributed_shares",
            "degraded_shares",
            "unmatched_shares",
        ):
            if name in event:
                _nonnegative_integer(event[name], field=f"reconciliation event {name}")
        if "reason" in event:
            _required_text(event["reason"], field="reconciliation event reason")
        if "quality" in event and event["quality"] != "degraded_external_inventory":
            raise RuntimeError("reconciliation event has invalid quality")
        if "default_lifecycle" in event and event["default_lifecycle"] not in lifecycles:
            raise RuntimeError("reconciliation event has invalid default_lifecycle")
        if "default_entry_date" in event:
            _required_iso_date(
                event["default_entry_date"],
                field="reconciliation event default_entry_date",
            )
        if "default_highest_close" in event:
            default_highest = _finite_number(
                event["default_highest_close"],
                field="reconciliation event default_highest_close",
                minimum=0.0,
            )
            if default_highest == 0.0:
                raise RuntimeError("reconciliation event default_highest_close must be positive")


def _validate_risk_events(
    *,
    risks: Any,
    state: Any,
) -> None:
    for event in _validate_event_array(state.risk_events, field="risk_events"):
        _required_iso_date(event.get("date"), field="risk event date")
        event_name = event.get("event")
        has_transition = "from" in event or "to" in event
        if event_name is not None and event_name not in {
            "sector_guard_on",
            "sector_guard_off",
        }:
            raise RuntimeError("risk event has invalid event type")
        if has_transition and (event.get("from") not in risks or event.get("to") not in risks):
            raise RuntimeError("risk event has invalid risk transition")
        for name in ("votes", "shock_count", "active_sessions"):
            if name in event:
                _nonnegative_integer(event[name], field=f"risk event {name}")
        if "reasons" in event:
            reasons = event["reasons"]
            if not isinstance(reasons, list) or any(
                not isinstance(reason, str) or not reason.strip() for reason in reasons
            ):
                raise RuntimeError("risk event reasons must be an array of text")
        if "severity" in event and event["severity"] not in _SHOCK_SEVERITIES:
            raise RuntimeError("risk event has invalid severity")
        if "route" in event:
            _required_text(event["route"], field="risk event route")
        for name in (
            "leadership_divergence",
            "equal_weight_return",
            "exposure_weighted_return",
        ):
            _optional_finite_event_number(event, name, field="risk event")


def _validate_audit_events(state: AccountState) -> None:
    """Validate structured strategy and lifecycle audit events in an account."""

    lifecycles = {item.value for item in Lifecycle}
    risks = {item.value for item in Risk}

    for event in _validate_event_array(
        state.replacement_events,
        field="replacement_events",
    ):
        _required_iso_date(event.get("signal_date"), field="replacement event signal_date")
        _required_text(event.get("old_symbol"), field="replacement event old_symbol")
        _required_text(event.get("new_symbol"), field="replacement event new_symbol")
        for name in ("old_close", "new_close"):
            value = _finite_number(
                event.get(name),
                field=f"replacement event {name}",
                minimum=0.0,
            )
            if value == 0.0:
                raise RuntimeError(f"replacement event {name} must be positive")
        _finite_number(event.get("edge"), field="replacement event edge")
        if "industry_handoff" in event and type(event["industry_handoff"]) is not bool:
            raise RuntimeError("replacement event industry_handoff must be boolean")
        if "route" in event:
            _required_text(event["route"], field="replacement event route")

    for event in _validate_event_array(
        state.lifecycle_events,
        field="lifecycle_events",
    ):
        _required_iso_date(event.get("date"), field="lifecycle event date")
        _required_text(event.get("symbol"), field="lifecycle event symbol")
        if event.get("from") not in {*lifecycles, "NONE"}:
            raise RuntimeError("lifecycle event has invalid from lifecycle")
        if event.get("to") not in lifecycles:
            raise RuntimeError("lifecycle event has invalid to lifecycle")
        _nonnegative_integer(
            event.get("shares"),
            field="lifecycle event shares",
            positive=True,
        )
        _required_text(event.get("reason"), field="lifecycle event reason")

    _validate_risk_events(
        risks=risks,
        state=state,
    )

    _validate_reconciliation_events(
        lifecycles=lifecycles,
        state=state,
    )


def _validate_strategy_controls_and_signals(
    *,
    state: Any,
    total_band_weight: Any,
) -> None:
    if total_band_weight > 1.0 + 1e-6:
        raise RuntimeError("strategic exit band total weight exceeds one")

    _validate_nonnegative_integer_map(state.leader_tenure, field="leader_tenure")
    _validate_nonnegative_integer_map(state.candidate_tenure, field="candidate_tenure")
    _validate_nonnegative_integer_map(state.replacement_tenure, field="replacement_tenure")
    _validate_risk_streaks(state.risk_streaks)
    for field, value in (
        ("sector_recovery_streak", state.sector_recovery_streak),
        ("dynamic_k", state.dynamic_k),
        ("strategic_epoch", state.strategic_epoch),
        ("strategic_epochs_completed", state.strategic_epochs_completed),
        ("risk_anchor_candidate_streak", state.risk_anchor_candidate_streak),
        ("capital_budget_level", state.capital_budget_level),
        ("capital_budget_repair_streak", state.capital_budget_repair_streak),
        ("chronic_level", state.chronic_level),
        ("chronic_streak", state.chronic_streak),
        ("chronic_repair_streak", state.chronic_repair_streak),
    ):
        _nonnegative_integer(value, field=field)
    if state.capital_budget_level > 4:
        raise RuntimeError("capital_budget_level exceeds its supported ladder")
    if state.chronic_level > 3:
        raise RuntimeError("chronic_level exceeds its supported ladder")
    if not isinstance(state.risk_signal_state, dict):
        raise RuntimeError("risk_signal_state must be an object")
    for key, signal_value in state.risk_signal_state.items():
        _required_text(key, field="risk_signal_state key")
        lower_bound = -1.000001 if key == "correlation" else 0.0
        upper_bound = (
            1.000001
            if key
            in {
                "breadth20",
                "breadth60",
                "leader_failure",
                "correlation",
                "transition_damage",
                "trend_health",
            }
            else None
        )
        _finite_number(
            signal_value,
            field=f"risk_signal_state[{key}]",
            minimum=lower_bound,
            maximum=upper_bound,
        )

    _validate_symbol_list(state.strategic_previous_symbols, field="strategic_previous_symbols")
    _validate_symbol_list(state.risk_anchor_symbols, field="risk_anchor_symbols")
    _validate_symbol_list(state.sector_guard_symbols, field="sector_guard_symbols")
    _validate_symbol_list(state.active_leaders, field="active_leaders")
    _validate_symbol_list(state.data_hash_symbols, field="data_hash_symbols")
    if not isinstance(state.sector_guard_active, bool):
        raise RuntimeError("sector_guard_active must be boolean")
    if state.sector_guard_active and not state.sector_guard_started:
        raise RuntimeError("active sector guard requires sector_guard_started")
    if not isinstance(state.sector_shock_dates, list):
        raise RuntimeError("sector_shock_dates must be an array")
    if not isinstance(state.rotation_dates, list):
        raise RuntimeError("rotation_dates must be an array")
    if not isinstance(state.satellite_entry_dates, dict):
        raise RuntimeError("satellite_entry_dates must be an object")
    for shock_date in state.sector_shock_dates:
        _required_iso_date(shock_date, field="sector_shock_dates")


def _validate_strategy_identity_and_weights(
    *,
    state: Any,
) -> Any:
    if not isinstance(state.shock_state, str) or state.shock_state not in _SHOCK_STATES:
        raise RuntimeError("account state has invalid shock_state")
    if not isinstance(state.shock_severity, str) or state.shock_severity not in _SHOCK_SEVERITIES:
        raise RuntimeError("account state has invalid shock_severity")
    if not isinstance(state.data_hash, str) or not isinstance(state.code_hash, str):
        raise RuntimeError("account validation hashes must be text")
    if not isinstance(state.account_identity, str):
        raise RuntimeError("account identity must be text")
    try:
        validate_strategic_qualification(state.strategic_qualification)
        if state.strategic_grant is not None:
            validate_strategic_grant(state.strategic_grant)
            if state.account_identity and state.strategic_grant.account_identity != state.account_identity:
                raise ValueError("strategic grant account identity differs from account")
            pending_grants = {order.grant_id for order in state.pending_orders if order.grant_id}
            if not state.strategic_grant.terminal and pending_grants - {
                state.strategic_grant.grant_id
            }:
                raise ValueError("another strategic grant has a pending execution owner")
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"account strategic grant state is invalid: {exc}") from exc

    _validate_weight_map(state.anchor_weights, field="anchor_weights")
    if not isinstance(state.recovery_conviction_symbol, str):
        raise RuntimeError("account state has invalid recovery_conviction_symbol")
    _validate_weight_map(state.protected_weights, field="protected_weights")
    cohort_keys = _validate_symbol_list(
        state.strategic_cohort_symbols,
        field="strategic_cohort_symbols",
    )
    target_keys = _validate_weight_map(
        state.strategic_cohort_targets,
        field="strategic_cohort_targets",
    )
    restore_keys = _validate_weight_map(
        state.strategic_restore_weights,
        field="strategic_restore_weights",
    )
    if not target_keys <= cohort_keys or not restore_keys <= cohort_keys:
        raise RuntimeError("strategic weights reference symbols outside the cohort")
    if not isinstance(state.strategic_exit_bands, dict) or not isinstance(
        state.strategic_active_bands,
        dict,
    ):
        raise RuntimeError("strategic band state must be objects")
    band_keys = set(state.strategic_exit_bands)
    if band_keys != set(state.strategic_active_bands):
        raise RuntimeError("strategic exit/active band keys differ")
    if not band_keys <= cohort_keys:
        raise RuntimeError("strategic bands reference symbols outside the cohort")
    total_band_weight = 0.0
    return total_band_weight


def _validate_strategy_risk_state(state: AccountState) -> None:
    """Validate durable strategy/risk state shared by save, load, and broker sync.

    These checks deliberately constrain only durable invariants.  They do not
    require anchor symbols to be held because a causal next-open buy or a
    broker-authoritative exit can temporarily separate targets from positions.
    """
    _finite_number(state.operating_peak, field="operating_peak", minimum=0.0)
    _finite_number(state.capital_peak, field="capital_peak", minimum=0.0)
    if not isinstance(state.opportunity, str) or state.opportunity not in {
        item.value for item in Opportunity
    }:
        raise RuntimeError("account state has invalid opportunity")
    if not isinstance(state.risk, str) or state.risk not in {item.value for item in Risk}:
        raise RuntimeError("account state has invalid risk")
    total_band_weight = _validate_strategy_identity_and_weights(
        state=state,
    )
    for symbol, bands in state.strategic_exit_bands.items():
        _required_text(symbol, field="strategic band symbol")
        active = state.strategic_active_bands[symbol]
        if not isinstance(bands, list) or not bands:
            raise RuntimeError("strategic exit bands must be non-empty arrays")
        if not isinstance(active, list) or len(active) != len(bands):
            raise RuntimeError("strategic exit/active band lengths differ")
        if any(type(item) is not bool for item in active):
            raise RuntimeError("strategic active bands must contain booleans")
        for index, weight in enumerate(bands):
            total_band_weight += _finite_number(
                weight,
                field=f"strategic_exit_bands[{symbol}][{index}]",
                minimum=0.0,
                maximum=1.0,
            )
    _validate_strategy_controls_and_signals(
        state=state,
        total_band_weight=total_band_weight,
    )
    for rotation_date in state.rotation_dates:
        _required_iso_date(rotation_date, field="rotation_dates")
    for symbol, entry_date in state.satellite_entry_dates.items():
        _required_text(symbol, field="satellite_entry_dates key")
        _required_iso_date(entry_date, field=f"satellite_entry_dates[{symbol}]")
    for date_field, optional_date in (
        ("sector_guard_started", state.sector_guard_started),
        ("cooldown_until", state.cooldown_until),
        ("last_k_change_date", state.last_k_change_date),
        ("recovery_anchor_date", state.recovery_anchor_date),
        ("strategic_last_exit_date", state.strategic_last_exit_date),
        ("strategic_rearm_date", state.strategic_rearm_date),
        ("scout_entry_date", state.scout_entry_date),
        ("shock_start_date", state.shock_start_date),
        ("last_shock_date", state.last_shock_date),
        ("last_successful_run", state.last_successful_run),
        ("data_hash_as_of", state.data_hash_as_of),
    ):
        _optional_iso_date(optional_date, field=date_field)
    _validate_audit_events(state)


validate_audit_events = _validate_audit_events
validate_risk_streaks = _validate_risk_streaks
validate_strategy_risk_state = _validate_strategy_risk_state
