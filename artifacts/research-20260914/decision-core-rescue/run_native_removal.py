"""Run one frozen Absolute LOO scenario as patch-declared diagnostic evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import subprocess  # nosec B404 - fixed read-only git command
from dataclasses import asdict
from pathlib import Path

from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.engine import code_fingerprint
from uquant.infrastructure.atomic_files import atomic_write_bytes
from uquant.provenance.fingerprints import source_surface_fingerprint
from uquant.validation.absolute_generalization import (
    build_leave_one_out_scenarios,
    derive_runtime_cell_artifact,
    load_absolute_generalization_contract,
)
from uquant.validation.absolute_generalization.replay import (
    AbsoluteGeneralizationReplayPayload,
    run_absolute_generalization_replay,
)

SOURCE_PATHS = (
    "uquant",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "benchmarks/reference_registry.json",
    "benchmarks/config_parameter_governance.json",
)


def _patch_sha256(root: Path) -> str:
    result = subprocess.run(  # nosec B603 - fixed git diff projection
        ["git", "-C", str(root), "diff", "HEAD", "--binary", "--", *SOURCE_PATHS],
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(result.stdout).hexdigest()


def _decode(payload: AbsoluteGeneralizationReplayPayload) -> object:
    if hashlib.sha256(payload.canonical_json).hexdigest() != payload.sha256:
        raise RuntimeError("native diagnostic payload digest differs")
    return strict_json_loads(payload.canonical_json)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--expected-patch-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()

    root = Path.cwd().resolve()
    observed_patch = _patch_sha256(root)
    if observed_patch != args.expected_patch_sha256:
        raise RuntimeError("native diagnostic source patch differs")
    if args.output.exists():
        raise RuntimeError("native diagnostic output already exists")

    contract = load_absolute_generalization_contract(
        root / "benchmarks/absolute_generalization_acceptance_contract.json"
    )
    scenario = next(
        item
        for item in build_leave_one_out_scenarios(contract)
        if item.removed_symbol == args.symbol
    )
    replay = run_absolute_generalization_replay(
        scenario,
        root=root,
        data_dir=root / "data/frozen",
        cache_dir=args.cache_dir,
    )
    artifact = derive_runtime_cell_artifact(replay, contract, root=root)

    order_changes = []
    fills = []
    for observation in replay.observations:
        for phase, account in (
            ("post_open", observation.post_open_account),
            ("post_decision", observation.post_decision_account),
        ):
            order_changes.extend(
                {
                    "session": observation.session,
                    "phase": phase,
                    "payload": _decode(payload),
                }
                for payload in account.changed_order_payloads
            )
        fills.extend(
            {"session": observation.session, "payload": _decode(payload)}
            for payload in observation.new_fills
        )

    evidence = {
        "schema_version": 1,
        "diagnostic_only": True,
        "canonical_acceptance": False,
        "reason": "working-tree patch and local uv version are not the canonical CI identity",
        "source": {
            "head": subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "patch_sha256": observed_patch,
            "production_source_sha256": code_fingerprint(),
            "economic_decision_sha256": source_surface_fingerprint(root, "economic_decision_v1"),
            "full_package_sha256": source_surface_fingerprint(root, "full_package_v1"),
        },
        "runtime": runtime_environment_provenance(root),
        "scenario": {
            "cell_id": scenario.cell_id,
            "removed_symbol": scenario.removed_symbol,
            "window_start": scenario.window_start.isoformat(),
            "window_end": scenario.window_end.isoformat(),
            "shard": scenario.shard,
            "is_critical": scenario.is_critical,
            "is_witness": scenario.is_witness,
            "contract_sha256": scenario.contract_sha256,
        },
        "status": artifact.status,
        "replay_error": artifact.replay_error,
        "artifact_sha256": artifact.canonical_sha256,
        "identities": asdict(artifact.identities),
        "metrics": None if artifact.metrics is None else artifact.metrics.to_dict(),
        "event_facts": {name: asdict(fact) for name, fact in artifact.event_facts},
        "order_changes": order_changes,
        "fills": fills,
    }
    encoded = gzip.compress(canonical_json_bytes(evidence) + b"\n", mtime=0)
    atomic_write_bytes(args.output, encoded)
    readback = gzip.decompress(args.output.read_bytes())
    if readback != canonical_json_bytes(evidence) + b"\n":
        raise RuntimeError("native diagnostic readback differs")
    print(canonical_json_bytes({
        "artifact_sha256": artifact.canonical_sha256,
        "metrics": evidence["metrics"],
        "output": str(args.output),
        "status": artifact.status,
    }).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
