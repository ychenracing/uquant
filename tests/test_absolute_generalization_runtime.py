from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields
from types import SimpleNamespace
from typing import cast

import pandas as pd
import pytest

import uquant.application.decision as decision_module
import uquant.validation.absolute_generalization.runtime as runtime_module
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import ProductionEngine
from uquant.types import (
    AccountState,
    Decision,
    LeaderScore,
    Opportunity,
    Risk,
    RiskAssessment,
    build_strategic_universe_roles,
)
from uquant.validation.absolute_generalization import (
    ChampionRuntimeEvidence,
    RecoveryReachabilityRuntimeEvidence,
    run_champion_runtime_evidence,
    run_recovery_and_reachability_runtime_evidence,
)
from uquant.validation.absolute_generalization.replay import (
    AbsoluteGeneralizationReplayObservation,
)
from uquant.validation.absolute_generalization.runtime import _project_baseline_views


def test_special_runtime_public_api_has_no_evidence_or_authority_injection() -> None:
    expected = ("root", "data_dir", "cache_dir", "contract")
    for producer in (
        run_champion_runtime_evidence,
        run_recovery_and_reachability_runtime_evidence,
    ):
        signature = inspect.signature(producer)
        assert tuple(signature.parameters) == expected
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in signature.parameters.values()
        )
        assert not {
            "engine",
            "account",
            "allocator",
            "callback",
            "intervention",
            "summary",
            "passed",
            "status",
        }.intersection(signature.parameters)


def test_special_runtime_has_no_extended_or_legacy_summary_producer() -> None:
    source = inspect.getsource(runtime_module)
    assert "run_generalization_matrix" not in source
    assert "_native_eligibility" not in source
    assert "not initialized" not in source


def test_special_runtime_results_are_frozen_and_do_not_carry_pass_claims() -> None:
    champion = ChampionRuntimeEvidence(payload=(("raw", "observed"),))
    recovery = RecoveryReachabilityRuntimeEvidence(
        failed_grant_recovery=(("raw", "observed"),),
        historical_crowning=(("raw", "observed"),),
        terminal_scc=(("raw", "observed"),),
        repair_bounds=((("raw", "observed"),),),
        cross_industry_crowning=(("raw", "observed"),),
    )

    assert champion.to_manifest_payload() == {"raw": "observed"}
    assert set(recovery.to_manifest_payload()) == {
        "failed_grant_recovery",
        "historical_crowning",
        "terminal_scc",
        "repair_bounds",
        "cross_industry_crowning",
    }
    assert "passed" not in repr((champion, recovery))
    with pytest.raises(FrozenInstanceError):
        champion.payload = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "forbidden",
    ("passed", "runner_success", "capability_pass", "status", "retry_sessions"),
)
def test_special_runtime_results_reject_legacy_summary_claims(forbidden: str) -> None:
    with pytest.raises(ValueError, match="raw production evidence"):
        ChampionRuntimeEvidence(payload=((forbidden, True),))


def _baseline_result(order_ids: tuple[str, str]) -> dict[str, object]:
    return {
        "decision_trace": [],
        "order_ledger": [
            {"event_id": "event-a", "order_id": order_ids[0], "shares": 100},
            {"event_id": "event-b", "order_id": order_ids[1], "shares": 200},
        ],
        "final_account": {
            "fills": [
                {"event_id": "event-a", "order_id": order_ids[0], "shares": 100},
                {"event_id": "event-b", "order_id": order_ids[1], "shares": 200},
            ]
        },
        "daily_replay_evidence": [],
        "equity_curve": [],
    }


def test_champion_baseline_projection_normalizes_only_physical_retry_ids() -> None:
    first = _project_baseline_views(_baseline_result(("O000000001", "O000000002")), frozenset())
    retry = _project_baseline_views(_baseline_result(("O000000004", "O000000005")), frozenset())
    assert first == retry
    assert set(first) == {"targets", "orders", "fills", "positions", "equity"}


def test_champion_baseline_projection_rejects_an_orphan_fill() -> None:
    result = _baseline_result(("O000000001", "O000000002"))
    account = cast(dict[str, object], result["final_account"])
    fills = cast(list[dict[str, object]], account["fills"])
    fills[0]["event_id"] = ""
    fills[0]["order_id"] = "O999999999"
    with pytest.raises(ValueError, match="matching economic order"):
        _project_baseline_views(result, frozenset())


def test_lossless_runtime_observation_is_private_and_decision_schema_is_unchanged() -> None:
    assert tuple(field.name for field in fields(Decision)) == (
        "date",
        "opportunity",
        "risk",
        "target_gross",
        "target_k",
        "targets",
        "pending_orders",
        "risk_summary",
        "decision_digest",
    )
    assert hasattr(ProductionEngine, "_observe_decision")
    decision = Decision(
        date="2026-08-05",
        opportunity=Opportunity.WEAK,
        risk=Risk.NORMAL,
        target_gross=0.0,
        target_k=0,
        targets=(),
        pending_orders=(),
        risk_summary={"target_gross_cap": 0.8, "system_gross_cap": 0.8},
        decision_digest="a" * 64,
    )
    canonical = decision.canonical_payload(effective_config_sha256=config_fingerprint(DEFAULT_CONFIG))
    assert decision.canonical_payload(effective_config_sha256=config_fingerprint(DEFAULT_CONFIG)) == canonical
    assert decision.decision_digest == "a" * 64


def test_replay_session_api_carries_lossless_decision_evidence() -> None:
    assert "decision_runtime_payload" in {
        field.name for field in fields(AbsoluteGeneralizationReplayObservation)
    }
    replay_source = inspect.getsource(runtime_module.run_absolute_generalization_replay)
    replay_module = __import__(
        "uquant.validation.absolute_generalization.replay",
        fromlist=["run_absolute_generalization_replay_sessions"],
    )
    session_source = inspect.getsource(replay_module.run_absolute_generalization_replay_sessions)
    assert "_session_observation_for_symbols" in session_source
    assert "symbols_for_session" in session_source
    assert "_observe_decision" not in replay_source


def test_private_observed_result_retains_exact_typed_task6_inputs() -> None:
    roles = build_strategic_universe_roles(
        as_of="2026-08-05",
        tradable_symbols=("sz300308",),
        qualification_reference_symbols=("sh688008",),
        risk_reference_symbols=("sh688008",),
        industries={"sh688008": "semiconductor"},
        available_symbols=("sz300308", "sh688008"),
    )
    user = LeaderScore(
        symbol="sz300308",
        score=1.0,
        confidence=0.75,
        mature=True,
        emerging=False,
        industry="semiconductor",
        components={"momentum": 0.5},
    )
    reference = LeaderScore(
        symbol="sh688008",
        score=0.8,
        confidence=0.6,
        mature=False,
        emerging=True,
        industry="semiconductor",
        components={"breakout_quality": 0.4},
    )
    allocation = decision_module._DecisionAllocation(
        opportunity=Opportunity.WEAK,
        risk=RiskAssessment(
            state=Risk.NORMAL,
            target_gross_cap=0.8,
            votes=0,
            evidence={"source": "observed"},
            reasons=(),
            shock_state="NORMAL",
        ),
        leader_factor_profile="CHOPPY",
        targets=(),
        orders=(),
        user_leaders={user.symbol: user},
        all_leaders={user.symbol: user, reference.symbol: reference},
        strategic_universe=roles,
        qualification_snapshots={"sh688008": {"ret20": 0.1, "transition_score": 0.2}},
    )
    result = decision_module._finalize_decision_result(
        inputs=cast(object, SimpleNamespace(date=pd.Timestamp("2026-08-05"))),
        market=cast(object, SimpleNamespace(cfg=DEFAULT_CONFIG)),
        allocation=allocation,
        account=AccountState.empty(DEFAULT_CONFIG.initial_cash),
    )

    assert result.observation.effective_config_sha256 == config_fingerprint(DEFAULT_CONFIG)
    assert result.observation.strategic_universe_roles is roles
    assert tuple(item["symbol"] for item in result.observation.leader_scores) == (
        "sz300308",
        "sh688008",
    )
    assert result.observation.leader_scores[0]["confidence"] == 0.75
    assert result.observation.leader_scores[0]["components"] == {"momentum": 0.5}
    assert result.observation.qualification_snapshots["sh688008"]["ret20"] == 0.1
    with pytest.raises(TypeError):
        result.observation.qualification_snapshots["sh688008"]["ret20"] = 9.0  # type: ignore[index]
