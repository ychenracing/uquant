"""Production settings and the complete effective policy identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Literal

from .policies import (
    FeaturesPolicy,
    LeaderSelectionPolicy,
    OpportunityPolicy,
    PortfolioPolicy,
    RecoveryPolicy,
    RiskPolicy,
    SectorRiskPolicy,
    StrategicPolicy,
)
from .validation.execution import validate_execution
from .validation.market import validate_market, validate_public_inputs
from .validation.portfolio import validate_portfolio
from .validation.recovery import validate_recovery
from .validation.risk import validate_crisis_and_sector, validate_risk
from .validation.sentinel import validate_sentinel
from .validation.strategic import (
    validate_strategic_discovery,
    validate_strategic_lifecycle,
    validate_strategic_transition,
)


def canonical_control_float(value: float) -> float:
    """Serialize one control-plane float with the exact schema-v2 precision."""

    return round(float(value), 12)


@dataclass(frozen=True, slots=True)
class SystemConfig(PortfolioPolicy, FeaturesPolicy, LeaderSelectionPolicy, RecoveryPolicy, StrategicPolicy, OpportunityPolicy, RiskPolicy, SectorRiskPolicy):
    """Immutable account, execution, and portfolio settings.

    The default instance is shared by daily decisions and historical replay so
    both paths use identical constraints. ``override`` returns a new instance;
    it never mutates the production defaults in place.
    """

    initial_cash: float = 2_000_000.0
    max_gross: float = 1.0
    max_symbol_weight: float = 0.60
    max_positions: int = 6
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty: float = 0.0005
    transfer_fee: float = 0.00001
    slippage: float = 0.001
    max_volume_participation: float = 0.005
    minimum_median_amount: float = 20_000_000.0
    min_trade_value: float = 20_000.0
    risk_sentinel_mode: Literal[
        "SHADOW",
        "FREEZE_ONLY",
    ] = "FREEZE_ONLY"

    def __post_init__(self) -> None:
        """Validate public inputs and the complete effective policy invariants."""

        validate_public_inputs(self)
        validate_market(self)
        validate_execution(self)
        validate_crisis_and_sector(self)
        validate_portfolio(self)
        validate_recovery(self)
        validate_strategic_discovery(self)
        validate_strategic_transition(self)
        validate_strategic_lifecycle(self)
        validate_risk(self)
        validate_sentinel(self)

    def __setstate__(self, state: list[object]) -> None:
        """Accept only the current configuration pickle shape and valid inputs."""
        public = fields(self)
        if not isinstance(state, list) or len(state) != len(public):
            raise ValueError("unsupported configuration pickle shape")
        for field, value in zip(public, state, strict=True):
            object.__setattr__(self, field.name, value)
        self.__post_init__()

    def override(self, **changes: Any) -> SystemConfig:
        """Return a validated immutable configuration with selected fields replaced."""

        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        """Return every effective setting and fixed rule for identity and reporting."""

        payload = asdict(self)
        for owner in (PortfolioPolicy, FeaturesPolicy, LeaderSelectionPolicy, RecoveryPolicy, StrategicPolicy, OpportunityPolicy, RiskPolicy, SectorRiskPolicy):
            payload.update((name, getattr(self, name)) for name in owner.__annotations__)
        return payload


DEFAULT_CONFIG = SystemConfig()


def config_fingerprint(cfg: SystemConfig = DEFAULT_CONFIG) -> str:
    """Return a canonical digest of every effective production setting."""

    payload = cfg.to_dict()
    if payload["risk_sentinel_causal_confirmation_enabled"] is False:
        # The disabled authority switch is excluded from the canonical payload
        # to preserve the reviewed economic identity; enabling it creates a
        # distinct configuration identity.
        payload.pop("risk_sentinel_causal_confirmation_enabled")
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
