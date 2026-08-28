from __future__ import annotations

import pandas as pd
from test_lifecycle_and_risk import _leader as _production_leader
from test_lifecycle_and_risk import _strategic_frame

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.models.strategic_universe import (
    ReferenceAvailability,
    build_strategic_universe_declaration,
    build_strategic_universe_roles,
)
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.quorum import (
    StrategicQuorumRoute,
    evaluate_strategic_quorum,
    route_consistent_owner_quality,
    strict_absolute_owner_quality,
)
from uquant.types import AccountState, LeaderScore, Opportunity, Risk, RiskAssessment
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _leader(symbol: str, *, score: float = 0.95, industry: str = "optical") -> LeaderScore:
    return LeaderScore(
        symbol=symbol,
        score=score,
        confidence=0.90,
        mature=True,
        emerging=False,
        industry=industry,
        components={
            "secular_score": 0.90,
            "secular_confidence": 0.90,
            "industry_inference_confidence": 0.95,
            "unknown_industry": 0.0,
            "momentum60": 0.85,
            "momentum120": 0.85,
            "relative_strength": 0.85,
            "trend_persistence": 0.90,
        },
    )


def _snapshot(*, score: float = 0.95, ret120: float = 0.30) -> dict[str, float]:
    return {
        "leader_score": score,
        "leader_confidence": 0.90,
        "secular_score": 0.90,
        "secular_confidence": 0.90,
        "momentum60": 0.85,
        "momentum120": 0.85,
        "relative_strength": 0.85,
        "trend_persistence": 0.90,
        "ret20": 0.10,
        "ret60": 0.20,
        "ret120": ret120,
        "persistent_ret240": 0.40,
        "industry_confidence": 0.95,
        "liquidity_confirmation": 1.0,
    }


def _risk() -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=0.95,
        votes=0,
        evidence={
            "breadth20": 0.80,
            "broad_ret20": 0.05,
            "tech_ret20": 0.08,
            "broad_ret120": 0.12,
            "tech_ret120": 0.20,
            "risk_anchor_group_count": 3,
        },
        reasons=(),
        shock_state="NONE",
    )


def _roles(*, unavailable: tuple[str, ...] = ()):
    symbols = ("sz300308", "sz300502", "sz300394", "sh688008")
    return build_strategic_universe_roles(
        as_of="2026-01-05",
        tradable_symbols=("sz300308", "sz300502", "sz300394"),
        qualification_reference_symbols=symbols,
        risk_reference_symbols=("sh000300", "sh000682"),
        industries={symbol: "optical" for symbol in symbols},
        available_symbols=set(symbols) - set(unavailable) | {"sh000300", "sh000682"},
    )


def test_universe_roles_bind_point_in_time_identity_and_unavailable_references() -> None:
    roles = _roles(unavailable=("sh688008",))

    assert roles.availability("sh688008") is ReferenceAvailability.UNAVAILABLE
    assert roles.availability("sz300308") is ReferenceAvailability.AVAILABLE
    assert roles.tradable_symbols == ("sz300308", "sz300394", "sz300502")
    assert roles.tradable_identity != roles.qualification_reference_identity
    assert roles.qualification_reference_identity != roles.risk_reference_identity
    assert len(roles.point_in_time_industry_identity) == 64


def test_role_absent_is_distinct_from_expected_but_unavailable() -> None:
    roles = _roles(unavailable=("sh688008",))

    assert roles.availability("sh688008") is ReferenceAvailability.UNAVAILABLE
    assert roles.availability("sh688347") is ReferenceAvailability.ROLE_ABSENT


def test_reference_declaration_has_independent_qualification_and_risk_membership() -> None:
    declaration = build_strategic_universe_declaration(
        qualification_reference_symbols=("sz300394", "sz300502"),
        risk_reference_symbols=("sh688008", "sz300394"),
    )

    assert declaration.qualification_reference_symbols == ("sz300394", "sz300502")
    assert declaration.risk_reference_symbols == ("sh688008", "sz300394")


def test_universe_role_identity_changes_only_when_declared_role_or_availability_changes() -> None:
    first = _roles()
    same_roles_next_session = build_strategic_universe_roles(
        as_of="2026-01-06",
        tradable_symbols=first.tradable_symbols,
        qualification_reference_symbols=first.qualification_reference_symbols,
        risk_reference_symbols=first.risk_reference_symbols,
        industries=dict(first.point_in_time_industries),
        available_symbols=first.available_symbols,
    )
    missing_expected_reference = build_strategic_universe_roles(
        as_of="2026-01-06",
        tradable_symbols=first.tradable_symbols,
        qualification_reference_symbols=first.qualification_reference_symbols,
        risk_reference_symbols=first.risk_reference_symbols,
        industries=dict(first.point_in_time_industries),
        available_symbols=set(first.available_symbols) - {"sh688008"},
    )

    assert same_roles_next_session.tradable_identity == first.tradable_identity
    assert (
        same_roles_next_session.qualification_reference_identity
        == first.qualification_reference_identity
    )
    assert same_roles_next_session.risk_reference_identity == first.risk_reference_identity
    assert (
        same_roles_next_session.point_in_time_industry_identity
        == first.point_in_time_industry_identity
    )
    assert (
        missing_expected_reference.qualification_reference_identity
        != first.qualification_reference_identity
    )


def test_risk_role_identity_tracks_membership_while_availability_is_a_pause() -> None:
    """Catches one missing risk print restarting an account repair episode."""

    first = _roles()
    temporarily_unavailable = build_strategic_universe_roles(
        as_of="2026-01-06",
        tradable_symbols=first.tradable_symbols,
        qualification_reference_symbols=first.qualification_reference_symbols,
        risk_reference_symbols=first.risk_reference_symbols,
        industries=dict(first.point_in_time_industries),
        available_symbols=set(first.available_symbols) - {"sh000300"},
    )

    assert temporarily_unavailable.availability("sh000300") is ReferenceAvailability.UNAVAILABLE
    assert temporarily_unavailable.risk_reference_identity == first.risk_reference_identity


def test_full_cohort_keeps_existing_gross_semantics() -> None:
    symbols = ("sz300308", "sz300502", "sz300394")
    result = evaluate_strategic_quorum(
        owner_symbol="sz300308",
        candidate_symbols=symbols,
        snapshots={symbol: _snapshot() for symbol in (*symbols, "sh688008")},
        leaders={symbol: _leader(symbol) for symbol in (*symbols, "sh688008")},
        risk=_risk(),
        universe=_roles(),
        cfg=DEFAULT_CONFIG,
    )

    assert result.qualified is True
    assert result.route is StrategicQuorumRoute.FULL_COHORT
    assert result.required_confirm_days == DEFAULT_CONFIG.strategic_cohort_confirm_days
    assert result.restricted_initial_weight is None


def test_strong_pair_and_absolute_single_are_bounded_by_existing_core_weight() -> None:
    roles = _roles()
    snapshots = {
        symbol: _snapshot()
        for symbol in ("sz300308", "sz300502", "sz300394", "sh688008")
    }
    leaders = {symbol: _leader(symbol) for symbol in snapshots}

    pair = evaluate_strategic_quorum(
        owner_symbol="sz300308",
        candidate_symbols=("sz300308", "sz300502"),
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        universe=roles,
        cfg=DEFAULT_CONFIG,
    )
    single = evaluate_strategic_quorum(
        owner_symbol="sz300308",
        candidate_symbols=("sz300308",),
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        universe=roles,
        cfg=DEFAULT_CONFIG,
    )

    expected_cap = min(
        DEFAULT_CONFIG.core_admission_weight,
        DEFAULT_CONFIG.max_symbol_weight,
        _risk().target_gross_cap,
    )
    assert pair.route is StrategicQuorumRoute.STRONG_PAIR
    assert pair.restricted_initial_weight == expected_cap
    assert pair.required_confirm_days == DEFAULT_CONFIG.strategic_two_name_confirm_days
    assert single.route is StrategicQuorumRoute.ABSOLUTE_SINGLE
    assert single.restricted_initial_weight == expected_cap
    assert single.required_confirm_days == DEFAULT_CONFIG.strategic_one_name_confirm_days


def test_one_unavailable_ghost_witness_does_not_zero_absolute_candidate() -> None:
    snapshots = {
        symbol: _snapshot()
        for symbol in ("sz300308", "sz300502", "sz300394")
    }
    leaders = {symbol: _leader(symbol) for symbol in snapshots}

    result = evaluate_strategic_quorum(
        owner_symbol="sz300308",
        candidate_symbols=("sz300308",),
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        universe=_roles(unavailable=("sh688008",)),
        cfg=DEFAULT_CONFIG,
    )

    assert result.qualified is True
    assert result.route is StrategicQuorumRoute.ABSOLUTE_SINGLE
    assert "sh688008" in result.unavailable_references


def test_insufficient_industry_coverage_fails_closed_without_negative_owner_score() -> None:
    snapshots = {"sz300308": _snapshot()}
    leaders = {"sz300308": _leader("sz300308")}
    result = evaluate_strategic_quorum(
        owner_symbol="sz300308",
        candidate_symbols=("sz300308",),
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        universe=_roles(unavailable=("sz300502", "sz300394", "sh688008")),
        cfg=DEFAULT_CONFIG,
    )

    assert result.qualified is False
    assert result.route is StrategicQuorumRoute.NONE
    assert result.owner_absolute_quality is True
    assert "INDUSTRY_REFERENCE_COVERAGE" in result.reasons


def test_absolute_single_cannot_bypass_negative_owner_quality() -> None:
    snapshots = {
        "sz300308": _snapshot(score=0.30, ret120=-0.20),
        "sz300502": _snapshot(),
    }
    leaders = {
        "sz300308": _leader("sz300308", score=0.30),
        "sz300502": _leader("sz300502"),
    }
    result = evaluate_strategic_quorum(
        owner_symbol="sz300308",
        candidate_symbols=("sz300308",),
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        universe=_roles(),
        cfg=DEFAULT_CONFIG,
    )

    assert result.qualified is False
    assert result.owner_absolute_quality is False
    assert "OWNER_ABSOLUTE_QUALITY" in result.reasons


def test_route_consistent_owner_quality_does_not_apply_single_name_floor_to_full_cohort() -> None:
    snapshot = _snapshot(score=0.80)
    snapshots = {"sz300394": snapshot}
    leaders = {"sz300394": _leader("sz300394", score=0.80)}

    assert strict_absolute_owner_quality(
        symbol="sz300394",
        snapshots=snapshots,
        leaders=leaders,
        cfg=DEFAULT_CONFIG,
    ) is False
    assert route_consistent_owner_quality(
        symbol="sz300394",
        qualification_route="established",
        quorum_route=StrategicQuorumRoute.FULL_COHORT.value,
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        cfg=DEFAULT_CONFIG,
    ) is True
    assert route_consistent_owner_quality(
        symbol="sz300394",
        qualification_route="established",
        quorum_route=StrategicQuorumRoute.ABSOLUTE_SINGLE.value,
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        cfg=DEFAULT_CONFIG,
    ) is False


def test_route_consistent_owner_quality_reuses_the_original_route_gates() -> None:
    snapshot = _snapshot(score=0.80)
    snapshot["ret20"] = DEFAULT_CONFIG.strategic_long_cycle_min_ret20 - 0.01
    snapshots = {"sz300394": snapshot}
    leaders = {"sz300394": _leader("sz300394", score=0.80)}

    assert route_consistent_owner_quality(
        symbol="sz300394",
        qualification_route="established",
        quorum_route=StrategicQuorumRoute.FULL_COHORT.value,
        snapshots=snapshots,
        leaders=leaders,
        risk=_risk(),
        cfg=DEFAULT_CONFIG,
    ) is False


def test_reference_symbols_confirm_qualification_but_never_receive_targets() -> None:
    dates = pd.bdate_range("2023-01-02", periods=250)
    tradable = ("sz300308",)
    references = ("sz300308", "sz300502", "sz300394", "sh688008")
    qualification_panel = {
        symbol: _strategic_frame(dates) for symbol in references
    }
    for frame in qualification_panel.values():
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * 0.99
        frame["volume"] = 100_000_000.0
    user_panel = {symbol: qualification_panel[symbol] for symbol in tradable}
    qualification_leaders = {
        symbol: _production_leader(symbol, 0.95, industry="optical")
        for symbol in references
    }
    account = AccountState.empty(2_000_000.0)
    account.account_identity = "account:primary"
    account.code_hash = "code:production"
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()),
        tradable_symbols=tradable,
        qualification_reference_symbols=references,
        risk_reference_symbols=("sh000300", "sh000682"),
        industries={symbol: "optical" for symbol in references},
        available_symbols=(*references, "sh000300", "sh000682"),
    )
    targets = ()

    qualification_sessions = dates[-7:-3]
    assert len(qualification_sessions) == DEFAULT_CONFIG.strategic_one_name_confirm_days
    for session in qualification_sessions:
        targets = allocator.allocate(
            date=session,
            opportunity=Opportunity.TREND,
            risk=_risk(),
            user_panel=user_panel,
            leaders={symbol: qualification_leaders[symbol] for symbol in tradable},
            account=account,
            prices={"sz300308": float(user_panel["sz300308"].loc[session, "close"])},
            qualification_panel=qualification_panel,
            qualification_leaders=qualification_leaders,
            strategic_universe=roles,
        )

    assert account.strategic_qualification.qualification_ready is True
    assert account.strategic_qualification.candidate_symbol == "sz300308"
    assert account.strategic_grant is not None
    assert len(account.strategic_epochs) == 1
    assert account.strategic_epochs[0].realized_status == "PROBE"
    assert account.active_strategic_epoch_id == ""
    assert account.strategic_grant.epoch_id == account.strategic_epochs[0].epoch_id
    assert {
        target.epoch_id for target in targets if target.symbol == "sz300308"
    } == {account.strategic_epochs[0].epoch_id}
    assert {target.symbol for target in targets} <= set(tradable)
    assert all(target.symbol not in set(references) - set(tradable) for target in targets)
    assert account.strategic_tradable_universe_identity == roles.tradable_identity
    assert (
        account.strategic_qualification_universe_identity
        == roles.qualification_reference_identity
    )
    assert account.strategic_risk_universe_identity == roles.risk_reference_identity

    signal_session = qualification_sessions[-1]
    attributed = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=str(signal_session.date()),
        targets=targets,
    )
    planned = plan_orders(
        signal_date=str(signal_session.date()),
        targets=attributed,
        account=account,
        prices={"sz300308": float(user_panel["sz300308"].loc[signal_session, "close"])},
        cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=[],
            current=planned,
            submitted_date=str(signal_session.date()),
        )
    )
    first_fill_session = dates[-3]
    first_fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=first_fill_session,
        account=account,
        panel=user_panel,
    )
    assert first_fills
    assert account.strategic_epochs[0].realized_status == "CORE"
    assert account.active_strategic_epoch_id == ""

    promotion_session = dates[-2]
    promoted_targets = allocator.allocate(
        date=promotion_session,
        opportunity=Opportunity.TREND,
        risk=_risk(),
        user_panel=user_panel,
        leaders={symbol: qualification_leaders[symbol] for symbol in tradable},
        account=account,
        prices={
            "sz300308": float(user_panel["sz300308"].loc[promotion_session, "close"])
        },
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        strategic_universe=roles,
    )
    promoted = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=str(promotion_session.date()),
        targets=promoted_targets,
        retained_orders=account.pending_orders,
    )
    planned_promotion = plan_orders(
        signal_date=str(promotion_session.date()),
        targets=promoted,
        account=account,
        prices={
            "sz300308": float(user_panel["sz300308"].loc[promotion_session, "close"])
        },
        cfg=DEFAULT_CONFIG,
    )
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=list(account.pending_orders),
            current=planned_promotion,
            submitted_date=str(promotion_session.date()),
        )
    )
    second_fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-1],
        account=account,
        panel=user_panel,
    )

    assert second_fills
    assert account.strategic_epochs[0].realized_status == "ACTIVE"
    assert account.active_strategic_epoch_id == account.strategic_epochs[0].epoch_id
