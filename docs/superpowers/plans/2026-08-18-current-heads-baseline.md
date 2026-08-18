# Current HEAD Baseline and Evidence Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze a reproducible, fail-closed comparison of the current uquant, aquant, qwenquant, and trade HEADs without changing uquant production economics.

**Architecture:** Keep all new behavior in validation, external adapters, generated evidence, CI checks, and documentation. Execute each competitor from an immutable read-only source snapshot with its own dependency contract, normalize every result into one status-aware schema, and retain every preregistered cell even when replay fails or a sample is insufficient.

**Tech Stack:** Python 3.12, uv 0.11.33, pytest, Git, SHA-256, JSON, existing uquant Phase 1/Phase 2 validation infrastructure.

## Global Constraints

- Start from `ea24f1837f8b7f2d91e73a5d3c70875f2ea98015`, the remotely verified `origin/main` at task start.
- Work only on `codex/uquant-phase-1-current-head-baseline` until all gates pass.
- Do not implement P0-1 or change per-symbol limits, strategic-leader exceptions, concentration policy, or related parameters.
- Do not modify uquant production strategy, risk, portfolio, execution, account, or economic configuration.
- Treat `ychenracing/aquant`, `ychenracing/qwenquant`, and `ychenracing/trade` as read-only inputs; create no commits and push no refs there.
- Use the frozen A-share contract: CNY 2,000,000 cash-only, close-t signal, next-tradable-open execution, no intraday exit, forward-adjusted equities, unadjusted indices, T+1, board lots, limits, suspensions, fees, slippage, and capacity.
- Preserve all six official windows, acute windows, pools A-E, full/remove-core/no-optical/industry/sub-industry/fixed-random scenarios, and preregistered seeds.
- Never remove or overwrite `replay_error` or `insufficient_sample` cells.
- Never tune competitor parameters, replace production entrypoints, lower baselines, alter windows, or substitute old results.
- A failing acceptance gate preserves diagnostics and blocks publication to `main`.

---

### Task 1: Freeze the untouched uquant economic baseline

**Files:**
- Create: `artifacts/current_heads/baseline/uquant_phase1.json`
- Create: `artifacts/current_heads/baseline/uquant_phase2.json`
- Create: `artifacts/current_heads/baseline/decision_equivalence.json`
- Create: `artifacts/current_heads/provenance_report.json`
- Create: `docs/superpowers/plans/2026-08-18-current-heads-baseline.md`

**Interfaces:**
- Consumes: exact `origin/main` source, `data/frozen`, `uv.lock`, Phase 1 promotion runner, six Phase 2 matrix windows.
- Produces: content-addressed baseline evidence later tasks must reference without recomputation or manual metric entry.

- [x] **Step 1: Record immutable source and input identities**

Run `git rev-parse HEAD`, `git rev-parse HEAD^{tree}`, SHA-256 over `uv.lock`, configuration governance, production Python, manifest/checksums/data, and runtime package versions. Record values from commands only.

- [x] **Step 2: Run the clean Engineering baseline**

Run the repository's complete lint, type, test/coverage, compile, build, Bandit, dependency-export, and audit commands in the frozen uv environment. Preserve exact failures; do not weaken gates.

- [x] **Step 3: Run untouched Phase 1 and six-window Phase 2**

Run full promotion and one 39-record generalization shard for each official window. Aggregate exactly 234 Phase 2 records and retain the decision, target, order, fill, risk-state, concentration, turnover, acute-return, and tail evidence produced by the runners.

- [x] **Step 4: Prove the baseline is bound to the tested source**

Validate `production_commit`, source/config/data/runtime identities, cell counts, decision digests, account orders, fill identities, and final-account attribution. Reject hand-entered values and self-referential post-generation HEAD claims.

- [x] **Step 5: Commit, publish, and tag the milestone**

Commit as `test: freeze pre-sentinel economic baseline`, publish the branch checkpoint, and create `pre-risk-sentinel-20260818` only if the immutable tag does not already exist and its target is the tested source commit.

### Task 2: Add fail-closed current-HEAD contract and Source Registry

**Files:**
- Create: `benchmarks/current_heads_comparison_contract.json`
- Create: `benchmarks/current_heads_source_registry.json`
- Create: `uquant/validation/current_heads.py`
- Create: `tests/test_current_heads_comparison.py`

**Interfaces:**
- Consumes: four remote HEAD/tree identities, stable participating-source inventory, dependency lock inventory, adapter bytes, and frozen comparison contract.
- Produces: `load_current_heads_contract(path)`, `build_source_registry(...)`, `validate_source_registry(...)`, canonical hashing helpers, and explicit `success` / `replay_error` / `insufficient_sample` cell types.

- [ ] **Step 1: Write failing contract and registry tests**

Cover stable source hashing under directory-order changes; missing 40-character commit/tree or 64-character hashes; remote/source/lock/adapter drift; duplicate JSON keys; altered execution contract; altered window/seed/pool contract; and mutation after registry creation.

- [ ] **Step 2: Verify RED**

Run `uv run pytest tests/test_current_heads_comparison.py -q` and confirm failure because the current-head validation API and contract files do not exist.

- [ ] **Step 3: Implement the minimum fail-closed validation model**

Implement canonical byte hashing, immutable source inventory hashing, lock hashing with explicit missing-lock sentinel bytes, strict schema parsing, provenance equality, and mutually exclusive cell status validation. Keep production modules untouched.

- [ ] **Step 4: Verify GREEN and mutation cases**

Run the focused test file, then deliberately mutate fixture source/lock/adapter/commit inputs through tests and confirm each stale artifact is rejected.

- [ ] **Step 5: Generate the factual contract and registry**

Read all four remote current HEADs and trees, reconstruct exact read-only competitor execution sources, hash stable participating Python inventories and dependency locks, then write the JSON files from the validated builder.

- [ ] **Step 6: Commit and publish the milestone**

Commit as `test: bind current-head source provenance` and publish the branch checkpoint.

### Task 3: Implement isolated current-production replay and status retention

**Files:**
- Create: `scripts/run_current_heads_competitor_matrix.py`
- Modify: `scripts/run_window_competitor_adapter.py`
- Modify: `tests/test_current_heads_comparison.py`
- Modify: `tests/test_window_competitor_adapter.py`

**Interfaces:**
- Consumes: validated contract/registry, read-only source roots, isolated interpreter commands, normalized bounded market data, and preregistered cell definitions.
- Produces: one normalized cell per `(system, window, scenario)` with status, metrics/error, commit, data/config/runtime/evidence hashes, plus deterministic aggregates.

- [ ] **Step 1: Write failing adapter and matrix tests**

Cover missing columns, future rows, prelisting rows, empty results, NaN/Infinity, wrong commit, changed adapter, dependency-command mismatch, child-process failure, insufficient history, and preservation of all expected cell identities.

- [ ] **Step 2: Verify RED**

Run the focused current-head and adapter tests and confirm each new behavior fails for the intended missing implementation.

- [ ] **Step 3: Implement isolated runner boundaries**

Use subprocess argument arrays without a shell, explicit read-only source roots, isolated environment commands, bounded normalized data directories, timeout/error capture, strict JSON decoding, and no fallback to archived metrics or uquant strategy logic.

- [ ] **Step 4: Normalize and validate evidence**

Require finite `final_wealth`, `total_return`, `cagr`, `sharpe`, `calmar`, `max_drawdown`, `account_orders`, `gross_turnover`, `annual_turnover`, `acute_return`, `top1_concentration`, `top3_concentration`, and `pnl_hhi` for success cells. Preserve typed replay errors and insufficient samples with null metrics and complete provenance.

- [ ] **Step 5: Run 1-3 sentinel cells twice**

Exercise one normal, one insufficient-history, and one induced replay-error path before the full matrix. Require deterministic canonical output and evidence digests.

- [ ] **Step 6: Commit and publish the milestone**

Commit as `test: add isolated current-head replay` and publish the branch checkpoint.

### Task 4: Generate the complete matrix, candidate baseline, CI, and documentation

**Files:**
- Create: `benchmarks/current_heads_competitor_matrix.json`
- Create: `docs/reviews/2026-08-18-current-heads-baseline.md`
- Modify: `artifacts/current_heads/baseline/uquant_phase1.json`
- Modify: `artifacts/current_heads/baseline/uquant_phase2.json`
- Modify: `artifacts/current_heads/baseline/decision_equivalence.json`
- Modify: `artifacts/current_heads/provenance_report.json`
- Modify: `uquant/validation/ci_artifacts.py`
- Modify: `tests/test_ci_artifact_validation.py`
- Modify: `docs/PERFORMANCE.md`
- Modify: `.github/workflows/strategy-performance.yml`

**Interfaces:**
- Consumes: Tasks 1-3 artifacts and runners.
- Produces: complete per-cell matrix, median/worst/p10/p90 aggregates, old-vs-current diagnostic kept outside evidence, a pre-sentinel candidate baseline distinct from the historical champion, and CI readback validation.

- [ ] **Step 1: Run the preregistered complete matrix without filtering**

Execute all four systems across every contract cell. Write every success, replay error, and insufficient sample; never reduce the expected row set.

- [ ] **Step 2: Repeat and compare determinism**

Run the matrix a second time in the same clean environment and compare canonical outputs, allowing only explicitly configured floating tolerance in metric comparison while requiring exact identities/status/evidence.

- [ ] **Step 3: Write failing CI readback tests and verify RED**

Cover stale HEAD/tree/source/lock/adapter/data/config/runtime identities, missing/extra cells, status overlap, unreferenced evidence, and self-referential generated HEAD claims.

- [ ] **Step 4: Implement CI validation and verify GREEN**

Add a current-head artifact validator invoked by CI without touching any production path. Run focused CI/current-head/adapter tests.

- [ ] **Step 5: Document evidence boundaries and observed results**

State that historical cross-system results do not guarantee future performance, the current matrix is a pre-change baseline rather than an automatic promotion gate, and all replay errors/insufficient samples remain visible. Record actual identities, completeness, aggregates, and limitations without claiming uquant dominance when cells disagree.

- [ ] **Step 6: Commit and publish the milestone**

Commit as `test: freeze current-head competitor matrix` and publish the branch checkpoint.

### Task 5: Final acceptance, fast-forward publication, and remote readback

**Files:**
- Modify only generated/review artifacts whose provenance must bind to the final candidate; do not edit production or economic configuration.

**Interfaces:**
- Consumes: final candidate tree from Tasks 1-4.
- Produces: complete gate evidence, branch commit list, fast-forwarded `main`, and remote readback proof.

- [ ] **Step 1: Verify forbidden-path and economic equivalence invariants**

Compare the branch against the starting commit and require no changes to production strategy/risk/portfolio/execution/account/economic configuration. Compare Phase 1 decisions, targets, orders, fills, final account, and Phase 2 identities/metrics to the untouched baseline.

- [ ] **Step 2: Run the final Engineering gate once**

Run `ruff`, strict `mypy`, full pytest with at least 85% branch coverage, compileall, build, Bandit, locked dependency export, and pip-audit; record exact exit codes and counts.

- [ ] **Step 3: Run final Phase 1, Phase 2, and current-head matrix**

Run full promotion, all six 39-record generalization shards plus the 234-record aggregator, and the complete current-head matrix. Validate exact final candidate provenance and retain any failure diagnostics.

- [ ] **Step 4: Publish only a passing candidate**

Commit any final provenance rebind that does not require economic recomputation, publish the branch, verify `main` still equals the original fork point, and update `main` by non-force fast-forward only.

- [ ] **Step 5: Read back remote state**

Fetch remote `main`, verify its commit and tree equal the accepted candidate, fetch the key contract/registry/matrix/review files through GitHub, and report four HEADs, source/data/config/lock/runtime summaries, matrix completeness, baseline metrics, all gates, commit SHAs, and known limitations.
