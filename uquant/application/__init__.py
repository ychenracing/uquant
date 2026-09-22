"""Explicit application orchestration dependencies for the engine facade."""

from __future__ import annotations

from ..config import DEFAULT_CONFIG as DEFAULT_CONFIG
from ..config import SystemConfig as SystemConfig
from ..contracts.universe import AIUniverse as AIUniverse
from ..data import DataStore as DataStore
from ..execution import ExecutionPlanner as ExecutionPlanner
from ..execution import reconcile_account_orders as reconcile_account_orders
from ..leader import REFERENCE_UNIVERSE as REFERENCE_UNIVERSE
from ..market import MarketWorkspace as MarketWorkspace
from ..market import ReplayCache as ReplayCache
from ..models.strategic_universe import StrategicUniverseDeclaration as StrategicUniverseDeclaration
from ..portfolio import PortfolioAllocator as PortfolioAllocator
from ..provenance.fingerprints import source_surface_fingerprint as source_surface_fingerprint
from ..risk import assess_risk as assess_risk
from ..risk_sentinel.history import build_risk_evidence_timeline as build_risk_evidence_timeline
from ..risk_sentinel.models import RiskEvidenceTimeline as RiskEvidenceTimeline
from ..risk_sentinel.service import evaluate_sentinel as evaluate_sentinel
from ..types import (
    AccountState as AccountState,
)
from ..types import (
    Decision as Decision,
)
from ..types import LeaderScore as LeaderScore
from ..types import (
    PendingOrder as PendingOrder,
)
from ..types import (
    Target as Target,
)
from .backtest import backtest as run_backtest
from .backtest import equity as mark_equity
from .decision import _DecisionResult as ObservedDecisionResult
from .decision import decide as run_decision
from .decision import decision_config_for_universe as decision_config_for_universe
from .decision import deterministic_decision as deterministic_decision
from .decision import mark_account_positions as mark_account_positions
from .decision import observed_decision as run_observed_decision
from .metrics import equity_drawdown_stats as drawdown_stats
from .metrics import performance_metrics as calculate_performance_metrics
from .risk_timeline_cache import canonical_risk_timeline_json as canonical_risk_json
from .risk_timeline_cache import causal_risk_timeline as causal_risk_timeline
from .risk_timeline_cache import load_risk_timeline_disk_cache as load_risk_timeline_disk_cache
from .risk_timeline_cache import risk_timeline_disk_path as risk_timeline_disk_path
from .risk_timeline_cache import write_risk_timeline_disk_cache as write_risk_timeline_disk_cache
from .target_attribution import attach_target_attribution as attach_target_attribution

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

__all__ = (
    "DEFAULT_CONFIG",
    "ENGINE_PUBLIC_NAMES",
    "REFERENCE_UNIVERSE",
    "AIUniverse",
    "AccountState",
    "DataStore",
    "Decision",
    "ExecutionPlanner",
    "LeaderScore",
    "MarketWorkspace",
    "ObservedDecisionResult",
    "PendingOrder",
    "PortfolioAllocator",
    "ReplayCache",
    "RiskEvidenceTimeline",
    "StrategicUniverseDeclaration",
    "SystemConfig",
    "Target",
    "assess_risk",
    "attach_target_attribution",
    "build_risk_evidence_timeline",
    "calculate_performance_metrics",
    "canonical_risk_json",
    "causal_risk_timeline",
    "decision_config_for_universe",
    "deterministic_decision",
    "drawdown_stats",
    "evaluate_sentinel",
    "load_risk_timeline_disk_cache",
    "mark_account_positions",
    "mark_equity",
    "reconcile_account_orders",
    "risk_timeline_disk_path",
    "run_backtest",
    "run_decision",
    "run_observed_decision",
    "source_surface_fingerprint",
    "write_risk_timeline_disk_cache",
)
