from __future__ import annotations

from dataclasses import fields, is_dataclass, replace

import pytest
from _absolute_generalization_metrics_fixture import (
    EPOCH_ID,
    OWNER,
    complete_replay,
    completed_sale_replay,
    payload,
    scenario,
)

from uquant.validation.absolute_generalization import (
    ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    CellArtifact,
    IdentityEnvelope,
    derive_cell_metrics,
    load_absolute_generalization_contract,
)
from uquant.validation.absolute_generalization.metrics import repair_episode_facts_from_trace


def _identities() -> IdentityEnvelope:
    contract = load_absolute_generalization_contract()
    replay = complete_replay()
    roles = replay.observations[-1].roles
    return IdentityEnvelope(
        head="a" * 40,
        tree="b" * 40,
        scenario_contract_sha256=contract.canonical_sha256,
        production_source_sha256=contract.candidate.production_source_sha256,
        effective_config_sha256=contract.inputs.effective_config_sha256,
        uv_lock_sha256=contract.inputs.uv_lock_sha256,
        frozen_data_manifest_sha256=contract.inputs.frozen_data.manifest_sha256,
        universe_sha256=contract.inputs.ai_universe_sha256,
        industry_mapping_sha256=roles.point_in_time_industry_identity,
        tradable_role_identity=roles.tradable_identity,
        qualification_reference_role_identity=roles.qualification_reference_identity,
        risk_reference_role_identity=roles.risk_reference_identity,
        execution_contract_identity=ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    )


def _assert_immutable(value: object) -> None:
    assert not isinstance(value, (dict, list, set))
    if is_dataclass(value):
        params = getattr(type(value), "__dataclass_params__", None)
        assert params is not None
        assert params.frozen is True
        for field in fields(value):
            _assert_immutable(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            _assert_immutable(item)


def test_derives_complete_literal_metrics_and_fill_gated_epoch_facts() -> None:
    artifact = derive_cell_metrics(complete_replay(), scenario(), _identities())

    assert isinstance(artifact, CellArtifact)
    assert artifact.status == "COMPLETE"
    assert artifact.metrics is not None
    metrics = artifact.metrics
    assert metrics.initial_cash == 1_000.0
    assert metrics.final_equity == 1_100.0
    assert metrics.final_wealth == 1.1
    assert metrics.total_return == pytest.approx(0.1)
    assert metrics.max_drawdown == 0.0
    assert metrics.account_orders == 1
    assert metrics.fill_count == 1
    assert metrics.gross_turnover == 0.1
    assert metrics.annual_turnover == pytest.approx(12.1)
    assert metrics.realized_pnl == 0.0
    assert metrics.open_pnl == pytest.approx(100.0)
    assert metrics.cash_drag == pytest.approx((1.0 + 900.0 / 1_100.0) / 2.0)
    assert (metrics.top1_concentration, metrics.top3_concentration, metrics.pnl_hhi) == (
        1.0,
        1.0,
        1.0,
    )
    assert metrics.positive_total_target_sessions == 1
    assert metrics.positive_strategic_target_sessions == 1
    assert metrics.first_positive_total_target_session == "2023-01-03"
    assert metrics.first_positive_strategic_target_session == "2023-01-03"
    assert metrics.longest_healthy_zero_total_target_streak == 1
    assert metrics.longest_healthy_zero_strategic_target_streak == 1
    assert metrics.qualification_ready_sessions == 2
    assert metrics.first_qualification_session == "2023-01-03"
    assert metrics.strategic_grant_count == 1
    assert metrics.first_strategic_grant_session == "2023-01-03"
    assert metrics.strategic_order_count == 1
    assert metrics.first_strategic_order_session == "2023-01-03"
    assert metrics.strategic_fill_count == 1
    assert metrics.first_strategic_fill_session == "2023-01-04"
    assert metrics.actual_strategic_epoch_count == 1
    assert metrics.first_actual_strategic_epoch_session == "2023-01-04"
    assert metrics.distinct_owner_count == 1
    assert metrics.owner_symbols == (OWNER,)
    assert len(metrics.epochs) == 1
    epoch = metrics.epochs[0]
    assert epoch.epoch_id == EPOCH_ID
    assert epoch.qualification_session == "2023-01-03"
    assert epoch.target_session == "2023-01-03"
    assert epoch.order_session == "2023-01-03"
    assert epoch.fill_session == "2023-01-04"
    assert epoch.active_session == "2023-01-04"
    assert metrics.repair_episode_count == 1
    assert metrics.repairs[0].actual_healthy_sessions_to_ready == 1
    assert metrics.intentional_role_absent_symbols == (scenario().removed_symbol,)
    assert metrics.expected_but_unavailable_symbols == ()
    assert metrics.qualification_coverage == 1.0
    assert metrics.risk_coverage == 1.0
    assert artifact.accounting_reconciled is True
    assert artifact.target_order_fill_identity_reconciled is True
    assert artifact.duplicate_grant_count == 0
    assert artifact.duplicate_order_count == 0
    assert artifact.duplicate_epoch_count == 0
    assert artifact.canonical_sha256
    _assert_immutable(artifact)


def test_simulated_empty_fill_id_reconciles_by_native_physical_identity() -> None:
    replay = complete_replay()
    from uquant.contracts.strict_json import strict_json_loads

    raw = strict_json_loads(replay.final_account_payload.canonical_json)
    assert isinstance(raw, dict)
    raw["fills"][0]["fill_id"] = ""
    final = replay.observations[-1]
    observed = strict_json_loads(final.new_fills[0].canonical_json)
    assert isinstance(observed, dict)
    observed["fill_id"] = ""
    replay = replace(
        replay,
        final_account_payload=payload(raw),
        observations=(
            *replay.observations[:-1],
            replace(final, new_fills=(payload(observed),)),
        ),
    )

    artifact = derive_cell_metrics(replay, scenario(), _identities())

    assert artifact.target_order_fill_identity_reconciled is True


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("mutation", "message"),
    (
        ("orphan_fill", "fill.*order"),
        ("mismatched_epoch", "fill.*epoch"),
        ("mismatched_event", "fill.*event"),
        ("duplicate_fill", "fill identity"),
        ("cash", "cash.*reconcile"),
        ("equity", "final equity"),
        ("removed_owner", "removed symbol"),
    ),
)
def test_rejects_cross_ledger_and_accounting_tampering(
    mutation: str,
    message: str,
) -> None:
    replay = complete_replay()
    final_account = replay.final_account_payload
    from uquant.contracts.strict_json import strict_json_loads

    raw = strict_json_loads(final_account.canonical_json)
    assert isinstance(raw, dict)
    fill_mutations = {
        "orphan_fill": ("order_id", "order_" + "9" * 64),
        "mismatched_epoch": ("epoch_id", "epoch_" + "9" * 64),
        "mismatched_event": ("event_id", "event-9"),
    }
    if mutation in fill_mutations:
        field, value = fill_mutations[mutation]
        raw["fills"][0][field] = value
        final = replay.observations[-1]
        observed_fill = strict_json_loads(final.new_fills[0].canonical_json)
        assert isinstance(observed_fill, dict)
        observed_fill[field] = value
        replay = replace(
            replay,
            observations=(
                *replay.observations[:-1],
                replace(final, new_fills=(payload(observed_fill),)),
            ),
        )
    if mutation == "duplicate_fill":
        raw["fills"].append(dict(raw["fills"][0]))
    elif mutation == "cash":
        raw["cash"] = 901.0
    elif mutation == "equity":
        replay = replace(replay, final_equity=1_101.0)
    elif mutation == "removed_owner":
        raw["strategic_epochs"][0]["owner_symbol"] = scenario().removed_symbol
    if mutation != "equity":
        replay = replace(replay, final_account_payload=payload(raw))

    with pytest.raises(ValueError, match=message):
        derive_cell_metrics(replay, scenario(), _identities())


def test_rejects_stale_or_tampered_required_identity() -> None:
    with pytest.raises(ValueError, match="production source identity"):
        derive_cell_metrics(
            complete_replay(),
            scenario(),
            replace(_identities(), production_source_sha256="f" * 64),
        )


def test_rejects_role_membership_tamper_with_stale_snapshot_identity() -> None:
    replay = complete_replay()
    final = replay.observations[-1]
    tampered_roles = replace(
        final.roles,
        tradable_symbols=(*final.roles.tradable_symbols, "sh688019"),
    )
    tampered = replace(
        replay,
        observations=(*replay.observations[:-1], replace(final, roles=tampered_roles)),
    )

    with pytest.raises(ValueError, match="tradable role identity"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_tampered_replay_manifest_digest() -> None:
    replay = complete_replay()
    final = replay.observations[-1]
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, data_manifest=replace(final.data_manifest, digest="f" * 64)),
        ),
    )

    with pytest.raises(ValueError, match="data manifest digest"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_final_fill_missing_from_incremental_replay_ledger() -> None:
    replay = complete_replay()
    final = replay.observations[-1]
    tampered = replace(
        replay,
        observations=(*replay.observations[:-1], replace(final, new_fills=())),
    )

    with pytest.raises(ValueError, match="incremental fill ledger"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_target_order_event_identity_mismatch() -> None:
    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    decision["targets"][0]["event_id"] = "event-9"
    tampered = replace(
        replay,
        observations=(
            replace(first, decision_payload=payload(decision)),
            *replay.observations[1:],
        ),
    )

    with pytest.raises(ValueError, match="order target identity"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_slippage_diagnostic_is_not_double_subtracted_from_account_cash() -> None:
    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    assert isinstance(account, dict)
    account["fills"][0]["slippage_cost"] = 1.0
    final = replay.observations[-1]
    observed_fill = strict_json_loads(final.new_fills[0].canonical_json)
    assert isinstance(observed_fill, dict)
    observed_fill["slippage_cost"] = 1.0
    replay = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, new_fills=(payload(observed_fill),)),
        ),
        final_account_payload=payload(account),
    )

    artifact = derive_cell_metrics(replay, scenario(), _identities())
    assert artifact.metrics is not None
    assert artifact.metrics.open_pnl == pytest.approx(100.0)


def test_failed_grant_without_retry_is_applicable_but_unobserved() -> None:
    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    assert isinstance(account, dict)
    expired = dict(account["strategic_epochs"][0])
    expired.update(
        {
            "epoch_id": "epoch_" + "9" * 64,
            "grant_id": "grant_" + "8" * 64,
            "first_fill_session": "",
            "active_session": "",
            "closed_session": "2023-01-04",
            "close_reason": "GRANT_EXPIRED",
            "realized_status": "EXPIRED",
        }
    )
    account["strategic_epochs"].append(expired)
    artifact = derive_cell_metrics(
        replace(replay, final_account_payload=payload(account)),
        scenario(),
        _identities(),
    )
    failed_retry = dict(artifact.event_facts)["failed_grant_retry"]

    assert failed_retry.applicable is True
    assert failed_retry.observed is False
    assert failed_retry.healthy_sessions == 0
    assert failed_retry.reason == "NO_RETRY"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("mutation", "message"),
    (
        ("trace_order_id", "order identity"),
        ("duplicate_trace_order", "duplicate trace order"),
        ("orphan_trace_order", "orphan trace order"),
        ("grant_candidate", "grant candidate"),
        ("grant_created_session", "grant session"),
        ("rearm_authorization", "authorization identity"),
        ("rearm_session", "authorization session"),
    ),
)
def test_rejects_non_exact_strategic_physical_chain(mutation: str, message: str) -> None:
    """Every strategic physical edge has one exact keyed causal counterpart."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    orders = decision["pending_orders"]
    assert isinstance(orders, list)
    risk = decision["risk_summary"]
    assert isinstance(risk, dict)
    grant = risk["strategic_grant"]
    rearm = risk["strategic_cash_rearm"]
    assert isinstance(grant, dict)
    assert isinstance(rearm, dict)

    if mutation == "trace_order_id":
        orders[0]["order_id"] = "order_" + "9" * 64
    elif mutation == "duplicate_trace_order":
        orders.append(dict(orders[0]))
    elif mutation == "orphan_trace_order":
        orphan = dict(orders[0])
        orphan["order_id"] = "order_" + "8" * 64
        orders.append(orphan)
    elif mutation == "grant_candidate":
        grant["candidate_symbol"] = "sh688019"
    elif mutation == "grant_created_session":
        grant["created_session"] = "2023-01-02"
    elif mutation == "rearm_authorization":
        rearm["authorization_id"] = "rearm_" + "9" * 64
    elif mutation == "rearm_session":
        rearm["authorized_session"] = "2023-01-04"

    tampered = replace(
        replay,
        observations=(
            replace(first, decision_payload=payload(decision)),
            *replay.observations[1:],
        ),
    )

    with pytest.raises(ValueError, match=message):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_rearm_identity_tamper_on_repeated_grant_observation() -> None:
    """Every repeated grant row remains bound to the same rearm identity."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    final = replay.observations[-1]
    decision = strict_json_loads(final.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    decision["risk_summary"]["strategic_cash_rearm"]["authorization_id"] = "rearm_" + "9" * 64
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, decision_payload=payload(decision)),
        ),
    )

    with pytest.raises(ValueError, match="authorization identity"):
        derive_cell_metrics(tampered, scenario(), _identities())


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("field", "value"),
    (
        ("qualification_signature", "strategic_qualification:tampered"),
        ("qualification_route", "TAMPERED_ROUTE"),
        ("qualification_quorum", "TAMPERED_QUORUM"),
        ("qualification_evidence_sha256", "f" * 64),
    ),
)
def test_rejects_grant_qualification_provenance_tamper(
    field: str,
    value: str,
) -> None:
    """Every grant observation stays bound to qualification and epoch provenance."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    observations = []
    for observation in replay.observations:
        decision = strict_json_loads(observation.decision_payload.canonical_json)
        assert isinstance(decision, dict)
        decision["risk_summary"]["strategic_grant"][field] = value
        observations.append(
            replace(observation, decision_payload=payload(decision))
        )
    tampered = replace(replay, observations=tuple(observations))

    with pytest.raises(ValueError, match="grant qualification"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_strategic_chain_relabelled_as_non_strategic() -> None:
    """A strategic trace order/fill cannot disappear by relabelling final ledgers."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    assert isinstance(account, dict)
    account["order_ledger"][0]["origin_subsystem"] = "RECOVERY"
    account["fills"][0]["origin_subsystem"] = "RECOVERY"
    final = replay.observations[-1]
    observed_fill = strict_json_loads(final.new_fills[0].canonical_json)
    assert isinstance(observed_fill, dict)
    observed_fill["origin_subsystem"] = "RECOVERY"
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, new_fills=(payload(observed_fill),)),
        ),
        final_account_payload=payload(account),
    )

    with pytest.raises(ValueError, match="strategic order origin"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_final_position_average_cost_not_derived_from_fills() -> None:
    """A resealable account avg_cost cannot invent the realized/open PnL split."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    assert isinstance(account, dict)
    account["positions"][OWNER]["avg_cost"] = 1.0
    tampered = replace(replay, final_account_payload=payload(account))

    with pytest.raises(ValueError, match="position average cost"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_closing_mark_that_does_not_reconcile_observed_equity() -> None:
    """Authenticated closing marks, rather than an equity residual, price open PnL."""

    replay = complete_replay()
    final = replay.observations[-1]
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, closing_marks=((OWNER, 19.0),)),
        ),
    )

    with pytest.raises(ValueError, match="closing mark equity"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_reported_repair_count_not_observed_in_session_progression() -> None:
    """Repair facts count literal ordered counter increments, not a final claim."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    final = replay.observations[-1]
    decision = strict_json_loads(final.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    decision["risk_summary"]["flat_book_capital_repair"]["healthy_session_count"] = 999
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, decision_payload=payload(decision)),
        ),
    )

    with pytest.raises(ValueError, match="repair healthy session progression"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_repair_literal_progression_restarts_after_reset() -> None:
    """Observed counter increments before a reset do not count toward later readiness."""

    def row(session: str, *, count: int, status: str, reset_reason: str = "") -> dict[str, object]:
        repair: dict[str, object] = {
            "repair_episode_id": "repair-reset",
            "capital_budget_level": 1,
            "repair_target_level": 0,
            "required_healthy_sessions": 2,
            "healthy_session_count": count,
            "first_observed_session": "2023-01-03",
            "last_ready_session": "2023-01-06" if status == "READY" else "",
            "reset_reason": reset_reason,
            "status": status,
        }
        if status == "RESET":
            repair["last_reset_session"] = session
        return {"session": session, "risk": {"flat_book_capital_repair": repair}}

    facts = repair_episode_facts_from_trace(
        (
            row("2023-01-03", count=1, status="COUNTING"),
            row("2023-01-04", count=0, status="RESET", reset_reason="OBSERVED_RESET"),
            row("2023-01-05", count=1, status="COUNTING"),
            row("2023-01-06", count=2, status="READY"),
        )
    )

    assert facts[0].actual_healthy_sessions_to_ready == 2


def test_repair_literal_progression_retains_counted_session_identity() -> None:
    """A non-incrementing row cannot advance the last counted session."""

    def row(session: str, *, last_counted_session: str) -> dict[str, object]:
        return {
            "session": session,
            "risk": {
                "flat_book_capital_repair": {
                    "repair_episode_id": "repair-counted-session",
                    "capital_budget_level": 1,
                    "repair_target_level": 0,
                    "required_healthy_sessions": 2,
                    "healthy_session_count": 1,
                    "first_observed_session": "2023-01-03",
                    "last_counted_session": last_counted_session,
                    "last_ready_session": "",
                    "last_reset_session": "",
                    "reset_reason": "",
                    "status": "COUNTING",
                }
            },
        }

    with pytest.raises(ValueError, match="repair counted session"):
        repair_episode_facts_from_trace(
            (
                row("2023-01-03", last_counted_session="2023-01-03"),
                row("2023-01-04", last_counted_session="2023-01-04"),
            )
        )


def test_repair_literal_progression_retains_reset_session_identity() -> None:
    """Rows after RESET must retain the actual last reset session."""

    base = {
        "repair_episode_id": "repair-reset-session",
        "capital_budget_level": 1,
        "repair_target_level": 0,
        "required_healthy_sessions": 2,
        "first_observed_session": "2023-01-03",
        "last_ready_session": "",
    }
    rows = (
        {
            "session": "2023-01-03",
            "risk": {
                "flat_book_capital_repair": {
                    **base,
                    "healthy_session_count": 1,
                    "last_counted_session": "2023-01-03",
                    "last_reset_session": "",
                    "reset_reason": "",
                    "status": "COUNTING",
                }
            },
        },
        {
            "session": "2023-01-04",
            "risk": {
                "flat_book_capital_repair": {
                    **base,
                    "healthy_session_count": 0,
                    "last_counted_session": "",
                    "last_reset_session": "2023-01-04",
                    "reset_reason": "OBSERVED_RESET",
                    "status": "RESET",
                }
            },
        },
        {
            "session": "2023-01-05",
            "risk": {
                "flat_book_capital_repair": {
                    **base,
                    "healthy_session_count": 1,
                    "last_counted_session": "2023-01-05",
                    "last_reset_session": "2023-01-03",
                    "reset_reason": "",
                    "status": "COUNTING",
                }
            },
        },
    )

    with pytest.raises(ValueError, match="repair reset session"):
        repair_episode_facts_from_trace(rows)


def test_terminal_zero_target_fact_does_not_claim_scc_analysis() -> None:
    """Task 5 reports only the literal trailing state duration; Task 6 owns SCCs."""

    artifact = derive_cell_metrics(complete_replay(), scenario(), _identities())
    assert artifact.metrics is not None
    raw_metrics = artifact.metrics.to_dict()
    assert "terminal_zero_strategic_target_state_sessions" in raw_metrics
    assert "terminal_zero_strategic_target_scc_sessions" not in raw_metrics
    events = dict(artifact.event_facts)
    assert "terminal_zero_strategic_target_state" in events
    assert "terminal_zero_strategic_target_scc" not in events


def test_reconstructs_fee_bearing_realized_pnl_from_attributed_sale_lot() -> None:
    replay = completed_sale_replay()

    artifact = derive_cell_metrics(replay, scenario(), _identities())

    assert artifact.metrics is not None
    assert artifact.metrics.realized_pnl == pytest.approx(97.0)
    assert artifact.metrics.open_pnl == 0.0
    assert artifact.metrics.final_equity == 1_097.0


def test_rejects_sold_lot_cost_not_derived_from_prior_buy() -> None:
    from uquant.contracts.strict_json import strict_json_loads

    replay = completed_sale_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    assert isinstance(account, dict)
    account["fills"][-1]["sold_tranches"][0]["unit_cost"] = 1.0
    final = replay.observations[-1]
    observed_fill = strict_json_loads(final.new_fills[0].canonical_json)
    assert isinstance(observed_fill, dict)
    observed_fill["sold_tranches"][0]["unit_cost"] = 1.0
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, new_fills=(payload(observed_fill),)),
        ),
        final_account_payload=payload(account),
    )

    with pytest.raises(ValueError, match="sold tranche cost"):
        derive_cell_metrics(tampered, scenario(), _identities())
