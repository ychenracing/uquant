"""Offline strategy research utilities isolated from the production engine.

The package intentionally depends on plain mappings and replay observations.
Nothing under :mod:`uquant` imports it, so research candidates cannot become a
second production route accidentally.
"""

from .candidate_runner import CandidateRunner, CellTrace, DecisionTrace, TraceDivergence, first_divergence
from .candidate_search import (
    CandidateEvaluation,
    GateMateriality,
    ObjectiveWeights,
    ReplayObservation,
    SearchResult,
    dominance_gate,
    enumerate_candidates,
    evaluate_candidate,
    pareto_gate,
    search_candidates,
    validate_shared_config,
)
from .statistics import (
    DeflatedSharpeResult,
    PBOResult,
    WalkForwardFold,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    walk_forward_folds,
)

__all__ = [
    "CandidateEvaluation",
    "CandidateRunner",
    "CellTrace",
    "DecisionTrace",
    "DeflatedSharpeResult",
    "GateMateriality",
    "ObjectiveWeights",
    "PBOResult",
    "ReplayObservation",
    "SearchResult",
    "TraceDivergence",
    "WalkForwardFold",
    "deflated_sharpe_ratio",
    "dominance_gate",
    "enumerate_candidates",
    "evaluate_candidate",
    "first_divergence",
    "pareto_gate",
    "probability_of_backtest_overfitting",
    "search_candidates",
    "validate_shared_config",
    "walk_forward_folds",
]
