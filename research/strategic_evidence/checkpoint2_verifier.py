"""Compact sealed checkpoint-2 full-window reproduction verifier."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from uquant.atomic_io import atomic_write_text

from .contract import load_contract
from .intervention import StrategicOwnerIntervention
from .models import canonical_sha256
from .provenance import build_provenance, seal_payload, verify_sealed_payload
from .replay import (
    ReplayRequest,
    common_activation_date,
    common_activation_target_gross,
    run_replay,
    validate_replay_accounting,
)
from .trace import first_divergence, strip_intervention_provenance

_SYMBOLS = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _research_source(root: Path) -> str:
    files = sorted((root / "research" / "strategic_evidence").glob("*.py"))
    return canonical_sha256({str(path.relative_to(root)): _sha(path) for path in files})


def _commit(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def write_checkpoint2_summary(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    contract = load_contract(root / "benchmarks" / "strategic_evidence_closure_contract.json")
    baseline = run_replay(root / "data" / "frozen", ReplayRequest(symbols=_SYMBOLS, start="2023-01-03", end="2026-08-05"))
    activation = common_activation_date(baseline)
    gross = common_activation_target_gross(baseline)
    forced = run_replay(
        root / "data" / "frozen",
        ReplayRequest(symbols=_SYMBOLS, start="2023-01-03", end="2026-08-05", scenario="forced-sz300308-common-date", intervention_date=activation),
        intervention=StrategicOwnerIntervention(owner="sz300308", target_gross=gross),
    )
    validate_replay_accounting(baseline)
    validate_replay_accounting(forced)
    base_rows, forced_rows = strip_intervention_provenance(baseline.trace), strip_intervention_provenance(forced.trace)
    base_trace, forced_trace = [asdict(row) for row in base_rows], [asdict(row) for row in forced_rows]
    scenario = {"kind": "checkpoint2_forced_zhongji", "symbols": list(_SYMBOLS), "start": "2023-01-03", "end": "2026-08-05", "owner": "sz300308"}
    provenance = build_provenance(contract, experiment_commit=_commit(root), research_source_sha256=_research_source(root), scenario=scenario, generated_at="2026-08-26T00:00:00Z")
    payload = seal_payload({
        "schema_version": 1, "provenance": provenance, "large_traces_committed": False,
        "window": {"start": "2023-01-03", "end": "2026-08-05", "future_holdout_boundary": "2026-08-06"},
        "universe": list(_SYMBOLS), "activation": {"date": activation, "owner": "sz300308", "target_gross": gross},
        "baseline": {"metrics": baseline.metrics, "final_account_sha256": baseline.trace[-1].account_sha256, "trace_sha256": canonical_sha256({"trace": base_trace})},
        "forced": {"metrics": forced.metrics, "final_account_sha256": forced.trace[-1].account_sha256, "trace_sha256": canonical_sha256({"trace": forced_trace}), "intervention_count": 1, "intervention_provenance_sha256": canonical_sha256(dict(forced.intervention_provenance or {}))},
        "equality": {"route": first_divergence(base_rows, forced_rows) is None, "targets": [r.targets for r in base_rows] == [r.targets for r in forced_rows], "orders": [r.orders for r in base_rows] == [r.orders for r in forced_rows], "fills": [r.fills for r in base_rows] == [r.fills for r in forced_rows], "equity": [r.equity for r in base_rows] == [r.equity for r in forced_rows], "state": [r.account_sha256 for r in base_rows] == [r.account_sha256 for r in forced_rows], "metrics": baseline.metrics == forced.metrics, "accounting": True},
    })
    target = root / "artifacts" / "strategic_evidence_closure" / "checkpoint2_forced_zhongji_reproduction.json"
    atomic_write_text(target, json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    readback = verify_sealed_payload(json.loads(target.read_text(encoding="utf-8")), label="checkpoint2 summary")
    if readback != payload:
        raise ValueError("checkpoint2 summary readback differs")
    return payload
