"""The stable production-engine facade over application orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pandas as pd

import uquant.application as _application
from uquant.application import (
    DEFAULT_CONFIG,
    DataStore,
    ExecutionPlanner,
    LeaderScore,
    PortfolioAllocator,
    RiskEvidenceTimeline,
    SystemConfig,
)
from uquant.application import (
    MarketWorkspace as _MarketWorkspace,
)
from uquant.application import (
    ReplayCache as _ReplayCache,
)

INDEX_SYMBOLS = ("sh000300", "sh000682")
REFERENCE_UNIVERSE = _application.REFERENCE_UNIVERSE
_LEGACY_INDUSTRY = "legacy_unmapped"
_LEGACY_MANIFEST_SHA256 = "0" * 64
_SHARED_RISK_TIMELINE_CACHE: _ReplayCache[object, object] = _ReplayCache(capacity=8)
_RISK_TIMELINE_BUILDER = _application.build_risk_evidence_timeline
_RISK_TIMELINE_CACHE_SCHEMA = "uquant.risk-evidence-cache.v1"
assess_risk = _application.assess_risk
evaluate_sentinel = _application.evaluate_sentinel
reconcile_account_orders = _application.reconcile_account_orders
build_risk_evidence_timeline = _application.build_risk_evidence_timeline
__all__ = _application.ENGINE_PUBLIC_NAMES

_canonical_json = _application.canonical_risk_json
_risk_timeline_disk_path = _application.risk_timeline_disk_path
_decision_config_for_universe = _application.decision_config_for_universe
performance_metrics = _application.calculate_performance_metrics
_drawdown_stats = _application.drawdown_stats
_load_risk_timeline_disk_cache, _write_risk_timeline_disk_cache = _application.bind_risk_timeline_disk_cache(
    lambda: _RISK_TIMELINE_CACHE_SCHEMA
)
_attach_target_attribution = _application.bind_target_attribution(
    lambda: _LEGACY_INDUSTRY,
    lambda: _LEGACY_MANIFEST_SHA256,
)


def code_fingerprint() -> str:
    """Return a stable digest of every production Python module and contract."""
    root = Path(__file__).resolve().parent.parent
    return _application.source_surface_fingerprint(root, "economic_decision_v1")


class ProductionEngine:
    """Own the single decision path used by both daily operation and replay."""

    def __setattr__(self, name: str, value: object) -> None:
        """Keep the frozen ``data`` attribute and workspace authority identical."""
        object.__setattr__(self, name, value)
        workspace = self.__dict__.get("workspace")
        if name == "data" and isinstance(workspace, _MarketWorkspace) and workspace.data is not value:
            workspace.replace_data_store(cast(DataStore, value))

    def __init__(self, data_dir: str | Path, cfg: SystemConfig = DEFAULT_CONFIG) -> None:
        self.cfg = cfg
        self.workspace = _MarketWorkspace.production(
            data_dir, cfg, reference_symbols=REFERENCE_UNIVERSE, index_symbols=INDEX_SYMBOLS
        )
        self.data = self.workspace.data
        self.execution = ExecutionPlanner(cfg)
        self.allocator = PortfolioAllocator(cfg)
        # Branch-free compatibility aliases for frozen test seams. Production,
        # validation, research, and scripts use the owned workspace API.
        self._raw = self.workspace._raw
        self._features = self.workspace._features
        self._code_hash: str | None = None
        self._leader_score_cache: dict[tuple[object, ...], dict[str, LeaderScore]] = {}
        self._risk_timeline_cache_key: tuple[object, ...] | None = None
        self._risk_timeline_cache: RiskEvidenceTimeline | None = None

    def _load(self, symbols: Iterable[str]) -> None:
        """Compatibility forwarder; market ownership lives in ``workspace``."""
        self.workspace.load(symbols)

    def _price(self, symbol: str, date: pd.Timestamp, field: str = "close") -> float:
        """Compatibility forwarder; market ownership lives in ``workspace``."""
        return self.workspace.price(symbol, date, field)

    @property
    def _reference_returns(self) -> pd.DataFrame | None:
        return self.workspace._reference_returns

    _causal_risk_timeline = _application.bind_causal_risk_timeline(
        lambda: build_risk_evidence_timeline,
        lambda: _RISK_TIMELINE_BUILDER,
        lambda: code_fingerprint,
        lambda: _SHARED_RISK_TIMELINE_CACHE,
        lambda: _load_risk_timeline_disk_cache,
        lambda: _write_risk_timeline_disk_cache,
    )
    equity = _application.mark_equity
    _mark_account_positions = _application.mark_account_positions
    decide = _application.bind_engine_decision(
        lambda: assess_risk,
        lambda: evaluate_sentinel,
        lambda: reconcile_account_orders,
        lambda: code_fingerprint,
        lambda: _attach_target_attribution,
    )
    deterministic_decision = _application.deterministic_decision
    backtest = _application.bind_engine_backtest(lambda: performance_metrics)
