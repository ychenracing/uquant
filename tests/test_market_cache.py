from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from uquant import engine as engine_module
from uquant.config import DEFAULT_CONFIG
from uquant.contracts.universe import default_ai_universe
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.leader import REFERENCE_UNIVERSE
from uquant.market import ReplayUniverse
from uquant.risk_sentinel.models import RiskEvidenceTimeline, SentinelLevel

ROOT = Path(__file__).parents[1]


def _timeline(as_of: str, marker: str) -> RiskEvidenceTimeline:
    return RiskEvidenceTimeline(
        as_of=as_of,
        sessions=(),
        sentinel_rows=(),
        base_rows=(),
        sentinel_first_family_dates=(),
        base_first_family_dates=(),
        incremental_families=(),
        earlier_families=(),
        confirmation_days=0,
        repair_days=0,
        effective_level=SentinelLevel.NORMAL,
        confirmed_since=None,
        confirmation_history_trusted=False,
        trust_reasons=(marker,),
    )


def test_shared_timeline_key_isolates_data_config_universe_source_and_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_module._SHARED_RISK_TIMELINE_CACHE.clear()
    builds: list[str] = []
    source_identity = ["source-a"]

    def first_builder(**kwargs: object) -> RiskEvidenceTimeline:
        builds.append("first")
        return _timeline(str(kwargs["as_of"]), "first")

    def second_builder(**kwargs: object) -> RiskEvidenceTimeline:
        builds.append("second")
        return _timeline(str(kwargs["as_of"]), "second")

    monkeypatch.setattr(engine_module, "build_risk_evidence_timeline", first_builder)
    monkeypatch.setattr(engine_module, "code_fingerprint", lambda: source_identity[0])

    def replay(
        *,
        data_identity: str = "data-a",
        cfg=DEFAULT_CONFIG,
        tradable: tuple[str, ...] = ("sz300308",),
    ) -> RiskEvidenceTimeline:
        engine = ProductionEngine(ROOT / "data" / "frozen", cfg)
        universe = ReplayUniverse.from_symbols(
            tradable_symbols=tradable,
            reference_symbols=REFERENCE_UNIVERSE,
            index_symbols=INDEX_SYMBOLS,
        )
        engine.workspace.prepare(universe)
        monkeypatch.setattr(
            engine.workspace,
            "manifest",
            lambda *args, **kwargs: SimpleNamespace(digest=data_identity),
        )
        return engine._causal_risk_timeline(
            as_of="2026-07-01",
            cfg=cfg,
            universe=default_ai_universe(),
        )

    replay()
    replay()
    assert builds == ["first"]
    replay(data_identity="data-b")
    replay(cfg=DEFAULT_CONFIG.override(atr_window=15))
    replay(tradable=("sz300502",))
    source_identity[0] = "source-b"
    replay()
    monkeypatch.setattr(engine_module, "build_risk_evidence_timeline", second_builder)
    replay()
    assert builds == ["first", "first", "first", "first", "first", "second"]
