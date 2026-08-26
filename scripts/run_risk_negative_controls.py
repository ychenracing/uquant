#!/usr/bin/env python3
"""Re-run rejected risk-policy candidates in detached historical worktrees."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess  # nosec B404 - fixed local git/python commands only
import sys
import tempfile
from pathlib import Path
from typing import Any

from research.risk_differential_models import canonical_sha256
from uquant.atomic_io import atomic_write_text

GROSS_CAP_REJECTION_COMMIT = "9a82143a3079bdd846c995962a246a66c834c1d5"
EXCLUSIVE_FREEZE_REPORT_COMMIT = "c559c009db309b3815aa8a3df8b59638504acc1a"
EXCLUSIVE_FREEZE_REVIEWED_COMMIT = "1441b8f4aa3131bb7c7c0b0e3f0c7fa222a17668"
EXCLUSIVE_FREEZE_ARCHIVE_COMMIT = "239d7957ee2e42c510cdb51802bd99574af8b0b1"
GROSS_CAP_ARCHIVED_EVIDENCE_SHA256 = "c9f1030f0871663ff1583950b69bcd637fb4196cc20b315f32c98bb7a49b3b59"


def _git_command(*arguments: str) -> list[str]:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git executable is unavailable")
    return [str(Path(executable).resolve()), *arguments]


def _run(command: list[str], *, cwd: Path, capture: bool = False) -> str:
    completed = subprocess.run(  # nosec B603
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else None,
    )
    return completed.stdout if capture else ""


def _object_exists(root: Path, revision: str) -> bool:
    return (
        subprocess.run(  # nosec B603
            _git_command("cat-file", "-e", f"{revision}^{{commit}}"),
            cwd=root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _gross_cap_rejection_control(worktree: Path) -> dict[str, Any]:
    output = worktree / "gross-cap-rerun.json"
    _run(
        [
            sys.executable,
            "-m",
            "research.sentinel_cap_ablation",
            "--data-dir",
            "data/frozen",
            "--pool",
            "a",
            "--window",
            "h1_2024",
            "--output",
            str(output),
        ],
        cwd=worktree,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    rerun_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "status": "REJECTED",
        "source_commit": GROSS_CAP_REJECTION_COMMIT,
        "detached_rerun": True,
        "rerun_sha256": rerun_sha256,
        "archived_evidence_sha256": GROSS_CAP_ARCHIVED_EVIDENCE_SHA256,
        "matches_archived_evidence": rerun_sha256 == GROSS_CAP_ARCHIVED_EVIDENCE_SHA256,
        "metrics": payload["metrics"],
        "gate_diagnostics": payload["gate_diagnostics"],
    }


_SOURCE_AVAILABILITY_HARNESS = r"""
import json
from pathlib import Path
from research.sentinel_exclusive_freeze import run_exclusive_freeze_comparison
from uquant.config import DEFAULT_CONFIG

contract = json.loads(Path('benchmarks/promotion_baseline.json').read_text())
cases = [('a', 'h1_2024'), ('d', 'h2_2024'), ('e', 'h2_2024')]
rows = []
for pool, window_name in cases:
    window = contract['contract']['windows'][window_name]
    row = run_exclusive_freeze_comparison(
        data_dir='data/frozen',
        symbols=contract['pools'][pool],
        start=window['start'],
        end=window['end'],
        scenario=f'{pool}/{window_name}',
        baseline_cfg=DEFAULT_CONFIG.override(
            risk_sentinel_causal_confirmation_enabled=False,
        ),
        candidate_cfg=DEFAULT_CONFIG.override(
            risk_sentinel_causal_confirmation_enabled=True,
        ),
    )
    rows.append({
        'scenario': row['scenario'],
        'baseline_config_sha256': row['baseline_config_sha256'],
        'candidate_config_sha256': row['candidate_config_sha256'],
        'hard_gate': row['hard_gate'],
        'value_gate': row['value_gate'],
        'exclusive_freeze_events': len(row['exclusive_freeze_events']),
        'blocked_new_risk_count': sum(
            int(event['blocked_new_risk_count'])
            for event in row['exclusive_freeze_events']
        ),
        'metrics': row['metrics'],
    })
print(json.dumps(rows, sort_keys=True, allow_nan=False))
"""


def _source_availability_control(worktree: Path, *, requested_commit_available: bool) -> dict[str, Any]:
    rows = json.loads(_run([sys.executable, "-c", _SOURCE_AVAILABILITY_HARNESS], cwd=worktree, capture=True))
    archived = json.loads(
        (worktree / "artifacts/sentinel/exclusive_freeze/small_gate.json").read_text()
    )
    archived_cells = archived["cells"]
    matches_archive = True
    for row in rows:
        archived_cell = archived_cells[row["scenario"]]
        for side in ("baseline", "candidate"):
            observed = row["metrics"][side]
            expected = archived_cell[side]
            matches_archive = matches_archive and all(
                observed[key] == expected[key] for key in observed
            )
        matches_archive = matches_archive and (
            row["exclusive_freeze_events"] == archived_cell["exclusive_freeze_events"]
            and row["blocked_new_risk_count"] == archived_cell["blocked_new_risk_count"]
        )
    exact_economic = all(row["metrics"]["baseline"] == row["metrics"]["candidate"] for row in rows)
    blocked = sum(int(row["blocked_new_risk_count"]) for row in rows)
    return {
        "status": "REJECTED",
        "requested_source_commit": EXCLUSIVE_FREEZE_REPORT_COMMIT,
        "requested_source_commit_available": requested_commit_available,
        "resolved_reviewed_terminal_commit": EXCLUSIVE_FREEZE_REVIEWED_COMMIT,
        "archive_commit": EXCLUSIVE_FREEZE_ARCHIVE_COMMIT,
        "source_resolution": (
            "the report SHA is unreachable; re-ran the reachable reviewed terminal "
            "of the archived candidate branch and bound the archive commit"
        ),
        "detached_rerun": True,
        "matches_archived_evidence": matches_archive,
        "cases": rows,
        "exclusive_events": sum(int(row["exclusive_freeze_events"]) for row in rows),
        "actionable_buy_intents": blocked,
        "exact_economic_equivalence": exact_economic,
        "economic_delta": 0.0 if exact_economic else None,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if not _object_exists(root, GROSS_CAP_REJECTION_COMMIT):
        raise RuntimeError("gross-cap rejection source commit is unavailable")
    if not _object_exists(root, EXCLUSIVE_FREEZE_REVIEWED_COMMIT):
        raise RuntimeError("reachable reviewed exclusive-freeze terminal is unavailable")
    requested_available = _object_exists(root, EXCLUSIVE_FREEZE_REPORT_COMMIT)
    with tempfile.TemporaryDirectory(prefix="uquant-negative-controls-") as temporary:
        temporary_root = Path(temporary)
        gross_cap_root = temporary_root / "gross-cap-rejection"
        exclusive_freeze_root = temporary_root / "exclusive-freeze-rejection"
        try:
            _run(
                _git_command(
                    "worktree", "add", "--detach", str(gross_cap_root), GROSS_CAP_REJECTION_COMMIT
                ),
                cwd=root,
            )
            _run(
                _git_command(
                    "worktree",
                    "add",
                    "--detach",
                    str(exclusive_freeze_root),
                    EXCLUSIVE_FREEZE_REVIEWED_COMMIT,
                ),
                cwd=root,
            )
            payload = {
                "schema_version": 1,
                "phase5_limited_gross_cap": _gross_cap_rejection_control(gross_cap_root),
                "phase7_exclusive_freeze": _source_availability_control(
                    exclusive_freeze_root,
                    requested_commit_available=requested_available,
                ),
            }
        finally:
            for worktree in (gross_cap_root, exclusive_freeze_root):
                if worktree.exists():
                    _run(
                        _git_command("worktree", "remove", "--force", str(worktree)),
                        cwd=root,
                    )
    payload["payload_sha256"] = canonical_sha256(payload)
    destination = root / "artifacts/sentinel/risk_differential/negative_controls_rerun.json"
    atomic_write_text(
        destination,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
