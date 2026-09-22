"""Behavioral coverage for explicit owners and typed opportunity boundaries."""
from __future__ import annotations

import ast
import copy
import inspect
import pickle
import subprocess
from pathlib import Path

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.data import DataStore
from uquant.engine import ProductionEngine
from uquant.features import compute_features
from uquant.opportunity import classify_opportunity
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, LeaderScore, Opportunity, Risk

BASE = "7bc5cd5e20038c94ab5cf556ce107104692236ac"
ROOT = Path(__file__).resolve().parents[1]


def _baseline(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT, text=True)


def test_portfolio_methods_are_declared_and_roundtrip_as_bound_methods():
    import uquant.portfolio_leaders as old_leaders
    import uquant.portfolio_strategic as old_strategic
    from uquant.portfolio.leaders import LeaderPortfolioPolicy
    from uquant.portfolio.strategic import StrategicPortfolioPolicy

    assert old_leaders.LeaderPortfolioPolicy is LeaderPortfolioPolicy
    assert old_strategic.StrategicPortfolioPolicy is StrategicPortfolioPolicy
    for path, cls in (
        ("uquant/portfolio/allocator.py", PortfolioAllocator),
        ("uquant/portfolio/leaders/admission.py", LeaderPortfolioPolicy),
        ("uquant/portfolio/strategic/discovery.py", StrategicPortfolioPolicy),
    ):
        node = next(n for n in ast.parse((ROOT / path).read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == cls.__name__)
        declared = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
        assert declared
        instance = cls(DEFAULT_CONFIG)
        for name in declared:
            bound = getattr(instance, name)
            restored = pickle.loads(pickle.dumps(bound))
            assert inspect.signature(restored) == inspect.signature(bound)
            assert getattr(restored, "__func__", restored) is getattr(bound, "__func__", bound)
    for path in ("uquant/portfolio/__init__.py", "uquant/portfolio/leaders/__init__.py",
                 "uquant/portfolio/strategic/__init__.py", "uquant/application/__init__.py"):
        source = (ROOT / path).read_text()
        assert "setattr(" not in source
        assert "__doc__ =" not in source
        assert "__annotations__" not in source
        assert "_assembly_method" not in source


def test_engine_decision_uses_current_dependency_seams(tmp_path, monkeypatch):
    import uquant.engine as engine
    import uquant.application as application

    seen = {}
    sentinel = object()
    def run(self, *deps, **kwargs):
        seen.update(deps=deps, kwargs=kwargs)
        return sentinel
    risk = object()
    monkeypatch.setattr(application, "run_decision", run)
    monkeypatch.setattr(engine, "assess_risk", risk)
    instance = ProductionEngine(tmp_path)
    assert instance.decide(symbols=("sz300308",), as_of="2026-01-06",
                           account=AccountState.empty(1000.0)) is sentinel
    assert seen["deps"][0] is risk


def test_typed_opportunity_flow_matches_base_outputs_and_mutations(data_dir):
    namespace = {"__name__": "uquant._audit_baseline_opportunity", "__package__": "uquant"}
    exec(compile(_baseline("uquant/opportunity.py"), "baseline-opportunity", "exec"), namespace)
    baseline = namespace["classify_opportunity"]
    store = DataStore(data_dir)
    cfg = DEFAULT_CONFIG
    broad = compute_features(store.load("sh000300"), cfg)
    tech = compute_features(store.load("sh000682"), cfg)
    panel = {symbol: compute_features(store.load(symbol), cfg)
             for symbol in ("sz300308", "sz300502", "sz300394")}
    leaders = {symbol: LeaderScore(symbol, 0.95 - i * 0.08, 1.0, True, False, "optical", {})
               for i, symbol in enumerate(panel)}
    dates = broad.index.intersection(tech.index)
    dates = dates[(dates >= pd.Timestamp("2026-07-01")) & (dates <= pd.Timestamp("2026-08-05"))]
    for opportunity in Opportunity:
        for risk in Risk:
            before = AccountState.empty(2e6)
            before.opportunity = opportunity.value
            after = copy.deepcopy(before)
            for date in dates:
                inputs = dict(date=date, broad=broad, tech=tech, reference_panel=panel,
                              leaders=leaders, risk=risk, cfg=cfg)
                assert baseline(account=before, **inputs) is classify_opportunity(account=after, **inputs)
                assert before.to_dict() == after.to_dict()
