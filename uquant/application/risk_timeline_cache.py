"""Deterministic cache for causal risk-timeline evaluations."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

import pandas as pd

from ..atomic_io import atomic_write_text
from ..config import SystemConfig, config_fingerprint
from ..contracts.universe import AIUniverse
from ..market import MarketWorkspace
from ..risk_sentinel.history import (
    risk_evidence_timeline_from_dict,
    risk_evidence_timeline_prefix,
    risk_evidence_timeline_to_dict,
)
from ..risk_sentinel.models import RiskEvidenceTimeline


class CacheEngineRuntime(Protocol):
    @property
    def workspace(self) -> MarketWorkspace: ...

    _features: dict[str, pd.DataFrame]
    _code_hash: str | None
    _risk_timeline_cache_key: tuple[object, ...] | None
    _risk_timeline_cache: RiskEvidenceTimeline | None

    @property
    def _reference_returns(self) -> pd.DataFrame | None: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _risk_timeline_disk_path(key: tuple[str, str, str, str]) -> Path:
    identity = hashlib.sha256(_canonical_json(list(key))).hexdigest()
    return Path(tempfile.gettempdir()) / "uquant-risk-evidence-v1" / f"{identity}.json"


def _load_risk_timeline_disk_cache(
    path: Path,
    cache_schema: str,
    *,
    key: tuple[str, str, str, str],
) -> RiskEvidenceTimeline | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
            return None
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            return None
        if hashlib.sha256(_canonical_json(payload)).hexdigest() != envelope["sha256"]:
            return None
        if payload.get("schema") != cache_schema:
            return None
        if payload.get("key") != list(key):
            return None
        timeline = payload.get("timeline")
        if not isinstance(timeline, dict):
            return None
        return risk_evidence_timeline_from_dict(timeline)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _write_risk_timeline_disk_cache(
    path: Path,
    cache_schema: str,
    *,
    key: tuple[str, str, str, str],
    timeline: RiskEvidenceTimeline,
) -> None:
    payload = {
        "schema": cache_schema,
        "key": list(key),
        "timeline": risk_evidence_timeline_to_dict(timeline),
    }
    envelope = {
        "payload": payload,
        "sha256": hashlib.sha256(_canonical_json(payload)).hexdigest(),
    }
    atomic_write_text(
        path,
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def _causal_risk_timeline(
    self: CacheEngineRuntime,
    timeline_builder: Callable[..., RiskEvidenceTimeline],
    native_timeline_builder: Callable[..., RiskEvidenceTimeline],
    code_fingerprint_fn: Callable[[], str],
    shared_timeline_cache: Any,
    load_disk_cache_fn: Callable[..., RiskEvidenceTimeline | None],
    write_disk_cache_fn: Callable[..., None],
    *,
    as_of: str,
    cfg: SystemConfig,
    universe: AIUniverse,
) -> RiskEvidenceTimeline:
    """Return one immutable data/config cache prefix without account inputs."""
    broad = self._features["sh000300"]
    tech = self._features["sh000682"]
    common = broad.index.intersection(tech.index)
    if common.empty:
        raise RuntimeError("Sentinel timeline has no common index session")
    full_as_of = str(pd.Timestamp(common[-1]).date())
    timeline_symbols = tuple(sorted({*universe.symbols, *self.workspace.universe.index_symbols}))
    full_data_digest = self.workspace.manifest(timeline_symbols, as_of=pd.Timestamp(full_as_of)).digest
    config_identity = config_fingerprint(cfg)
    source_identity = self._code_hash or code_fingerprint_fn()
    key, disk_key = self.workspace.universe.cache_keys(
        data_identity=full_data_digest,
        config_identity=config_identity,
        semantic_universe_identity=str(universe.sha256),
        source_identity=source_identity,
        builder_identity=timeline_builder,
    )
    if self._risk_timeline_cache_key != key:
        disk_path = _risk_timeline_disk_path(disk_key)

        def build_timeline() -> RiskEvidenceTimeline:
            cached = None
            if timeline_builder is native_timeline_builder:
                cached = load_disk_cache_fn(disk_path, key=disk_key)
            if cached is not None:
                return cached
            built = timeline_builder(
                as_of=full_as_of,
                broad_frame=broad,
                tech_frame=tech,
                reference_panel={
                    symbol: self._features[symbol]
                    for symbol in sorted(universe.symbols)
                    if symbol in self._features
                },
                reference_returns=self._reference_returns,
                universe=universe,
                cfg=cfg,
            )
            if timeline_builder is native_timeline_builder:
                write_disk_cache_fn(disk_path, key=disk_key, timeline=built)
            return built

        timeline = cast(RiskEvidenceTimeline, shared_timeline_cache.get_or_build(key, build_timeline))
        self._risk_timeline_cache_key = key
        self._risk_timeline_cache = timeline
    if self._risk_timeline_cache is None:
        raise RuntimeError("Sentinel timeline cache was not initialized")
    return risk_evidence_timeline_prefix(self._risk_timeline_cache, as_of=as_of, cfg=cfg)


canonical_risk_timeline_json = _canonical_json
causal_risk_timeline = _causal_risk_timeline
load_risk_timeline_disk_cache = _load_risk_timeline_disk_cache
risk_timeline_disk_path = _risk_timeline_disk_path
write_risk_timeline_disk_cache = _write_risk_timeline_disk_cache
