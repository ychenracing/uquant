"""Public immutable Absolute Generalization Acceptance contract surface."""

from .contract import (
    ABSOLUTE_GENERALIZATION_CONTRACT_SHA256,
    AbsoluteGeneralizationContract,
    load_absolute_generalization_contract,
)
from .scenarios import AbsoluteGeneralizationScenario, build_leave_one_out_scenarios

__all__ = (
    "ABSOLUTE_GENERALIZATION_CONTRACT_SHA256",
    "AbsoluteGeneralizationContract",
    "AbsoluteGeneralizationScenario",
    "build_leave_one_out_scenarios",
    "load_absolute_generalization_contract",
)
