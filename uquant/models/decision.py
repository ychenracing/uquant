"""Immutable score, risk, target, and decision models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import canonical_control_float
from .enums import Opportunity, ReductionPolicy, Risk
from .trading import PendingOrder


@dataclass(frozen=True, slots=True)
class LeaderScore:
    """Point-in-time leadership strength, confidence, and classification."""

    symbol: str
    score: float
    confidence: float
    mature: bool
    emerging: bool
    industry: str
    components: dict[str, float]


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Risk state, exposure cap, and auditable evidence for one session."""

    state: Risk
    target_gross_cap: float
    votes: int
    evidence: dict[str, Any]
    reasons: tuple[str, ...]
    shock_state: str
    freeze_new_risk: bool = False
    reduction_level: int = 0
    severity: str = "NORMAL"


@dataclass(frozen=True, slots=True)
class Target:
    """Final desired weight and lifecycle for one symbol."""

    symbol: str
    weight: float
    lifecycle: str
    alpha_score: float
    confidence: float
    reason: str
    reduction_policy: str = ReductionPolicy.FIFO.value
    reason_code: str = "strategy_target"
    exit_kind: str = "strategy"
    entry_industry_strength: float = 0.0
    event_id: str = ""
    origin_subsystem: str = ""
    mechanism: str = ""
    origin_lifecycle: str = ""
    replaces_symbol: str | None = None
    industry_at_entry: str = ""
    industry_manifest_sha256: str = ""
    grant_id: str = ""
    epoch_id: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    """Immutable daily output containing targets, intents, and risk evidence."""

    date: str
    opportunity: Opportunity
    risk: Risk
    target_gross: float
    target_k: int
    targets: tuple[Target, ...]
    pending_orders: tuple[PendingOrder, ...]
    risk_summary: dict[str, Any]
    decision_digest: str

    def canonical_payload(self, *, effective_config_sha256: str) -> dict[str, Any]:
        """Return the complete deterministic decision contract for evidence."""

        pending_by_event = {item.event_id: item for item in self.pending_orders if item.event_id}
        targets: list[dict[str, Any]] = []
        for item in self.targets:
            retained = pending_by_event.get(item.event_id)
            event_signal_date = self.date if retained is None else retained.signal_date
            event_target_weight = item.weight if retained is None else retained.target_weight
            targets.append(
                {
                    "symbol": item.symbol,
                    "weight": round(item.weight, 12),
                    "lifecycle": item.lifecycle,
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                    "event_id": item.event_id,
                    "event_signal_date": event_signal_date,
                    "event_target_weight_hex": float(event_target_weight).hex(),
                    "origin_subsystem": item.origin_subsystem,
                    "mechanism": item.mechanism,
                    "origin_lifecycle": item.origin_lifecycle,
                    "replaces_symbol": item.replaces_symbol,
                    "industry_at_entry": item.industry_at_entry,
                    "industry_manifest_sha256": item.industry_manifest_sha256,
                    "grant_id": item.grant_id,
                    "epoch_id": item.epoch_id,
                }
            )
        return {
            "schema": "uquant.decision-control-plane.v2",
            "date": self.date,
            "opportunity": self.opportunity.value,
            "risk": {
                # Descriptive shock/severity diagnostics stay in risk_summary, but
                # are not control evidence: no independent daily carrier can replay
                # them.  The fields below are cross-bound to the frozen digest,
                # daily ledger, compiled config, or exact targets.
                "state": self.risk.value,
                "target_gross_cap": canonical_control_float(
                    float(self.risk_summary.get("target_gross_cap", 0.0))
                ),
                "system_gross_cap": canonical_control_float(
                    float(self.risk_summary.get("system_gross_cap", 0.0))
                ),
            },
            "target_gross": round(self.target_gross, 12),
            "targets": targets,
            "orders": [
                {
                    "order_id": item.order_id,
                    "signal_date": item.signal_date,
                    "snapshot_kind": ("ORIGIN" if item.signal_date == self.date else "CARRIED_FORWARD"),
                    "symbol": item.symbol,
                    "side": item.side,
                    "target_weight": round(item.target_weight, 12),
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                    "event_id": item.event_id,
                    "origin_subsystem": item.origin_subsystem,
                    "mechanism": item.mechanism,
                    "origin_lifecycle": item.origin_lifecycle,
                    "replaces_symbol": item.replaces_symbol,
                    "industry_at_entry": item.industry_at_entry,
                    "industry_manifest_sha256": item.industry_manifest_sha256,
                    "grant_id": item.grant_id,
                    "epoch_id": item.epoch_id,
                }
                for item in self.pending_orders
            ],
            "effective_config_sha256": effective_config_sha256,
        }

    def legacy_canonical_payload(self) -> dict[str, Any]:
        """Reconstruct the exact frozen schema-v3 decision digest payload."""

        return {
            "date": self.date,
            "opportunity": self.opportunity.value,
            "risk": self.risk.value,
            "targets": [
                {
                    "symbol": item.symbol,
                    "weight": round(item.weight, 12),
                    "lifecycle": item.lifecycle,
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                }
                for item in self.targets
            ],
            "orders": [
                {
                    "order_id": item.order_id,
                    "symbol": item.symbol,
                    "side": item.side,
                    "target_weight": round(item.target_weight, 12),
                    "reduction_policy": item.reduction_policy,
                    "reason_code": item.reason_code,
                    "exit_kind": item.exit_kind,
                }
                for item in self.pending_orders
            ],
        }


__all__ = ("Decision", "LeaderScore", "RiskAssessment", "Target")
