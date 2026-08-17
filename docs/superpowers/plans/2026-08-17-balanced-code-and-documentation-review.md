# Balanced Code and Documentation Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete a bounded, repeated review of every tracked Python source file,
test, comment, workflow, and Markdown document; fix all demonstrated Critical and
Important issues without material strategy or economic regression; publish the
verified result to GitHub.

**Architecture:** Review the repository by ownership boundary rather than by file
size alone: durable state/execution, production strategy, evidence/control plane,
research and CLI tooling, tests/workflows, then documentation. Each boundary gets
a focused audit, an optional red-green fix cycle, a complete diff review, and a
scoped commit. Production-source changes trigger one final authenticated Phase 1
and six-window Phase 2 run plus the repository's exact provenance rebinding path.

**Tech Stack:** Python 3.12, pandas 3.0.5, NumPy 2.5.1, pytest, Hypothesis,
Ruff, strict mypy, Bandit, pip-audit, uv 0.11.33, GitHub Actions.

## Global Constraints

- Baseline commit: `e2663695fd008fb960b86f33bc36309a2f525b68`.
- Work branch: `agent/balanced-review` in the existing linked worktree.
- Preserve `AGENTS.md` byte-for-byte.
- Do not tune strategy parameters, official windows, pools, seeds, universe, data,
  market rules, or frozen policy thresholds.
- Structural refactors must preserve historical decisions, orders, account state,
  replay output, and economic results.
- A behavioral defect fix requires a deterministic failing test, the smallest
  coherent production fix, adjacent-suite verification, and expanded economic
  validation whenever the changed boundary can affect results.
- Do not remove compatibility, migration, audit, provenance, or historical evidence
  solely because it is labeled legacy, deprecated, or obsolete.
- Run complete authenticated economic validation once in Task 8 on the final
  candidate production tree. After Task 8 commits evidence bindings and ledger
  metadata, Task 9 runs the zero-deselection complete Engineering/provenance proof
  on that exact HEAD; do not repeat full economics after behavior-neutral metadata
  changes when equivalence holds.
- Stop when no known Critical or Important issue remains and every acceptance gate
  passes. Do not continue marginal style churn.

## Finding and Fix Protocol

Every audit task uses this exact severity model:

- **Critical:** corruption, unsafe execution, fabricated evidence, security exploit,
  or strategy/economic regression that can invalidate release. Fix immediately.
- **Important:** incorrect behavior, fail-open validation, misleading current
  documentation, materially tangled responsibility, or an untested high-risk edge.
  Fix before proceeding.
- **Minor:** local readability or style improvement with no correctness impact. Fix
  only when the diff stays small and behavior-neutral.

For a behavioral finding, map it to the adjacent test file named in the task, add a
test whose name describes the observed invariant, run that node ID to confirm the
failure, apply the minimal fix, rerun the node ID, then rerun the complete adjacent
test file. For a behavior-neutral extraction, compare executable ASTs when
applicable and run the exact adjacent suites. Record the finding, resolution, and
verification in `docs/reviews/2026-08-17-balanced-review.md`.

---

### Task 1: Establish the review ledger and authenticated baseline

**Files:**
- Create: `docs/reviews/2026-08-17-balanced-review.md`
- Read only: `AGENTS.md`, `pyproject.toml`, `.github/workflows/*.yml`

**Interfaces:**
- Consumes: baseline commit `e2663695fd008fb960b86f33bc36309a2f525b68`
  and the approved design.
- Produces: one concise ledger containing scope, severity definitions, baseline
  hashes, review rounds, resolved findings, final commands, and publication state.

- [ ] **Step 1: Create the ledger with immutable baseline facts**

  Include the baseline commit and tree, Python/uv/runtime lock identities, frozen
  data manifest identity, `1198`-test green baseline, three green GitHub workflow
  run links, the approved balanced-level constraints, and status `IN PROGRESS`.

- [ ] **Step 2: Capture mechanical review inputs**

  Run:

  ```bash
  uv run ruff check .
  uv run mypy uquant scripts research
  uv run bandit -q -r uquant research scripts
  git ls-files '*.py' '*.md' '.github/workflows/*.yml' | sort
  git status --short
  ```

  Record that the standard checks pass, the repository contains no bare exception,
  `shell=True`, `eval`, `exec`, or mutable-default finding, and that advisory
  complexity counts are review-routing signals rather than automatic refactors.

- [ ] **Step 3: Verify the protected instruction file is unchanged**

  Run:

  ```bash
  git diff --exit-code e2663695fd008fb960b86f33bc36309a2f525b68 -- AGENTS.md
  git diff --check
  ```

- [ ] **Step 4: Commit the review ledger**

  ```bash
  git add docs/reviews/2026-08-17-balanced-review.md
  git commit -m "Start balanced repository review ledger"
  ```

### Task 2: Review durable account, execution, and journal boundaries

**Files:**
- Review: `uquant/account.py`
- Review: `uquant/atomic_io.py`
- Review: `uquant/broker.py`
- Review: `uquant/execution.py`
- Review: `uquant/execution_journal.py`
- Review: `uquant/types.py`
- Test: `tests/test_account_broker_schema.py`
- Test: `tests/test_account_schema_v3_integrity.py`
- Test: `tests/test_attribution_identity.py`
- Test: `tests/test_broker_sync.py`
- Test: `tests/test_engine_contracts.py`
- Test: `tests/test_execution.py`
- Test: `tests/test_execution_journal.py`
- Update: `docs/reviews/2026-08-17-balanced-review.md`

**Interfaces:**
- Consumes: canonical account schema, stable order/fill identities, broker snapshot,
  atomic-write, and append-only journal contracts.
- Produces: reviewed persistence and execution code with unchanged order/account
  semantics except for separately proven defects.

- [ ] **Step 1: Audit exact invariants line by line**

  Check schema migrations, sequence non-reuse, attribution identity, duplicate fill
  handling, T+1/cash/lot enforcement, atomic replace cleanup, journal hash-chain
  continuity, daily checkpoint continuity, exception specificity, and comments that
  explain why a fail-closed condition exists.

- [ ] **Step 2: Apply the Finding and Fix Protocol to every valid finding**

  Use only the mapped test files. Preserve public serialized field names and stable
  reason codes. Do not consolidate validators merely to reduce advisory complexity;
  extract only a cohesive repeated rule with an explicit invariant test.

- [ ] **Step 3: Run the complete boundary suite**

  ```bash
  uv run pytest -q \
    tests/test_account_broker_schema.py \
    tests/test_account_schema_v3_integrity.py \
    tests/test_attribution_identity.py \
    tests/test_broker_sync.py \
    tests/test_engine_contracts.py \
    tests/test_execution.py \
    tests/test_execution_journal.py
  uv run ruff check uquant/account.py uquant/atomic_io.py uquant/broker.py \
    uquant/execution.py uquant/execution_journal.py uquant/types.py tests
  uv run mypy uquant/account.py uquant/atomic_io.py uquant/broker.py \
    uquant/execution.py uquant/execution_journal.py uquant/types.py
  ```

- [ ] **Step 4: Review and commit only validated changes**

  ```bash
  git diff --check
  git diff -- AGENTS.md
  git add uquant tests docs/reviews/2026-08-17-balanced-review.md
  git commit -m "Tighten account and execution clarity"
  ```

  Skip the commit when the audit produces no file change; record the clean review in
  the ledger instead.

### Task 3: Review strategy, portfolio, risk, and engine orchestration

**Files:**
- Review: `uquant/config.py`
- Review: `uquant/config_governance.py`
- Review: `uquant/data.py`
- Review: `uquant/features.py`
- Review: `uquant/industry.py`
- Review: `uquant/leader.py`
- Review: `uquant/opportunity.py`
- Review: `uquant/portfolio.py`
- Review: `uquant/portfolio_core.py`
- Review: `uquant/portfolio_leaders.py`
- Review: `uquant/portfolio_recovery.py`
- Review: `uquant/portfolio_strategic.py`
- Review: `uquant/reference.py`
- Review: `uquant/reference_registry.py`
- Review: `uquant/risk.py`
- Review: `uquant/risk_sector.py`
- Review: `uquant/engine.py`
- Test: `tests/test_config_contracts.py`
- Test: `tests/test_config_governance.py`
- Test: `tests/test_data_and_leader.py`
- Test: `tests/test_industry_rotation.py`
- Test: `tests/test_lifecycle_and_risk.py`
- Test: `tests/test_portfolio_calendar.py`
- Test: `tests/test_properties.py`
- Test: `tests/test_recovery_contracts.py`
- Test: `tests/test_risk_transitions.py`
- Test: `tests/test_sector_guard.py`
- Update: `docs/reviews/2026-08-17-balanced-review.md`

**Interfaces:**
- Consumes: frozen `SystemConfig`, point-in-time market data, opportunity/risk state,
  and single-owner target allocation.
- Produces: clearer orchestration without unreviewed signal, threshold, lifecycle,
  risk-authority, or target-vector changes.

- [ ] **Step 1: Audit strategy ownership and comments**

  Trace `ProductionEngine.decide()` through opportunity, reference, risk, portfolio,
  execution, and durable state. Check point-in-time boundaries, owner handoff,
  confirmation state, replacement tenure, recovery/restoration eligibility, gross
  and risk caps, deterministic ordering, calendar/T+1 semantics, and every comment
  that claims one component is the unique owner of a transition.

- [ ] **Step 2: Limit structural work to cohesive behavior-neutral extractions**

  Do not rewrite `_allocate_strategy`, `_leader_targets`, strategic cohort functions,
  or `assess_risk` simply because they are long. An extraction is accepted only when
  inputs/outputs are explicit, no control-flow order changes, focused tests pass,
  and pre/post decision traces remain identical. Apply the Finding and Fix Protocol
  to any demonstrated defect.

- [ ] **Step 3: Run focused strategy suites and deterministic properties**

  ```bash
  uv run pytest -q \
    tests/test_config_contracts.py tests/test_config_governance.py \
    tests/test_data_and_leader.py tests/test_industry_rotation.py \
    tests/test_lifecycle_and_risk.py tests/test_portfolio_calendar.py \
    tests/test_properties.py tests/test_recovery_contracts.py \
    tests/test_risk_transitions.py tests/test_sector_guard.py
  uv run ruff check uquant tests
  uv run mypy uquant
  ```

- [ ] **Step 4: Run a decision-equivalence sentinel before committing**

  Create a detached baseline checkout at `/tmp/uquant-balanced-baseline` from
  `e2663695fd008fb960b86f33bc36309a2f525b68`, then run:

  ```bash
  git worktree add --detach /tmp/uquant-balanced-baseline \
    e2663695fd008fb960b86f33bc36309a2f525b68
  mkdir -p /tmp/uquant-balanced-review
  uv run python scripts/verify_phase1_decision_equivalence.py \
    --frozen-root /tmp/uquant-balanced-baseline \
    --candidate-root "$PWD" \
    --data-dir data/frozen \
    --output /tmp/uquant-balanced-review/phase1-decision-equivalence.json
  ```

  Require the report to pass. A deliberate defect fix may differ only in the tested
  trace and must be documented before expanded economics.

- [ ] **Step 5: Commit the reviewed strategy boundary**

  ```bash
  git diff --check
  git diff -- AGENTS.md
  git add uquant tests docs/reviews/2026-08-17-balanced-review.md
  git commit -m "Clarify strategy orchestration invariants"
  ```

  Skip the commit when no file changes are justified.

### Task 4: Review attribution, validation, holdout, and evidence control planes

**Files:**
- Review: `uquant/attribution.py`
- Review: `uquant/report.py`
- Review: `uquant/validation/*.py`
- Review: `uquant/validation/resources/*.json`
- Review: `research/*.py`
- Review: `scripts/run_phase1_diagnostic.py`
- Review: `scripts/run_phase2_ablation.py`
- Review: `scripts/verify_phase1_decision_equivalence.py`
- Test: `tests/test_attribution.py`
- Test: `tests/test_attribution_identity.py`
- Test: `tests/test_economic_attribution.py`
- Test: `tests/test_future_holdout.py`
- Test: `tests/test_future_holdout_runtime.py`
- Test: `tests/test_generalization*.py`
- Test: `tests/test_phase1*.py`
- Test: `tests/test_phase2*.py`
- Test: `tests/test_validation*.py`
- Update: `docs/reviews/2026-08-17-balanced-review.md`

**Interfaces:**
- Consumes: authenticated raw engine results, immutable contracts, Git-object source
  identities, canonical JSON, and frozen data.
- Produces: fail-closed validators and replay/holdout evidence whose comments and
  exception boundaries match their actual guarantees.

- [ ] **Step 1: Audit every fail-closed path**

  Check duplicate-key rejection, finite-number validation, canonical seals, exact
  path inventories, source fingerprints, Git-object reads, replay error retention,
  attribution reconciliation, shard aggregation, prior-close account binding,
  first/consecutive holdout session continuity, journal checkpoints, and atomic
  replay/decision output protection.

- [ ] **Step 2: Review broad exception boundaries and subprocess calls**

  Confirm each of the five `except Exception` sites is an intentional boundary that
  converts arbitrary worker/replay/input failures into deterministic evidence while
  preserving type/message/date or exception chaining. Confirm every subprocess uses
  fixed executable/argument arrays, `check=True`, captured output where required,
  and no shell. Improve misleading comments; narrow exceptions only when the full
  valid failure set remains covered.

- [ ] **Step 3: Apply the Finding and Fix Protocol and run the boundary suite**

  ```bash
  uv run pytest -q \
    tests/test_attribution.py tests/test_attribution_identity.py \
    tests/test_economic_attribution.py tests/test_future_holdout.py \
    tests/test_future_holdout_runtime.py tests/test_generalization.py \
    tests/test_generalization_contract.py tests/test_generalization_matrix.py \
    tests/test_generalization_smoke.py tests/test_phase1_decision_equivalence.py \
    tests/test_phase1_diagnostic_runner.py tests/test_phase1_evidence.py \
    tests/test_phase2_ablation.py tests/test_phase2_ci_contract.py \
    tests/test_validation.py tests/test_validation_cli.py
  uv run ruff check uquant/validation uquant/attribution.py uquant/report.py \
    research scripts tests
  uv run mypy uquant/validation uquant/attribution.py uquant/report.py research scripts
  uv run bandit -q -r uquant/validation uquant/attribution.py research scripts
  ```

- [ ] **Step 4: Commit only justified evidence-boundary changes**

  ```bash
  git diff --check
  git diff -- AGENTS.md
  git add uquant research scripts tests docs/reviews/2026-08-17-balanced-review.md
  git commit -m "Tighten evidence and validation clarity"
  ```

### Task 5: Review remaining CLI, data acquisition, adapters, and tests

**Files:**
- Review: `uquant/__init__.py`
- Review: `uquant/__main__.py`
- Review: `uquant/cli.py`
- Review: `scripts/backfill_tencent_history.py`
- Review: `scripts/run_five_window_outperformance.py`
- Review: `scripts/run_pareto_evidence.py`
- Review: `scripts/run_window_competitor_adapter.py`
- Review: `scripts/run_window_outperformance.py`
- Review: every remaining `tests/test_*.py` not covered by Tasks 2–4
- Update: `docs/reviews/2026-08-17-balanced-review.md`

**Interfaces:**
- Consumes: explicit command arguments, bounded paths, fixed competitor contracts,
  and production APIs.
- Produces: clear user-facing errors, deterministic adapter outputs, and tests that
  assert behavior rather than incidental implementation.

- [ ] **Step 1: Audit CLI and script safety**

  Check argument validation, path containment, overwrite behavior, subprocess
  isolation, temporary cleanup, deterministic sorting, external-data boundaries,
  and whether help text/comments describe the executed code.

- [ ] **Step 2: Audit the remaining tests**

  Check negative cases, edge boundaries, deterministic fixtures, mutation tests,
  over-broad mocks, implementation-detail assertions, and tests whose docstrings no
  longer match the protected behavior.

- [ ] **Step 3: Apply the Finding and Fix Protocol and run all non-economic tests**

  ```bash
  uv run pytest -q --ignore=tests/test_five_window_outperformance.py \
    --ignore=tests/test_window_outperformance.py
  uv run ruff check .
  uv run mypy uquant scripts research
  uv run bandit -q -r uquant research scripts
  ```

- [ ] **Step 4: Review and commit justified changes**

  ```bash
  git diff --check
  git diff -- AGENTS.md
  git add uquant scripts tests docs/reviews/2026-08-17-balanced-review.md
  git commit -m "Clarify command and test boundaries"
  ```

### Task 6: Audit workflows and every tracked document

**Files:**
- Review: `.github/dependabot.yml`
- Review: `.github/workflows/ci.yml`
- Review: `.github/workflows/strategy-performance.yml`
- Review: `.github/workflows/strategy-generalization.yml`
- Review: `README.md`
- Review: `docs/ARCHITECTURE.md`
- Review: `docs/CONFIGURATION.md`
- Review: `docs/DEVELOPMENT.md`
- Review: `docs/OPERATIONS.md`
- Review: `docs/PERFORMANCE.md`
- Review: `docs/QUALITY.md`
- Review: `docs/STRATEGY.md`
- Review: `artifacts/phase1/before/README.md`
- Review: `artifacts/phase2/ablations/conclusions.md`
- Review: `artifacts/phase2/final-acceptance.md`
- Review: `docs/superpowers/plans/*.md`
- Review: `docs/superpowers/specs/*.md`
- Review: `.superpowers/sdd/**/*.md`
- Update: `docs/reviews/2026-08-17-balanced-review.md`
- Test: `tests/test_phase2_ci_contract.py`
- Test: `tests/test_engineering_gate_edges.py`

**Interfaces:**
- Consumes: final reviewed code, CLI help, workflow commands, frozen contracts, and
  historical evidence timestamps.
- Produces: concise current documentation, preserved historical provenance, valid
  navigation, and workflow prose that matches actual blocking behavior.

- [ ] **Step 1: Review current user and developer documentation completely**

  Verify every command against `--help`, every module ownership statement against
  imports and call flow, every configuration name against `SystemConfig`, and every
  Phase 1/Phase 2/holdout statement against the current contracts. Remove duplicate
  prose, add missing navigation or limitations, and keep runnable examples compact.

- [ ] **Step 2: Review historical material without rewriting history**

  Preserve original commits, hashes, metrics, and decisions. Add an explicit
  historical-snapshot/superseded-context notice where a report can be mistaken for
  current operational truth, especially `artifacts/phase2/final-acceptance.md`.
  Correct broken links and objective contradictions only.

- [ ] **Step 3: Verify document structure and local links**

  Run a local Markdown link check over every tracked `*.md`, scan headings for
  duplicate top-level titles, compare documented commands with CLI/workflow help,
  and run:

  ```bash
  uv run pytest -q tests/test_phase2_ci_contract.py tests/test_engineering_gate_edges.py
  rg -n 'TBD|TODO|FIXME|obsolete smoke|optional Performance|nonblocking' \
    README.md docs artifacts .superpowers/sdd -g '*.md'
  git diff --check
  git diff --exit-code e2663695fd008fb960b86f33bc36309a2f525b68 -- AGENTS.md
  ```

- [ ] **Step 4: Commit the documentation cleanup**

  ```bash
  git add README.md docs artifacts .superpowers .github \
    tests/test_phase2_ci_contract.py tests/test_engineering_gate_edges.py
  git commit -m "Align project documentation and review guidance"
  ```

### Task 7: Perform a fresh second review and close all blocking findings

**Files:**
- Review: complete `e2663695..HEAD` diff
- Update: `docs/reviews/2026-08-17-balanced-review.md`
- Modify/Test: only files implicated by valid second-round findings

**Interfaces:**
- Consumes: all Task 1–6 changes and the approved design.
- Produces: a candidate with zero known Critical or Important findings.

- [ ] **Step 1: Review the complete diff independent of task order**

  Check requirement coverage, behavior changes, duplicate helpers, comment/code
  mismatch, error paths, security, performance, tests, docs, protected contracts,
  and accidental edits. Classify every finding in the ledger.

- [ ] **Step 2: Fix every valid Critical or Important finding**

  Apply the Finding and Fix Protocol, rerun its adjacent suite, and repeat the diff
  review after any material fix. Minor findings remain only when fixing them would
  add more risk or churn than clarity.

- [ ] **Step 3: Run the engineering gate with the exact Task 8 anchors isolated**

  Run the complete pytest command first and require it to fail only at the exact
  four source-identity-only nodes listed below. This is the intentional fail-closed
  precondition for Task 8, not authorization to rebind, weaken, or relabel an
  anchor during Task 7.

  ```bash
  export UV_CACHE_DIR=/tmp/uquant-balanced-review/uv-cache
  uv run ruff check .
  uv run mypy uquant scripts research
  uv run python -m uquant.validation data-manifest --data-dir data/frozen
  uv run pytest --cov=uquant --cov-report=term-missing --cov-report=xml
  ```

  The exact Task 8-owned failures are:

  - `tests/test_engineering_gate_edges.py::test_current_holdout_binding_matches_exact_head_and_reviewed_strategy_anchors`
  - `tests/test_future_holdout.py::test_current_strategy_cli_matches_reviewed_anchor`
  - `tests/test_future_holdout.py::test_current_code_fingerprint_matches_frozen_candidate_account_code_hash`
  - `tests/test_phase2_ablation.py::test_current_source_matches_reviewed_post_task8_contract`

  These nodes contain only candidate-to-reviewed-identity assertions. Defensive
  binding/JSON/Git validation, session/score/manifest/data/account tamper checks,
  Phase 2 market-safety and checkout checks, source-mutation rejection, and the
  deterministic runner remain selected and must pass independently.

  Then rerun pytest with exactly those nodes deselected and require the provisional
  non-anchor suite to pass. Continue the remaining engineering checks normally.

  ```bash
  uv run pytest --cov=uquant --cov-report=term-missing --cov-report=xml \
    --deselect tests/test_engineering_gate_edges.py::test_current_holdout_binding_matches_exact_head_and_reviewed_strategy_anchors \
    --deselect tests/test_future_holdout.py::test_current_strategy_cli_matches_reviewed_anchor \
    --deselect tests/test_future_holdout.py::test_current_code_fingerprint_matches_frozen_candidate_account_code_hash \
    --deselect tests/test_phase2_ablation.py::test_current_source_matches_reviewed_post_task8_contract
  uv run python -m compileall -q uquant scripts research tests
  uv run python -m build
  uv run bandit -q -r uquant research scripts
  uv export --frozen --no-dev --no-emit-project --no-hashes \
    --output-file /tmp/uquant-balanced-review/requirements.txt
  uv run pip-audit --cache-dir /tmp/uquant-balanced-review/pip-audit-cache \
    --requirement /tmp/uquant-balanced-review/requirements.txt
  ```

- [ ] **Step 4: Record the provisionally clean review round and commit**

  Mark the ledger `CODE AND DOCUMENT REVIEW CLEAN; TASK 8 SOURCE REBIND PENDING`
  only after the fresh diff review finds zero Critical/Important issues, the first
  full pytest run fails at exactly the four identity-only anchors and nowhere else, and
  the provisional non-anchor pytest run plus every other command above exits zero.
  Task 8 must rebind the reviewed source identities and rerun every affected
  contract before Task 9. Do not claim the complete Engineering gate is green
  until Task 9 reruns the full suite with zero deselections after Task 8's last
  metadata commit.

  ```bash
  git add -A
  git commit -m "Close balanced review findings"
  ```

### Task 8: Run final economic validation and bind exact provenance

**Files:**
- Generate ignored evidence under: `/tmp/uquant-balanced-review/final/`
- Conditionally modify: `benchmarks/future_holdout_contract.json`
- Conditionally modify: `uquant/validation/holdout.py`
- Conditionally modify: `artifacts/phase2/ablations/post_task8_source_contract.json`
- Conditionally modify: `research/ablation_registry.py`
- Conditionally modify: exact contract tests
- Update: `docs/reviews/2026-08-17-balanced-review.md`

**Interfaces:**
- Consumes: the final reviewed production tree, frozen data/config/universe/runtime,
  and authenticated baseline contracts.
- Produces: full Phase 1 and Phase 2 evidence, plus exact source/holdout contracts
  when production-source bytes changed.

- [ ] **Step 1: Run complete Phase 1**

  ```bash
  mkdir -p /tmp/uquant-balanced-review/final
  uv run python -m uquant.validation promotion \
    --data-dir data/frozen \
    --profile full \
    --output /tmp/uquant-balanced-review/final/phase1.json
  uv run python -m uquant.validation.ci_artifacts phase1 \
    --artifact /tmp/uquant-balanced-review/final/phase1.json \
    --report-output /tmp/uquant-balanced-review/final/phase1-validation.json \
    --upstream-result success \
    --data-dir data/frozen
  ```

  Require 30 official and 15 protected records, zero failures, and no unauthorized
  deterioration from the baseline tree.

- [ ] **Step 2: Run all six Phase 2 windows and aggregate policy**

  ```bash
  prefix=balanced-final
  shard_root=/tmp/uquant-balanced-review/final/shards
  for window in h1_2023 h2_2023 h1_2024 h2_2024 \
    bull_crash_2025_2026 continuous_ai_era; do
    artifact="$shard_root/${prefix}-${window}"
    mkdir -p "$artifact"
    uv run python -m uquant.validation generalization-matrix \
      --data-dir data/frozen \
      --window "$window" \
      --output "$artifact/${window}.json"
  done
  uv run python -m uquant.validation.ci_artifacts generalization \
    --shard-root "$shard_root" \
    --artifact-prefix "$prefix" \
    --report-output /tmp/uquant-balanced-review/final/generalization-policy-report.json \
    --merged-output /tmp/uquant-balanced-review/final/generalization-matrix.json \
    --upstream-result success \
    --data-dir data/frozen
  ```

  Require 234 total records, 192 economic cells, zero replay errors, complete
  attribution reconciliation, and a passing frozen policy report.

- [ ] **Step 3: Rebind provenance only when source inventories require it**

  If production-source or holdout-source bytes changed, create an immutable remote
  candidate anchor. Compute strategy identity with
  `uquant.validation.holdout._strategy_source_sha256`, account-code identity with
  `_strategy_account_code_sha256`, and the prior-close account digest from the
  authentic `continuous_ai_era/full` final account in the merged artifact. Update
  `future_holdout_contract.json`, its Python constants, and the canonical seal.
  Publish that holdout anchor, then regenerate the post-Task8 source delta from Git
  objects with `_production_paths_at_commit`, `_source_fingerprint_at_commit`, and
  `_source_delta`; update its canonical seal and Python constant. Never hand-enter a
  score or reuse a digest from a different tree.

- [ ] **Step 4: Rerun exact affected contract tests after rebinding**

  ```bash
  uv run pytest -q tests/test_future_holdout.py tests/test_future_holdout_runtime.py \
    tests/test_phase2_ablation.py tests/test_phase2_ci_contract.py \
    tests/test_engineering_gate_edges.py
  uv run ruff check .
  uv run mypy uquant scripts research
  git diff --check
  git diff --exit-code e2663695fd008fb960b86f33bc36309a2f525b68 -- AGENTS.md
  ```

- [ ] **Step 5: Commit final evidence bindings and ledger results**

  ```bash
  git add benchmarks uquant research artifacts tests \
    docs/reviews/2026-08-17-balanced-review.md
  git commit -m "Bind balanced review evidence to final source"
  ```

### Task 9: Final review, GitHub publication, and remote verification

**Files:**
- Review: complete baseline-to-final diff
- Update: `docs/reviews/2026-08-17-balanced-review.md`

**Interfaces:**
- Consumes: Task 8's committed identities, authenticated economics,
  affected-contract results, and every prior review result.
- Produces: a zero-deselection full local proof of that exact committed HEAD,
  followed by non-forced publication of an identical remote tree and three
  successful blocking workflows.

- [ ] **Step 1: Perform final requirement and diff review**

  Verify all four user requirements line by line, confirm no Critical/Important
  finding remains, confirm `AGENTS.md` is unchanged, and confirm the worktree contains
  only intended tracked changes.

- [ ] **Step 2: Rerun the smallest final-tree proof after the last metadata commit**

  Run Ruff, strict mypy, frozen manifest, full pytest with coverage and zero
  deselections, compile, build, Bandit, dependency audit, exact holdout/ablation
  contract tests, and provenance readback. This step—not Task 8's affected
  suite—establishes the complete Engineering gate. If any tracked metadata changes
  afterward, commit it and rerun this proof before publication. Reuse Phase 1/Phase 2
  economic artifacts only if the production tree, configuration, data, universe,
  runtime lock, and runner bytes are unchanged.

- [ ] **Step 3: Publish without force**

  Confirm remote `main` still points to the expected parent. Push the verified tree
  through the configured GitHub path using a fast-forward/non-forced update. Stop
  and investigate if the remote moved; never force over concurrent work.

  ```bash
  git fetch origin main
  test "$(git rev-parse origin/main)" = \
    "e2663695fd008fb960b86f33bc36309a2f525b68"
  git push origin HEAD:main
  ```

- [ ] **Step 4: Verify the remote result**

  Fetch `main`, compare the remote tree SHA to the locally verified tree, and wait
  for `Engineering gates`, `AI-Era performance`, and `AI-Era generalization` to
  complete with `success`. Report the immutable workflow URLs in the final external
  report without creating another tracked commit. If ledger metadata must be
  published, commit it before Step 2, rerun Step 2 against the new HEAD, and
  publish/verify that exact tree.

- [ ] **Step 5: Deliver the final report**

  Report the final GitHub commit, reviewed file groups, resolved Critical/Important
  findings, documents changed or deliberately retained, local verification totals,
  economic-equivalence/non-regression result, three workflow states, and any
  intentionally retained Minor observations.

- [ ] **Step 6: Remove the temporary baseline checkout**

  After all comparisons and publication checks finish, run from the main worktree:

  ```bash
  git worktree remove /tmp/uquant-balanced-baseline
  git worktree prune
  ```
