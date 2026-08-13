"""Deterministic, diagnostic-only generalization smoke evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from uquant.config import SystemConfig
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.reference_registry import DEFAULT_REGISTRY_PATH
from uquant.validation import generalization as generalization_module
from uquant.validation.ai_era import require_ai_era_interval
from uquant.validation.generalization import (
    GeneralizationObservation,
    GeneralizationScenario,
    aggregate_metrics,
    build_generalization_provenance,
    build_generalization_scenarios,
    compute_pre_window_evidence,
    observation_from_result,
    prior_dependence,
    scenario_fingerprint,
)
from uquant.validation.manifest import verify_data_manifest

_SCHEMA_VERSION = 2
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_REFERENCE_REGISTRY_PATH = Path("benchmarks/reference_registry.json")


def build_smoke_scenarios(
    prices: Mapping[str, pd.Series | pd.DataFrame],
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    *,
    window_start: str,
    lookback_sessions: int = 120,
) -> tuple[GeneralizationScenario, ...]:
    """Select the reviewed 24-case smoke from the full scenario builder."""
    symbols = tuple(sorted(universe))
    priors = tuple(sorted(prior_symbols))
    if len(priors) != 3:
        raise ValueError("generalization smoke requires exactly three prior symbols")
    evidence = compute_pre_window_evidence(
        prices,
        symbols,
        window_start=window_start,
        lookback_sessions=lookback_sessions,
    )
    complete = build_generalization_scenarios(
        symbols,
        industries,
        priors,
        window_start=window_start,
        pre_window_evidence=evidence,
        random_sizes=(6, 12, 24),
        random_seeds=(0, 1),
        leave_top_k=(1,),
    )
    selected = tuple(
        case
        for case in complete
        if case.family in {"baseline", "remove_one", "remove_all", "no_optical", "balanced", "random"}
        or (case.family == "industry_only" and case.name.startswith("industry_only__"))
    )
    family_counts = {
        family: sum(case.family == family for case in selected)
        for family in {case.family for case in selected}
    }
    expected_counts = {
        "baseline": 1,
        "remove_one": 3,
        "remove_all": 1,
        "no_optical": 1,
        "industry_only": 11,
        "balanced": 1,
        "random": 6,
    }
    if len(selected) != 24 or family_counts != expected_counts:
        raise ValueError(
            "generalization smoke requires exactly 24 cases with eleven real industry groups: "
            f"observed={family_counts}"
        )
    return selected


def _observation_payload(observation: GeneralizationObservation) -> dict[str, Any]:
    return {
        "name": observation.name,
        "family": observation.family,
        "final_wealth": observation.final_wealth,
        "max_drawdown": observation.max_drawdown,
        "account_orders": observation.account_orders,
        "deployed_exposure": [
            {"symbol": symbol, "lifecycle": lifecycle} for symbol, lifecycle in observation.deployed_exposure
        ],
    }


def _reference_registry_identity(repository_root: Path) -> dict[str, str]:
    relative = _REFERENCE_REGISTRY_PATH.as_posix()
    status = generalization_module._git_stdout(
        repository_root,
        ["status", "--porcelain", "--untracked-files=all", "--", relative],
        label="cannot inspect smoke reference registry",
    )
    if status.strip():
        raise RuntimeError("generalization smoke requires committed reference registry")
    commit = generalization_module._git_stdout(
        repository_root,
        ["log", "-1", "--format=%H", "--", relative],
        label="cannot resolve immutable smoke reference registry commit",
    ).strip()
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("cannot resolve immutable smoke reference registry commit")
    path = repository_root / _REFERENCE_REGISTRY_PATH
    if path.is_symlink() or not path.is_file() or path.resolve() != DEFAULT_REGISTRY_PATH.resolve():
        raise RuntimeError("smoke reference registry path is not canonical")
    return {
        "path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "commit": commit,
        "status": "committed",
    }


def _assert_immutable_inputs(
    *,
    data_dir: str | Path,
    repository_root: Path,
    data_before: Mapping[str, Any],
    commit_before: str,
    source_before: str,
    engine_code_before: str,
    registry_before: Mapping[str, str],
) -> None:
    data_after = verify_data_manifest(data_dir)
    commit_after = generalization_module._production_commit(repository_root)
    source_after = generalization_module._production_source_fingerprint(repository_root)
    engine_code_after = code_fingerprint()
    registry_after = _reference_registry_identity(repository_root)
    if data_after != data_before or commit_after != commit_before or source_after != source_before:
        raise RuntimeError("source or data changed during smoke replay")
    if engine_code_after != engine_code_before or registry_after != registry_before:
        raise RuntimeError("decision inputs changed during smoke replay")


def run_generalization_smoke(
    *,
    data_dir: str | Path,
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    start: str,
    end: str,
    lookback_sessions: int = 120,
) -> dict[str, Any]:
    """Replay the fixed smoke through one production engine without a gate."""
    start, end = require_ai_era_interval(start, end)
    symbols = tuple(sorted(universe))
    priors = tuple(sorted(prior_symbols))
    repository_root = Path(__file__).resolve().parents[1]
    data_before = verify_data_manifest(data_dir)
    commit_before = generalization_module._production_commit(repository_root)
    source_before = generalization_module._production_source_fingerprint(repository_root)
    engine_code_before = code_fingerprint()
    registry_before = _reference_registry_identity(repository_root)
    provenance = build_generalization_provenance(
        data=data_before,
        universe=symbols,
        industries=industries,
        prior_symbols=priors,
        start=start,
        end=end,
        production_commit=commit_before,
        production_source_sha256=source_before,
        initial_cash=SystemConfig().initial_cash,
    )
    provenance["decision_inputs"] = {
        "engine_code_sha256": engine_code_before,
        "reference_registry": registry_before,
    }
    engine = ProductionEngine(data_dir)
    histories = {symbol: engine.data.load(symbol)["close"] for symbol in symbols}
    cases = build_smoke_scenarios(
        histories,
        symbols,
        industries,
        priors,
        window_start=start,
        lookback_sessions=lookback_sessions,
    )
    observations: list[GeneralizationObservation] = []
    try:
        for case in cases:
            result = engine.backtest(symbols=case.symbols, start=start, end=end)
            observations.append(observation_from_result(case, result))
    finally:
        _assert_immutable_inputs(
            data_dir=data_dir,
            repository_root=repository_root,
            data_before=data_before,
            commit_before=commit_before,
            source_before=source_before,
            engine_code_before=engine_code_before,
            registry_before=registry_before,
        )
    evidence_case = cases[0]
    stress = tuple(item for item in observations if item.family != "baseline")
    return {
        "schema_version": _SCHEMA_VERSION,
        "diagnostic_only": True,
        "scenario_fingerprint": scenario_fingerprint(cases),
        "provenance": provenance,
        "pre_window_evidence": {
            "as_of": evidence_case.evidence_as_of,
            "eligible_symbols": list(evidence_case.evidence_eligible_symbols),
            "ineligible_symbols": list(evidence_case.evidence_ineligible_symbols),
        },
        "aggregate": aggregate_metrics(stress),
        "prior_dependence": prior_dependence(observations),
        "observations": [_observation_payload(item) for item in observations],
    }
