"""Offline strategy research utilities isolated from the production engine.

The package intentionally depends on plain mappings and replay observations.
Nothing under :mod:`uquant` imports it, so research candidates cannot become a
second production route accidentally.
"""

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

__all__ = [
    "CandidateEvaluation",
    "GateMateriality",
    "ObjectiveWeights",
    "ReplayObservation",
    "SearchResult",
    "dominance_gate",
    "enumerate_candidates",
    "evaluate_candidate",
    "pareto_gate",
    "search_candidates",
    "validate_shared_config",
]
