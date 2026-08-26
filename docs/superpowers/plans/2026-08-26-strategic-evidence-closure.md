# Strategic Evidence Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a sealed production-backed research system that closes strategic handoff, witness, reachability, and absolute-robustness evidence without changing production economics.

**Architecture:** A research-only package wraps the official replay loop and durable account model, adds a single auditable intervention point plus read-only universe diagnostics, writes resumable deterministic shards, and evaluates literal preregistered policy. Compact evidence and a non-blocking workflow are tracked; large traces remain workflow artifacts.

**Tech Stack:** Python 3.12, dataclasses, pandas/NumPy, gzip JSONL, NetworkX-free deterministic Tarjan graph analysis, pytest, Ruff, strict MyPy, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-26-strategic-evidence-closure-design.md`

## Global Constraints

- Base commit is `70d66b37edea3cd42ffb19c896b3f318e8bd536e` and the economic window is 2023-01-03 through 2026-08-05.
- Do not modify `uquant/`, `data/frozen/`, production configuration, existing policies, `uv.lock`, or dependencies.
- Every economic cell uses `ProductionEngine.decide()` and production execution/accounting.
- Preserve every `REPLAY_ERROR` and `INSUFFICIENT_SAMPLE`; never tune scenarios, thresholds, or seed after observation.
- Future Holdout observations beginning 2026-08-06 are unavailable for selection.

---

### Task 1: Freeze contract and evidence identities

**Files:**
- Create: `research/strategic_evidence/models.py`
- Create: `research/strategic_evidence/contract.py`
- Create: `benchmarks/strategic_evidence_closure_contract.json`
- Test: `tests/test_strategic_evidence_contract.py`

**Interfaces:**
- Produces: `canonical_sha256(payload) -> str`, `load_contract(path) -> StrategicEvidenceContract`, and fixed required-cell identifiers.

- [ ] Write contract tests for canonical sealing, fixed identities, 34-symbol universe, forced controls, 14 states, 6 paths, health rules, literal thresholds, and failure semantics.
- [ ] Run the focused test and verify import/contract failure.
- [ ] Implement immutable models and fail-closed contract loading.
- [ ] Seal v1 and run focused tests, Ruff, and MyPy for the new files.
- [ ] Commit and push Checkpoint 1 with the spec, plan, base identity, and contract.

### Task 2: Production-backed trace, provenance, and intervention

**Files:**
- Create: `research/strategic_evidence/provenance.py`
- Create: `research/strategic_evidence/trace.py`
- Create: `research/strategic_evidence/intervention.py`
- Create: `research/strategic_evidence/replay.py`
- Test: `tests/test_strategic_evidence_provenance.py`
- Test: `tests/test_strategic_owner_intervention.py`
- Test: `tests/test_strategic_evidence_trace.py`

**Interfaces:**
- Produces: `StrategicOwnerIntervention.apply(account)`, `ReplayRequest`, `ReplayResult`, `RouteTraceRow`, `first_divergence()`, deterministic gzip shard read/write, and accounting reconciliation.

- [ ] Write failing tests that name mixed-owner, future-data, unsealed-shard, layer-order, and accounting mutations.
- [ ] Implement atomic owner rewriting and account invariant validation.
- [ ] Implement the official close/open loop with a one-shot intervention callback and causal evidence capture.
- [ ] Verify unforced and forced-`sz300308` common-date traces are economically exact after stripping intervention provenance.
- [ ] Run the Task 2 focused suite and commit/push Checkpoint 2.

### Task 3: Forced-owner matrix

**Files:**
- Create: `research/strategic_evidence/forced_owner.py`
- Test: `tests/test_strategic_forced_owner.py`

**Interfaces:**
- Produces: causal activation detection, native eligibility evidence, frozen positive/negative controls, complete per-cell metrics, and forced-owner summary.

- [ ] Write failing causal-selection and deterministic-negative-control tests with hand-derived fixtures.
- [ ] Implement common/native cell construction and result aggregation.
- [ ] Run L2 baseline, positive, and negative sentinel cells.
- [ ] Run/resume the complete forced-owner matrix; validate readback and required coverage.
- [ ] Commit/push Checkpoint 3 with compact evidence and shard identities.

### Task 4: Witness ablation and first divergence

**Files:**
- Create: `research/strategic_evidence/witness_ablation.py`
- Test: `tests/test_strategic_witness_ablation.py`

**Interfaces:**
- Produces: three-axis removals, economic/diagnostic labeling, role classification, critical-symbol ranking, bounded delta debugging, minimal witness sets, and route/state/economic divergence.

- [ ] Write failing tests for ghost-witness classification, divergence ordering, and bounded deterministic pair/triple selection.
- [ ] Implement 34 leave-one-out, report-universe, industry, and diagnostic cells.
- [ ] Run L2 single-removal sentinel, then complete/resumable matrix and bounded critical search.
- [ ] Validate every required cell and commit/push Checkpoint 4.

### Task 5: State reachability and cash vacancy

**Files:**
- Create: `research/strategic_evidence/reachability.py`
- Test: `tests/test_strategic_reachability.py`

**Interfaces:**
- Produces: validated S01-S14 states, deterministic P01-P06 paths, R1-R8 observations, blocker timelines, Tarjan SCCs, terminal dead-state classification, and repair latency.

- [ ] Write failing invariant, causal synthetic-path, reach-node, and terminal-SCC tests.
- [ ] Implement historical checkpoint extraction and synthetic fallback with explicit provenance.
- [ ] Run one capital-budget L2 cell, then the complete mandatory matrix.
- [ ] Validate graph determinism, healthy-session accounting, and repeated-crowning trace.
- [ ] Commit/push Checkpoint 5.

### Task 6: Literal policy, artifacts, workflow, and final acceptance

**Files:**
- Create: `research/strategic_evidence/absolute_policy.py`
- Create: `research/strategic_evidence/report.py`
- Create: `scripts/run_strategic_evidence_closure.py`
- Create: `tests/test_strategic_absolute_policy.py`
- Create: `tests/test_strategic_evidence_artifacts.py`
- Create: `.github/workflows/strategic-evidence-closure.yml`
- Create: `artifacts/strategic_evidence_closure/README.md`
- Create: `artifacts/strategic_evidence_closure/analysis.md`
- Create: `artifacts/strategic_evidence_closure/evidence_manifest.json`
- Create: `artifacts/strategic_evidence_closure/compact_summary.json`
- Create: `artifacts/strategic_evidence_closure/SHA256SUMS`

**Interfaces:**
- Produces: `evaluate_absolute_policy(...)`, resumable CLI phases, complete evidence validator, and final direct-answer report.

- [ ] Write failing literal-tail, missing-cell, replay-error, insufficient-sample, and workflow artifact tests.
- [ ] Implement policy/report/orchestrator and non-blocking workflow with pinned actions.
- [ ] Run the three complete matrices once on the stable candidate, then seal/read back evidence.
- [ ] Run focused research tests; affected portfolio/risk/execution/account tests; Ruff; strict MyPy; compileall; branch coverage; artifact/provenance validators; Windows smoke; and protected-path/economic-equivalence checks.
- [ ] Obtain independent code review, repair blocking findings with focused revalidation, commit/push Checkpoint 6, create PR, and merge only when the approved path conditions remain true.
