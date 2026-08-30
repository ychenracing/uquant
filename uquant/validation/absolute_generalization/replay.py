"""Causal production replay for one canonical full-removal scenario."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_bytes
from uquant.data import DataManifest
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.market import ReplayUniverse
from uquant.models.strategic_universe import (
    StrategicUniverseRoles,
    build_strategic_universe_declaration,
    build_strategic_universe_roles,
)
from uquant.types import AccountOrder, AccountState, Decision, StrategicEpoch
from uquant.validation.universe import default_ai_universe

from .contract import load_absolute_generalization_contract
from .scenarios import (
    AbsoluteGeneralizationScenario,
    build_leave_one_out_scenarios,
)

_CUMULATIVE_ACCOUNT_FIELDS = frozenset(
    {
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
)
_EntityKey = str
_EMPTY_ENTITY_CHAIN_SHA256 = hashlib.sha256(
    canonical_json_bytes({"kind": "empty_entity_ledger"})
).hexdigest()


class _EquityEngine(Protocol):
    def equity(self, account: AccountState, date: pd.Timestamp) -> float: ...


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationReplayPayload:
    """Deeply immutable canonical JSON evidence and its exact digest."""

    canonical_json: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationReplayRoleSnapshot:
    """Immutable production role membership and identities for one session."""

    as_of: str
    tradable_symbols: tuple[str, ...]
    qualification_reference_symbols: tuple[str, ...]
    risk_reference_symbols: tuple[str, ...]
    available_symbols: tuple[str, ...]
    unavailable_reference_symbols: tuple[str, ...]
    point_in_time_industries: tuple[tuple[str, str], ...]
    tradable_identity: str
    qualification_reference_identity: str
    risk_reference_identity: str
    point_in_time_industry_identity: str


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationReplayManifestSnapshot:
    """Immutable bounded data manifest without a mutable files mapping."""

    generated_at: str
    source: str
    adjustment: str
    files: tuple[tuple[str, str], ...]
    symbols: tuple[str, ...]
    start: str
    end: str
    digest: str


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationReplayAccountSnapshot:
    """Current-only account state plus order/epoch transition deltas."""

    account_payload: AbsoluteGeneralizationReplayPayload
    changed_order_payloads: tuple[AbsoluteGeneralizationReplayPayload, ...]
    changed_epoch_payloads: tuple[AbsoluteGeneralizationReplayPayload, ...]
    removed_order_keys: tuple[_EntityKey, ...]
    removed_epoch_keys: tuple[_EntityKey, ...]
    order_ledger_chain_sha256: str
    epoch_ledger_chain_sha256: str


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationReplayObservation:
    """One next-open execution followed by one production close decision."""

    session: str
    equity: float
    decision_payload: AbsoluteGeneralizationReplayPayload
    new_fills: tuple[AbsoluteGeneralizationReplayPayload, ...]
    post_open_account: AbsoluteGeneralizationReplayAccountSnapshot
    post_decision_account: AbsoluteGeneralizationReplayAccountSnapshot
    roles: AbsoluteGeneralizationReplayRoleSnapshot
    intentional_role_absent_symbols: tuple[str, ...]
    expected_but_unavailable_symbols: tuple[str, ...]
    replay_universe_identity: str
    data_manifest: AbsoluteGeneralizationReplayManifestSnapshot
    loaded_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationReplay:
    """Raw production evidence; policy success is deliberately not represented."""

    scenario: AbsoluteGeneralizationScenario
    status: str
    replay_error: str
    initial_cash: float
    final_equity: float
    observations: tuple[AbsoluteGeneralizationReplayObservation, ...]
    final_account_payload: AbsoluteGeneralizationReplayPayload


def _jsonable(value: object) -> object:
    """Project repository dataclasses and enums into strict JSON values."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("absolute replay JSON mapping keys must be strings")
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"absolute replay cannot encode {type(value).__name__}")


def _payload(value: object) -> AbsoluteGeneralizationReplayPayload:
    encoded = canonical_json_bytes(_jsonable(value))
    return AbsoluteGeneralizationReplayPayload(
        canonical_json=encoded,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _safe_final_equity(
    engine: _EquityEngine,
    account: AccountState,
    date: pd.Timestamp,
    *,
    fallback: float,
) -> tuple[float, str]:
    """Retain a usable result when the final production mark cannot be read."""

    try:
        return float(engine.equity(account, date)), ""
    except Exception as exc:  # operational finalization failures are evidence
        return float(fallback), f"{type(exc).__name__}: {exc}"


class _EntityTracker:
    """Track stable-ID entity changes without revisiting historical prefixes."""

    __slots__ = (
        "_chain_sha256",
        "_entities",
        "_identity_field",
        "_payloads",
    )

    def __init__(self) -> None:
        self._chain_sha256 = _EMPTY_ENTITY_CHAIN_SHA256
        self._entities: dict[str, object] = {}
        self._identity_field = ""
        self._payloads: dict[str, bytes] = {}

    def changes(
        self,
        *,
        identity_field: str,
        appended_entities: Iterable[object],
        changed_entities: Iterable[object],
        changed_ids: Iterable[str],
        removed_ids: Iterable[str],
    ) -> tuple[
        tuple[AbsoluteGeneralizationReplayPayload, ...],
        tuple[_EntityKey, ...],
        str,
    ]:
        """Apply explicit appends, replacements, mutations, and removals."""

        if self._identity_field not in {"", identity_field}:
            raise RuntimeError("absolute replay entity tracker identity changed")
        self._identity_field = identity_field

        removed = self._stable_ids(removed_ids, label="removed")
        removed_set = set(removed)
        self._validate_removed_ids(removed, identity_field=identity_field)
        appended, explicit_changes = self._validated_entity_maps(
            appended_entities=appended_entities,
            changed_entities=changed_entities,
            removed_ids=removed_set,
            identity_field=identity_field,
        )
        affected = self._affected_entities(
            changed_ids=changed_ids,
            appended_ids=set(appended),
            explicit_changes=explicit_changes,
            removed_ids=removed_set,
            identity_field=identity_field,
        )

        for stable_id in removed:
            self._entities.pop(stable_id)
            self._payloads.pop(stable_id)
        affected.update(appended)

        changed: list[AbsoluteGeneralizationReplayPayload] = []
        chain_changes: list[tuple[str, str]] = []
        for stable_id, entity in sorted(affected.items()):
            evidence = _payload(entity)
            previous = self._payloads.get(stable_id)
            self._entities[stable_id] = entity
            self._payloads[stable_id] = evidence.canonical_json
            if previous == evidence.canonical_json and stable_id not in appended:
                continue
            changed.append(evidence)
            chain_changes.append((stable_id, evidence.sha256))
        if chain_changes or removed:
            transition = canonical_json_bytes(
                {
                    "changed": [
                        {"sha256": sha256, "stable_id": stable_id}
                        for stable_id, sha256 in chain_changes
                    ],
                    "previous_sha256": self._chain_sha256,
                    "removed": list(removed),
                }
            )
            self._chain_sha256 = hashlib.sha256(transition).hexdigest()
        return tuple(changed), removed, self._chain_sha256

    def _validate_removed_ids(
        self,
        removed_ids: Iterable[str],
        *,
        identity_field: str,
    ) -> None:
        if any(stable_id not in self._entities for stable_id in removed_ids):
            raise RuntimeError(
                f"absolute replay removal references unknown {identity_field}"
            )

    def _validated_entity_maps(
        self,
        *,
        appended_entities: Iterable[object],
        changed_entities: Iterable[object],
        removed_ids: set[str],
        identity_field: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        appended = self._entity_map(
            appended_entities,
            identity_field=identity_field,
            label="appended",
        )
        explicit_changes = self._entity_map(
            changed_entities,
            identity_field=identity_field,
            label="changed",
        )
        appended_ids = set(appended)
        explicit_change_ids = set(explicit_changes)
        if appended_ids & explicit_change_ids:
            raise RuntimeError(
                f"absolute replay {identity_field} is both appended and changed"
            )
        if any(
            stable_id in self._entities and stable_id not in removed_ids
            for stable_id in appended
        ):
            raise RuntimeError(
                f"absolute replay {identity_field} must be globally unique"
            )
        if any(stable_id not in self._entities for stable_id in explicit_changes):
            raise RuntimeError(
                f"absolute replay change references unknown {identity_field}"
            )
        if explicit_change_ids & removed_ids:
            raise RuntimeError(
                f"absolute replay removed {identity_field} cannot also be changed"
            )
        return appended, explicit_changes

    def _affected_entities(
        self,
        *,
        changed_ids: Iterable[str],
        appended_ids: set[str],
        explicit_changes: dict[str, object],
        removed_ids: set[str],
        identity_field: str,
    ) -> dict[str, object]:
        affected_ids = set(self._stable_ids(changed_ids, label="changed"))
        if any(
            stable_id not in self._entities and stable_id not in appended_ids
            for stable_id in affected_ids
        ):
            raise RuntimeError(
                f"absolute replay change references unknown {identity_field}"
            )
        affected_ids -= removed_ids | appended_ids | set(explicit_changes)
        affected = {
            stable_id: self._entities[stable_id]
            for stable_id in affected_ids
        }
        affected.update(explicit_changes)
        return affected

    @staticmethod
    def _stable_ids(values: Iterable[str], *, label: str) -> tuple[str, ...]:
        materialized = tuple(values)
        if any(not isinstance(value, str) or not value for value in materialized):
            raise RuntimeError(f"absolute replay {label} ID must be non-empty text")
        return tuple(sorted(set(materialized)))

    def _entity_map(
        self,
        entities: Iterable[object],
        *,
        identity_field: str,
        label: str,
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for entity in entities:
            stable_id = self._stable_id(entity, identity_field=identity_field)
            if stable_id in result:
                raise RuntimeError(
                    f"absolute replay {label} {identity_field} is duplicated"
                )
            result[stable_id] = entity
        return result

    @staticmethod
    def _stable_id(entity: object, *, identity_field: str) -> str:
        if identity_field == "order_id" and isinstance(entity, AccountOrder):
            stable_id = entity.order_id
        elif identity_field == "epoch_id" and isinstance(entity, StrategicEpoch):
            stable_id = entity.epoch_id
        else:
            raise RuntimeError(
                f"absolute replay {identity_field} has an unexpected entity type"
            )
        if not isinstance(stable_id, str) or not stable_id:
            raise RuntimeError(
                f"absolute replay {identity_field} must be a stable non-empty ID"
            )
        return stable_id


def _account_snapshot(
    account: AccountState,
    *,
    order_tracker: _EntityTracker,
    epoch_tracker: _EntityTracker,
    appended_orders: Iterable[AccountOrder] = (),
    appended_epochs: Iterable[StrategicEpoch] = (),
    changed_orders: Iterable[AccountOrder] = (),
    changed_epochs: Iterable[StrategicEpoch] = (),
    changed_order_ids: Iterable[str] = (),
    changed_epoch_ids: Iterable[str] = (),
    removed_order_ids: Iterable[str] = (),
    removed_epoch_ids: Iterable[str] = (),
) -> AbsoluteGeneralizationReplayAccountSnapshot:
    current_account = {
        field.name: getattr(account, field.name)
        for field in fields(account)
        if field.name not in _CUMULATIVE_ACCOUNT_FIELDS
    }
    changed_order_payloads, removed_orders, order_chain = order_tracker.changes(
        identity_field="order_id",
        appended_entities=appended_orders,
        changed_entities=changed_orders,
        changed_ids=changed_order_ids,
        removed_ids=removed_order_ids,
    )
    changed_epoch_payloads, removed_epochs, epoch_chain = epoch_tracker.changes(
        identity_field="epoch_id",
        appended_entities=appended_epochs,
        changed_entities=changed_epochs,
        changed_ids=changed_epoch_ids,
        removed_ids=removed_epoch_ids,
    )
    return AbsoluteGeneralizationReplayAccountSnapshot(
        account_payload=_payload(current_account),
        changed_order_payloads=changed_order_payloads,
        changed_epoch_payloads=changed_epoch_payloads,
        removed_order_keys=removed_orders,
        removed_epoch_keys=removed_epochs,
        order_ledger_chain_sha256=order_chain,
        epoch_ledger_chain_sha256=epoch_chain,
    )


def _decision_evidence_payload(
    decision: Decision,
    *,
    epoch_ledger_chain_sha256: str,
) -> AbsoluteGeneralizationReplayPayload:
    """Project cumulative epoch detail onto its bounded account-delta chain."""

    risk_summary = {
        key: value
        for key, value in decision.risk_summary.items()
        if key != "strategic_epochs"
    }
    if "strategic_epochs" not in decision.risk_summary:
        raise RuntimeError("production decision omitted strategic epoch evidence")
    risk_summary["strategic_epochs"] = {
        "delta_chain_sha256": epoch_ledger_chain_sha256,
    }
    projected = {
        field.name: getattr(decision, field.name)
        for field in fields(decision)
        if field.name != "risk_summary"
    }
    projected["risk_summary"] = risk_summary
    return _payload(projected)


def _order_change_ids(orders: Iterable[object]) -> tuple[str, ...]:
    return tuple(
        stable_id
        for order in orders
        if isinstance((stable_id := getattr(order, "order_id", None)), str)
        and stable_id
    )


def _epoch_change_ids(
    account: AccountState,
    *,
    orders: Iterable[object],
) -> tuple[str, ...]:
    grant = account.strategic_grant
    candidates = {
        account.active_strategic_epoch_id,
        "" if grant is None else grant.epoch_id,
        *(
            epoch_id
            for order in orders
            if isinstance((epoch_id := getattr(order, "epoch_id", None)), str)
        ),
    }
    return tuple(sorted(candidates - {""}))


def _role_snapshot(
    roles: StrategicUniverseRoles,
) -> AbsoluteGeneralizationReplayRoleSnapshot:
    return AbsoluteGeneralizationReplayRoleSnapshot(
        as_of=roles.as_of,
        tradable_symbols=roles.tradable_symbols,
        qualification_reference_symbols=roles.qualification_reference_symbols,
        risk_reference_symbols=roles.risk_reference_symbols,
        available_symbols=roles.available_symbols,
        unavailable_reference_symbols=roles.unavailable_reference_symbols,
        point_in_time_industries=roles.point_in_time_industries,
        tradable_identity=roles.tradable_identity,
        qualification_reference_identity=roles.qualification_reference_identity,
        risk_reference_identity=roles.risk_reference_identity,
        point_in_time_industry_identity=roles.point_in_time_industry_identity,
    )


def _manifest_snapshot(
    manifest: DataManifest,
) -> AbsoluteGeneralizationReplayManifestSnapshot:
    return AbsoluteGeneralizationReplayManifestSnapshot(
        generated_at=manifest.generated_at,
        source=manifest.source,
        adjustment=manifest.adjustment,
        files=tuple(sorted(manifest.files.items())),
        symbols=manifest.symbols,
        start=manifest.start,
        end=manifest.end,
        digest=manifest.digest,
    )


def _trusted_scenario(
    scenario: AbsoluteGeneralizationScenario,
    *,
    root: Path,
) -> AbsoluteGeneralizationScenario:
    if type(scenario) is not AbsoluteGeneralizationScenario:
        raise ValueError("absolute replay requires the exact scenario type")
    trusted = next(
        (
            item
            for item in build_leave_one_out_scenarios(
                load_absolute_generalization_contract(
                    root / "benchmarks/absolute_generalization_acceptance_contract.json"
                )
            )
            if item.cell_id == scenario.cell_id
        ),
        None,
    )
    if trusted is None:
        raise ValueError("absolute replay scenario is not registered")
    values = (
        scenario.cell_id,
        scenario.removed_symbol,
        scenario.window_start,
        scenario.window_end,
        scenario.shard,
        scenario.is_critical,
        scenario.is_witness,
        scenario.contract_sha256,
    )
    trusted_values = (
        trusted.cell_id,
        trusted.removed_symbol,
        trusted.window_start,
        trusted.window_end,
        trusted.shard,
        trusted.is_critical,
        trusted.is_witness,
        trusted.contract_sha256,
    )
    if any(
        type(value) is not type(expected)
        for value, expected in zip(values, trusted_values, strict=True)
    ):
        raise ValueError("absolute replay scenario has an unsafe runtime shape")
    if scenario != trusted:
        raise ValueError("absolute replay scenario differs from the registered contract")
    return trusted


def _point_in_time_symbols(
    *,
    scenario: AbsoluteGeneralizationScenario,
    session: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    active = default_ai_universe().symbols_as_of(session)
    absent = (scenario.removed_symbol,) if scenario.removed_symbol in active else ()
    return (
        tuple(symbol for symbol in active if symbol != scenario.removed_symbol),
        absent,
    )


def _production_roles(
    *,
    engine: ProductionEngine,
    symbols: tuple[str, ...],
    session: str,
    frames: Mapping[str, pd.DataFrame],
) -> StrategicUniverseRoles:
    date = pd.Timestamp(session)
    universe = default_ai_universe()
    available = tuple(
        symbol
        for symbol in (*symbols, *INDEX_SYMBOLS)
        if symbol in frames and date in frames[symbol].index
    )
    return build_strategic_universe_roles(
        as_of=session,
        tradable_symbols=symbols,
        qualification_reference_symbols=symbols,
        risk_reference_symbols=(*symbols, *INDEX_SYMBOLS),
        industries={symbol: universe.industry_of(symbol, session) for symbol in symbols},
        available_symbols=available,
    )


def _roles_match_account(
    roles: StrategicUniverseRoles,
    account: AccountState,
) -> bool:
    return (
        roles.tradable_identity == account.strategic_tradable_universe_identity
        and roles.qualification_reference_identity
        == account.strategic_qualification_universe_identity
        and roles.risk_reference_identity == account.strategic_risk_universe_identity
    )


def _session_observation(
    *,
    engine: ProductionEngine,
    account: AccountState,
    scenario: AbsoluteGeneralizationScenario,
    date: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
    order_tracker: _EntityTracker,
    epoch_tracker: _EntityTracker,
) -> AbsoluteGeneralizationReplayObservation:
    session = str(date.date())
    symbols, intentional_absence = _point_in_time_symbols(
        scenario=scenario,
        session=session,
    )
    replay_universe = ReplayUniverse.from_symbols(
        tradable_symbols=symbols,
        reference_symbols=symbols,
        index_symbols=INDEX_SYMBOLS,
    )
    engine.workspace.prepare(replay_universe)
    for symbol in symbols:
        if symbol not in frames:
            frames[symbol] = engine.workspace.raw_frame(symbol)
    opening_orders = tuple(account.pending_orders)
    opening_epoch_ids = _epoch_change_ids(account, orders=opening_orders)
    opening_order_ledger_start = len(account.order_ledger)
    opening_epoch_ledger_start = len(account.strategic_epochs)
    fill_start = len(account.fills)
    engine.execution.execute_open(
        date=date,
        account=account,
        panel={symbol: frames[symbol] for symbol in symbols},
    )
    equity = engine.equity(account, date)
    new_fills = tuple(_payload(fill) for fill in account.fills[fill_start:])
    post_open_account = _account_snapshot(
        account,
        order_tracker=order_tracker,
        epoch_tracker=epoch_tracker,
        appended_orders=account.order_ledger[opening_order_ledger_start:],
        appended_epochs=account.strategic_epochs[opening_epoch_ledger_start:],
        changed_order_ids=_order_change_ids(opening_orders),
        changed_epoch_ids=(
            *opening_epoch_ids,
            *_epoch_change_ids(account, orders=account.pending_orders),
        ),
    )
    previous_orders = tuple(account.pending_orders)
    decision_epoch_ids = _epoch_change_ids(account, orders=previous_orders)
    decision_order_ledger_start = len(account.order_ledger)
    decision_epoch_ledger_start = len(account.strategic_epochs)
    decision = engine.decide(
        symbols=symbols,
        as_of=session,
        account=account,
        strategic_universe_declaration=build_strategic_universe_declaration(
            qualification_reference_symbols=symbols,
            risk_reference_symbols=symbols,
        ),
    )
    account.pending_orders = list(decision.pending_orders)
    post_decision_account = _account_snapshot(
        account,
        order_tracker=order_tracker,
        epoch_tracker=epoch_tracker,
        appended_orders=account.order_ledger[decision_order_ledger_start:],
        appended_epochs=account.strategic_epochs[decision_epoch_ledger_start:],
        changed_order_ids=(
            *_order_change_ids(previous_orders),
            *_order_change_ids(decision.pending_orders),
        ),
        changed_epoch_ids=(
            *decision_epoch_ids,
            *_epoch_change_ids(account, orders=decision.pending_orders),
        ),
    )
    decision_payload = _decision_evidence_payload(
        decision,
        epoch_ledger_chain_sha256=(
            post_decision_account.epoch_ledger_chain_sha256
        ),
    )
    roles = _production_roles(
        engine=engine,
        symbols=symbols,
        session=session,
        frames=frames,
    )
    if not _roles_match_account(roles, account):
        raise RuntimeError("absolute replay role identities differ from production")
    manifest = engine.workspace.manifest(
        replay_universe.all_symbols,
        as_of=date,
    )
    if (
        account.data_hash != manifest.digest
        or tuple(account.data_hash_symbols) != manifest.symbols
    ):
        raise RuntimeError("absolute replay data identity differs from production")
    return AbsoluteGeneralizationReplayObservation(
        session=session,
        equity=float(equity),
        decision_payload=decision_payload,
        new_fills=new_fills,
        post_open_account=post_open_account,
        post_decision_account=post_decision_account,
        roles=_role_snapshot(roles),
        intentional_role_absent_symbols=intentional_absence,
        expected_but_unavailable_symbols=roles.unavailable_reference_symbols,
        replay_universe_identity=replay_universe.identity_sha256,
        data_manifest=_manifest_snapshot(manifest),
        loaded_symbols=engine.workspace.loaded_symbols,
    )


def run_absolute_generalization_replay(
    scenario: AbsoluteGeneralizationScenario,
    *,
    root: str | Path,
    data_dir: str | Path,
    cache_dir: str | Path,
) -> AbsoluteGeneralizationReplay:
    """Replay one registered removal through next-open execution and ``decide``."""

    repository = Path(root).resolve()
    if not repository.is_dir():
        raise ValueError("absolute replay root must be a directory")
    trusted = _trusted_scenario(scenario, root=repository)
    data = Path(data_dir)
    if data.is_symlink() or not data.is_dir():
        raise ValueError("absolute replay data directory is missing or unsafe")
    cache = Path(cache_dir)
    if cache.is_symlink():
        raise ValueError("absolute replay cache directory is unsafe")
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    initial_cash = account.initial_cash
    observations: list[AbsoluteGeneralizationReplayObservation] = []
    status = "COMPLETE"
    replay_error = ""
    engine: ProductionEngine | None = None
    sessions = pd.DatetimeIndex(())
    try:
        cache.mkdir(parents=True, exist_ok=True)
        engine = ProductionEngine(data)
        index_universe = ReplayUniverse.from_symbols(
            tradable_symbols=(),
            reference_symbols=(),
            index_symbols=INDEX_SYMBOLS,
        )
        engine.workspace.prepare(index_universe)
        sessions = engine.workspace.common_sessions(*INDEX_SYMBOLS)
        sessions = sessions[
            (sessions >= pd.Timestamp(trusted.window_start))
            & (sessions <= pd.Timestamp(trusted.window_end))
        ]
        if len(sessions) < 2:
            raise RuntimeError(
                "absolute replay window has fewer than two index sessions"
            )
        order_tracker = _EntityTracker()
        epoch_tracker = _EntityTracker()
        frames: dict[str, pd.DataFrame] = {
            symbol: engine.workspace.raw_frame(symbol) for symbol in INDEX_SYMBOLS
        }
        for date in sessions:
            observations.append(  # noqa: PERF401 - retain the completed prefix on error
                _session_observation(
                    engine=engine,
                    account=account,
                    scenario=trusted,
                    date=date,
                    frames=frames,
                    order_tracker=order_tracker,
                    epoch_tracker=epoch_tracker,
                )
            )
    except Exception as exc:  # operational replay failures are retained as evidence
        status = "REPLAY_ERROR"
        replay_error = f"{type(exc).__name__}: {exc}"

    final_equity = observations[-1].equity if observations else initial_cash
    if engine is not None and len(sessions) > 0:
        final_index = (
            len(sessions) - 1
            if status == "COMPLETE"
            else min(len(observations), len(sessions) - 1)
        )
        final_equity, final_equity_error = _safe_final_equity(
            engine,
            account,
            sessions[final_index],
            fallback=final_equity,
        )
        if final_equity_error:
            status = "REPLAY_ERROR"
            replay_error = (
                f"{replay_error}; final equity failed with {final_equity_error}"
                if replay_error
                else final_equity_error
            )
    return AbsoluteGeneralizationReplay(
        scenario=trusted,
        status=status,
        replay_error=replay_error,
        initial_cash=initial_cash,
        final_equity=float(final_equity),
        observations=tuple(observations),
        final_account_payload=_payload(account.to_dict()),
    )


__all__ = (
    "AbsoluteGeneralizationReplay",
    "AbsoluteGeneralizationReplayObservation",
    "run_absolute_generalization_replay",
)
