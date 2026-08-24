"""Deterministic universe-generalization diagnostics and frozen references.

The production engine remains the only strategy implementation.  This module
only constructs causal universe perturbations, replays them through a supplied
runner, and aggregates dependency evidence.  It deliberately has no API that
writes a baseline file: reference updates must remain an explicit, reviewed
repository change.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

import pandas as pd

from ...config import SystemConfig
from ...engine import ProductionEngine
from ..ai_era import require_ai_era_interval
from .baseline import (
    load_generalization_baseline,
)
from .baseline import (
    read_generalization_baseline as _read_generalization_baseline,
)
from .baseline import (
    validate_baseline_envelope as _validate_baseline_envelope,
)
from .gates import evaluate_generalization
from .metrics import symbol_pnl_from_result
from .models import GeneralizationBaseline, GeneralizationScenario
from .provenance import (
    build_generalization_provenance,
    generalization_runtime_capabilities,
)
from .provenance import (
    immutable_validation_inputs as _immutable_validation_inputs,
)
from .provenance import (
    production_commit as _production_commit,
)
from .provenance import (  # noqa: F401 - immutable public-import projection
    production_source_fingerprint as _production_source_fingerprint,
)
from .provenance import (
    validated_provenance as _validated_provenance,
)
from .scenarios import (
    build_generalization_scenarios,
    compute_pre_window_evidence,
)
from .scenarios import (
    canonical_symbols as _canonical_symbols,
)
from .scenarios import (
    validate_industry_coverage as _validate_industry_coverage,
)


def _run_production_generalization_case(
    *,
    engine: ProductionEngine,
    case: GeneralizationScenario,
    start: str,
    end: str,
) -> Mapping[str, Any]:
    """Enrich one production replay with exact symbol-level PnL."""

    result = engine.backtest(symbols=case.symbols, start=start, end=end)
    final_date = pd.Timestamp(str(result["end"]))
    final_account = result.get("final_account", {})
    raw_positions = final_account.get("positions", {}) if isinstance(final_account, Mapping) else {}
    if not isinstance(raw_positions, Mapping):
        raise ValueError("backtest final positions are invalid")
    price = engine.workspace.price
    final_prices = {str(symbol): price(str(symbol), final_date) for symbol in raw_positions}
    enriched = dict(result)
    enriched["symbol_pnl"] = symbol_pnl_from_result(result, final_prices)
    return enriched


def _prepare_generalization_cases(
    *,
    data_dir: str | Path,
    symbols: tuple[str, ...],
    industries: Mapping[str, str],
    priors: tuple[str, ...],
    start: str,
    baseline_source: Path,
    expected_provenance: Mapping[str, Any],
    pre_window_prices: Mapping[str, pd.Series | pd.DataFrame] | None,
    runner: Callable[[GeneralizationScenario], Mapping[str, Any]] | None,
    lookback_sessions: int,
    random_sizes: Iterable[int],
    random_seeds: Iterable[int],
    base_seed: int,
    leave_top_k: Iterable[int],
    balanced_per_industry: int,
    industry_min_members: int,
) -> tuple[ProductionEngine | None, tuple[GeneralizationScenario, ...], GeneralizationBaseline]:
    engine: ProductionEngine | None = None
    histories = pre_window_prices
    if histories is None:
        if runner is not None:
            raise ValueError("a custom runner must provide pre_window_prices")
        engine = ProductionEngine(data_dir)
        engine.workspace.load(symbols)  # Same causal source used by production replay.
        histories = {symbol: engine.workspace.raw_frame(symbol)["close"] for symbol in symbols}
    evidence = compute_pre_window_evidence(
        histories,
        symbols,
        window_start=start,
        lookback_sessions=lookback_sessions,
    )
    cases = build_generalization_scenarios(
        symbols,
        industries,
        priors,
        window_start=start,
        pre_window_evidence=evidence,
        random_sizes=random_sizes,
        random_seeds=random_seeds,
        base_seed=base_seed,
        leave_top_k=leave_top_k,
        balanced_per_industry=balanced_per_industry,
        industry_min_members=industry_min_members,
    )
    baseline = load_generalization_baseline(
        baseline_source,
        cases,
        expected_provenance=expected_provenance,
    )
    return engine, cases, baseline


def run_generalization(
    *,
    data_dir: str | Path,
    universe: Iterable[str],
    industries: Mapping[str, str],
    prior_symbols: Iterable[str],
    start: str,
    end: str,
    baseline_path: str | Path,
    pre_window_prices: Mapping[str, pd.Series | pd.DataFrame] | None = None,
    runner: Callable[[GeneralizationScenario], Mapping[str, Any]] | None = None,
    lookback_sessions: int = 120,
    random_sizes: Iterable[int] = (6, 12, 24),
    random_seeds: Iterable[int] = range(100),
    leave_top_k: Iterable[int] = (1, 2, 3, 5),
    balanced_per_industry: int = 2,
    industry_min_members: int = 2,
    base_seed: int = 20260810,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full read-only generalization gate through ProductionEngine."""
    start, end = require_ai_era_interval(start, end)
    symbols = _canonical_symbols(universe, label="generalization universe")
    priors = _canonical_symbols(prior_symbols, label="prior symbols")
    _validate_industry_coverage(symbols, industries)
    if provenance is not None and runner is None:
        raise ValueError("explicit generalization provenance is only valid with a custom runner")
    if provenance is None:
        repository_root = Path(__file__).resolve().parents[3]
        capabilities = generalization_runtime_capabilities()
        data_before = capabilities.verify_data_manifest(data_dir)
        source_before = capabilities.production_source_fingerprint(repository_root)
        expected_provenance = build_generalization_provenance(
            data=data_before,
            universe=symbols,
            industries=industries,
            prior_symbols=priors,
            start=start,
            end=end,
            production_commit=_production_commit(repository_root),
            production_source_sha256=source_before,
            initial_cash=SystemConfig().initial_cash,
        )
    else:
        expected_provenance = _validated_provenance(provenance)
        expected_dataset = {
            "universe": list(symbols),
            "industries": dict(sorted(industries.items())),
            "prior_symbols": list(priors),
            "start": start,
            "end": end,
        }
        if expected_provenance["dataset"] != expected_dataset:
            raise RuntimeError("generalization supplied provenance does not match run inputs")

    baseline_source = Path(baseline_path)
    baseline_bytes, envelope = _read_generalization_baseline(baseline_source)
    _validate_baseline_envelope(envelope, expected_provenance=expected_provenance)
    guard: AbstractContextManager[None] = nullcontext()
    if provenance is None:
        guard = _immutable_validation_inputs(
            baseline_path=baseline_source,
            baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest(),
            data_dir=data_dir,
            repository_root=repository_root,
            data_before=data_before,
            source_before=source_before,
        )

    with guard:
        engine, cases, baseline = _prepare_generalization_cases(
            data_dir=data_dir,
            symbols=symbols,
            industries=industries,
            priors=priors,
            start=start,
            baseline_source=baseline_source,
            expected_provenance=expected_provenance,
            pre_window_prices=pre_window_prices,
            runner=runner,
            lookback_sessions=lookback_sessions,
            random_sizes=random_sizes,
            random_seeds=random_seeds,
            base_seed=base_seed,
            leave_top_k=leave_top_k,
            balanced_per_industry=balanced_per_industry,
            industry_min_members=industry_min_members,
        )

        selected_runner = runner
        if selected_runner is None:
            if engine is None:  # pragma: no cover - guarded above
                raise RuntimeError("production generalization runner was not initialized")

            def production_runner(case: GeneralizationScenario) -> Mapping[str, Any]:
                return _run_production_generalization_case(
                    engine=engine,
                    case=case,
                    start=start,
                    end=end,
                )

            selected_runner = production_runner

        report = evaluate_generalization(
            cases,
            selected_runner,
            industries=industries,
            baseline=baseline,
        )
        current_hash = hashlib.sha256(baseline_source.read_bytes()).hexdigest()
        if current_hash != baseline.sha256:
            raise RuntimeError("generalization baseline changed during validation")
        return report
