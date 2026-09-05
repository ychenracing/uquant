from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_epoch import StrategicEpochStatus
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.rearm import (
    CASH_REARM_HEALTHY_SESSION_LIMITS,
    observe_strategic_cash_rearm,
)
from uquant.types import (
    AccountOrder,
    AccountState,
    LeaderScore,
    Opportunity,
    OrderStatus,
    PendingOrder,
    Risk,
    RiskAssessment,
    StrategicQualificationObservation,
)


def _snapshot(*, score: float = 0.95) -> dict[str, float]:
    return {
        "leader_score": score,
        "leader_confidence": 0.95,
        "secular_score": score,
        "secular_confidence": 0.95,
        "momentum60": 0.90,
        "momentum120": 0.90,
        "relative_strength": 0.90,
        "trend_persistence": 1.0,
        "ret20": 0.20,
        "ret60": 0.30,
        "ret120": 0.50,
        "persistent_ret240": 2.00,
        "industry_confidence": 0.95,
        "liquidity_confirmation": 1.0,
    }


def _roles(as_of: str = "2026-01-05"):
    symbols = ("sz300308", "sz300394", "sz300502")
    return build_strategic_universe_roles(
        as_of=as_of,
        tradable_symbols=symbols,
        qualification_reference_symbols=symbols,
        risk_reference_symbols=("sh000300", "sh000682"),
        industries={symbol: "optical" for symbol in symbols},
        available_symbols=(*symbols, "sh000300", "sh000682"),
    )


def _risk(**overrides: object) -> RiskAssessment:
    values: dict[str, object] = {
        "state": Risk.NORMAL,
        "target_gross_cap": 0.50,
        "votes": 0,
        "evidence": {
            "freeze_new_risk": True,
            "reference_coverage": 1.0,
            "transition_damage": 0.10,
            "sector_guard_active": False,
            "strategic_damage_guard": False,
            "acute_sector_evacuation": False,
            "sentinel_freeze_new_risk": False,
            "configured_user_universe_size": 3,
            "risk_anchor_group_count": 3,
            "breadth20": 1.0,
            "broad_ret20": 0.05,
            "tech_ret20": 0.05,
            "broad_ret120": 0.0,
            "tech_ret120": 0.0,
        },
        "reasons": ("portfolio capital damage",),
        "shock_state": "NONE",
        "freeze_new_risk": True,
        "reduction_level": 2,
    }
    values.update(overrides)
    return RiskAssessment(**values)  # type: ignore[arg-type]


def _strict_inputs() -> tuple[dict[str, dict[str, float]], dict[str, LeaderScore]]:
    symbols = ("sz300308", "sz300394", "sz300502")
    return (
        {symbol: _snapshot(score=0.95 - 0.01 * index) for index, symbol in enumerate(symbols)},
        {
            symbol: _leader(symbol, 0.95 - 0.01 * index, industry="optical")
            for index, symbol in enumerate(symbols)
        },
    )


def _qualification(*, ready: bool = True) -> StrategicQualificationObservation:
    return StrategicQualificationObservation(
        candidate_symbol="sz300308",
        qualification_signature="qualification:optical",
        qualification_route="established",
        qualification_evidence_sha256="a" * 64,
        qualification_ready=ready,
        deployment_blocked=True,
        deployment_block_reason="freeze_new_risk",
        qualification_streak=3 if ready else 0,
        qualification_last_observed_session="2025-01-02",
        qualification_quorum="FULL_COHORT",
        candidate_symbols=["sz300308", "sz300394", "sz300502"],
    )


def test_cash_rearm_uses_fixed_healthy_boundary_without_resetting_budget() -> None:
    snapshots, leaders = _strict_inputs()
    account = AccountState.empty(2_000_000.0)
    account.capital_budget_level = 3
    account.opportunity = Opportunity.TREND.value
    account.strategic_qualification = _qualification()
    limit = CASH_REARM_HEALTHY_SESSION_LIMITS[account.capital_budget_level]

    authorized = False
    previous_session = ""
    for session in pd.bdate_range("2025-01-02", periods=limit):
        authorized = observe_strategic_cash_rearm(
            account=account,
            risk=_risk(),
            universe=_roles(str(session.date())),
            snapshots=snapshots,
            leaders=leaders,
            candidate_symbol="sz300308",
            qualification_ready=True,
            observed_session=str(session.date()),
            previous_observed_session=previous_session,
            cfg=DEFAULT_CONFIG,
        )
        previous_session = str(session.date())

    assert authorized is True
    assert account.flat_book_capital_repair.healthy_session_count == limit
    assert account.flat_book_capital_repair.status == "READY"
    assert account.strategic_cash_rearm.status == "AUTHORIZED"
    assert not any(
        key.startswith("strategic_cash_rearm_") for key in account.candidate_tenure
    )
    assert CASH_REARM_HEALTHY_SESSION_LIMITS[3] == 60
    assert account.capital_budget_level == 3
    assert account.capital_budget_repair_streak == 0


@pytest.mark.parametrize(
    "mutation",
    (
        "pending_order",
        "unsettled_order",
        "protected_owner",
        "recovery_owner",
        "reference_gap",
        "unrepaired_shock",
        "weak_absolute_quality",
        "candidate_not_ready",
    ),
)
def test_cash_rearm_fails_closed_for_each_safety_boundary(mutation: str) -> None:
    snapshots, leaders = _strict_inputs()
    account = AccountState.empty(2_000_000.0)
    account.capital_budget_level = 3
    account.opportunity = Opportunity.TREND.value
    account.strategic_qualification = _qualification()
    risk = _risk()
    universe = _roles()
    qualification_ready = True
    if mutation == "pending_order":
        account.pending_orders.append(
            PendingOrder(
                signal_date="2026-01-02",
                symbol="sz300308",
                side="BUY",
                target_weight=0.20,
                reason="unsettled",
                lifecycle="CORE",
            )
        )
    elif mutation == "unsettled_order":
        account.order_ledger.append(
            AccountOrder(
                order_id="O000000001",
                signal_date="2026-01-02",
                submitted_date="2026-01-02",
                symbol="sz300308",
                side="BUY",
                target_weight=0.20,
                reason="unsettled",
                lifecycle="CORE",
                status=OrderStatus.OPEN.value,
                remaining_shares=100,
            )
        )
    elif mutation == "protected_owner":
        account.protected_weights = {"sz300308": 0.10}
        # An unresolved recorded strategic owner is still a blocking residue.
        account.protected_weight_epoch_ids = {"sz300308": "epoch_" + "f" * 64}
    elif mutation == "recovery_owner":
        account.anchor_weights = {"sz300308": 0.10}
    elif mutation == "reference_gap":
        evidence = dict(risk.evidence)
        evidence["reference_coverage"] = 0.99
        risk = _risk(evidence=evidence)
    elif mutation == "unrepaired_shock":
        risk = _risk(shock_state="RECOVERY")
    elif mutation == "weak_absolute_quality":
        snapshots["sz300308"] = _snapshot(score=0.89)
        snapshots["sz300394"] = _snapshot(score=0.89)
        snapshots["sz300502"] = _snapshot(score=0.89)
    elif mutation == "candidate_not_ready":
        qualification_ready = False
        account.strategic_qualification = _qualification(ready=False)

    authorized = observe_strategic_cash_rearm(
        account=account,
        risk=risk,
        universe=universe,
        snapshots=snapshots,
        leaders=leaders,
        candidate_symbol="sz300308",
        qualification_ready=qualification_ready,
        observed_session="2026-01-05",
        previous_observed_session="2026-01-02",
        cfg=DEFAULT_CONFIG,
    )

    assert authorized is False


def test_zero_risk_anchor_count_is_available_evidence_not_missing_coverage() -> None:
    snapshots, leaders = _strict_inputs()
    account = AccountState.empty(2_000_000.0)
    account.capital_budget_level = 3
    account.opportunity = Opportunity.TREND.value
    account.strategic_qualification = _qualification()
    evidence = dict(_risk().evidence)
    evidence["risk_anchor_group_count"] = 0

    authorized = False
    previous_session = ""
    for session in pd.bdate_range(
        "2026-01-05",
        periods=CASH_REARM_HEALTHY_SESSION_LIMITS[account.capital_budget_level],
    ):
        authorized = observe_strategic_cash_rearm(
            account=account,
            risk=_risk(evidence=evidence),
            universe=_roles(str(session.date())),
            snapshots=snapshots,
            leaders=leaders,
            candidate_symbol="sz300308",
            qualification_ready=True,
            observed_session=str(session.date()),
            previous_observed_session=previous_session,
            cfg=DEFAULT_CONFIG,
        )
        previous_session = str(session.date())

    assert authorized is True


def test_level_three_cash_rearm_creates_only_one_formal_bounded_probe() -> None:
    dates = pd.bdate_range("2023-01-02", periods=247)
    symbols = ("sz300308", "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    leaders = {
        symbol: _leader(symbol, 0.95 - index * 0.01, industry="optical")
        for index, symbol in enumerate(symbols)
    }
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:cash-rearm"
    account.code_hash = "code:production"
    account.capital_budget_level = 3
    account.opportunity = Opportunity.TREND.value
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    targets = ()

    for session in dates[-85:-24]:
        targets = allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=deepcopy(_risk()),
            user_panel=panel,
            leaders=leaders,
            account=account,
            prices={symbol: float(panel[symbol].loc[session, "close"]) for symbol in symbols},
        )

    positive = [target for target in targets if target.weight > 0.0]
    assert len(positive) == 1
    assert positive[0].weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    assert positive[0].grant_id
    assert positive[0].epoch_id
    assert account.strategic_grant is not None
    assert account.strategic_grant.authorization_id
    assert account.strategic_grant.candidate_symbol == positive[0].symbol
    assert account.strategic_grant.target_weight == pytest.approx(
        DEFAULT_CONFIG.core_admission_weight
    )
    assert len(account.strategic_epochs) == 1
    assert account.strategic_epochs[0].realized_status == StrategicEpochStatus.PROBE.value
    assert account.active_strategic_epoch_id == ""
    assert account.capital_budget_level == 3
    assert account.strategic_cash_rearm.status == "CONSUMED"
    assert account.strategic_cash_rearm.consumed_grant_id == account.strategic_grant.grant_id
    assert not any(
        key.startswith("strategic_cash_rearm_") for key in account.candidate_tenure
    )
