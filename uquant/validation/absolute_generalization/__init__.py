"""Public immutable Absolute Generalization Acceptance contract surface."""

from .contract import (
    ABSOLUTE_GENERALIZATION_CONTRACT_SHA256,
    AbsoluteGeneralizationContract,
    load_absolute_generalization_contract,
)
from .replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayObservation,
    run_absolute_generalization_replay,
)
from .scenarios import AbsoluteGeneralizationScenario, build_leave_one_out_scenarios

__all__ = (
    "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256",
    "AbsoluteGeneralizationContract",
    "AbsoluteGeneralizationReplay",
    "AbsoluteGeneralizationReplayObservation",
    "AbsoluteGeneralizationScenario",
    "build_leave_one_out_scenarios",
    "load_absolute_generalization_contract",
    "run_absolute_generalization_replay",
)
