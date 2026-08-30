"""Public immutable Absolute Generalization Acceptance contract surface."""

from .artifacts import (
    ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256,
    CellArtifact,
    EventFact,
    IdentityEnvelope,
    derive_cell_metrics,
    validate_cell_artifact,
)
from .contract import (
    ABSOLUTE_GENERALIZATION_CONTRACT_SHA256,
    AbsoluteGeneralizationContract,
    load_absolute_generalization_contract,
)
from .metrics import CellMetrics, EpochFact, RepairEpisodeFact
from .replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayObservation,
    run_absolute_generalization_replay,
)
from .scenarios import AbsoluteGeneralizationScenario, build_leave_one_out_scenarios

__all__ = (
    "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256",
    "ABSOLUTE_GENERALIZATION_EXECUTION_CONTRACT_SHA256",
    "AbsoluteGeneralizationContract",
    "AbsoluteGeneralizationReplay",
    "AbsoluteGeneralizationReplayObservation",
    "AbsoluteGeneralizationScenario",
    "CellArtifact",
    "CellMetrics",
    "EpochFact",
    "EventFact",
    "IdentityEnvelope",
    "RepairEpisodeFact",
    "build_leave_one_out_scenarios",
    "derive_cell_metrics",
    "load_absolute_generalization_contract",
    "run_absolute_generalization_replay",
    "validate_cell_artifact",
)
