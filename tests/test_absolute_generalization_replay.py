from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import pandas as pd
import pytest

import uquant.validation.absolute_generalization.replay as replay_module
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.data import DataManifest
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.market import ReplayUniverse
from uquant.models.decision import Decision
from uquant.models.strategic_epoch import StrategicEpoch
from uquant.models.strategic_universe import build_strategic_universe_declaration
from uquant.models.trading import AccountOrder, Fill, PendingOrder
from uquant.types import AccountState, Opportunity, Risk
from uquant.validation.absolute_generalization import (
    build_leave_one_out_scenarios,
    load_absolute_generalization_contract,
)
from uquant.validation.absolute_generalization.replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayObservation,
    run_absolute_generalization_replay,
)
from uquant.validation.universe import default_ai_universe


def _payload_dict(value: Any) -> dict[str, Any]:
    assert hashlib.sha256(value.canonical_json).hexdigest() == value.sha256
    decoded = strict_json_loads(value.canonical_json)
    assert isinstance(decoded, dict)
    assert canonical_json_bytes(decoded) == value.canonical_json
    return decoded


def _assert_deeply_immutable(value: object) -> None:
    mutable_economic_types = (
        AccountState,
        Decision,
        Fill,
        DataManifest,
        AccountOrder,
        PendingOrder,
        StrategicEpoch,
        list,
        dict,
    )
    assert not isinstance(value, mutable_economic_types)
    if is_dataclass(value):
        params = type(value).__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen is True
        for field in fields(value):
            _assert_deeply_immutable(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            _assert_deeply_immutable(item)


def _apply_entity_deltas(
    active: dict[str, str],
    payloads: tuple[Any, ...],
    removed: tuple[str, ...],
    *,
    identity_field: str,
    previous_chain_sha256: str,
) -> str:
    changed: list[dict[str, str]] = []
    for stable_id in removed:
        assert stable_id in active
        del active[stable_id]
    for payload in payloads:
        entity = _payload_dict(payload)
        stable_id = entity[identity_field]
        assert isinstance(stable_id, str) and stable_id
        assert active.get(stable_id) != payload.sha256
        active[stable_id] = payload.sha256
        changed.append({"sha256": payload.sha256, "stable_id": stable_id})
    if not changed and not removed:
        return previous_chain_sha256
    transition = canonical_json_bytes(
        {
            "changed": changed,
            "previous_sha256": previous_chain_sha256,
            "removed": list(removed),
        }
    )
    return hashlib.sha256(transition).hexdigest()


def _entity_digests(
    entities: list[dict[str, Any]],
    *,
    identity_field: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for entity in entities:
        stable_id = entity[identity_field]
        assert isinstance(stable_id, str) and stable_id
        assert stable_id not in result
        result[stable_id] = hashlib.sha256(canonical_json_bytes(entity)).hexdigest()
    return result


def _order(order_id: str) -> AccountOrder:
    return AccountOrder(
        order_id=order_id,
        signal_date="2026-01-05",
        submitted_date="2026-01-05",
        symbol="sz300502",
        side="BUY",
        target_weight=0.1,
        reason="scaling evidence",
        lifecycle="CORE",
    )


class _VisitCountingLedger(list[AccountOrder]):
    def __init__(self, orders: list[AccountOrder]) -> None:
        super().__init__(orders)
        self.entity_visits = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        for entity in super().__iter__():
            self.entity_visits += 1
            yield entity

    def __getitem__(self, index):  # type: ignore[no-untyped-def]
        result = super().__getitem__(index)
        self.entity_visits += len(result) if isinstance(index, slice) else 1
        return result


class _VisitCountingMapping(dict[str, Any]):
    def __init__(self, values: dict[str, Any]) -> None:
        super().__init__(values)
        self.entity_visits = 0

    def __iter__(self) -> Iterator[str]:
        for stable_id in super().__iter__():
            self.entity_visits += 1
            yield stable_id

    def keys(self) -> Iterator[str]:  # type: ignore[override]
        for stable_id in super().__iter__():
            self.entity_visits += 1
            yield stable_id

    def items(self) -> Iterator[tuple[str, Any]]:  # type: ignore[override]
        for stable_id, entity in super().items():
            self.entity_visits += 1
            yield stable_id, entity

    def values(self) -> Iterator[Any]:  # type: ignore[override]
        for entity in super().values():
            self.entity_visits += 1
            yield entity


def _unchanged_snapshot_entity_visits(order_count: int) -> tuple[int, int]:
    orders = [_order(f"O{index:09d}") for index in range(1, order_count + 1)]
    account = AccountState.empty(2_000_000.0)
    account.order_ledger = _VisitCountingLedger(orders)
    order_tracker = replay_module._EntityTracker()
    epoch_tracker = replay_module._EntityTracker()
    order_tracker.changes(
        identity_field="order_id",
        appended_entities=orders,
        changed_entities=(),
        changed_ids=(),
        removed_ids=(),
    )
    tracked_entities = _VisitCountingMapping(order_tracker._entities)
    tracked_payloads = _VisitCountingMapping(order_tracker._payloads)
    order_tracker._entities = tracked_entities
    order_tracker._payloads = tracked_payloads
    snapshot = replay_module._account_snapshot(
        account,
        order_tracker=order_tracker,
        epoch_tracker=epoch_tracker,
    )
    assert snapshot.changed_order_payloads == ()
    return (
        account.order_ledger.entity_visits,
        tracked_entities.entity_visits + tracked_payloads.entity_visits,
    )


def test_replay_evidence_surface_is_immutable_and_incremental() -> None:
    """Daily evidence must not deepcopy cumulative mutable economic state."""

    observation_fields = {
        field.name for field in fields(AbsoluteGeneralizationReplayObservation)
    }
    assert "post_open_account" in observation_fields
    assert "post_decision_account" in observation_fields
    assert "account" not in observation_fields
    assert "decision" not in observation_fields
    assert "decision_payload" in observation_fields
    assert "closing_marks" in observation_fields

    observation_hints = get_type_hints(AbsoluteGeneralizationReplayObservation)
    replay_hints = get_type_hints(AbsoluteGeneralizationReplay)
    assert AccountState not in observation_hints.values()
    assert AccountState not in replay_hints.values()
    assert "final_account_payload" in replay_hints


def test_unchanged_cumulative_ledgers_have_constant_snapshot_work() -> None:
    """An unchanged snapshot must not visit any historical ledger entity."""

    assert _unchanged_snapshot_entity_visits(1) == (0, 0)
    assert _unchanged_snapshot_entity_visits(4096) == (0, 0)


def test_entity_tracker_reconstructs_replacement_removal_and_reappend() -> None:
    """Explicit stable-ID deltas remain complete without a historical scan."""

    tracker = replay_module._EntityTracker()
    chain = hashlib.sha256(
        canonical_json_bytes({"kind": "empty_entity_ledger"})
    ).hexdigest()
    active: dict[str, str] = {}

    first = _order("O000000001")
    second = _order("O000000002")
    changed, removed, observed_chain = tracker.changes(
        identity_field="order_id",
        appended_entities=(first, second),
        changed_entities=(),
        changed_ids=(),
        removed_ids=(),
    )
    chain = _apply_entity_deltas(
        active,
        changed,
        removed,
        identity_field="order_id",
        previous_chain_sha256=chain,
    )
    assert observed_chain == chain

    replacement = _order(first.order_id)
    replacement.status = "FILLED"
    changed, removed, observed_chain = tracker.changes(
        identity_field="order_id",
        appended_entities=(),
        changed_entities=(replacement,),
        changed_ids=(),
        removed_ids=(),
    )
    chain = _apply_entity_deltas(
        active,
        changed,
        removed,
        identity_field="order_id",
        previous_chain_sha256=chain,
    )
    assert len(changed) == 1
    assert removed == ()
    assert observed_chain == chain

    changed, removed, observed_chain = tracker.changes(
        identity_field="order_id",
        appended_entities=(),
        changed_entities=(),
        changed_ids=(),
        removed_ids=(first.order_id,),
    )
    chain = _apply_entity_deltas(
        active,
        changed,
        removed,
        identity_field="order_id",
        previous_chain_sha256=chain,
    )
    assert changed == ()
    assert removed == (first.order_id,)
    assert observed_chain == chain

    reappended = _order(second.order_id)
    reappended.status = "CANCELLED"
    changed, removed, observed_chain = tracker.changes(
        identity_field="order_id",
        appended_entities=(reappended,),
        changed_entities=(),
        changed_ids=(),
        removed_ids=(second.order_id,),
    )
    chain = _apply_entity_deltas(
        active,
        changed,
        removed,
        identity_field="order_id",
        previous_chain_sha256=chain,
    )
    assert len(changed) == 1
    assert removed == (second.order_id,)
    assert observed_chain == chain
    assert active == _entity_digests(
        [_payload_dict(changed[0])],
        identity_field="order_id",
    )


def test_daily_decision_projection_is_independent_of_cumulative_epoch_count() -> None:
    """Daily Decision evidence links deltas instead of copying every prior epoch."""

    def decision(epoch_count: int) -> Decision:
        return Decision(
            date="2026-01-05",
            opportunity=Opportunity.CHOPPY,
            risk=Risk.NORMAL,
            target_gross=0.0,
            target_k=0,
            targets=(),
            pending_orders=(),
            risk_summary={
                "strategic_epochs": [
                    {"epoch_id": f"epoch_{index:064x}"}
                    for index in range(epoch_count)
                ]
            },
            decision_digest="0" * 64,
        )

    small = replay_module._decision_evidence_payload(
        decision(1),
        epoch_ledger_chain_sha256="1" * 64,
    )
    large = replay_module._decision_evidence_payload(
        decision(256),
        epoch_ledger_chain_sha256="1" * 64,
    )

    assert small == large
    assert _payload_dict(large)["risk_summary"]["strategic_epochs"] == {
        "delta_chain_sha256": "1" * 64
    }


def test_final_equity_failure_returns_bounded_fallback_evidence() -> None:
    """An operational final mark failure must not escape replay finalization."""

    class FailingEquityEngine:
        @staticmethod
        def equity(_account: AccountState, _date: pd.Timestamp) -> float:
            raise RuntimeError("final mark unavailable")

    final_equity, replay_error = replay_module._safe_final_equity(
        FailingEquityEngine(),
        AccountState.empty(2_000_000.0),
        pd.Timestamp("2026-08-05"),
        fallback=1_900_000.0,
    )

    assert final_equity == 1_900_000.0
    assert replay_error == "RuntimeError: final mark unavailable"


def test_role_absent_symbol_is_not_read_by_the_causal_risk_timeline(
    data_dir: Path,
    tmp_path: Path,
) -> None:
    """A full removal must not re-enter through risk-cache data identity."""

    removed = "sz300308"
    isolated = tmp_path / "frozen"
    shutil.copytree(data_dir, isolated)
    (isolated / f"{removed}.csv").unlink()
    remaining = tuple(
        symbol for symbol in default_ai_universe().symbols if symbol != removed
    )
    replay_universe = ReplayUniverse.from_symbols(
        tradable_symbols=remaining,
        reference_symbols=remaining,
        index_symbols=INDEX_SYMBOLS,
    )
    engine = ProductionEngine(isolated)
    engine.workspace.prepare(replay_universe)

    decision = engine.decide(
        symbols=remaining,
        as_of="2026-06-30",
        account=AccountState.empty(engine.cfg.initial_cash),
        strategic_universe_declaration=build_strategic_universe_declaration(
            qualification_reference_symbols=remaining,
            risk_reference_symbols=remaining,
        ),
    )

    assert removed not in engine.workspace.loaded_symbols
    assert removed not in decision.risk_summary["reference_expected_symbols"]
    assert decision.risk_summary["strategic_universe_identities"][
        "qualification_reference"
    ]


def test_full_removal_replay_is_causal_and_preserves_raw_production_evidence(
    data_dir: Path,
    tmp_path: Path,
) -> None:
    """Catches role, panel, identity, and next-open leakage in one real replay."""

    root = Path(__file__).resolve().parents[1]
    scenario = next(
        item
        for item in build_leave_one_out_scenarios(
            load_absolute_generalization_contract()
        )
        if item.removed_symbol == "sh688347"
    )

    replay = run_absolute_generalization_replay(
        scenario,
        root=root,
        data_dir=data_dir,
        cache_dir=tmp_path / "cache",
    )

    assert replay.status == "COMPLETE"
    assert replay.replay_error == ""
    assert replay.scenario == scenario
    assert replay.observations
    by_session = {item.session: item for item in replay.observations}
    before_removed_membership = by_session["2023-12-29"]
    after_removed_membership = by_session["2024-01-02"]
    before_other_future_member = by_session["2023-05-18"]
    after_other_future_member = by_session["2023-05-19"]

    assert before_removed_membership.intentional_role_absent_symbols == ()
    assert after_removed_membership.intentional_role_absent_symbols == (
        scenario.removed_symbol,
    )
    assert "sh688361" not in before_other_future_member.roles.tradable_symbols
    assert "sh688361" not in before_other_future_member.loaded_symbols
    assert "sh688361" in after_other_future_member.roles.tradable_symbols
    assert "sh688361" in after_other_future_member.loaded_symbols

    first = replay.observations[0]
    assert first.session == "2023-01-03"
    assert "sz000636" in first.roles.qualification_reference_symbols
    assert "sz000636" in first.expected_but_unavailable_symbols
    assert first.intentional_role_absent_symbols == ()
    decision = _payload_dict(first.decision_payload)
    post_open = _payload_dict(first.post_open_account.account_payload)
    post_decision = _payload_dict(first.post_decision_account.account_payload)
    assert decision["targets"] == []
    assert decision["pending_orders"] == []
    assert first.new_fills == ()
    assert post_open["last_successful_run"] == ""
    assert post_decision["last_successful_run"] == first.session
    assert post_decision["pending_orders"] == decision["pending_orders"]
    assert post_decision["strategic_grant"] is None
    assert "strategic_epochs" not in post_decision

    cumulative_fields = {
        "account_migrations",
        "fills",
        "lifecycle_events",
        "order_ledger",
        "reconciliation_events",
        "replacement_events",
        "risk_events",
        "rotation_dates",
        "sector_shock_dates",
        "strategic_epochs",
    }
    current_account_fields = {
        field.name for field in fields(AccountState)
    } - cumulative_fields
    active_order_digests: dict[str, str] = {}
    active_epoch_digests: dict[str, str] = {}
    empty_chain = hashlib.sha256(
        canonical_json_bytes({"kind": "empty_entity_ledger"})
    ).hexdigest()
    order_chain = empty_chain
    epoch_chain = empty_chain

    for observation in replay.observations:
        _assert_deeply_immutable(observation)
        for account_snapshot in (
            observation.post_open_account,
            observation.post_decision_account,
        ):
            account_payload = _payload_dict(account_snapshot.account_payload)
            assert set(account_payload) == current_account_fields
            order_chain = _apply_entity_deltas(
                active_order_digests,
                account_snapshot.changed_order_payloads,
                account_snapshot.removed_order_keys,
                identity_field="order_id",
                previous_chain_sha256=order_chain,
            )
            epoch_chain = _apply_entity_deltas(
                active_epoch_digests,
                account_snapshot.changed_epoch_payloads,
                account_snapshot.removed_epoch_keys,
                identity_field="epoch_id",
                previous_chain_sha256=epoch_chain,
            )
            assert account_snapshot.order_ledger_chain_sha256 == order_chain
            assert account_snapshot.epoch_ledger_chain_sha256 == epoch_chain
            for order_payload in account_snapshot.changed_order_payloads:
                order = _payload_dict(order_payload)
                assert order["order_id"]
                assert order["symbol"] != scenario.removed_symbol
            for epoch_payload in account_snapshot.changed_epoch_payloads:
                epoch = _payload_dict(epoch_payload)
                assert epoch["epoch_id"]
                assert epoch["owner_symbol"] != scenario.removed_symbol
        assert scenario.removed_symbol not in observation.roles.tradable_symbols
        assert (
            scenario.removed_symbol
            not in observation.roles.qualification_reference_symbols
        )
        assert scenario.removed_symbol not in observation.roles.risk_reference_symbols
        assert scenario.removed_symbol not in observation.loaded_symbols
        assert scenario.removed_symbol not in observation.data_manifest.symbols
        decision = _payload_dict(observation.decision_payload)
        assert decision["risk_summary"]["strategic_epochs"] == {
            "delta_chain_sha256": (
                observation.post_decision_account.epoch_ledger_chain_sha256
            )
        }
        assert all(
            target["symbol"] != scenario.removed_symbol
            for target in decision["targets"]
        )
        assert all(
            order["symbol"] != scenario.removed_symbol
            for order in decision["pending_orders"]
        )
        assert all(
            (fill := _payload_dict(fill_payload))["symbol"]
            != scenario.removed_symbol
            and fill["signal_date"] < fill["fill_date"]
            for fill_payload in observation.new_fills
        )

    final_account = _payload_dict(replay.final_account_payload)
    assert set(final_account) == {field.name for field in fields(AccountState)}
    assert active_order_digests == _entity_digests(
        final_account["order_ledger"],
        identity_field="order_id",
    )
    assert active_epoch_digests == _entity_digests(
        final_account["strategic_epochs"],
        identity_field="epoch_id",
    )
    assert scenario.removed_symbol not in final_account["positions"]
    assert all(
        order["symbol"] != scenario.removed_symbol
        for order in final_account["order_ledger"]
    )
    assert all(
        fill["symbol"] != scenario.removed_symbol
        for fill in final_account["fills"]
    )
    assert all(
        epoch["owner_symbol"] != scenario.removed_symbol
        for epoch in final_account["strategic_epochs"]
    )


def test_replay_error_retains_one_complete_final_account_payload(
    data_dir: Path,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    scenario = next(
        item
        for item in build_leave_one_out_scenarios(
            load_absolute_generalization_contract()
        )
        if item.removed_symbol == "sh688347"
    )
    isolated = tmp_path / "frozen"
    shutil.copytree(data_dir, isolated)
    (isolated / "sz000636.csv").unlink()

    replay = run_absolute_generalization_replay(
        scenario,
        root=root,
        data_dir=isolated,
        cache_dir=tmp_path / "cache",
    )

    assert replay.status == "REPLAY_ERROR"
    assert replay.replay_error.startswith("DataContractError:")
    assert replay.observations == ()
    assert _payload_dict(replay.final_account_payload) == AccountState.empty(
        replay.initial_cash
    ).to_dict()


def test_replay_setup_error_retains_complete_initial_account(
    data_dir: Path,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    scenario = next(
        item
        for item in build_leave_one_out_scenarios(
            load_absolute_generalization_contract()
        )
        if item.removed_symbol == "sh688347"
    )
    isolated = tmp_path / "frozen"
    shutil.copytree(data_dir, isolated)
    (isolated / "sh000300.csv").unlink()

    try:
        replay = run_absolute_generalization_replay(
            scenario,
            root=root,
            data_dir=isolated,
            cache_dir=tmp_path / "cache",
        )
    except Exception as exc:  # pragma: no cover - RED diagnostic branch
        pytest.fail(f"operational setup error escaped replay evidence: {exc!r}")

    assert replay.status == "REPLAY_ERROR"
    assert replay.replay_error.startswith("DataContractError:")
    assert replay.observations == ()
    assert replay.final_equity == replay.initial_cash
    assert _payload_dict(replay.final_account_payload) == AccountState.empty(
        replay.initial_cash
    ).to_dict()


def test_replay_short_session_window_is_retained_as_setup_error(
    data_dir: Path,
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    scenario = next(
        item
        for item in build_leave_one_out_scenarios(
            load_absolute_generalization_contract()
        )
        if item.removed_symbol == "sh688347"
    )
    isolated = tmp_path / "frozen"
    shutil.copytree(data_dir, isolated)
    for symbol in INDEX_SYMBOLS:
        path = isolated / f"{symbol}.csv"
        frame = pd.read_csv(path)
        frame.loc[frame["date"] <= str(scenario.window_start)].to_csv(
            path,
            index=False,
        )

    replay = run_absolute_generalization_replay(
        scenario,
        root=root,
        data_dir=isolated,
        cache_dir=tmp_path / "cache",
    )

    assert replay.status == "REPLAY_ERROR"
    assert replay.replay_error == (
        "RuntimeError: absolute replay window has fewer than two index sessions"
    )
    assert replay.observations == ()
    assert replay.final_equity == replay.initial_cash
    assert _payload_dict(replay.final_account_payload) == AccountState.empty(
        replay.initial_cash
    ).to_dict()
