"""Validate and sequentially replay the immutable Phase 2 ablation registry."""

# ruff: noqa: F401 - finite legacy import-mode aliases

from __future__ import annotations

import subprocess  # nosec B404 - compatibility seam; command owner uses fixed argv
import sys
from functools import wraps
from pathlib import Path

# A reviewed checkout must import its own moved runner rather than whichever
# editable worktree happens to own the invoking interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.generalization_ablation_cli import (
    BASELINE_CARRIER_SHA256 as _BASELINE_CARRIER_SHA256,
)
from research.generalization_ablation_cli import CAUSAL_STAGES as _CAUSAL_STAGES
from research.generalization_ablation_cli import (
    EVIDENCE_MANIFEST_CANONICAL_SHA256 as _EVIDENCE_MANIFEST_CANONICAL_SHA256,
)
from research.generalization_ablation_cli import (
    EVIDENCE_MANIFEST_PATH as _EVIDENCE_MANIFEST_PATH,
)
from research.generalization_ablation_cli import (
    MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256 as _MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256,
)
from research.generalization_ablation_cli import (
    MINIMAL_EVIDENCE_MANIFEST_PATH as _MINIMAL_EVIDENCE_MANIFEST_PATH,
)
from research.generalization_ablation_cli import (
    baseline_config_sha256 as _owner_baseline_config_sha256,
)
from research.generalization_ablation_cli import canonical_bytes as _canonical_bytes
from research.generalization_ablation_cli import (
    checkpoint_payload_schema as _checkpoint_payload_schema,
)
from research.generalization_ablation_cli import (
    compare_worker_payloads as _compare_worker_payloads,
)
from research.generalization_ablation_cli import (
    compile_evidence_manifest as _compile_evidence_manifest,
)
from research.generalization_ablation_cli import evidence_coverage as _evidence_coverage
from research.generalization_ablation_cli import (
    evidence_manifest_anchor as _evidence_manifest_anchor,
)
from research.generalization_ablation_cli import (
    first_hashed_divergence as _first_hashed_divergence,
)
from research.generalization_ablation_cli import (
    frozen_replay_error_anchors as _frozen_replay_error_anchors,
)
from research.generalization_ablation_cli import git_output as _git_output
from research.generalization_ablation_cli import (
    isolated_evidence_checkout as _isolated_evidence_checkout,
)
from research.generalization_ablation_cli import load_json_mapping as _load_json_mapping
from research.generalization_ablation_cli import (
    load_trusted_evidence_manifest as _load_trusted_evidence_manifest,
)
from research.generalization_ablation_cli import main, generalization_cli_seams
from research.generalization_ablation_cli import probe_checkout as _probe_checkout
from research.generalization_ablation_cli import read_baseline_result as _read_baseline_result
from research.generalization_ablation_cli import read_checkpoint as _read_checkpoint
from research.generalization_ablation_cli import (
    read_experiment_result as _read_experiment_result,
)
from research.generalization_ablation_cli import read_worker_artifact as _read_worker_artifact
from research.generalization_ablation_cli import replay_cell as _replay_cell
from research.generalization_ablation_cli import replay_command as _replay_command
from research.generalization_ablation_cli import (
    select_experiment_result_path as _select_experiment_result_path,
)
from research.generalization_ablation_cli import sha256_mapping as _sha256_mapping
from research.generalization_ablation_cli import (
    validate_evidence_manifest_entry as _validate_evidence_manifest_entry,
)
from research.generalization_ablation_cli import (
    validate_experiment_checkpoints as _validate_experiment_checkpoints,
)
from research.generalization_ablation_cli import validate_replay_command as _validate_replay_command
from research.generalization_ablation_cli import validate_worker_payload as _validate_worker_payload
from research.generalization_ablation_cli import write_baseline_result as _write_baseline_result
from research.generalization_ablation_cli import write_checkpoint as _write_checkpoint
from research.generalization_ablation_cli import (
    write_experiment_result as _write_experiment_result,
)
from research.generalization_ablation_cli import write_worker_artifact as _write_worker_artifact

__all__ = ("main",)


@wraps(_owner_baseline_config_sha256)
def _baseline_config_sha256(source_root: Path) -> str:
    with generalization_cli_seams(probe_checkout_seam=_probe_checkout):
        return _owner_baseline_config_sha256(source_root)


if __name__ == "__main__":
    _status = main()
    raise SystemExit(_status)
