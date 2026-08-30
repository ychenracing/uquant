"""Public immutable Absolute Generalization Acceptance contract surface."""

from .aggregation import (
    AcceptanceReport,
    ShardManifest,
    aggregate_acceptance,
    build_error_shard_manifest,
    seal_shard_manifest,
    validate_shard_manifest,
)
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
from .policy import ComponentResult
from .reachability import (
    FailedGrantRecoveryAnalysis,
    HealthProjection,
    TerminalSccAnalysis,
    analyze_failed_grant_recovery,
    analyze_terminal_scc,
    is_positive_strategic_outlet,
    project_flat_book_repair_health,
    project_qualification_opportunity_health,
)
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
    "AcceptanceReport",
    "CellArtifact",
    "CellMetrics",
    "ComponentResult",
    "EpochFact",
    "EventFact",
    "FailedGrantRecoveryAnalysis",
    "HealthProjection",
    "IdentityEnvelope",
    "RepairEpisodeFact",
    "ShardManifest",
    "TerminalSccAnalysis",
    "aggregate_acceptance",
    "analyze_failed_grant_recovery",
    "analyze_terminal_scc",
    "build_error_shard_manifest",
    "build_leave_one_out_scenarios",
    "derive_cell_metrics",
    "is_positive_strategic_outlet",
    "load_absolute_generalization_contract",
    "project_flat_book_repair_health",
    "project_qualification_opportunity_health",
    "run_absolute_generalization_replay",
    "seal_shard_manifest",
    "validate_cell_artifact",
    "validate_shard_manifest",
)
