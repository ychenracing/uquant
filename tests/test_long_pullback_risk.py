"""Bounded BaseRisk permission units, not full historical risk replays."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_long_pullback_entry import EPISODES, _native_inputs

from uquant.config import DEFAULT_CONFIG
from uquant.models.strategic_epoch import StrategicEpoch
from uquant.models.strategic_grant import StrategicGrantIntent
from uquant.risk.pullback import authorize_pullback_entry
from uquant.types import AccountState, PendingOrder, Position, Risk, RiskAssessment


def _case():
    symbol, date, frame, leader, _ = _native_inputs(*EPISODES[0])
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    evidence = dict(
        capital_budget_level=0,
        chronic_level=0,
        independent_damage=False,
        sector_guard_active=False,
        acute_sector_evacuation=False,
        strategic_damage_guard=False,
    )
    risk = RiskAssessment(Risk.CAUTION, 0.60, 4, evidence, (), "NONE", freeze_new_risk=True)
    return symbol, date, {symbol: frame}, {symbol: leader}, account, risk


def _authorize(case):
    _, date, panel, leaders, account, risk = case
    return authorize_pullback_entry(
        date=date, risk=risk, account=account, user_panel=panel, leaders=leaders, cfg=DEFAULT_CONFIG
    )


def _denied(case):
    result = _authorize(case)
    assert not result.evidence.get("ordinary_pullback_permission")
    assert (result.state, result.freeze_new_risk, result.target_gross_cap) == (
        case[-1].state,
        case[-1].freeze_new_risk,
        case[-1].target_gross_cap,
    )


@pytest.mark.parametrize("state", [Risk.NORMAL, Risk.CAUTION])
def test_final_safe_flat_base_risk_may_offer_only_shared_core_budget(state):
    case = list(_case())
    case[-1] = replace(case[-1], state=state, freeze_new_risk=state is Risk.CAUTION)
    result = _authorize(case)
    permission = result.evidence["ordinary_pullback_permission"]
    assert permission["as_of"] == str(case[1].date())
    assert permission["maximum_weight"] == DEFAULT_CONFIG.core_admission_weight
    assert permission["risk_state"] == state.value
    assert set(permission["proofs"]) == {case[0]}
    assert permission["proofs"][case[0]]["block"] == "READY"
    assert (result.state, result.freeze_new_risk, result.target_gross_cap) == (
        case[-1].state,
        case[-1].freeze_new_risk,
        case[-1].target_gross_cap,
    )
    assert case[-2].strategic_grant is None and not case[-2].strategic_epochs
    assert not case[-2].order_ledger and not case[-2].fills


@pytest.mark.parametrize(
    "field",
    [
        "capital_budget_level",
        "chronic_level",
        "independent_damage",
        "sector_guard_active",
        "acute_sector_evacuation",
        "strategic_damage_guard",
    ],
)
@pytest.mark.parametrize("failure", ["missing", "unsafe", "invalid"])
def test_risk_permission_requires_explicit_safe_evidence(field, failure):
    case = list(_case())
    evidence = dict(case[-1].evidence)
    if failure == "missing":
        evidence.pop(field)
    elif failure == "unsafe":
        evidence[field] = 1 if field.endswith("_level") else True
    else:
        evidence[field] = float("nan")
    case[-1] = replace(case[-1], evidence=evidence)
    _denied(case)


@pytest.mark.parametrize(
    "restriction",
    ["risk_off", "crisis", "reduction", "shock", "capital", "chronic", "sector_guard", "sentinel"],
)
def test_hard_risk_and_actual_account_damage_reject(restriction):
    case = list(_case())
    account, risk = case[-2:]
    if restriction in ("risk_off", "crisis"):
        case[-1] = replace(risk, state=Risk.RISK_OFF if restriction == "risk_off" else Risk.CRISIS)
    elif restriction == "reduction":
        case[-1] = replace(risk, reduction_level=2)
    elif restriction == "shock":
        case[-1] = replace(risk, shock_state="RECOVERY")
    elif restriction == "capital":
        account.capital_budget_level = 1
    elif restriction == "chronic":
        account.chronic_level = 1
    elif restriction == "sector_guard":
        account.sector_guard_active = True
    else:
        case[-1] = replace(risk, evidence={**risk.evidence, "sentinel_freeze_new_risk": True})
    _denied(case)


@pytest.mark.parametrize(
    "restriction",
    [
        "position",
        "pending_buy",
        "pending_sell",
        "live_ledger",
        "grant",
        "epoch",
        "active_epoch",
        "anchor_weights",
        "protected_weights",
        "strategic_restore_weights",
        "strategic_cohort_targets",
    ],
)
def test_new_permission_never_takes_occupied_or_unsettled_capital(restriction):
    case = list(_case())
    symbol, date, _, _, account, _ = case
    session = str(date.date())
    if restriction == "position":
        account.positions[symbol] = Position(symbol, 100, 60.0, session, 60.0)
    elif restriction.startswith("pending_"):
        account.pending_orders = [
            PendingOrder(
                session, symbol, restriction.split("_")[1].upper(), 0.20, "prior submitted intent", "CORE"
            )
        ]
    elif restriction == "live_ledger":
        from uquant.application.target_attribution import attach_target_attribution
        from uquant.execution import plan_orders, reconcile_account_orders
        from uquant.types import Target
        from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

        targets = attach_target_attribution(
            "compute",
            REQUIRED_AI_UNIVERSE_SHA256,
            signal_date=session,
            targets=(
                Target(
                    symbol,
                    0.20,
                    "CORE",
                    0.9,
                    0.9,
                    "prior ordinary intent",
                    origin_subsystem="LEADER",
                    mechanism="LEADER_SELECTION",
                    origin_lifecycle="CORE",
                ),
            ),
        )
        planned = plan_orders(
            signal_date=session, targets=targets, account=account, prices={symbol: 60.0}, cfg=DEFAULT_CONFIG
        )
        reconcile_account_orders(account=account, previous=[], current=planned, submitted_date=session)
        assert account.order_ledger and not account.pending_orders
    elif restriction == "grant":
        # Negative boundary object only; no fabricated executable authorization.
        account.strategic_grant = StrategicGrantIntent(
            "grant:test", symbol, "proof:test", "persistent_industry", "a" * 64, session, session
        )
    elif restriction == "epoch":
        account.strategic_epochs = [
            StrategicEpoch(
                "epoch:test",
                symbol,
                "proof:test",
                "persistent_industry",
                "FULL_COHORT",
                "grant:test",
                session,
            )
        ]
    elif restriction == "active_epoch":
        account.active_strategic_epoch_id = "epoch:unsettled"
    else:
        getattr(account, restriction)[symbol] = 0.20
    _denied(case)


@pytest.mark.parametrize("restriction", ["no_panel", "no_leader", "bad_quality", "insufficient_capacity"])
def test_only_current_stock_proof_with_actual_account_capacity_may_be_offered(restriction):
    case = list(_case())
    symbol = case[0]
    if restriction == "no_panel":
        case[2] = {}
    elif restriction == "no_leader":
        case[3] = {}
    elif restriction == "bad_quality":
        case[3] = {symbol: replace(case[3][symbol], confidence=0.0)}
    else:
        # Liquidity remains above20m, but cannot support this real account's20%.
        case[2] = {symbol: case[2][symbol].assign(amount=DEFAULT_CONFIG.minimum_median_amount)}
        assert DEFAULT_CONFIG.minimum_median_amount * DEFAULT_CONFIG.max_volume_participation < (
            case[-2].initial_cash * DEFAULT_CONFIG.core_admission_weight
        )
    _denied(case)


def test_missing_proof_does_not_retain_an_old_permission():
    case = list(_case())
    previous = _authorize(case)
    assert previous.evidence.get("ordinary_pullback_permission")
    case[-1] = previous
    case[2] = {}
    _denied(case)


def test_cancelled_but_broker_live_strategic_remainder_is_not_settled():
    from uquant.models.trading import AccountOrder, late_strategic_fill_allowed

    case = list(_case())
    symbol, date, _, _, account, _ = case
    session = str(date.date())
    # Explicit negative broker-state unit, not a historical execution claim.
    order = AccountOrder(
        "broker:late",
        session,
        session,
        symbol,
        "BUY",
        0.20,
        "strategic partial remainder",
        "CORE",
        status="CANCELLED",
        requested_shares=200,
        filled_shares=100,
        remaining_shares=100,
        cancel_reason="strategic partial remainder replaced",
        grant_id="grant:old",
    )
    assert late_strategic_fill_allowed(order)
    account.order_ledger = [order]
    _denied(case)


def test_multiple_current_proofs_share_one_budget_and_ignore_reference_only_leaders():
    case = list(_case())
    symbol = case[0]
    other, reference = "sh688072", "sh688012"
    # Duplicate quality only isolates shared-budget cardinality, not original
    # historical evidence for these other names or simulated economic orders.
    case[2] = {**case[2], other: case[2][symbol].copy()}
    case[3] = {
        **case[3],
        other: replace(case[3][symbol], symbol=other),
        reference: replace(case[3][symbol], symbol=reference),
    }
    permission = _authorize(case).evidence["ordinary_pullback_permission"]
    assert set(permission["proofs"]) == {symbol, other}
    assert permission["maximum_weight"] == DEFAULT_CONFIG.core_admission_weight
    assert reference not in permission["proofs"]


def test_permission_cannot_enlarge_existing_risk_gross_cap():
    case = list(_case())
    case[-1] = replace(case[-1], target_gross_cap=0.10)
    result = _authorize(case)
    assert result.evidence["ordinary_pullback_permission"]["maximum_weight"] == 0.10
    assert result.target_gross_cap == 0.10
