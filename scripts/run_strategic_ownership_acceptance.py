"""Run the bounded strategic-ownership production acceptance."""

# ruff: noqa: E402 - direct execution must prefer this checkout's packages

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.candidate_runner import CandidateRunner, CausalReplayDataStore
from research.strategic_evidence.replay import (
    ReplayRequest,
    ReplayResult,
    reconcile_accounting,
    run_replay,
    validate_replay_accounting,
)
from research.strategic_evidence.trace import RouteTraceRow
from scripts.run_strategic_grant_acceptance import run_baseline
from uquant.account import economic_state_sha256
from uquant.account.validation_attribution import validate_lot_origin_chains, validate_order_intent
from uquant.account.validation_positions import position_from_payload, validate_position_state
from uquant.atomic_io import atomic_write_text
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.contracts.universe import default_ai_universe
from uquant.engine import ProductionEngine, code_fingerprint, performance_metrics
from uquant.market import ReplayHarness
from uquant.models.strategic_universe import (
    build_strategic_universe_declaration,
    build_strategic_universe_roles,
)
from uquant.provenance.fingerprints import source_surface_fingerprint
from uquant.provenance.surfaces import load_source_surface_registry
from uquant.types import (
    ATTRIBUTION_IDENTITY_FIELDS,
    ORDER_INTENT_IMMUTABLE_FIELDS,
    AccountOrder,
    AccountState,
    Fill,
    PendingOrder,
)
from uquant.validation.absolute_generalization._acceptance_evidence import (
    current_candidate_champion_evidence,
)
from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
    validate_exact_execution_chain,
)
from uquant.validation.absolute_generalization._physical_identity import (
    physical_fill_identity_map,
    physical_fill_identity_sha256,
)
from uquant.validation.absolute_generalization.metrics import (
    actual_epoch_facts_from_rows,
    assert_unique_execution_rows,
    first_repair_ready_fact,
    longest_healthy_zero_target_streak,
)
from uquant.validation.manifest import verify_data_manifest

CONTRACT_PATH = ROOT / "benchmarks" / "strategic_ownership_acceptance_contract.json"
GRANT_CONTRACT_PATH = ROOT / "benchmarks" / "strategic_grant_acceptance_contract.json"
SHARD_NAMES = ("champion", "critical", "ghost-a", "ghost-b", "continuity")
SCENARIO_NAMES = (
    "champion-5",
    "report-13",
    "remove-sz300308",
    "remove-sz300394",
    "remove-sh603688",
    "remove-sh688008",
    "remove-sh688082",
    "remove-sz002409",
    "remove-sz300666",
    "remove-sz300502",
    "same-industry-crowning",
    "cross-industry-crowning",
    "failed-first-grant",
)
_SCENARIO_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_OPTICAL_SYMBOLS = ("sz300308", "sz300502", "sz300394")
_MATERIAL_SYMBOLS = ("sh688019", "sh688300", "sz300666")
_INDEX_SYMBOLS = ("sh000300", "sh000682")
_CONTINUITY_SOURCE = "remove-sz300502"
_CURRENT_CONTINUITY_CONTRACT = ROOT / "benchmarks" / "cross_ai_core_strategy_contract.json"
_CURRENT_CONTINUITY_CONTRACT_SHA256 = "9ec5992df69d4466cb2b26cea0e67bbe93f4c6317ba5b8a500ca7b89a75d78b4"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is malformed")
    return cast(Mapping[str, Any], value)


def _sequence(value: object, *, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} is malformed")
    return value


def _date_index(dates: pd.DatetimeIndex, session: str) -> int:
    requested = pd.DatetimeIndex([pd.Timestamp(session)])
    index = int(dates.get_indexer(requested)[0])
    if index < 0:
        raise ValueError(f"fixture session {session} is outside its date range")
    return index


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    """Load the finite ownership contract without consulting generated artifacts."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("strategic ownership contract is malformed")
    return cast(dict[str, Any], raw)


def validate_contract(contract: Mapping[str, Any]) -> None:
    """Reject widened matrices, unknown shards, and ambiguous scenario identities."""

    if set(contract) != {
        "canonical_universe",
        "champion",
        "report_universe_13",
        "shards",
        "thresholds",
        "window",
    }:
        raise ValueError("strategic ownership contract fields differ")
    canonical = tuple(
        str(item) for item in _sequence(contract["canonical_universe"], label="canonical universe")
    )
    if len(canonical) != 34 or canonical != tuple(sorted(set(canonical))):
        raise ValueError("strategic ownership canonical universe differs")
    report = tuple(str(item) for item in _sequence(contract["report_universe_13"], label="report universe"))
    champion = _mapping(contract["champion"], label="champion contract")
    champion_symbols = tuple(
        str(item) for item in _sequence(champion.get("symbols"), label="champion symbols")
    )
    if len(report) != 13 or len(set(report)) != 13 or not set(report) <= set(canonical):
        raise ValueError("strategic ownership report universe differs")
    if len(champion_symbols) != 5 or not set(champion_symbols) <= set(canonical):
        raise ValueError("strategic ownership champion universe differs")
    shards = _mapping(contract["shards"], label="ownership shards")
    if set(shards) != set(SHARD_NAMES):
        raise ValueError("strategic ownership shards differ")
    seen: set[str] = set()
    allowed_kinds = {
        "champion",
        "cross_industry",
        "failed_grant",
        "full_removal",
        "report",
        "same_industry_alias",
    }
    for shard in SHARD_NAMES:
        rows = _sequence(shards[shard], label=f"ownership shard {shard}")
        if not rows:
            raise ValueError("strategic ownership shard is empty")
        local: set[str] = set()
        for value in rows:
            spec = _mapping(value, label="ownership scenario")
            scenario_id = str(spec.get("scenario_id", ""))
            kind = str(spec.get("kind", ""))
            if not _SCENARIO_ID.fullmatch(scenario_id) or scenario_id in seen:
                raise ValueError("strategic ownership scenario identity differs")
            if kind not in allowed_kinds:
                raise ValueError("strategic ownership scenario kind differs")
            if kind == "full_removal" and spec.get("removed_symbol") not in canonical:
                raise ValueError("strategic ownership removal lies outside canonical universe")
            if kind == "same_industry_alias" and spec.get("source_scenario_id") not in local:
                raise ValueError("same-industry ownership alias must follow its source replay")
            seen.add(scenario_id)
            local.add(scenario_id)
    expected = {
        "champion-5",
        "report-13",
        "remove-sz300308",
        "remove-sz300502",
        "remove-sz300394",
        "remove-sh603688",
        "remove-sh688008",
        "remove-sh688082",
        "remove-sz002409",
        "remove-sz300666",
        "same-industry-crowning",
        "cross-industry-crowning",
        "failed-first-grant",
    }
    if seen != expected:
        raise ValueError("strategic ownership scenario coverage differs")
    thresholds = _mapping(contract["thresholds"], label="ownership thresholds")
    expected_thresholds = {
        "failed_grant_retry_healthy_sessions": 20,
        "maximum_drawdown": 0.3,
        "maximum_healthy_zero_target_streak": 60,
        "maximum_level_three_repair_sessions": 60,
        "minimum_distinct_owners": 2,
        "minimum_final_wealth": 1.0,
        "minimum_strategic_epochs": 2,
    }
    if dict(thresholds) != expected_thresholds:
        raise ValueError("strategic ownership thresholds differ")
    window = _mapping(contract["window"], label="ownership window")
    if dict(window) != {"start": "2023-01-03", "end": "2026-08-05"}:
        raise ValueError("strategic ownership window differs")


def _trace_rows(result: ReplayResult) -> tuple[Mapping[str, object], ...]:
    """Project the ownership trace onto the validation-owned helper boundary."""

    return tuple(
        {
            "session": row.date,
            "risk": row.risk,
            "opportunity": row.opportunity,
            "target_gross": row.target_gross,
            "targets": row.targets,
            "orders": row.orders,
            "fills": row.fills,
            "qualification_coverage": row.reference_context.get("reference_coverage", 0.0),
        }
        for row in result.trace
    )


def actual_epoch_facts(result: ReplayResult) -> list[dict[str, Any]]:
    """Return validation-owned fill-gated facts in the legacy report shape."""

    return [
        fact.to_dict()
        for fact in actual_epoch_facts_from_rows(
            final_account=result.final_account,
            trace=_trace_rows(result),
        )
    ]


def _summarize_replay(result: ReplayResult, *, scenario_id: str) -> dict[str, Any]:
    if result.status != "SUCCESS":
        raise RuntimeError(f"{scenario_id} ended as {result.status}: {result.error}")
    if result.intervention_provenance is not None:
        raise RuntimeError(f"{scenario_id} used a research intervention")
    validate_replay_accounting(result)
    trace = _trace_rows(result)
    assert_unique_execution_rows(
        final_account=result.final_account,
        trace=trace,
        allowed_symbols=result.request.symbols,
    )
    initial_cash = float(result.final_account.get("initial_cash", 0.0))
    final_equity = float(result.metrics.get("final_equity", 0.0))
    if initial_cash <= 0.0 or final_equity <= 0.0:
        raise ValueError("ownership replay wealth is malformed")
    epochs = actual_epoch_facts(result)
    positive_sessions = sum(
        any(
            str(item.get("origin_subsystem", "")) == "STRATEGIC"
            and isinstance(item.get("weight"), (int, float))
            and not isinstance(item.get("weight"), bool)
            and float(cast(float, item["weight"])) > 0.0
            for item in row.targets
        )
        for row in result.trace
    )
    repair = first_repair_ready_fact(trace)
    return {
        "accounting_reconciled": True,
        "actual_strategic_epoch_count": len(epochs),
        "distinct_owners": sorted({str(item["owner_symbol"]) for item in epochs}),
        "epochs": epochs,
        "final_wealth": final_equity / initial_cash,
        "longest_healthy_zero_target_streak": longest_healthy_zero_target_streak(trace, strategic_only=False),
        "max_drawdown": float(result.metrics["max_drawdown"]),
        "positive_target_sessions": positive_sessions,
        "repair_ready": (
            None
            if repair is None
            else {
                "capital_budget_level": repair.capital_budget_level,
                "healthy_session_count": repair.reported_healthy_sessions,
                "ready_session": repair.last_ready_session,
                "repair_episode_id": repair.repair_episode_id,
                "required_healthy_sessions": repair.required_healthy_sessions,
            }
        ),
        "scenario_id": scenario_id,
        "status": "PASS",
    }


def _require_economic_thresholds(
    summary: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Any],
) -> None:
    if float(summary["final_wealth"]) <= float(thresholds["minimum_final_wealth"]):
        raise RuntimeError(f"{summary['scenario_id']} final wealth did not exceed one")
    if float(summary["max_drawdown"]) > float(thresholds["maximum_drawdown"]):
        raise RuntimeError(f"{summary['scenario_id']} maximum drawdown exceeded the contract")
    if int(summary["longest_healthy_zero_target_streak"]) > int(
        thresholds["maximum_healthy_zero_target_streak"]
    ):
        raise RuntimeError(f"{summary['scenario_id']} healthy zero-target streak exceeded the contract")
    if int(summary["positive_target_sessions"]) < 1:
        raise RuntimeError(f"{summary['scenario_id']} has no positive strategic target")


def _champion_evidence(
    contract: Mapping[str, Any], *, raw: Mapping[str, object],
    scenario_id: str, expected_source: str,
) -> dict[str, Any]:
    """Reconstruct current acceptance, retaining rejected raw for literal audit."""
    champion = _mapping(contract["champion"], label="champion contract")
    row: dict[str, Any] = {
        "scenario_id": scenario_id, "raw_replay": dict(raw),
        "frozen_final_wealth": champion["frozen_final_wealth"],
        "status": "FAIL", "violations": [],
        "acceptance_basis": {"mode": "current_candidate_rejected"},
    }
    try:
        summary = current_candidate_champion_evidence(raw)
        row.update(summary)
        row["path_sha256"] = summary["sha256"]
        basis = _mapping(summary["acceptance_basis"], label="champion acceptance basis")
        if basis["production_source_sha256"] != expected_source:
            raise ValueError("ownership champion raw account source differs")
        metrics = _mapping(summary["metrics"], label="champion metrics")
        if float(metrics["final_wealth"]) < float(champion["minimum_final_wealth"]):
            row["violations"].append("champion preservation wealth differs")
        limit = float(_mapping(contract["thresholds"], label="thresholds")["maximum_drawdown"])
        if float(metrics["max_drawdown"]) > limit:
            row["violations"].append("champion preservation drawdown differs")
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        row["violations"].append(f"{type(exc).__name__}: {exc}")
    if not row["violations"]:
        row["status"] = "PASS"
    return row


def _run_champion(contract: Mapping[str, Any], *, scenario_id: str) -> dict[str, Any]:
    grant_contract = json.loads(GRANT_CONTRACT_PATH.read_text(encoding="utf-8"))
    baseline = run_baseline(grant_contract)
    raw = _mapping(baseline.get("raw_replay", {}), label="champion raw replay")
    return _champion_evidence(
        contract, raw=raw, scenario_id=scenario_id, expected_source=code_fingerprint(),
    )


def _frozen_replay(
    contract: Mapping[str, Any],
    *,
    scenario_id: str,
    symbols: tuple[str, ...],
    references: tuple[str, ...] | None,
) -> ReplayResult:
    window = _mapping(contract["window"], label="ownership window")
    return run_replay(
        ROOT / "data" / "frozen",
        ReplayRequest(
            symbols=symbols,
            start=str(window["start"]),
            end=str(window["end"]),
            scenario=f"strategic-ownership:{scenario_id}",
            qualification_reference_symbols=references,
            risk_reference_symbols=references,
        ),
    )


def _write_prices(
    root: Path,
    *,
    symbol: str,
    dates: pd.DatetimeIndex,
    daily_returns: Sequence[float],
    locked_session: str = "",
) -> None:
    price = 20.0
    closes: list[float] = []
    for change in daily_returns:
        price *= 1.0 + float(change)
        closes.append(price)
    previous = [closes[0] / (1.0 + float(daily_returns[0])), *closes[:-1]]
    opens = [
        prior * (1.0 + float(change) * 0.45) for prior, change in zip(previous, daily_returns, strict=True)
    ]
    highs = [max(open_price, close) * 1.004 for open_price, close in zip(opens, closes, strict=True)]
    lows = [min(open_price, close) * 0.996 for open_price, close in zip(opens, closes, strict=True)]
    if locked_session:
        index = _date_index(dates, locked_session)
        locked = closes[index - 1] * 1.20
        opens[index] = locked
        highs[index] = locked
        lows[index] = locked
        closes[index] = locked
    volume = 8_000_000.0
    frame = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
            "amount": [close * volume for close in closes],
        }
    )
    frame.to_csv(root / f"{symbol}.csv", index=False)


def _cross_industry_fixture(root: Path) -> None:
    dates = pd.bdate_range("2022-01-03", "2026-08-05")
    replay_index = _date_index(dates, "2023-01-03")
    material_rates = {"sh688019": 0.009, "sh688300": 0.006, "sz300666": 0.005}
    for symbol in (*_OPTICAL_SYMBOLS, *_MATERIAL_SYMBOLS, *_INDEX_SYMBOLS):
        changes: list[float] = []
        for index in range(len(dates)):
            offset = index - replay_index
            if symbol in _INDEX_SYMBOLS:
                change = 0.0012
            elif symbol in _OPTICAL_SYMBOLS:
                change = 0.0045 if offset < 210 else -0.009 if offset < 285 else 0.0005
            else:
                change = -0.0001 if offset < 360 else material_rates[symbol] if offset < 800 else 0.001
            changes.append(change)
        _write_prices(root, symbol=symbol, dates=dates, daily_returns=changes)


def _run_cross_industry(contract: Mapping[str, Any], *, scenario_id: str) -> dict[str, Any]:
    symbols = (*_OPTICAL_SYMBOLS, *_MATERIAL_SYMBOLS)
    with tempfile.TemporaryDirectory(prefix="uquant-cross-industry-") as temporary:
        root = Path(temporary)
        _cross_industry_fixture(root)
        result = run_replay(
            root,
            ReplayRequest(
                symbols=symbols,
                start="2023-01-03",
                end="2026-08-05",
                scenario=f"strategic-ownership:{scenario_id}",
                qualification_reference_symbols=tuple(sorted(symbols)),
                risk_reference_symbols=tuple(sorted(symbols)),
            ),
        )
    summary = _summarize_replay(result, scenario_id=scenario_id)
    thresholds = _mapping(contract["thresholds"], label="thresholds")
    _require_economic_thresholds(summary, thresholds=thresholds)
    epochs = _sequence(summary["epochs"], label="cross-industry epochs")
    owners = [str(_mapping(item, label="cross-industry epoch")["owner_symbol"]) for item in epochs]
    universe = default_ai_universe()
    industries = [universe.industry_of(owner, "2026-08-05") for owner in owners]
    if len(epochs) < int(thresholds["minimum_strategic_epochs"]):
        raise RuntimeError("cross-industry replay has fewer than two actual epochs")
    if len(set(owners)) < int(thresholds["minimum_distinct_owners"]):
        raise RuntimeError("cross-industry replay has fewer than two owners")
    if len(set(industries)) < 2:
        raise RuntimeError("cross-industry replay did not cross an industry boundary")
    summary["industries"] = industries
    return summary


def _failed_grant_fixture(root: Path) -> None:
    dates = pd.bdate_range("2022-01-03", "2023-02-28")
    material_rates = {"sh688019": 0.007, "sh688300": 0.0055, "sz300666": 0.005}
    for symbol in (*_OPTICAL_SYMBOLS, *_MATERIAL_SYMBOLS, *_INDEX_SYMBOLS):
        if symbol in _INDEX_SYMBOLS:
            rate = 0.001
        elif symbol in _OPTICAL_SYMBOLS:
            rate = 0.0045
        else:
            rate = material_rates[symbol]
        _write_prices(
            root,
            symbol=symbol,
            dates=dates,
            daily_returns=[rate] * len(dates),
            locked_session="2023-01-05" if symbol in _MATERIAL_SYMBOLS else "",
        )


def _route_row(
    *,
    account: AccountState,
    decision: Any,
    equity: float,
    new_fills: Sequence[Any],
    close_marks: Mapping[str, float],
) -> RouteTraceRow:
    summary = decision.risk_summary
    raw_leaders = summary.get("leader_ranking", ())
    leaders = tuple(dict(item) for item in raw_leaders if isinstance(item, Mapping))
    reference_context = {
        str(name): value for name, value in summary.items() if str(name).startswith("reference_")
    }
    risk = {
        str(name): value
        for name, value in summary.items()
        if name not in {"leader_ranking", "effective_config_sha256"}
        and not str(name).startswith("reference_")
    }
    return RouteTraceRow(
        date=decision.date,
        reference_context=reference_context,
        leaders=leaders,
        risk={"state": decision.risk.value, **risk},
        opportunity=decision.opportunity.value,
        targets=tuple(asdict(item) for item in decision.targets),
        orders=tuple(asdict(item) for item in decision.pending_orders),
        fills=tuple(asdict(item) for item in new_fills),
        account_sha256=economic_state_sha256(account),
        equity=equity,
        target_gross=decision.target_gross,
        intervention_provenance=None,
        cash=account.cash,
        position_shares={
            symbol: position.shares for symbol, position in account.positions.items() if position.shares
        },
        close_marks=dict(close_marks),
    )


def _failed_grant_replay(root: Path) -> tuple[ReplayResult, list[dict[str, Any]]]:
    all_symbols = (*_OPTICAL_SYMBOLS, *_MATERIAL_SYMBOLS)
    engine = ProductionEngine(root)
    engine.data = CausalReplayDataStore(root)
    harness = ReplayHarness(
        workspace=engine.workspace,
        universe=CandidateRunner(root).replay_universe(all_symbols),
    )
    sessions = harness.sessions(start="2023-01-03", end="2023-02-28")
    panel = harness.raw_panel(all_symbols)
    account = AccountState.empty(engine.cfg.initial_cash)
    trace: list[RouteTraceRow] = []
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    fill_cursor = 0
    grants: dict[str, dict[str, Any]] = {}
    for session in sessions:
        engine.execution.execute_open(date=session, account=account, panel=panel)
        equity = engine.equity(account, session)
        equity_rows.append((session, equity))
        new_fills = tuple(account.fills[fill_cursor:])
        fill_cursor = len(account.fills)
        symbols = all_symbols if session < pd.Timestamp("2023-01-05") else _OPTICAL_SYMBOLS
        references = tuple(sorted(symbols))
        decision = engine.decide(
            symbols=symbols,
            as_of=str(session.date()),
            account=account,
            strategic_universe_declaration=build_strategic_universe_declaration(
                qualification_reference_symbols=references,
                risk_reference_symbols=references,
            ),
        )
        account.pending_orders = list(decision.pending_orders)
        if account.strategic_grant is not None:
            grants[account.strategic_grant.grant_id] = asdict(account.strategic_grant)
        close_marks = {
            symbol: engine.workspace.price(symbol, session)
            for symbol, position in account.positions.items()
            if position.shares > 0
        }
        reconcile_accounting(
            cash=account.cash,
            position_shares={
                symbol: position.shares for symbol, position in account.positions.items() if position.shares
            },
            close_marks=close_marks,
            equity=equity,
        )
        trace.append(
            _route_row(
                account=account,
                decision=decision,
                equity=equity,
                new_fills=new_fills,
                close_marks=close_marks,
            )
        )
    final_equity = engine.equity(account, sessions[-1])
    metrics = performance_metrics(
        equity_rows=equity_rows,
        fills=account.fills,
        orders=account.order_ledger,
        initial_cash=account.initial_cash,
        risk_events=account.risk_events,
        benchmark_total_return=(
            engine.workspace.price("sh000682", sessions[-1]) / engine.workspace.price("sh000682", sessions[0])
            - 1.0
        ),
    )
    metrics["final_equity"] = final_equity
    result = ReplayResult(
        request=ReplayRequest(
            symbols=all_symbols,
            start="2023-01-03",
            end="2023-02-28",
            scenario="strategic-ownership:failed-first-grant",
        ),
        metrics=metrics,
        trace=tuple(trace),
        final_account=account.to_dict(),
        intervention_provenance=None,
    )
    return result, list(grants.values())


def _run_failed_grant(contract: Mapping[str, Any], *, scenario_id: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="uquant-failed-grant-") as temporary:
        root = Path(temporary)
        _failed_grant_fixture(root)
        result, grants = _failed_grant_replay(root)
    summary = _summarize_replay(result, scenario_id=scenario_id)
    if len(grants) != 2:
        raise RuntimeError("failed-grant replay did not create exactly two economic grants")
    first, second = grants
    if (
        first.get("status") != "EXPIRED"
        or int(first.get("filled_shares", -1)) != 0
        or not first.get("expiry_reason")
    ):
        raise RuntimeError("failed-grant replay did not terminally expire the unfilled grant")
    if (
        second.get("previous_grant_id") != first.get("grant_id")
        or second.get("grant_id") == first.get("grant_id")
        or int(second.get("filled_shares", 0)) <= 0
    ):
        raise RuntimeError("failed-grant replay identity chain differs")
    actual = _sequence(summary["epochs"], label="failed-grant actual epochs")
    if len(actual) != 1 or _mapping(actual[0], label="failed-grant epoch")["grant_id"] != second["grant_id"]:
        raise RuntimeError("failed-grant replay did not activate only the second grant")
    expired_epoch = next(
        (
            item
            for item in _sequence(result.final_account.get("strategic_epochs", []), label="epochs")
            if _mapping(item, label="epoch").get("grant_id") == first["grant_id"]
        ),
        None,
    )
    if (
        expired_epoch is None
        or _mapping(expired_epoch, label="expired epoch").get("realized_status") != "EXPIRED"
    ):
        raise RuntimeError("failed grant left an orphan epoch")
    trace_dates = [row.date for row in result.trace]
    retry_start = str(_mapping(expired_epoch, label="expired epoch")["closed_session"])
    retry_end = str(second["created_session"])
    retry_sessions = sum(retry_start < session <= retry_end for session in trace_dates)
    maximum = int(_mapping(contract["thresholds"], label="thresholds")["failed_grant_retry_healthy_sessions"])
    if retry_sessions > maximum:
        raise RuntimeError("failed-grant retry exceeded the bounded session contract")
    summary["first_grant"] = first
    summary["retry_sessions"] = retry_sessions
    summary["second_grant"] = second
    return summary


def _validate_full_removal(
    contract: Mapping[str, Any],
    *,
    removed_symbol: str,
    summary: dict[str, Any],
) -> None:
    thresholds = _mapping(contract["thresholds"], label="thresholds")
    _require_economic_thresholds(summary, thresholds=thresholds)
    owners = set(str(item) for item in _sequence(summary["distinct_owners"], label="owners"))
    if removed_symbol in owners:
        raise RuntimeError("removed symbol became a strategic owner")
    if removed_symbol in {"sz300308", "sz300502", "sz300394"} and not owners:
        raise RuntimeError("critical removal formed no actual strategic epoch")
    if removed_symbol == "sz300308":
        ready = summary.get("repair_ready")
        if not isinstance(ready, Mapping):
            raise RuntimeError("owner removal has no ready capital-repair episode")
        if int(ready.get("healthy_session_count", 0)) > int(
            thresholds["maximum_level_three_repair_sessions"]
        ):
            raise RuntimeError("level-three capital repair exceeded its bounded clock")
        epochs = _sequence(summary["epochs"], label="owner-removal epochs")
        if not epochs or not _mapping(epochs[0], label="owner-removal epoch").get("authorization_id"):
            raise RuntimeError("owner-removal grant lacks its rearm authorization")


def _continuity_basis() -> dict[str, str]:
    """Bind the current interpretation without rewriting the historical contract."""
    raw = _CURRENT_CONTINUITY_CONTRACT.read_bytes()
    contract_id = "cross-ai-core-strategy-20260905-v1"
    if (
        hashlib.sha256(raw).hexdigest() != _CURRENT_CONTINUITY_CONTRACT_SHA256
        or json.loads(raw).get("contract_id") != contract_id
    ):
        raise ValueError("current continuity contract identity differs")
    return {
        "contract_id": contract_id,
        "contract_sha256": _CURRENT_CONTINUITY_CONTRACT_SHA256,
        "source_mode": "complete_linked_epochs_may_cross_industries",
        "alias_mode": "adjacent_distinct_owners_same_admission_industry_real_fills",
        "config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "production_source_sha256": code_fingerprint(),
    }


def _continuity_result(raw: Mapping[str, Any]) -> ReplayResult:
    request = dict(_mapping(raw["request"], label="continuity request"))
    for field in ("symbols", "qualification_reference_symbols", "risk_reference_symbols"):
        request[field] = tuple(_sequence(request[field], label=f"continuity {field}"))
    return ReplayResult(
        **{
            **raw, "request": ReplayRequest(**request),
            "trace": tuple(RouteTraceRow(**row) for row in _sequence(raw["trace"], label="continuity trace")),
        }
    )


def _continuity_summary(contract: Mapping[str, Any], result: ReplayResult) -> dict[str, Any]:
    """Retain the complete raw source and derive immutable, fill-backed admissions."""
    summary = _summarize_replay(result, scenario_id=_CONTINUITY_SOURCE)
    _validate_full_removal(contract, removed_symbol="sz300502", summary=summary)
    symbols = tuple(symbol for symbol in contract["canonical_universe"] if symbol != "sz300502")
    window = _mapping(contract["window"], label="continuity window")
    expected_request = ReplayRequest(
        symbols=symbols, start=window["start"], end=window["end"],
        scenario=f"strategic-ownership:{_CONTINUITY_SOURCE}",
        qualification_reference_symbols=symbols, risk_reference_symbols=symbols,
    )
    if result.request != expected_request:
        raise ValueError("continuity source request or removal roles differ")
    dates = [row.date for row in result.trace]
    if dates != sorted(set(dates)) or dates[0] != window["start"] or dates[-1] != window["end"]:
        raise ValueError("continuity full trace sessions differ")
    universe = default_ai_universe()
    for row in result.trace:
        references = tuple(symbol for symbol in symbols if symbol in universe.symbols_as_of(row.date))
        roles = build_strategic_universe_roles(
            as_of=row.date, tradable_symbols=symbols,
            qualification_reference_symbols=references,
            risk_reference_symbols=(*references, *_INDEX_SYMBOLS),
            industries={symbol: universe.industry_of(symbol, row.date) for symbol in references},
            available_symbols=(),
        )
        observed = _mapping(row.risk.get("strategic_universe_identities"), label="continuity daily roles")
        # Qualification identity also binds availability, which this research
        # trace does not expose separately. Preserve it literally; do not invent
        # an all-available daily role observation from the request.
        if (
            observed.get("tradable") != roles.tradable_identity
            or observed.get("risk_reference") != roles.risk_reference_identity
            or re.fullmatch(r"[0-9a-f]{64}", str(observed.get("qualification_reference", ""))) is None
        ):
            raise ValueError("continuity daily role identity differs")
        if row.intervention_provenance is not None or set(row.position_shares) - set(symbols):
            raise ValueError("continuity trace intervention or removed position differs")
        if any(fill.get("fill_date") != row.date for fill in row.fills):
            raise ValueError("continuity physical fill trace session differs")
    raw_account = result.final_account
    positions = _mapping(raw_account.get("positions"), label="continuity positions")
    if set(positions) - set(symbols) or raw_account.get("code_hash") != code_fingerprint():
        raise ValueError("continuity account removal or production source differs")
    orders = {}
    for item in _sequence(raw_account.get("order_ledger"), label="continuity orders"):
        order = AccountOrder(**_mapping(item, label="continuity order"))
        orders[order.order_id] = order
    for order in orders.values():
        validate_order_intent(order, label="continuity order", validate_attribution=True)
    for item in _sequence(raw_account.get("pending_orders", []), label="continuity pending orders"):
        pending = PendingOrder(**_mapping(item, label="continuity pending order"))
        if pending.symbol not in symbols or pending.order_id not in orders or any(
            getattr(pending, field) != getattr(orders[pending.order_id], field)
            for field in ORDER_INTENT_IMMUTABLE_FIELDS
        ):
            raise ValueError("continuity pending order attribution differs")
    fills = tuple(_mapping(item, label="continuity fill") for item in raw_account["fills"])
    traced_fills = physical_fill_identity_map(tuple(fill for row in result.trace for fill in row.fills))
    if _canonical_json(list(traced_fills.values())) != _canonical_json(list(physical_fill_identity_map(fills).values())):
        raise ValueError("continuity trace and ledger fills differ")
    for fill in fills:
        fill_order = orders.get(str(fill["order_id"]))
        if fill_order is None or any(
            fill.get(field) != getattr(fill_order, field)
            for field in (*ATTRIBUTION_IDENTITY_FIELDS, "symbol", "signal_date", "side")
        ):
            raise ValueError("continuity fill attribution differs")
    state = AccountState(
        initial_cash=float(raw_account["initial_cash"]), cash=float(raw_account["cash"]),
        order_ledger=list(orders.values()), fills=[Fill(**item) for item in fills],
        positions={symbol: position_from_payload(dict(value)) for symbol, value in positions.items()},
    )
    validate_position_state(state, validate_attribution=True)
    validate_lot_origin_chains(state)
    trace = _trace_rows(result)
    facts = actual_epoch_facts_from_rows(final_account=raw_account, trace=trace)
    validate_exact_execution_chain(final_account=raw_account, trace=trace, epochs=facts)
    admissions = []
    for fact in facts:
        first_fill = min(
            (fill for fill in fills if fill["epoch_id"] == fact.epoch_id
             and fill["grant_id"] == fact.grant_id and fill["symbol"] == fact.owner_symbol
             and fill["side"] == "BUY"),
            key=lambda fill: (fill["fill_date"], physical_fill_identity_sha256(fill)),
        )
        order = orders[str(first_fill["order_id"])]
        targets = [target for row in result.trace if row.date == order.signal_date
                   for target in row.targets if target.get("event_id") == order.event_id
                   and target.get("symbol") == order.symbol]
        if len(targets) != 1 or any(
            targets[0].get(field) != getattr(order, field) for field in ATTRIBUTION_IDENTITY_FIELDS
        ):
            raise ValueError("continuity admission target attribution differs")
        admissions.append({
            **fact.to_dict(), "admission_session": order.signal_date,
            "order_id": order.order_id, "event_id": order.event_id,
            "industry_at_entry": order.industry_at_entry,
            "industry_manifest_sha256": order.industry_manifest_sha256,
            "physical_fill_sha256": physical_fill_identity_sha256(first_fill),
        })
    raw = asdict(result)
    summary["raw_replay"] = raw
    summary["continuity"] = {
        "basis": _continuity_basis(), "raw_sha256": _canonical_sha256(raw), "admissions": admissions,
    }
    return summary


def _validate_repeated(
    contract: Mapping[str, Any],
    *,
    summary: Mapping[str, Any],
    same_industry: bool,
) -> dict[str, Any] | None:
    raw = _mapping(summary.get("raw_replay"), label="continuity raw replay")
    expected = _continuity_summary(contract, _continuity_result(raw))
    scenario_id = summary.get("scenario_id")
    if scenario_id not in {_CONTINUITY_SOURCE, "same-industry-crowning"}:
        raise ValueError("continuity scenario identity differs")
    if scenario_id == "same-industry-crowning" and summary.get("source_scenario_id") != _CONTINUITY_SOURCE:
        raise ValueError("continuity alias source differs")
    expected["scenario_id"] = scenario_id
    actual = {key: value for key, value in summary.items()
              if key not in {"cache_hit", "source_scenario_id", "same_industry_witness"}}
    if _canonical_json(actual) != _canonical_json(expected):
        raise ValueError("continuity summary differs from raw replay")
    thresholds = _mapping(contract["thresholds"], label="thresholds")
    epochs = _sequence(summary["epochs"], label="repeated-crowning epochs")
    owners = [str(_mapping(item, label="repeated epoch")["owner_symbol"]) for item in epochs]
    if len(epochs) < int(thresholds["minimum_strategic_epochs"]):
        raise RuntimeError("repeated-crowning replay has fewer than two actual epochs")
    if len(set(owners)) < int(thresholds["minimum_distinct_owners"]):
        raise RuntimeError("repeated-crowning replay has fewer than two owners")
    for previous, successor in pairwise(epochs):
        previous_epoch = _mapping(previous, label="previous repeated epoch")
        successor_epoch = _mapping(successor, label="successor repeated epoch")
        if successor_epoch.get("previous_epoch_id") != previous_epoch.get("epoch_id"):
            raise RuntimeError("repeated-crowning epoch identity chain differs")
        if successor_epoch.get("previous_grant_id") != previous_epoch.get("grant_id"):
            raise RuntimeError("repeated-crowning grant identity chain differs")
    witness = None
    if same_industry:
        for previous, successor in pairwise(expected["continuity"]["admissions"]):
            if (
                previous["owner_symbol"] != successor["owner_symbol"]
                and previous["industry_at_entry"] == successor["industry_at_entry"]
            ):
                witness = {
                    "source_scenario_id": _CONTINUITY_SOURCE,
                    "raw_sha256": expected["continuity"]["raw_sha256"],
                    "industry_at_entry": previous["industry_at_entry"],
                    "admissions": [previous, successor],
                }
                break
        if witness is None:
            raise RuntimeError("same-industry replay has no adjacent real same-industry successor")
    if "same_industry_witness" in summary and summary["same_industry_witness"] != witness:
        raise ValueError("continuity supplied witness differs from raw replay")
    return witness


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_identity_context(contract: Mapping[str, Any]) -> dict[str, object]:
    grant_contract = json.loads(GRANT_CONTRACT_PATH.read_text(encoding="utf-8"))
    registry = load_source_surface_registry(ROOT)
    return {
        "config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "frozen_data": verify_data_manifest(ROOT / "data" / "frozen"),
        "full_package_source_sha256": source_surface_fingerprint(
            ROOT, "full_package_v1"
        ),
        "grant_contract_sha256": _canonical_sha256(grant_contract),
        "grant_runner_source_sha256": _sha256_file(
            ROOT / "scripts" / "run_strategic_grant_acceptance.py"
        ),
        "ownership_contract_sha256": _canonical_sha256(contract),
        "production_source_sha256": code_fingerprint(),
        "runner_source_sha256": _sha256_file(Path(__file__)),
        "runtime": runtime_environment_provenance(ROOT),
        "schema_version": 1,
        "source_surface_registry_sha256": registry.canonical_sha256,
        "validation_runner_source_sha256": source_surface_fingerprint(
            ROOT, "validation_runner_v1"
        ),
    }


def _cache_identity_payload(
    contract: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    payload = dict(context or _cache_identity_context(contract))
    payload["scenario"] = dict(spec)
    if spec.get("scenario_id") in {_CONTINUITY_SOURCE, "same-industry-crowning"}:
        payload["continuity_basis"] = _continuity_basis()
    return payload


def _read_cache(path: Path, *, identity: str) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(envelope, Mapping) or set(envelope) != {"identity", "payload", "sha256"}:
        return None
    payload = envelope.get("payload")
    if (
        envelope.get("identity") != identity
        or not isinstance(payload, Mapping)
        or envelope.get("sha256") != _canonical_sha256(payload)
    ):
        return None
    if payload.get("scenario_id") == "champion-5" and payload.get("status") == "PASS":
        raw = payload.get("raw_replay")
        if not isinstance(raw, Mapping):
            return None
        expected = _champion_evidence(
            load_contract(), raw=raw, scenario_id="champion-5", expected_source=code_fingerprint(),
        )
        if dict(payload) != expected or expected["status"] != "PASS":
            return None
    if payload.get("scenario_id") in {_CONTINUITY_SOURCE, "same-industry-crowning"}:
        try:
            _validate_repeated(
                load_contract(), summary=payload,
                same_industry=payload["scenario_id"] == "same-industry-crowning",
            )
        except (ValueError, RuntimeError, KeyError, TypeError):
            return None
    return dict(payload)


def _write_cache(path: Path, *, identity: str, payload: Mapping[str, Any]) -> None:
    envelope = {
        "identity": identity,
        "payload": dict(payload),
        "sha256": _canonical_sha256(payload),
    }
    atomic_write_text(path, json.dumps(envelope, indent=2, sort_keys=True) + "\n")


def _execute_scenario(
    contract: Mapping[str, Any],
    *,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    scenario_id = str(spec["scenario_id"])
    kind = str(spec["kind"])
    if kind == "champion":
        return _run_champion(contract, scenario_id=scenario_id)
    if kind == "report":
        symbols = tuple(
            str(item) for item in _sequence(contract["report_universe_13"], label="report universe")
        )
        references = tuple(
            str(item) for item in _sequence(contract["canonical_universe"], label="canonical universe")
        )
        summary = _summarize_replay(
            _frozen_replay(
                contract,
                scenario_id=scenario_id,
                symbols=symbols,
                references=references,
            ),
            scenario_id=scenario_id,
        )
        _require_economic_thresholds(
            summary,
            thresholds=_mapping(contract["thresholds"], label="thresholds"),
        )
        return summary
    if kind == "full_removal":
        removed_symbol = str(spec["removed_symbol"])
        symbols = tuple(
            str(item)
            for item in _sequence(contract["canonical_universe"], label="canonical universe")
            if str(item) != removed_symbol
        )
        replay = _frozen_replay(
            contract, scenario_id=scenario_id, symbols=symbols, references=symbols,
        )
        if scenario_id == _CONTINUITY_SOURCE:
            summary = _continuity_summary(contract, replay)
            _validate_repeated(contract, summary=summary, same_industry=False)
            return summary
        summary = _summarize_replay(replay, scenario_id=scenario_id)
        _validate_full_removal(
            contract,
            removed_symbol=removed_symbol,
            summary=summary,
        )
        return summary
    if kind == "cross_industry":
        return _run_cross_industry(contract, scenario_id=scenario_id)
    if kind == "failed_grant":
        return _run_failed_grant(contract, scenario_id=scenario_id)
    raise ValueError("ownership alias cannot execute without its source replay")


def run_acceptance_shard(
    *,
    shard: str,
    scenario: str | None = None,
    output: Path,
    cache_dir: Path,
) -> dict[str, Any]:
    """Run one finite shard or one diagnostic scenario and persist compact facts."""

    contract = load_contract()
    validate_contract(contract)
    if shard not in SHARD_NAMES:
        raise ValueError("unknown strategic ownership shard")
    cache_dir.mkdir(parents=True, exist_ok=True)
    shards = _mapping(contract["shards"], label="ownership shards")
    shard_specs = tuple(
        _mapping(value, label="ownership scenario")
        for value in _sequence(shards[shard], label="ownership shard")
    )
    specs_by_id = {str(spec["scenario_id"]): spec for spec in shard_specs}
    if scenario is None:
        execution_specs = shard_specs
    else:
        if scenario not in SCENARIO_NAMES:
            raise ValueError("unknown strategic ownership scenario")
        selected = specs_by_id.get(scenario)
        if selected is None:
            raise ValueError("strategic ownership scenario does not belong to shard")
        if selected["kind"] == "same_industry_alias":
            source_id = str(selected["source_scenario_id"])
            execution_specs = (specs_by_id[source_id], selected)
        else:
            execution_specs = (selected,)

    identity_context = _cache_identity_context(contract)
    by_id: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    cache_metadata: dict[str, dict[str, object]] = {}
    for spec in execution_specs:
        scenario_id = str(spec["scenario_id"])
        if spec["kind"] == "same_industry_alias":
            source_id = str(spec["source_scenario_id"])
            source = dict(by_id[source_id])
            source["scenario_id"] = scenario_id
            source["source_scenario_id"] = source_id
            source["same_industry_witness"] = _validate_repeated(contract, summary=source, same_industry=True)
            row = source
            identity_payload = _cache_identity_payload(
                contract, spec, context=identity_context
            )
            source_metadata = cache_metadata[source_id]
            cache_metadata[scenario_id] = {
                "cache_dependencies": {source_id: source_metadata},
                "cache_hit": bool(source_metadata["cache_hit"]),
                "cache_identity": _canonical_sha256(identity_payload),
                "cache_identity_payload": identity_payload,
            }
        else:
            identity_payload = _cache_identity_payload(
                contract, spec, context=identity_context
            )
            identity = _canonical_sha256(identity_payload)
            cache_path = cache_dir / f"{scenario_id}-{identity}.json"
            cached = _read_cache(cache_path, identity=identity)
            if cached is None:
                row = _execute_scenario(contract, spec=spec)
                _write_cache(cache_path, identity=identity, payload=row)
                row["cache_hit"] = False
            else:
                row = cached
                row["cache_hit"] = True
            if spec["kind"] == "full_removal" and scenario_id == "remove-sz300502":
                _validate_repeated(contract, summary=row, same_industry=False)
            cache_metadata[scenario_id] = {
                "cache_dependencies": {},
                "cache_hit": bool(row["cache_hit"]),
                "cache_identity": identity,
                "cache_identity_payload": identity_payload,
            }
        by_id[scenario_id] = row
        if scenario is None or scenario_id == scenario:
            rows.append(row)
    result: dict[str, Any] = {
        "contract_sha256": _canonical_sha256(contract),
        "production_source_identity": code_fingerprint(),
        "scenarios": rows,
        "shard": shard,
        "status": "PASS" if all(row.get("status") == "PASS" for row in rows) else "FAIL",
    }
    if scenario is not None:
        selected_metadata = cache_metadata[scenario]
        result.update(
            {
                "authoritative_acceptance": False,
                "cache_dependencies": selected_metadata["cache_dependencies"],
                "cache_hit": selected_metadata["cache_hit"],
                "cache_identity": selected_metadata["cache_identity"],
                "cache_identity_payload": selected_metadata[
                    "cache_identity_payload"
                ],
                "diagnostic_only": True,
                "selected_scenario": scenario,
            }
        )
    atomic_write_text(output, json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", choices=SHARD_NAMES, required=True)
    parser.add_argument("--scenario", choices=SCENARIO_NAMES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_acceptance_shard(
        shard=args.shard,
        scenario=args.scenario,
        output=args.output,
        cache_dir=args.cache_dir,
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "SCENARIO_NAMES",
    "SHARD_NAMES",
    "actual_epoch_facts",
    "load_contract",
    "main",
    "run_acceptance_shard",
    "validate_contract",
)
