from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass, replace
from datetime import date, timedelta

import pytest
from _absolute_generalization_metrics_fixture import (
    EPOCH_ID,
    GRANT_ID,
    OWNER,
    complete_replay,
    completed_sale_replay,
    payload,
    scenario,
)

from uquant.types import FlatBookCapitalRepairState
from uquant.validation.absolute_generalization import (
    ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    CellArtifact,
    IdentityEnvelope,
    derive_cell_metrics,
    load_absolute_generalization_contract,
)
from uquant.validation.absolute_generalization._metrics_reconciliation import (
    _upstream_chain_flags,
)
from uquant.validation.absolute_generalization.metrics import (
    assert_unique_execution_rows,
    repair_episode_facts_from_trace,
)


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
    assert metrics.repairs[0].actual_healthy_sessions_to_ready == 0
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


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("collection", "field"),
    (
        ("order_ledger", "symbol"),
        ("fills", "symbol"),
        ("strategic_epochs", "owner_symbol"),
    ),
)
def test_rejects_reference_only_account_capital_rows(
    collection: str, field: str
) -> None:
    from uquant.contracts.strict_json import strict_json_loads

    account = strict_json_loads(complete_replay().final_account_payload.canonical_json)
    assert isinstance(account, dict)
    account[collection][0][field] = scenario().removed_symbol

    with pytest.raises(ValueError, match="reference-only symbol"):
        assert_unique_execution_rows(
            final_account=account,
            trace=(),
            allowed_symbols=(OWNER,),
        )


def test_rejects_stale_or_tampered_required_identity() -> None:
    with pytest.raises(ValueError, match="production source identity"):
        derive_cell_metrics(
            complete_replay(),
            scenario(),
            replace(_identities(), production_source_sha256="f" * 64),
        )


def test_rejects_role_membership_tamper_against_data_manifest() -> None:
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

    with pytest.raises(ValueError, match="data manifest role symbols"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_stale_role_snapshot_identity() -> None:
    replay = complete_replay()
    final = replay.observations[-1]
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(
                final,
                roles=replace(final.roles, tradable_identity="f" * 64),
            ),
        ),
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


def test_accepts_causal_manifest_prefixes_without_a_shared_trading_interval() -> None:
    replay = complete_replay()
    first = replay.observations[0]
    unavailable = "sh000682"
    roles = replace(
        first.roles,
        available_symbols=tuple(
            symbol
            for symbol in first.roles.available_symbols
            if symbol != unavailable
        ),
        unavailable_reference_symbols=(unavailable,),
    )
    disjoint_prefixes = replace(
        first.data_manifest,
        start="2022-12-21",
        end="2021-12-31",
    )
    observed = replace(
        replay,
        observations=(
            replace(
                first,
                roles=roles,
                expected_but_unavailable_symbols=(unavailable,),
                data_manifest=disjoint_prefixes,
            ),
            *replay.observations[1:],
        ),
    )

    artifact = derive_cell_metrics(observed, scenario(), _identities())

    assert artifact.status == "COMPLETE"


def test_rejects_disjoint_manifest_prefixes_without_unavailable_roles() -> None:
    replay = complete_replay()
    final = replay.observations[-1]
    disjoint_prefixes = replace(
        final.data_manifest,
        start="2022-12-21",
        end="2021-12-31",
    )
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, data_manifest=disjoint_prefixes),
        ),
    )

    with pytest.raises(ValueError, match="data manifest interval"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_disjoint_manifest_prefixes_with_only_extra_loaded_symbol() -> None:
    replay = complete_replay()
    final = replay.observations[-1]
    extra = "sh688019"
    disjoint_prefixes = replace(
        final.data_manifest,
        symbols=tuple(sorted((*final.data_manifest.symbols, extra))),
        start="2022-12-21",
        end="2021-12-31",
    )
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(
                final,
                data_manifest=disjoint_prefixes,
                loaded_symbols=tuple(sorted((*final.loaded_symbols, extra))),
            ),
        ),
    )

    with pytest.raises(ValueError, match="data manifest role symbols"):
        derive_cell_metrics(tampered, scenario(), _identities())


def test_rejects_extra_loaded_symbol_mixed_with_valid_unavailable_role() -> None:
    replay = complete_replay()
    first = replay.observations[0]
    unavailable = "sh000682"
    extra = "sh688019"
    roles = replace(
        first.roles,
        available_symbols=tuple(
            symbol
            for symbol in first.roles.available_symbols
            if symbol != unavailable
        ),
        unavailable_reference_symbols=(unavailable,),
    )
    mixed_manifest = replace(
        first.data_manifest,
        symbols=tuple(sorted((*first.data_manifest.symbols, extra))),
        start="2022-12-21",
        end="2021-12-31",
    )
    tampered = replace(
        replay,
        observations=(
            replace(
                first,
                roles=roles,
                expected_but_unavailable_symbols=(unavailable,),
                data_manifest=mixed_manifest,
                loaded_symbols=tuple(sorted((*first.loaded_symbols, extra))),
            ),
            *replay.observations[1:],
        ),
    )

    with pytest.raises(ValueError, match="data manifest role symbols"):
        derive_cell_metrics(tampered, scenario(), _identities())


@pytest.mark.parametrize("field", ("start", "end"))
def test_rejects_data_manifest_prefix_after_observation_session(field: str) -> None:
    replay = complete_replay()
    final = replay.observations[-1]
    future_prefix = replace(final.data_manifest, **{field: "2023-01-05"})
    tampered = replace(
        replay,
        observations=(
            *replay.observations[:-1],
            replace(final, data_manifest=future_prefix),
        ),
    )

    with pytest.raises(ValueError, match="data manifest interval"):
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


def _strategic_order_chain(
    *,
    side: str,
    order_weight: float,
    target_weight: float | None = None,
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Return one exact physical order/Target pair without economic success facts."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    account = strict_json_loads(replay.final_account_payload.canonical_json)
    decision = strict_json_loads(replay.observations[0].decision_payload.canonical_json)
    assert isinstance(account, dict)
    assert isinstance(decision, dict)
    order = dict(account["order_ledger"][0])
    target = dict(decision["targets"][0])
    order.update({"side": side, "target_weight": order_weight})
    target["weight"] = order_weight if target_weight is None else target_weight
    return (
        {"order_ledger": [order], "fills": []},
        (
            {
                "session": order["signal_date"],
                "orders": [{**order, "status": None}],
                "targets": [target],
            },
        ),
    )


def _partial_remainder_successor_chain(
    *,
    prior_status: str = "CANCELLED",
    prior_cancel_reason: str = "strategic partial remainder replaced",
    prior_last_event: str = "PARTIAL_REMAINDER_RELEASED",
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    """Return one inherited-signal successor with an exact prior physical order."""

    account, trace = _strategic_order_chain(side="BUY", order_weight=0.2)
    prior = account["order_ledger"][0]  # type: ignore[index]
    assert isinstance(prior, dict)
    successor_id = "order_" + "4" * 64
    prior.update(
        {
            "status": prior_status,
            "cancel_reason": prior_cancel_reason,
            "last_event": prior_last_event,
            "last_update_date": "2023-01-04",
            "requested_shares": 10,
            "filled_shares": 4,
            "remaining_shares": 6,
            "replaced_by": successor_id,
            "remainder_release_session": "2023-01-04",
            "remainder_release_shares": 6,
        }
    )
    fills = account["fills"]
    assert isinstance(fills, list)
    from uquant.contracts.strict_json import strict_json_loads

    source_account = strict_json_loads(
        complete_replay().final_account_payload.canonical_json
    )
    assert isinstance(source_account, dict)
    source_fills = source_account["fills"]
    assert isinstance(source_fills, list)
    predecessor_fill = dict(source_fills[0])
    assert isinstance(predecessor_fill, dict)
    predecessor_fill.update(
        {
            "shares": 4,
            "gross_value": 40.0,
        }
    )
    fills.append(predecessor_fill)
    successor = {
        **prior,
        "order_id": successor_id,
        "submitted_date": "2023-01-04",
        "status": "FILLED",
        "requested_shares": 6,
        "filled_shares": 6,
        "remaining_shares": 0,
        "replaced_by": "",
        "cancel_reason": "",
        "last_event": "FILL",
        "last_update_date": "2023-01-05",
    }
    ledger = account["order_ledger"]
    assert isinstance(ledger, list)
    ledger.append(successor)
    origin = trace[0]
    target = dict(origin["targets"][0])  # type: ignore[index]
    target["weight"] = 0.19
    successor_row: dict[str, object] = {
        "session": "2023-01-04",
        "orders": [{**successor, "status": None}],
        "targets": [target],
    }
    return account, (*trace, successor_row)


@pytest.mark.parametrize("target_weight", (0.1, 0.0))  # type: ignore[untyped-decorator]
def test_reconciles_strategic_sell_order_to_its_exact_target(
    target_weight: float,
) -> None:
    """A reduction or full exit is reconciled but never promoted to a BUY success."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _strategic_order_chain(
        side="SELL",
        order_weight=target_weight,
    )

    validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_reconciles_immutable_cross_session_strategic_order_snapshots() -> None:
    """A retained pending order remains one physical order across sessions."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _strategic_order_chain(side="BUY", order_weight=0.2)
    origin = trace[0]
    carried = {
        **origin,
        "session": "2023-01-04",
        "targets": [],
        "orders": [dict(origin["orders"][0])],  # type: ignore[index]
    }

    validate_exact_execution_chain(
        final_account=account,
        trace=(*trace, carried),
        epochs=(),
    )


def test_reconciles_partial_remainder_successor_at_prior_release_session() -> None:
    """A replacement starts when the prior remainder releases, not at inherited signal date."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()

    validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_reconciles_partial_remainder_successor_after_late_predecessor_fill() -> None:
    """Late broker state changes cannot erase the durable release origin."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()
    predecessor = account["order_ledger"][0]  # type: ignore[index]
    successor = account["order_ledger"][1]  # type: ignore[index]
    assert isinstance(predecessor, dict)
    assert isinstance(successor, dict)
    predecessor.update(
        {
            "status": "FILLED",
            "filled_shares": 10,
            "remaining_shares": 0,
            "last_event": "BROKER_FILL",
            "last_update_date": "2023-01-05",
        }
    )
    fills = account["fills"]
    assert isinstance(fills, list)
    late_fill = dict(fills[0])
    late_fill.update(
        {
            "fill_id": "fill_" + "6" * 64,
            "fill_date": "2023-01-05",
            "shares": 6,
            "gross_value": 60.0,
        }
    )
    fills.append(late_fill)
    successor.update(
        {
            "status": "CANCELLED",
            "filled_shares": 0,
            "remaining_shares": 6,
            "last_event": "LATE_FILL_SUPPRESSED_RETRY",
            "last_update_date": "2023-01-05",
            "cancel_reason": "late fill satisfied strategic grant",
        }
    )

    validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_partial_remainder_successor_without_release_evidence() -> None:
    """A linked physical order cannot self-report an inherited origin."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()
    predecessor = account["order_ledger"][0]  # type: ignore[index]
    assert isinstance(predecessor, dict)
    predecessor.pop("remainder_release_session")
    predecessor.pop("remainder_release_shares")

    with pytest.raises(ValueError, match="remainder successor release evidence"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_partial_remainder_successor_without_exact_predecessor_link() -> None:
    """A same-identity order cannot inherit a release without the durable edge."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()
    predecessor = account["order_ledger"][0]  # type: ignore[index]
    assert isinstance(predecessor, dict)
    predecessor["replaced_by"] = "order_" + "5" * 64

    with pytest.raises(ValueError, match="remainder successor identity"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_partial_remainder_successor_replacement_cycle() -> None:
    """A replacement edge must point strictly forward through the physical ledger."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()
    predecessor = account["order_ledger"][0]  # type: ignore[index]
    successor = account["order_ledger"][1]  # type: ignore[index]
    assert isinstance(predecessor, dict)
    assert isinstance(successor, dict)
    successor["replaced_by"] = predecessor["order_id"]

    with pytest.raises(ValueError, match="order replacement topology"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_partial_remainder_successor_quantity_divergence() -> None:
    """A successor cannot claim a quantity other than the released remainder."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()
    predecessor = account["order_ledger"][0]  # type: ignore[index]
    assert isinstance(predecessor, dict)
    predecessor["remainder_release_shares"] = 5

    with pytest.raises(ValueError, match="remainder successor quantity"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_partial_remainder_successor_requested_quantity_divergence() -> None:
    """A successor must consume the exact quantity released by its predecessor."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()
    successor = account["order_ledger"][1]  # type: ignore[index]
    assert isinstance(successor, dict)
    successor.update(
        {
            "requested_shares": 5,
            "filled_shares": 5,
            "remaining_shares": 0,
        }
    )

    with pytest.raises(ValueError, match="remainder successor quantity"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_partial_remainder_successor_release_session_divergence() -> None:
    """A later broker fill cannot be relabeled as the original release."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain()
    predecessor = account["order_ledger"][0]  # type: ignore[index]
    assert isinstance(predecessor, dict)
    predecessor["remainder_release_session"] = "2023-01-05"

    with pytest.raises(ValueError, match="remainder successor release evidence"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_inherited_signal_successor_without_partial_release_marker() -> None:
    """Inherited signal dates cannot admit an unrelated delayed strategic order."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _partial_remainder_successor_chain(
        prior_cancel_reason="broker cancelled",
    )

    with pytest.raises(ValueError, match="remainder successor release evidence"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


def test_rejects_cross_session_strategic_order_intent_divergence() -> None:
    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _strategic_order_chain(side="BUY", order_weight=0.2)
    origin = trace[0]
    carried_order = dict(origin["orders"][0])  # type: ignore[index]
    carried_order["target_weight"] = 0.9
    carried = {
        **origin,
        "session": "2023-01-04",
        "targets": [],
        "orders": [carried_order],
    }

    with pytest.raises(ValueError, match="strategic order target_weight differs"):
        validate_exact_execution_chain(
            final_account=account,
            trace=(*trace, carried),
            epochs=(),
        )


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("side", "order_weight", "target_weight", "message"),
    (
        ("HOLD", 0.1, 0.1, "order session"),
        ("BUY", 0.0, 0.0, "order session"),
        ("SELL", 0.1, 0.2, "order target identity"),
    ),
)
def test_rejects_strategic_order_without_exact_directional_target(
    side: str,
    order_weight: float,
    target_weight: float,
    message: str,
) -> None:
    """SELL support cannot weaken side, BUY, or exact Target reconciliation."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _strategic_order_chain(
        side=side,
        order_weight=order_weight,
        target_weight=target_weight,
    )

    with pytest.raises(ValueError, match=message):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("ledger", "field", "value"),
    (
        ("final", "target_weight", 0.9),
        ("trace", "signal_date", "2023-01-04"),
    ),
)
def test_rejects_strategic_order_immutable_intent_divergence(
    ledger: str,
    field: str,
    value: object,
) -> None:
    """Final and trace Orders share the production immutable intent identity."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account, trace = _strategic_order_chain(side="BUY", order_weight=0.2)
    if ledger == "final":
        account["order_ledger"][0][field] = value  # type: ignore[index]
    else:
        trace[0]["orders"][0][field] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=rf"strategic order {field} differs"):
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=())


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


def _repair_trace_row(
    session: str,
    *,
    episode_id: str,
    count: int,
    status: str,
    last_counted_session: str,
    last_ready_session: str,
    first_observed_session: str = "2023-01-03",
    reset_reason: str = "",
    last_reset_session: str = "",
) -> dict[str, object]:
    return {
        "session": session,
        "risk": {
            "flat_book_capital_repair": {
                "repair_episode_id": episode_id,
                "capital_budget_level": 1,
                "repair_target_level": 0,
                "required_healthy_sessions": 20,
                "healthy_session_count": count,
                "first_observed_session": first_observed_session,
                "last_counted_session": last_counted_session,
                "last_ready_session": last_ready_session,
                "last_reset_session": last_reset_session,
                "reset_reason": reset_reason,
                "status": status,
            }
        },
    }


def _ready_repair_rows(episode_id: str) -> list[dict[str, object]]:
    rows = []
    first = date(2023, 1, 3)
    for offset in range(20):
        session = (first + timedelta(days=offset)).isoformat()
        count = offset + 1
        rows.append(
            _repair_trace_row(
                session,
                episode_id=episode_id,
                count=count,
                status="READY" if count == 20 else "ACCUMULATING",
                last_counted_session=session,
                last_ready_session=session if count == 20 else "",
            )
        )
    return rows


def test_repair_ready_fact_survives_live_authority_reset() -> None:
    """A mutable authority reset cannot erase the episode's historical READY fact."""

    rows = _ready_repair_rows("repair-reset")
    rows.append(
        _repair_trace_row(
            "2023-01-23",
            episode_id="repair-reset",
            count=0,
            status="RESET",
            last_counted_session="",
            last_ready_session="",
            reset_reason="LIVE_CAPITAL_AUTHORITY",
            last_reset_session="2023-01-23",
        )
    )
    facts = repair_episode_facts_from_trace(tuple(rows))

    assert facts[0].actual_healthy_sessions_to_ready == 20
    assert facts[0].reported_healthy_sessions == 20
    assert facts[0].last_ready_session == "2023-01-22"
    assert facts[0].status == "RESET"


def test_capital_budget_clear_retains_repair_state_provenance() -> None:
    """The production clear path resets count while retaining its prior provenance."""

    rows = _ready_repair_rows("repair-budget-clear")
    rows.append(
        _repair_trace_row(
            "2023-01-23",
            episode_id="repair-budget-clear",
            count=0,
            status="RESET",
            last_counted_session="2023-01-22",
            last_ready_session="2023-01-22",
            reset_reason="CAPITAL_BUDGET_CLEARED",
            last_reset_session="2023-01-23",
        )
    )
    facts = repair_episode_facts_from_trace(tuple(rows))

    assert facts[0].actual_healthy_sessions_to_ready == 20
    assert facts[0].reported_healthy_sessions == 20
    assert facts[0].last_ready_session == "2023-01-22"


def test_repair_episode_bootstraps_transition_reset_before_health_evaluation() -> None:
    """A new transition episode retains its reset even when the first row is blocked."""

    reset_reason = "RISK_REFERENCE_IDENTITY_CHANGED"
    base = {
        "repair_episode_id": "repair-transition-reset",
        "capital_budget_level": 2,
        "repair_target_level": 1,
        "required_healthy_sessions": 40,
        "first_observed_session": "2024-01-02",
        "last_ready_session": "",
        "last_reset_session": "2024-01-02",
        "reset_reason": reset_reason,
    }
    facts = repair_episode_facts_from_trace(
        (
            {
                "session": "2024-01-02",
                "risk": {
                    "flat_book_capital_repair": {
                        **base,
                        "healthy_session_count": 0,
                        "last_counted_session": "",
                        "status": "BLOCKED",
                    }
                },
            },
            {
                "session": "2024-01-03",
                "risk": {
                    "flat_book_capital_repair": {
                        **base,
                        "healthy_session_count": 1,
                        "last_counted_session": "2024-01-03",
                        "status": "ACCUMULATING",
                    }
                },
            },
        )
    )

    assert facts[0].first_observed_session == "2024-01-02"
    assert facts[0].reported_healthy_sessions == 1
    assert facts[0].reset_reason == reset_reason


def test_rejects_repair_reset_reason_mutation_inside_one_episode() -> None:
    """Transition reset provenance stays fixed until the episode reaches READY."""

    def row(session: str, reset_reason: str) -> dict[str, object]:
        return {
            "session": session,
            "risk": {
                "flat_book_capital_repair": {
                    "repair_episode_id": "repair-reset-mutation",
                    "capital_budget_level": 2,
                    "repair_target_level": 1,
                    "required_healthy_sessions": 40,
                    "healthy_session_count": 0,
                    "first_observed_session": "2024-01-02",
                    "last_counted_session": "",
                    "last_ready_session": "",
                    "last_reset_session": "2024-01-02",
                    "reset_reason": reset_reason,
                    "status": "BLOCKED",
                }
            },
        }

    with pytest.raises(ValueError, match="repair reset reason"):
        repair_episode_facts_from_trace(
            (
                row("2024-01-02", "RISK_REFERENCE_IDENTITY_CHANGED"),
                row("2024-01-03", "CONFIG_IDENTITY_CHANGED"),
            )
        )


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "reset_reason",
    ("LIVE_CAPITAL_AUTHORITY", "CAPITAL_BUDGET_CLEARED"),
)
def test_rejects_reset_only_reason_on_nonreset_repair_row(reset_reason: str) -> None:
    row = _repair_trace_row(
        "2023-01-03",
        episode_id="repair-reset-only-reason",
        count=0,
        status="BLOCKED",
        last_counted_session="",
        last_ready_session="",
        reset_reason=reset_reason,
        last_reset_session="2023-01-03",
    )

    with pytest.raises(ValueError, match="repair reset reason"):
        repair_episode_facts_from_trace((row,))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("level", "target", "required"),
    ((4, 0, 1), (99, 98, 1)),
)
def test_rejects_repair_tier_outside_frozen_production_mapping(
    level: int,
    target: int,
    required: int,
) -> None:
    row = {
        "session": "2024-01-02",
        "risk": {
            "flat_book_capital_repair": {
                "repair_episode_id": "repair-forged-tier",
                "capital_budget_level": level,
                "repair_target_level": target,
                "required_healthy_sessions": required,
                "healthy_session_count": required,
                "first_observed_session": "2024-01-02",
                "last_counted_session": "2024-01-02",
                "last_ready_session": "2024-01-02",
                "last_reset_session": "",
                "reset_reason": "",
                "status": "READY",
            }
        },
    }

    with pytest.raises(ValueError, match="repair tier/bound"):
        repair_episode_facts_from_trace((row,))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("reset_reason", "last_reset_session"),
    (
        ("RISK_REFERENCE_IDENTITY_CHANGED", ""),
        ("", "2024-01-02"),
        ("RISK_REFERENCE_IDENTITY_CHANGED", "2024-01-01"),
    ),
)
def test_rejects_incomplete_repair_transition_reset_bootstrap(
    reset_reason: str,
    last_reset_session: str,
) -> None:
    """Transition-reset provenance is complete and bound to the episode's first row."""

    row = {
        "session": "2024-01-02",
        "risk": {
            "flat_book_capital_repair": {
                "repair_episode_id": "repair-transition-reset",
                "capital_budget_level": 2,
                "repair_target_level": 1,
                "required_healthy_sessions": 40,
                "healthy_session_count": 0,
                "first_observed_session": "2024-01-02",
                "last_counted_session": "",
                "last_ready_session": "",
                "last_reset_session": last_reset_session,
                "reset_reason": reset_reason,
                "status": "BLOCKED",
            }
        },
    }

    with pytest.raises(ValueError, match="repair reset bootstrap"):
        repair_episode_facts_from_trace((row,))


def test_repair_facts_ignore_only_the_canonical_empty_production_state() -> None:
    empty = asdict(FlatBookCapitalRepairState())

    assert repair_episode_facts_from_trace(
        ({"session": "2023-01-03", "risk": {"flat_book_capital_repair": empty}},)
    ) == ()

    malformed = dict(empty)
    malformed["status"] = "READY"
    with pytest.raises(ValueError, match="empty repair state"):
        repair_episode_facts_from_trace(
            (
                {
                    "session": "2023-01-03",
                    "risk": {"flat_book_capital_repair": malformed},
                },
            )
        )


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
                    "required_healthy_sessions": 20,
                    "healthy_session_count": 1,
                    "first_observed_session": "2023-01-03",
                    "last_counted_session": last_counted_session,
                    "last_ready_session": "",
                    "last_reset_session": "",
                    "reset_reason": "",
                    "status": "ACCUMULATING",
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


def test_repair_ready_provenance_survives_blocking_and_saturated_health() -> None:
    """READY provenance persists while later healthy rows refresh their count date."""

    rows = _ready_repair_rows("repair-ready-persistence")
    rows.extend(
        (
            _repair_trace_row(
                "2023-01-23", episode_id="repair-ready-persistence", count=20,
                status="BLOCKED", last_counted_session="2023-01-22",
                last_ready_session="2023-01-22",
            ),
            _repair_trace_row(
                "2023-01-24", episode_id="repair-ready-persistence", count=20,
                status="READY", last_counted_session="2023-01-24",
                last_ready_session="2023-01-22",
            ),
            _repair_trace_row(
                "2023-01-25", episode_id="repair-ready-persistence", count=20,
                status="CONSUMED", last_counted_session="2023-01-25",
                last_ready_session="2023-01-22",
            ),
        )
    )
    facts = repair_episode_facts_from_trace(tuple(rows))

    assert facts[0].actual_healthy_sessions_to_ready == 20
    assert facts[0].last_ready_session == "2023-01-22"
    assert facts[0].reported_healthy_sessions == 20
    assert facts[0].status == "CONSUMED"


def test_rejects_saturated_ready_without_current_counted_session() -> None:
    """Every newly observed healthy READY row refreshes its counted-session fact."""

    rows = _ready_repair_rows("repair-stale-saturated-session")
    rows.append(
        _repair_trace_row(
            "2023-01-23", episode_id="repair-stale-saturated-session", count=20,
            status="READY", last_counted_session="2023-01-22",
            last_ready_session="2023-01-22",
        )
    )

    with pytest.raises(ValueError, match="repair counted session"):
        repair_episode_facts_from_trace(tuple(rows))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("reset_reason", "last_counted_session", "last_ready_session"),
    (
        ("LIVE_CAPITAL_AUTHORITY", "", "2023-01-22"),
        ("CAPITAL_BUDGET_CLEARED", "", "2023-01-22"),
    ),
)
def test_rejects_reset_repair_provenance_mismatch(
    reset_reason: str,
    last_counted_session: str,
    last_ready_session: str,
) -> None:
    """Each production reset reason has an exact clear-or-retain provenance rule."""

    rows = _ready_repair_rows("repair-reset-provenance")
    rows.append(
        _repair_trace_row(
            "2023-01-23", episode_id="repair-reset-provenance", count=0,
            status="RESET", last_counted_session=last_counted_session,
            last_ready_session=last_ready_session, reset_reason=reset_reason,
            last_reset_session="2023-01-23",
        )
    )

    with pytest.raises(ValueError, match=r"repair .* session"):
        repair_episode_facts_from_trace(tuple(rows))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("status", "reset_reason", "message"),
    (
        ("COUNTING", "", "repair status"),
        ("RESET", "OBSERVED_RESET", "repair reset reason"),
    ),
)
def test_rejects_nonproduction_repair_status_or_reset_reason(
    status: str,
    reset_reason: str,
    message: str,
) -> None:
    """Literal repair facts accept only the production lifecycle vocabulary."""

    row = {
        "session": "2023-01-03",
        "risk": {
            "flat_book_capital_repair": {
                    "repair_episode_id": "repair-invalid-vocabulary",
                    "capital_budget_level": 1,
                    "repair_target_level": 0,
                    "required_healthy_sessions": 20,
                "healthy_session_count": 0 if status == "RESET" else 1,
                "first_observed_session": "2023-01-03",
                "last_counted_session": "" if status == "RESET" else "2023-01-03",
                "last_ready_session": "",
                "last_reset_session": "2023-01-03" if status == "RESET" else "",
                "reset_reason": reset_reason,
                "status": status,
            }
        },
    }

    with pytest.raises(ValueError, match=message):
        repair_episode_facts_from_trace((row,))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("blocked_ready_session", "blocked_counted_session", "message"),
    (
        ("2023-01-23", "2023-01-22", "repair ready session"),
        ("2023-01-22", "2023-01-23", "repair counted session"),
    ),
)
def test_rejects_blocked_repair_provenance_mutation(
    blocked_ready_session: str,
    blocked_counted_session: str,
    message: str,
) -> None:
    """A blocked row may retain, but cannot rewrite, prior repair provenance."""

    rows = _ready_repair_rows("repair-blocked-provenance")
    rows.append(
        _repair_trace_row(
            "2023-01-23", episode_id="repair-blocked-provenance", count=20,
            status="BLOCKED", last_counted_session=blocked_counted_session,
            last_ready_session=blocked_ready_session,
        )
    )

    with pytest.raises(ValueError, match=message):
        repair_episode_facts_from_trace(tuple(rows))


def test_repair_literal_progression_retains_reset_session_identity() -> None:
    """Rows before READY retain the episode's transition reset session."""

    base = {
        "repair_episode_id": "repair-reset-session",
        "capital_budget_level": 2,
        "repair_target_level": 1,
        "required_healthy_sessions": 40,
        "first_observed_session": "2023-01-03",
        "last_ready_session": "",
        "reset_reason": "RISK_REFERENCE_IDENTITY_CHANGED",
    }
    rows = (
        {
            "session": "2023-01-03",
            "risk": {
                "flat_book_capital_repair": {
                    **base,
                    "healthy_session_count": 0,
                    "last_counted_session": "",
                    "last_reset_session": "2023-01-03",
                    "status": "BLOCKED",
                }
            },
        },
        {
            "session": "2023-01-04",
            "risk": {
                "flat_book_capital_repair": {
                    **base,
                    "healthy_session_count": 1,
                    "last_counted_session": "2023-01-04",
                    "last_reset_session": "2023-01-02",
                    "status": "ACCUMULATING",
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


def test_grant_chain_ignores_positive_strategic_target_without_grant_identity() -> None:
    """A retained strategic target is not itself evidence of a new grant chain."""

    from uquant.contracts.strict_json import strict_json_loads

    replay = complete_replay()
    first = replay.observations[0]
    decision = strict_json_loads(first.decision_payload.canonical_json)
    assert isinstance(decision, dict)
    retained = dict(decision["targets"][0])
    retained.update({"grant_id": "", "epoch_id": "", "event_id": ""})
    decision["targets"].insert(0, retained)
    replay = replace(
        replay,
        observations=(
            replace(first, decision_payload=payload(decision)),
            *replay.observations[1:],
        ),
    )

    artifact = derive_cell_metrics(replay, scenario(), _identities())

    grant_to_target = dict(artifact.event_facts)["grant_to_target"]
    assert grant_to_target.applicable is True
    assert grant_to_target.observed is True


def test_empty_grant_target_does_not_create_grant_chain_evidence() -> None:
    flags = _upstream_chain_flags(
        (
            (("2023-01-03", OWNER),),
            ((GRANT_ID, "2023-01-03", OWNER),),
            (("2023-01-03", {"symbol": OWNER, "grant_id": ""}),),
            (),
        )
    )

    assert flags == (True, False, False)


@pytest.mark.parametrize("grant_fields", ({}, {"grant_id": None}, {"grant_id": 0}))
def test_grant_chain_rejects_malformed_target_grant_identity(
    grant_fields: dict[str, object],
) -> None:
    target = {"symbol": OWNER, **grant_fields}
    with pytest.raises(ValueError, match="target grant"):
        _upstream_chain_flags(
            (
                (("2023-01-03", OWNER),),
                ((GRANT_ID, "2023-01-03", OWNER),),
                (("2023-01-03", target),),
                (),
            )
        )


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
