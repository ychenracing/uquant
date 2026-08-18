# Future Holdout Lanes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an append-only, lane-bound Future Holdout observation system and complete observational execution journal without changing production economic behavior.

**Architecture:** Preserve `phase2-future-holdout-v1` byte-for-byte. Add a validation-only lane registry whose immutable identities are checked against the sealed contract and prior registry state. Existing deterministic replay remains authoritative, but formal score fields stay null until a lane reaches 20 sessions; diagnostics and the manual journal remain separate evidence channels.

**Tech Stack:** Python 3.12, dataclasses, strict JSON/JSONL, SHA-256, pytest, existing uquant validation and atomic I/O utilities.

**Spec:** User-supplied `02_阶段2_Future_Holdout与真实执行证据.md` and `benchmarks/future_holdout_contract.json`.

## Global Constraints

- Do not implement P0-1 or change any concentration policy.
- Do not change strategy, risk, portfolio, order, account, or execution economics.
- Preserve `last_in_sample=2026-08-05`, `first_holdout=2026-08-06`, milestones `20/40/60`, and `parameter_changes_from_observation=false`.
- Reject backfill, lane deletion, identity mutation, data overwrite, duplicate/unknown JSON fields, unsafe paths, and journal tampering.
- Formal economic scores are all null before 20 observed lane sessions.
- Historical replay, Future Holdout replay, and manual execution evidence are separate.
- Run one final complete Engineering, Phase 1, and Phase 2 validation on the final candidate tree.

---

### Task 1: Sealed contract and append-only Lane Registry

**Files:**
- Create: `uquant/validation/holdout_lanes.py`
- Create: `benchmarks/future_holdout_lane_registry.json`
- Create: `tests/test_future_holdout_lanes.py`
- Create: `artifacts/holdout/lane_validation.json`
- Modify: `tests/test_future_holdout.py`

**Interfaces:**
- Consumes: `FutureHoldoutContract`, sealed strategy anchor, runtime provenance.
- Produces: `HoldoutLane`, `load_lane_registry()`, `validate_lane_registry()`, `validate_lane_registry_transition()`, `build_lane_validation_report()`.

- [ ] Write failing tests for unique IDs, parent existence/order, exact hashes/runtime, activation sessions, legacy champion boundary, no backfill, no deletion, no identity mutation, no behavior downgrade, and strict JSON decoding.
- [ ] Run `uv run pytest -q tests/test_future_holdout_lanes.py tests/test_future_holdout.py` and confirm failures are caused by the missing lane module/registry.
- [ ] Implement strict immutable dataclasses and validation-only registry parsing.
- [ ] Add the single legacy `champion_pre_sentinel` lane bound to the existing sealed strategy anchor; do not invent Sentinel candidates.
- [ ] Generate a sealed validation artifact reporting contract identity, lane identity, observed sessions, next milestone, and null formal scores.
- [ ] Run focused tests, Ruff, and mypy; verify `benchmarks/future_holdout_contract.json` hash is unchanged.
- [ ] Commit and push `feat: add append-only future holdout lanes`.

### Task 2: Lane-scoped replay and milestone score policy

**Files:**
- Modify: `uquant/validation/holdout.py`
- Modify: `uquant/validation/holdout_runtime.py`
- Modify: `uquant/validation/holdout_lanes.py`
- Modify: `tests/test_future_holdout.py`
- Modify: `tests/test_future_holdout_runtime.py`
- Modify: `tests/test_future_holdout_lanes.py`

**Interfaces:**
- Consumes: validated lane and append-only holdout data identity.
- Produces: lane session slice, `score_status`, `observed_metrics`, and milestone-gated formal `scores`.

- [ ] Write failing tests proving pre-activation rows never enter a lane, fewer than 20 sessions force every formal score to null, missing data stays non-reviewable, 20/40/60 status is derived only from observed sessions, and caller-supplied detached scores remain prohibited.
- [ ] Add regression tests proving append succeeds for exactly one next complete session and overwrites/restatements fail without damaging prior data.
- [ ] Run the focused tests and confirm expected red failures.
- [ ] Implement lane session slicing and separate diagnostic metrics from formal scores; preserve replay determinism and prior-close execution semantics.
- [ ] Bind each replay/report to lane, commit/source/config, contract/data manifest, and runtime summaries.
- [ ] Run focused runtime/readback tests and confirm Journal bytes do not affect model decisions or formal scores.
- [ ] Commit and push `feat: enforce lane-scoped holdout milestones`.

### Task 3: Complete append-only manual Journal and operations surface

**Files:**
- Modify: `uquant/execution_journal.py`
- Modify: `uquant/cli.py`
- Modify: `uquant/report.py`
- Modify: `tests/test_execution_journal.py`
- Modify: `tests/test_future_holdout_runtime.py`
- Modify: `uquant/validation/cli.py`
- Modify: `.github/workflows/ci.yml`
- Create: `artifacts/holdout/README.md`
- Create: `docs/reviews/2026-08-18-future-holdout-operations.md`
- Modify: `docs/PERFORMANCE.md`
- Modify: `docs/OPERATIONS.md`

**Interfaces:**
- Consumes: plan/fill/skip operator inputs and retained external checkpoint.
- Produces: schema-validated records containing decision date, planned weight/reference, next open, actual fill, broker order ID, skip reason, derived slippage, record hash, and previous hash.

- [ ] Write failing tests for required Journal v2 fields, hash-chain mutation, chronology, broker IDs, append atomicity, and unchanged decision digest/account state with and without Journal records.
- [ ] Run Journal and replay tests and confirm expected red failures.
- [ ] Extend observational Journal records without importing or calling production engine/account/portfolio/risk modules.
- [ ] Add lane validation CLI/readback and CI step; keep generated mutable holdout data and real Journal ignored.
- [ ] Document append/import/replay/report procedures and evidence separation.
- [ ] Run focused tests, Ruff, mypy, compileall, and an AST/import isolation check.
- [ ] Commit and push `test: enforce holdout and execution journal integrity` and documentation checkpoint if independently reviewable.

### Task 4: Final economic-equivalence and publication gate

**Files:**
- Verify only; no production-economic modifications.

**Interfaces:**
- Consumes: final candidate HEAD and the pre-stage baseline/provenance artifacts.
- Produces: final test logs, economic equivalence evidence, and remote ref readback.

- [ ] Verify protected production file hashes and `future_holdout_contract.json` are unchanged from `b9ee014c3d71a16060c5189bbd4b5d3ddee9c5e6`.
- [ ] Run full Engineering workflow commands including 85% branch coverage and dependency audit.
- [ ] Run full Phase 1 and Phase 2 against frozen data and compare decision/economic digests with the baseline.
- [ ] Validate the tracked Lane Registry/artifact, current observed-session count, next milestone, null formal scores, and Journal checkpoint.
- [ ] Push the final target branch, verify it is ahead of and not behind `origin/main`, then update `main` with `force=false` only.
- [ ] Fetch/read back remote `main`, compare commit/tree/artifact hashes, and report limitations including absent real future rows.
