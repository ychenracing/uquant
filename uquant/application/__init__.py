"""Application orchestration and the engine facade's single dependency boundary."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..config import DEFAULT_CONFIG as DEFAULT_CONFIG
from ..config import SystemConfig as SystemConfig
from ..contracts.universe import AIUniverse as AIUniverse
from ..data import DataStore as DataStore
from ..execution import ExecutionPlanner as ExecutionPlanner
from ..execution import reconcile_account_orders as reconcile_account_orders
from ..leader import REFERENCE_UNIVERSE as REFERENCE_UNIVERSE
from ..market import MarketWorkspace as MarketWorkspace
from ..models.strategic_universe import StrategicUniverseDeclaration
from ..market import ReplayCache as ReplayCache
from ..portfolio import PortfolioAllocator as PortfolioAllocator
from ..provenance.fingerprints import source_surface_fingerprint as source_surface_fingerprint
from ..risk import assess_risk as assess_risk
from ..risk_sentinel.history import build_risk_evidence_timeline as build_risk_evidence_timeline
from ..risk_sentinel.models import RiskEvidenceTimeline as RiskEvidenceTimeline
from ..risk_sentinel.service import evaluate_sentinel as evaluate_sentinel
from ..types import AccountState, Decision, PendingOrder, Target
from ..types import LeaderScore as LeaderScore
from .backtest import backtest as run_backtest
from .backtest import equity as mark_equity
from .decision import decide as run_decision
from .decision import decision_config_for_universe as decision_config_for_universe
from .decision import deterministic_decision
from .decision import mark_account_positions as mark_account_positions
from .metrics import equity_drawdown_stats as drawdown_stats
from .metrics import performance_metrics as calculate_performance_metrics
from .risk_timeline_cache import canonical_risk_timeline_json as canonical_risk_json
from .risk_timeline_cache import causal_risk_timeline as causal_risk_timeline
from .risk_timeline_cache import load_risk_timeline_disk_cache as load_risk_timeline_disk_cache
from .risk_timeline_cache import risk_timeline_disk_path as risk_timeline_disk_path
from .risk_timeline_cache import write_risk_timeline_disk_cache as write_risk_timeline_disk_cache
from .target_attribution import attach_target_attribution as attach_target_attribution

__all__ = (
    "calculate_performance_metrics",
    "canonical_risk_json",
    "decision_config_for_universe",
    "deterministic_decision",
    "drawdown_stats",
    "mark_account_positions",
    "mark_equity",
    "risk_timeline_disk_path",
)


def _restore_class_method_docstring(function: Any) -> None:
    docstring = function.__doc__
    if docstring is None:
        return
    head, *tail = docstring.splitlines()
    function.__doc__ = "\n".join((head, *(f"    {line}" if line else line for line in tail)))


_restore_class_method_docstring(mark_account_positions)
_restore_class_method_docstring(run_decision)

ENGINE_PUBLIC_NAMES = (
    "INDEX_SYMBOLS",
    "ProductionEngine",
    "_LEGACY_INDUSTRY",
    "_LEGACY_MANIFEST_SHA256",
    "_RISK_TIMELINE_BUILDER",
    "_RISK_TIMELINE_CACHE_SCHEMA",
    "_SHARED_RISK_TIMELINE_CACHE",
    "code_fingerprint",
    "performance_metrics",
)


def _engine_function[FunctionT: Callable[..., object]](
    function: FunctionT,
    qualname: str,
) -> FunctionT:
    function.__module__ = "uquant.engine"
    function.__name__ = qualname.rpartition(".")[2]
    function.__qualname__ = qualname
    function.__annotations__.pop("self", None)
    return function


def bind_risk_timeline_disk_cache(
    cache_schema: Callable[[], str],
) -> tuple[Callable[..., RiskEvidenceTimeline | None], Callable[..., None]]:
    def load(path: Path, *, key: tuple[str, str, str, str]) -> RiskEvidenceTimeline | None:
        return load_risk_timeline_disk_cache(path, cache_schema(), key=key)

    def write(
        path: Path,
        *,
        key: tuple[str, str, str, str],
        timeline: RiskEvidenceTimeline,
    ) -> None:
        write_risk_timeline_disk_cache(path, cache_schema(), key=key, timeline=timeline)

    return (
        _engine_function(load, "_load_risk_timeline_disk_cache"),
        _engine_function(write, "_write_risk_timeline_disk_cache"),
    )


def bind_target_attribution(
    legacy_industry: Callable[[], str],
    legacy_manifest_sha256: Callable[[], str],
) -> Callable[..., tuple[Target, ...]]:
    def bound(
        *,
        signal_date: str,
        targets: tuple[Target, ...],
        retained_orders: Iterable[PendingOrder] = (),
        cfg: SystemConfig = DEFAULT_CONFIG,
    ) -> tuple[Target, ...]:
        return attach_target_attribution(
            legacy_industry(),
            legacy_manifest_sha256(),
            signal_date=signal_date,
            targets=targets,
            retained_orders=retained_orders,
            cfg=cfg,
        )

    bound.__doc__ = attach_target_attribution.__doc__
    return _engine_function(bound, "_attach_target_attribution")


def bind_causal_risk_timeline(
    timeline_builder: Callable[[], Any],
    native_timeline_builder: Callable[[], Any],
    code_fingerprint_fn: Callable[[], Any],
    shared_timeline_cache: Callable[[], Any],
    load_disk_cache_fn: Callable[[], Any],
    write_disk_cache_fn: Callable[[], Any],
) -> Callable[..., RiskEvidenceTimeline]:
    def bound(self: Any, *, as_of: str, cfg: SystemConfig, universe: AIUniverse) -> RiskEvidenceTimeline:
        return causal_risk_timeline(
            self,
            timeline_builder(),
            native_timeline_builder(),
            code_fingerprint_fn(),
            shared_timeline_cache(),
            load_disk_cache_fn(),
            write_disk_cache_fn(),
            as_of=as_of,
            cfg=cfg,
            universe=universe,
        )

    bound.__doc__ = causal_risk_timeline.__doc__
    return _engine_function(bound, "ProductionEngine._causal_risk_timeline")


def bind_engine_decision(
    assess_risk_fn: Callable[[], Any],
    evaluate_sentinel_fn: Callable[[], Any],
    reconcile_account_orders_fn: Callable[[], Any],
    code_fingerprint_fn: Callable[[], Any],
    attach_target_attribution_fn: Callable[[], Any],
) -> Callable[..., Decision]:
    def bound(
        self: Any,
        *,
        symbols: Iterable[str],
        as_of: str,
        account: AccountState,
        strategic_universe_declaration: StrategicUniverseDeclaration | None = None,
    ) -> Decision:
        return run_decision(
            self,
            assess_risk_fn(),
            evaluate_sentinel_fn(),
            reconcile_account_orders_fn(),
            code_fingerprint_fn(),
            attach_target_attribution_fn(),
            symbols=symbols,
            as_of=as_of,
            account=account,
            strategic_universe_declaration=strategic_universe_declaration,
        )

    bound.__doc__ = run_decision.__doc__
    return _engine_function(bound, "ProductionEngine.decide")


def bind_engine_backtest(
    performance_metrics_fn: Callable[[], Any],
) -> Callable[..., dict[str, Any]]:
    def bound(
        self: Any,
        *,
        symbols: Iterable[str],
        start: str,
        end: str,
        initial_cash: float | None = None,
    ) -> dict[str, Any]:
        return run_backtest(
            self,
            performance_metrics_fn(),
            symbols=symbols,
            start=start,
            end=end,
            initial_cash=initial_cash,
        )

    bound.__doc__ = run_backtest.__doc__
    return _engine_function(bound, "ProductionEngine.backtest")


# Directly assigned compatibility methods retain their immutable engine identities.
for _function, _qualname in (
    (mark_equity, "ProductionEngine.equity"),
    (mark_account_positions, "ProductionEngine._mark_account_positions"),
    (deterministic_decision, "ProductionEngine.deterministic_decision"),
    (decision_config_for_universe, "_decision_config_for_universe"),
    (canonical_risk_json, "_canonical_json"),
    (risk_timeline_disk_path, "_risk_timeline_disk_path"),
    (drawdown_stats, "_drawdown_stats"),
    (calculate_performance_metrics, "performance_metrics"),
):
    _engine_function(_function, _qualname)
del _function, _qualname
