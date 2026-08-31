# Absolute Generalization Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a blocking, production-trace-backed Absolute Generalization Acceptance whose only green result is `passed = runner_success and capability_pass`, with all seven fixed capability components true over the complete canonical 34-symbol leave-one-out matrix.

**Architecture:** Add one validation-owned `uquant.validation.absolute_generalization` package that freezes the contract, constructs point-in-time full-removal scenarios, replays the existing production engine, derives independently recomputable evidence, analyzes bounded reachability, and aggregates all shards. Keep `ProductionEngine.decide`, Risk, `PortfolioAllocator`, `AccountState`, Target/Order/Fill, and strategic ownership as the only economic authorities. The script and workflow are thin orchestration layers and never self-assert capability success.

**Tech Stack:** Python 3.12.13, dataclasses/typing, canonical JSON/SHA-256, pandas/NumPy, pytest, Ruff, strict MyPy, uv, GitHub Actions.

**Spec:** `/workspace/scratch/1a8f428176e6/recovered_requirements/Tra/粘贴的文本 (1)(5).txt`

## Global Constraints

- Baseline is current `origin/main`; at plan creation it is `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`, descended from the prompt baseline `0ad52adcf59290e4f2b69523e3d51faa1455446b`.
- Work only on `codex/absolute-generalization-acceptance` in its linked worktree. Never reuse, revive, inspect for implementation, or modify `origin/codex/strategic-owner-continuity` or backups derived from it.
- Never run `git reset`, `git clean`, `git rebase`, force push, or any checkout/restore that discards work. Never overwrite unrelated user changes.
- `ProductionEngine.decide()` remains the single production decision entry; Risk remains the only `target_gross_cap` owner; `PortfolioAllocator` remains the only target owner; `AccountState` remains the only economic account owner.
- No second `AIUniverse`, Risk, allocator, capital budget, owner state machine, economic account, or direct position channel may be introduced.
- Production code must not import `scripts` or `research`; acceptance orchestration may call only public production/validation interfaces.
- No monkeypatch, research intervention, direct grant/Target/Order/Fill/epoch injection, future data, same-session non-causal fill, symbol fallback, parameter search, threshold relaxation, or baseline grandfathering of absolute limits.
- A production behavior change is allowed only after a real trace establishes the first divergence and a focused RED regression reproduces it.
- Window is exactly `2023-01-03` through `2026-08-05`; the matrix is exactly 34/34 canonical full-removal cells with zero missing, duplicate, or replay-error cells.
- Critical removals are exactly `sz300308`, `sz300502`, `sz300394`. Required witnesses are exactly `sh603688`, `sh688008`, `sh688082`, `sz002409`, `sz300666`.
- Fixed thresholds: witness fraction `1.0`; positive-return fraction `>= 0.90` where positive means `final_wealth > 1.0`; p10 wealth `>= 1.0`; p90 MDD `<= 0.30`; p90 healthy zero-total-target `<= 60`; worst `<= 120`; failed-grant retry `<= 20`; repair mappings `1→0/20`, `2→1/40`, `3→2/60`, `4→3/60`; terminal zero-strategic-target SCC `<= 60`.
- Repeated crowning requires at least two Fill-gated actual epochs and at least two distinct owners in one historical replay, plus the cross-industry production-semantic fixture.
- Frozen champion wealth is `24.509661802900865`, 95% floor is `23.28417871275582`, and champion MDD must remain `<= 0.30`.
- Historical lost Task 2 reported contract seal `d85d875e4bf30463a8dbd43130f50698d37e85c3720bd82e5301559cb182399a`; its exact preimage is unavailable and must never be fabricated. Under the user's explicit redo-from-Task-1 authorization, Task 2 freezes a new semantically identical contract against current baseline `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5` and records its newly computed canonical seal. `baseline_can_relax_absolute_limits` remains false; no absolute threshold, historical baseline, or champion identity may change.
- Static shards are exact and immutable: `loo-a = (sh600487, sh688019, sh688120, sh688300, sz002281, sz300394)`; `loo-b = (sh601869, sh688037, sh688146, sh688347, sz002371, sz300502)`; `loo-c = (sh603688, sh688041, sh688200, sh688361, sz002409, sz300604)`; `loo-d = (sh603986, sh688072, sh688233, sh688498, sz300054, sz300666)`; `loo-e = (sh688008, sh688082, sh688256, sh688766, sz300223)`; `loo-f = (sh688012, sh688110, sh688268, sz000636, sz300308)`. They cover each canonical symbol exactly once.
- Frozen baseline identities are production source `cacef64c25053a84e1aad073feec252d8cb9d2decb19576460642a3b6ec6573f` and ownership contract `72e6b510c3bcf44ac77d2c13613f4d72a14ae8dab0d60a19e5947055ae7cbf08`; current candidate/source identities may change only through the existing registry authority and must never rewrite these frozen baseline facts.
- Do not run Extended Performance Matrix or Extended Economic Matrix; both remain `workflow_dispatch` only. Skip the slow `architecture-portfolio` local shard per the user's explicit instruction; required normal CI still determines final merge eligibility.
- Use progressive L1→L4 verification. Run the full 34 matrix only after Tasks 7–8 and the champion/critical/witness/non-critical sentinels are ready.
- Each meaningful, coherent, basically verified checkpoint is committed and pushed to the remote feature branch; verify the remote SHA. Never push a broken checkpoint. SDD reports and ledgers remain ignored working material.
- Final green is exactly `passed = runner_success and capability_pass`; `capability_pass` is the conjunction of exactly: `champion_non_regression`, `absolute_strategic_robustness`, `failed_grant_recovery`, `witness_resilience`, `repeated_crowning`, `bounded_healthy_cash_vacancy`, `complete_literal_metrics`.

---

### Task 1: Re-establish the authoritative baseline

**Files:**
- Inspect: `benchmarks/strategic_grant_acceptance_contract.json`
- Inspect: `benchmarks/strategic_ownership_acceptance_contract.json`
- Inspect: `scripts/run_strategic_grant_acceptance.py`
- Inspect: `scripts/run_strategic_ownership_acceptance.py`
- Report only: SDD `task-1-report.md` (ignored; no tracked production edit)

**Interfaces:**
- Consumes: current clean `origin/main`, frozen data, existing grant/ownership runners.
- Produces: exact current champion, report-13, critical-removal, repair, grant, epoch, owner, and provenance facts used to distinguish pre-existing behavior from regressions.

- [ ] **Step 1: Verify the branch identity and frozen inputs**

Run `git status --porcelain=v2 --branch`, `git rev-parse HEAD HEAD^{tree} origin/main`, `git merge-base HEAD origin/main`, frozen-data integrity, and the contract validators. Record exact outputs in the ignored report.

- [ ] **Step 2: Run the focused grant and ownership model baseline**

Run the exact test lists from `.github/workflows/strategic-grant-acceptance.yml` and the `ownership-tests` job. A failure is baseline evidence to diagnose; do not edit production behavior in Task 1.

- [ ] **Step 3: Run the production acceptance sentinels**

Run `scripts/run_strategic_grant_acceptance.py`, then ownership shards `champion` and `critical`. Store output under a temporary directory, not in Git.

- [ ] **Step 4: Reconcile the historical invariants**

Confirm or explicitly report divergence from champion wealth `24.509661802900865`, champion MDD `0.27146973146234554`, remove-`sz300308` wealth `1.2632209078168604` with owner `sh601869` and repair `60/60`, and remove-`sz300394` wealth `7.809998638641716` with owner `sz300502`.

- [ ] **Step 5: Publish the recoverable branch anchor**

If the baseline is coherent, push the branch without fabricating a tracked report and verify `refs/heads/codex/absolute-generalization-acceptance` equals local HEAD. Task 1 itself needs no empty commit.

---

### Task 2: Freeze the absolute contract and canonical 34 scenarios

**Files:**
- Create: `benchmarks/absolute_generalization_acceptance_contract.json`
- Create: `uquant/validation/absolute_generalization/__init__.py`
- Create: `uquant/validation/absolute_generalization/contract.py`
- Create: `uquant/validation/absolute_generalization/scenarios.py`
- Modify: `benchmarks/source_surface_registry.json`
- Modify: directly affected public/reference governance files if the current registry requires them
- Create: `tests/test_absolute_generalization_contract.py`
- Create: `tests/test_absolute_generalization_scenarios.py`

**Interfaces:**
- Consumes: canonical `AIUniverse`, `benchmarks/strategic_ownership_acceptance_contract.json`, strict/canonical JSON helpers, frozen source/config/data identities.
- Produces: `AbsoluteGeneralizationContract`, `AbsoluteGeneralizationScenario`, `load_absolute_generalization_contract(path)`, and `build_leave_one_out_scenarios(contract) -> tuple[AbsoluteGeneralizationScenario, ...]`.

- [ ] **Step 1: Write RED contract tests**

Test exact field sets, duplicate-key rejection, NaN/Infinity rejection, tamper rejection, compiled seal equality, `baseline_can_relax_absolute_limits is False`, exact window/components/thresholds/critical/witness/shards, and equality with the canonical 34-member production/ownership universe.

- [ ] **Step 2: Write RED scenario tests**

Assert exactly 34 sorted unique cells; each removes one and only one canonical symbol; static shards are disjoint and complete; critical/witness flags use fixed membership; no runtime selection or random subdivision exists.

- [ ] **Step 3: Implement strict immutable contract loading**

Use duplicate-detecting JSON parsing, finite-number validation, canonical serialization, a compiled `ABSOLUTE_GENERALIZATION_CONTRACT_SHA256`, and exact identity verification. Expose no writer or auto-accept path.

- [ ] **Step 4: Implement canonical scenario construction**

Define a frozen scenario record carrying `cell_id`, `removed_symbol`, window, shard, critical/witness flags, and contract identity. Construct from the validated contract only, then independently check 34/34 coverage.

- [ ] **Step 5: Register the reviewed source surface**

Update the current registry according to its canonical rules and recompute only the candidate registry seal; do not alter frozen baseline/absolute thresholds.

- [ ] **Step 6: Verify and checkpoint**

Run the two focused test files, affected contract/source/public-API gates, Ruff and strict MyPy for touched modules, `compileall`, and `git diff --check`; commit a coherent Task 2 checkpoint, push, and verify remote SHA.

---

### Task 3: Centralize the sole linear quantile authority

**Files:**
- Create: `uquant/validation/statistics.py`
- Modify: `uquant/validation/generalization_matrix_evidence.py`
- Modify: `uquant/validation/generalization_policy/tail_evaluation.py` and direct callers using duplicate percentile logic
- Create: `tests/test_validation_statistics.py`
- Modify: affected existing generalization tests

**Interfaces:**
- Consumes: finite numeric sequences and a probability in `[0, 1]`.
- Produces: `linear_quantile(values: Sequence[float], probability: float) -> float`, using sorted values and interpolation at `(n - 1) * probability`.

- [ ] **Step 1: Write RED quantile tests**

Cover n=1, n=2, odd/even samples, p=0.10, p=0.90, p=0, p=1, order independence, empty input, out-of-range probability, booleans, NaN, and Infinity.

- [ ] **Step 2: Implement the minimal finite linear interpolation owner**

Reject invalid inputs before sorting; calculate lower/upper indices from `(n - 1) * p`; interpolate without nearest-rank/midpoint/lower/higher behavior.

- [ ] **Step 3: Replace duplicate production-validation implementations**

Make matrix and old policy tails import the single function. Preserve public compatibility aliases only where existing API contracts require them; remove copied formulas.

- [ ] **Step 4: Verify and checkpoint**

Run focused statistics, old matrix/policy tests, exact affected architecture/import/public-API gates, Ruff, strict MyPy, `compileall`, and diff check; commit, push, and verify remote SHA.

---

### Task 4: Add causal point-in-time full-removal replay

**Files:**
- Create: `uquant/validation/absolute_generalization/replay.py`
- Modify: `uquant/application/decision.py` only where a public production replay projection is genuinely missing
- Modify: directly related market/universe cache identity modules if required by evidence
- Create: `tests/test_absolute_generalization_replay.py`
- Modify: `tests/test_engine_contracts.py` only for shared role semantics

**Interfaces:**
- Consumes: `AbsoluteGeneralizationScenario`, production `AIUniverse`, point-in-time membership, `ProductionEngine`, frozen data, and next-open execution.
- Produces: `run_absolute_generalization_replay(scenario, *, root, data_dir, cache_dir) -> AbsoluteGeneralizationReplay`, with removed-symbol role absence and full raw production ledgers.

- [ ] **Step 1: Write RED full-removal and PIT tests**

Prove the removed symbol is absent from tradable, qualification-reference, risk-reference, panel loading, role declaration, and cache/data identity only on sessions where it would otherwise be effective. Prove future members do not enter earlier denominators.

- [ ] **Step 2: Write RED negative controls**

Keep a symbol in a role while withholding required data and assert `EXPECTED_BUT_UNAVAILABLE`, fail-closed qualification, and no authorization/grant/Target/Order/Fill/epoch. Distinguish this from intentional `ROLE_ABSENT`.

- [ ] **Step 3: Implement replay orchestration through production authorities**

Use `ProductionEngine.decide` and the existing execution-aware next-open path. Do not import research/scripts, inject events, or create a second economic state. Bind point-in-time role declarations and the absence set into provenance/cache identity.

- [ ] **Step 4: Prove default-path non-regression**

With no removal, verify the champion decision/equity path and identities remain unchanged; with a removal, verify the removed symbol is never loaded or emitted as owner/Target/Order/Fill/epoch.

- [ ] **Step 5: Verify and checkpoint**

Run focused replay tests, directly affected engine/market/account/execution regressions, champion sentinel, contract/source/import gates, Ruff, strict MyPy, `compileall`, and diff check; commit, push, and verify remote SHA.

---

### Task 5: Derive complete literal metrics, artifacts, identities, accounting, and epoch facts

**Files:**
- Create: `uquant/validation/absolute_generalization/metrics.py`
- Create: `uquant/validation/absolute_generalization/artifacts.py`
- Modify: `scripts/run_strategic_ownership_acceptance.py` to consume public validation helpers instead of owning duplicate definitions
- Create: `tests/test_absolute_generalization_metrics.py`
- Create: `tests/test_absolute_generalization_artifacts.py`

**Interfaces:**
- Consumes: `AbsoluteGeneralizationReplay`, Target/Order/Fill/account/grant/epoch/repair/role ledgers and frozen provenance.
- Produces: immutable `EpochFact`, `RepairEpisodeFact`, `CellMetrics`, `IdentityEnvelope`, `CellArtifact`; `derive_cell_metrics(replay, scenario, identities) -> CellArtifact`; `validate_cell_artifact(raw, contract) -> CellArtifact`.

- [ ] **Step 1: Write RED complete-metric tests**

Require, at minimum, finite/recomputable `initial_cash`, `final_equity`, `final_wealth`, `total_return`, `max_drawdown`, `account_orders`, `fill_count`, `gross_turnover`, `annual_turnover`, `realized_pnl`, `open_pnl`, `cash_drag`, `top1_concentration`, `top3_concentration`, `pnl_hhi`, both positive-target session counts and first sessions, both longest healthy zero-target streaks, qualification/grant/order/fill/epoch sessions and counts, distinct owners/owner symbols, complete epoch rows, complete repair episode rows, role coverage/witness facts, and transition/retry/SCC facts. Represent non-applicable event facts explicitly with exactly `applicable/observed/healthy_sessions/reason`, never null.

- [ ] **Step 2: Write RED cross-ledger identity/accounting tests**

Reject orphan/mismatched/duplicated Target→Order→Fill→grant→epoch chains, inconsistent account equity/PnL/cash, duplicate physical IDs, wrong removed owner, stale/tampered identity, fabricated replay-error metrics, and self-asserted pass fields.

- [ ] **Step 3: Implement validation-owned derivation**

Move reusable epoch, date, healthy-zero-target, repair-ready, uniqueness, and accounting calculations out of the script. Preserve complete actual Fill-gated epoch facts and both total and strategic target streaks.

- [ ] **Step 4: Implement strict artifact serialization and validation**

Canonicalize and seal cells/manifests; bind `cell_id`, `removed_symbol`, window, scenario-contract SHA, HEAD, tree, production-source SHA, effective-config SHA, `uv.lock` SHA, frozen-data-manifest SHA, universe SHA, industry-mapping SHA, tradable/qualification-reference/risk-reference role identities, and execution-contract identity. Keep replay-error facts explicit, non-applicable, and metric-free.

- [ ] **Step 5: Convert ownership orchestration to the public helpers**

The script remains thin and produces equivalent current evidence. `uquant` must never import the script.

- [ ] **Step 6: Verify and checkpoint**

Run focused metrics/artifact tests, ownership evidence/runner/contract/replay regressions, accounting and architecture/source/import gates, Ruff, strict MyPy, `compileall`, and diff check; commit, push, and verify remote SHA.

---

### Task 6: Project production health and bounded strategic reachability

**Files:**
- Create: `uquant/validation/absolute_generalization/reachability.py`
- Modify: `uquant/portfolio/strategic/rearm.py` or `rearm_predicates.py` only after a trace-backed RED proves missing reusable authority
- Create: `tests/test_absolute_generalization_reachability.py`

**Interfaces:**
- Consumes: immutable production snapshots and observed consecutive production transitions.
- Produces: `project_flat_book_repair_health(...)`, `project_qualification_opportunity_health(...)`, `is_positive_strategic_outlet(...)`, and `analyze_terminal_scc(...)`, all fail-closed on malformed/UNKNOWN input.

- [ ] **Step 1: Write RED projection tests**

Cover repair health requiring: all-cash/no positive positions; no pending, UNKNOWN, unsettled, or late-fill execution; no ACTIVE/nonterminal epoch; no nonterminal grant or real capital authority; normalized orphan residue; `Risk.NORMAL`, zero risk votes, positive gross cap, shock `NONE`; repaired transition damage; no sector/strategic/acute/sentinel/chronic guard; opportunity `TREND` or `STRONG_TREND`; complete usable risk references; and only a repairable freeze/budget deployment block. Prove it is independent of candidate/leader/qualification identity. Qualification-opportunity health reuses the safe account/risk/reference predicates but does not require a repairable block and additionally requires route-consistent qualification-ready, tradable candidate, complete qualification references, no incumbent authority, and no pending/UNKNOWN execution. Cover duplicate-session behavior, reset causes, READY persistence, one-shot authorization, and repair mappings `20/40/60/60` without off-by-one.

- [ ] **Step 2: Write RED strategic-outlet tests**

Require positive allocator weight, STRATEGIC origin identity, current grant, current epoch candidate, and reconciled order/fill chain. Prove ordinary targets, qualification-ready states, and unfilled PROBE epochs do not count.

- [ ] **Step 3: Write RED finite-transition and hostile-runtime tests**

Reject malformed nested repair containers, fabricated submitted/acknowledged grant IDs, impossible status/event pairs, unsupported mutation, non-finite/huge state groups, and UNKNOWN edges. Accept broker Fill without local acknowledgement, post-COMPLETED same-grant epoch orders, real blocker events, and legal production no-Fill marks.

- [ ] **Step 4: Implement exact observed-production authority**

Use finite runtime type guards, exact order status/last-event combinations, `S ⊆ Lg`, `A ⊆ S`, unique resolvable IDs, legal position/tranche mark transitions, and production predicate reuse. Do not hand-author theoretical success edges.

- [ ] **Step 5: Implement terminal SCC analysis**

Build deterministic state nodes/edges only from observed transitions, classify positive strategic exits, compute maximum terminal zero-strategic-target duration, and fail if it exceeds 60 or cannot be bounded.

- [ ] **Step 6: Verify and checkpoint A**

Run all Task 6 focused tests, affected production regressions, Tasks 2–5 contract/metrics/artifact/source/import gates, Ruff, strict MyPy, `compileall`, and diff check. Skip only the user-excluded slow `architecture-portfolio` local shard. Obtain independent task review, commit/push, verify remote SHA, and record Checkpoint A once Tasks 2–6 are clean.

---

### Task 7: Implement the seven-component policy and independent aggregation

**Files:**
- Create: `uquant/validation/absolute_generalization/policy.py`
- Create: `uquant/validation/absolute_generalization/aggregation.py`
- Modify: `uquant/validation/absolute_generalization/__init__.py`
- Create: `tests/test_absolute_generalization_policy.py`
- Create: `tests/test_absolute_generalization_acceptance.py`

**Interfaces:**
- Consumes: validated champion/recovery/reachability/cross-industry facts and all static shard manifests plus `AbsoluteGeneralizationContract`.
- Produces: immutable `ComponentResult`, `AcceptanceReport`, and `aggregate_acceptance(shard_manifests, contract) -> AcceptanceReport`.

- [ ] **Step 1: Write RED component-policy tests**

Independently test champion floor/MDD/path, all critical literal gates, 34-cell absolute robustness, fixed witness denominator 5/5, failed-grant successor within 20 healthy sessions, two Fill-gated historical epochs/two owners plus cross-industry semantics, all four repair bounds, terminal SCC <=60, and complete literal metrics.

- [ ] **Step 2: Write RED aggregate trust-boundary tests**

Reject missing/duplicate/unexpected shards or cells, upstream failure, replay error, malformed/tampered/stale provenance, incomplete metrics, duplicate IDs, fabricated metrics on error cells, inconsistent summaries, cache `passed=true`, and raw-cell or shard self-asserted pass/capability fields.

- [ ] **Step 3: Implement literal policies without baseline relaxation**

Use `linear_quantile` over exactly all 34 validated cells. Positive return is strictly `final_wealth > 1.0`; no failed/loss cells are removed from the denominator.

- [ ] **Step 4: Implement independent aggregation**

Recompute cells, counts, fractions, quantiles, worst cases, seven components, `runner_success`, and `capability_pass`. Set `passed` only as `runner_success and capability_pass`; no alternate path may set it.

- [ ] **Step 5: Verify and checkpoint**

Run both new files plus Tasks 2–6 tests and directly affected old policy/matrix/architecture/source/import gates; run Ruff, strict MyPy, `compileall`, and diff check. Obtain independent review, commit, push, and verify remote SHA.

---

### Task 8: Add the thin runner and blocking workflow

**Files:**
- Create: `scripts/run_absolute_generalization_acceptance.py`
- Create: `.github/workflows/absolute-generalization-acceptance.yml`
- Modify: the existing Extended Economic Matrix workflow final diagnostic name only if it still conflicts
- Create or modify: workflow/runner contract tests, including `tests/test_absolute_generalization_acceptance.py`

**Interfaces:**
- Consumes: the public contract/scenario/replay/artifact/reachability/aggregation package.
- Produces: deterministic shard manifests and final report; CLI exits zero only for `report.passed`; GitHub final check is exactly `Generalization Acceptance`.

- [ ] **Step 1: Write RED runner/workflow contract tests**

Assert supported shard names, deterministic static membership, `fail-fast: false`, `if: always()` aggregation, upstream failure propagation, artifact/cache identities, run_id/run_attempt/shard names, triggers `pull_request`, `push` main, `workflow_dispatch`, and the exact final check name.

- [ ] **Step 2: Implement thin CLI orchestration**

Parse shard/output/cache arguments, call package interfaces, always emit a compact sealed manifest (including error manifests), and never own policy/economic definitions or accept cached pass assertions.

- [ ] **Step 3: Implement workflow sharding and aggregation**

Use champion, loo-a..loo-f, recovery-and-reachability, and final aggregation jobs. Upload artifacts on failure and have the final job independently require every upstream result and manifest.

- [ ] **Step 4: Preserve manual-only Extended workflows**

Do not add automatic triggers. Rename only the old conflicting terminal check to `Extended Economic Matrix Diagnostics`.

- [ ] **Step 5: Verify and checkpoint**

Run runner/workflow/CI-contract tests, import/source/public API/repository governance tests, Ruff, strict MyPy, `compileall`, and diff check. Obtain independent review, commit, push, and verify remote SHA.

---

### Task 9: Execute progressive economic acceptance and fix only proven production gaps

**Files:**
- Modify: production files only if a first-divergence trace and RED regression require the minimal fix
- Modify: directly covering tests for any such fix
- Generate: replay/manifests/traces under temporary artifact paths only; never commit them

**Interfaces:**
- Consumes: stable Tasks 2–8 candidate and frozen data.
- Produces: verified champion, critical, witness, representative shard, 34/34 matrix, recovery/reachability, and final aggregation evidence.

- [ ] **Step 1: Run champion**

Require champion wealth >= `23.28417871275582`, MDD <=0.30, no unexplained path/identity drift, and both existing strategic acceptances' invariant checks.

- [ ] **Step 2: Run three critical removals**

Require each literal economic/execution/epoch/cash-vacancy gate. On failure, stop expansion and trace the earliest transition divergence.

- [ ] **Step 3: Run five witness removals**

Require fixed denominator 5, five production recoveries, real Fill-gated epochs, wealth >1, MDD <=0.30, and zero intervention.

- [ ] **Step 4: Run one representative non-critical LOO shard**

Validate runner schema, artifact upload/readback, accounting, identity, metrics, and aggregation before the full matrix.

- [ ] **Step 5: Run complete 34 LOO exactly once on a stable candidate**

Require 34 valid, zero error/missing/duplicate, 34 complete metrics, all strategic outlets/epochs, and fixed literal distribution gates.

- [ ] **Step 6: Run recovery/reachability and final aggregation**

Require retry <=20, repair `20/40/60/60`, terminal SCC <=60, repeated crowning, seven components true, and exact final conjunction.

- [ ] **Step 7: Apply trace-backed fixes if necessary**

For each real failure: write one focused RED, make the smallest production-semantic fix without thresholds/fallbacks/intervention, rerun the RED, failed cell, champion, affected shard, then repeat the full matrix only if the change invalidates it.

- [ ] **Step 8: Checkpoint B/C evidence-bearing code**

Once the stable code producing all required evidence is reviewed and verified, commit any remaining coherent changes, push, verify remote SHA, and record Checkpoints B and C without committing generated traces.

---

### Task 10: Close the PR, required CI, squash merge, verification, and cleanup

**Files:**
- Modify only if final review/CI finds a real defect
- Update: PR body as the dynamic task state; do not add repository checkpoint/handoff/release documents

**Interfaces:**
- Consumes: clean pushed final candidate and all verified evidence.
- Produces: non-Draft PR, required green checks, squash-merged `main`, verified remote/local main, and removed feature branch/worktree.

- [ ] **Step 1: Run final local gates on the stable candidate**

Run required affected Engineering gates excluding only the explicitly skipped slow local `architecture-portfolio` shard, Strategic Grant Acceptance, Strategic Ownership Acceptance, final Generalization Acceptance, Ruff, strict MyPy, `compileall`, frozen-data/Future Holdout/Sentinel/security/dependency/Windows-contract/source-surface/public-API/repository-governance checks, and `git diff --check`. Never run either Extended Matrix.

- [ ] **Step 2: Obtain the broad whole-branch review**

Review from merge-base through HEAD for spec compliance, economic authority, fail-closed trust boundaries, security, and maintainability. Resolve every load-bearing finding through one reviewed fix wave.

- [ ] **Step 3: Push final candidate and create a normal PR**

Verify local/remote SHA equality. Create a non-Draft PR whose body reports the seven components, 34 LOO, critical/witness/recovery/repeated-crowning/repair/SCC/champion evidence, exact local tests, and explicitly states both Extended workflows were not run and remain manual-only.

- [ ] **Step 4: Resolve required PR checks and reviews**

Wait for and fix `Generalization Acceptance`, `Strategic Grant Acceptance`, `Strategic Ownership Acceptance`, and normal Engineering gates. Do not wait for or trigger either Extended workflow. Do not bypass protections.

- [ ] **Step 5: Squash merge and verify main**

After all normal blockers are green, squash merge using GitHub, then verify the remote `main` SHA/tree and required main-push checks.

- [ ] **Step 6: Clean up only after verified merge**

Update local main by normal fast-forward, verify it matches remote, remove the remote/local feature branch and linked worktree using non-destructive normal Git operations, and confirm no task branch/worktree remains. Do not create a tag, release, version bump, changelog, handoff, or migration history.
