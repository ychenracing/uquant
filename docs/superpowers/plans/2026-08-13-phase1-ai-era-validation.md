# UQuant Phase 1 AI-Era Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make UQuant's only production promotion contract evaluate 2023+ AI-industry data, restore the specified 2023–2026 economics without weakening risk protection, and bind deterministic evidence to the final production commit.

**Architecture:** Keep `ProductionEngine` as the single daily/backtest decision kernel. Centralize calendar windows, economic start enforcement, effective configuration, canonical decision hashing, and runtime provenance in small validation-facing contracts; reuse those contracts from promotion, research scripts, reports, and CI. Diagnose economic regressions with the existing trace/ablation path and change only the first causal divergence that is supported by a red regression test.

**Tech Stack:** Python 3.12, NumPy, pandas, pytest, Hypothesis, uv, Ruff, strict mypy, GitHub Actions.

## Global Constraints

- Economic validation begins no earlier than `2023-01-01`; earlier rows are warm-up only.
- Production remains A-share, AI-industry, cash-only, long-only, daily close decision, next-session execution, manually assisted.
- Priority is return, then drawdown, then transaction count, while preserving causality, determinism, and daily/backtest parity.
- Do not remove 2023+ scenarios, loosen thresholds, overwrite baselines with failures, add window-specific strategy rules, add ML/RL/LLM selection, or roll back a whole release.
- Preserve the improved 2024 drawdown/order count, 2023 order count, and 2026 acute-crash defense.
- Only merge and push `main` after Engineering Gate and AI-Era Performance Gate both pass.

---

### Task 1: Freeze the Execution-Time Baseline

**Files:**
- Create: `artifacts/phase1/before/environment.json`
- Create: `artifacts/phase1/before/effective-config.json`
- Create: `artifacts/phase1/before/ai-era-matrix.json`
- Create: `artifacts/phase1/before/ai-era-performance.json`

**Interfaces:**
- Consumes: `ProductionEngine.backtest`, `SystemConfig.to_dict`, frozen-data manifest, commit `685c600d0af5d85af87fb6553df81d4e4b10c358`.
- Produces: immutable before-change evidence used for first-divergence and final comparisons.

- [ ] Install exactly the current lock with `uv sync --frozen --extra dev` under Python 3.12.
- [ ] Record `git rev-parse HEAD`, `python --version`, `uv --version`, installed NumPy/pandas versions, `sha256sum uv.lock`, frozen manifest identity, and canonical `DEFAULT_CONFIG` JSON.
- [ ] Run the current Engineering Gate commands and save their exact outputs.
- [ ] Run every current economic validation path; retain failures instead of changing thresholds, and record which paths duplicate the future unified gate.
- [ ] Verify repository status contains only these new evidence/plan files, then commit the frozen baseline.

### Task 2: Establish the AI-Era Calendar and Measurement Contract

**Files:**
- Create: `uquant/validation/ai_era.py`
- Modify: `uquant/validation/promotion.py`
- Modify: `research/window_matrix.py`
- Test: `tests/test_ai_era_contract.py`
- Test: `tests/test_validation.py`
- Test: `tests/test_window_matrix.py`

**Interfaces:**
- Produces: `AI_ERA_START`, `AI_ERA_WINDOWS`, `AI_ERA_ACUTE_WINDOWS`, `require_ai_era_interval(start, end)`, and `runtime_environment_provenance()`.
- Consumes: common broad/technology trading calendars when resolving requested calendar bounds to executable sessions.

- [ ] Write a failing test proving all six official windows are present and every economic start is on/after 2023-01-01.
- [ ] Run the test and verify the missing centralized contract is the failure.
- [ ] Add the immutable window contract and make promotion/research scripts import it instead of maintaining duplicate dictionaries.
- [ ] Run the targeted tests and verify they pass.
- [ ] Write a failing engine-level test where pre-2023 rows affect MA/ATR warm-up but cannot affect initial equity, orders, fills, turnover, drawdown, Sharpe, or Calmar.
- [ ] Run it and verify the pre-start measurement leak is observed.
- [ ] Separate feature preload bounds from economic replay bounds in `ProductionEngine.backtest`; seed accounting at the first official session and filter all economic ledgers/statistics to that bound.
- [ ] Run the new contract tests and existing backtest/daily-kernel parity tests.
- [ ] Commit the calendar and measurement contract.

### Task 3: Remove Pre-2023 Economic Gates Without Removing Warm-up Data

**Files:**
- Modify: `benchmarks/promotion_baseline.json`
- Modify: `uquant/validation/promotion.py`
- Modify: `tests/test_validation.py`
- Modify: `tests/test_engine_contracts.py`
- Modify: `README.md`
- Modify: `docs/PERFORMANCE.md`
- Modify: `docs/OPERATIONS.md`

**Interfaces:**
- Consumes: centralized `AI_ERA_WINDOWS` and the reviewed 2023+ acceptance thresholds.
- Produces: one promotion profile vocabulary containing `h1_2023`, `h2_2023`, `h1_2024`, `h2_2024`, `bull_crash_2025_2026`, and `continuous_ai_era` only.

- [ ] Write failing validation tests that reject every economic scenario whose start precedes `AI_ERA_START`.
- [ ] Run them and confirm the current baseline/validator accepts the forbidden scenarios.
- [ ] Replace old scenarios/profiles/aggregate keys with the six official AI-era windows while preserving frozen acceptance values rather than copying current failures.
- [ ] Update engineering-invariant tests to use 2023+ dates; retain older rows only in fixtures that explicitly prove warm-up or point-in-time visibility.
- [ ] Run validation, AI-era calendar, engine-contract, and date-vocabulary tests.
- [ ] Search the repository for pre-2023 economic claims and classify every remaining hit as warm-up/data provenance or non-economic terminology.
- [ ] Commit the pre-2023 gate removal.

### Task 4: Lock the Numerical Runtime and Evidence Provenance

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `requirements.txt`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/strategy-performance.yml`
- Modify: `uquant/validation/promotion.py`
- Test: `tests/test_validation.py`
- Test: `tests/test_config_contracts.py`

**Interfaces:**
- Produces: exact Python 3.12 project range, locked NumPy/pandas versions, and provenance fields `python_full_version`, `numpy_version`, `pandas_version`, `uv_version`, `uv_lock_sha256`.

- [ ] Write failing provenance tests requiring every environment field and exact lock hash format.
- [ ] Run them and confirm the current report omits the fields.
- [ ] Set `requires-python = ">=3.12,<3.13"`, Ruff `py312`, exact production NumPy/pandas constraints, and regenerate `uv.lock` with Python 3.12.
- [ ] Collapse CI to Python 3.12 and keep every install as `uv sync --frozen`.
- [ ] Add runtime evidence generation and strict validation to promotion artifacts.
- [ ] Run lock consistency, config, provenance, lint, typing, and package-build tests.
- [ ] Commit the numerical runtime lock.

### Task 5: Make Effective Configuration Explicit and Decisions Deterministic

**Files:**
- Modify: `uquant/config.py`
- Modify: `uquant/engine.py`
- Modify: `uquant/report.py`
- Modify: `uquant/types.py` only if canonical decision serialization needs a public method.
- Test: `tests/test_config_contracts.py`
- Test: `tests/test_engine_contracts.py`
- Test: `tests/test_cli_and_report.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG` as the exact production policy, `effective_config_sha256`, and canonical Decision payload/hash.
- Consumes: one `SystemConfig` instance shared by leaders, reference, risk, opportunity, allocator, and execution planner.

- [ ] Write a failing test proving `ProductionEngine(...).cfg`, the decision config, and serialized effective config are identical for pool sizes 1 through 32.
- [ ] Run it and confirm `_decision_config_for_universe` is the semantic divergence.
- [ ] Move the four effective false values into `SystemConfig` defaults and reduce `_decision_config_for_universe` to an identity/diagnostic function.
- [ ] Write a failing integration test that runs the same fixed date/account/input twice on fresh engines and compares canonical Decision, targets, orders, risk/opportunity state, and config hash byte-for-byte.
- [ ] Run it and isolate any nondeterministic ordering/state mutation before modifying production code.
- [ ] Canonicalize only the proven nondeterministic boundary; emit the effective config hash in daily reports and backtest/promotion evidence.
- [ ] Run determinism repeatedly plus engine, execution, report, configuration, and state-round-trip suites.
- [ ] Commit explicit configuration and determinism.

### Task 6: Locate the Three Economic Regressions

**Files:**
- Modify: `research/first_divergence.py` only if a missing observation prevents locating the first branch.
- Modify: `research/ablation.py` only if an existing effective flag cannot be isolated.
- Create: `artifacts/phase1/diagnostics/first-divergence.json`
- Create: `artifacts/phase1/diagnostics/ablation.json`
- Create: `artifacts/phase1/diagnostics/phase1-history.bundle` so every recorded local evidence commit remains importable from a clean clone.
- Test: `tests/test_first_divergence.py`
- Test: `tests/test_research.py`

**Interfaces:**
- Consumes: reference commit `ea4fb1cef59256f76ef9f810440c87ef53108aa2` as the frozen economic anchor, candidate branch HEAD, identical frozen data/effective config, and the ordered comparison path `reference_context -> leaders -> risk -> opportunity -> targets -> orders -> fills`. If the anchor predates the trace adapter, retain it for thresholds and use the earliest source-compatible commit boundaries plus patch/config reverse ablations; the diagnostic artifact must record that deviation.
- Produces: one evidence-backed first divergence and one minimal hypothesis for each of 2025–2026 wealth/orders, 2023 drawdown, and 2024 wealth/turnover.

- [ ] Replay source-compatible commit boundaries and the candidate with the same Python 3.12 lock and official window inputs; retain `ea4fb1...` as the frozen economic reference when its source cannot emit the required daily trace.
- [ ] Compare aligned daily traces and record the first changed field/date, downstream trade attribution, and implicated commit/config flag.
- [ ] Run one-variable ablations for only the implicated mechanisms; do not add parameters or strategy state.
- [ ] State and test one hypothesis at a time; reject hypotheses whose ablation does not move the expected metric while preserving the protected metrics.
- [ ] If tracing lacks a required boundary, first add a failing trace-contract test, then add that observation without changing decisions.
- [ ] Commit diagnostic tooling changes separately from strategy behavior changes.

### Task 7: Repair the Proven First Divergence With Minimal Strategy Changes

**Files:**
- Modify: only the production module proven by Task 6 (`uquant/leader.py`, `uquant/risk.py`, `uquant/opportunity.py`, `uquant/portfolio*.py`, or `uquant/execution.py`).
- Test: the corresponding focused contract test plus `tests/test_lifecycle_and_risk.py` or `tests/test_recovery_contracts.py`.
- Create: `artifacts/phase1/candidates/ai-era-matrix.json`.

**Interfaces:**
- Consumes: the first-divergence date/field and a hand-derived regression fixture.
- Produces: general economic behavior that meets all protected-window gates without date/window-specific branches.

- [ ] Write the smallest failing behavioral test that reproduces the first divergence and names the invalid transition/order/target behavior.
- [ ] Run it and confirm it fails for the expected economic branch, not fixture setup.
- [ ] Make one minimal production change at the causal source.
- [ ] Run the focused test, the affected window, and the protected acute window.
- [ ] Repeat the scientific cycle for the next independently proven regression; stop after three failed fixes and reassess the architecture instead of stacking patches.
- [ ] Run the complete six-window matrix after every accepted candidate and retain only candidates satisfying all hard gates.
- [ ] Commit each independently validated root-cause repair.

### Task 8: Make AI-Era Performance the Only Blocking Economic CI Gate

**Files:**
- Modify: `uquant/validation/promotion.py`
- Modify: `uquant/validation/cli.py`
- Modify: `.github/workflows/strategy-performance.yml`
- Modify: `benchmarks/promotion_baseline.json`
- Test: `tests/test_validation.py`
- Test: `tests/test_validation_cli.py`

**Interfaces:**
- Produces: a blocking full matrix for six official windows and per-window protected thresholds, including 2026 acute return.

- [ ] Write failing tests proving every required window/pool is mandatory and a missing/failing cell fails closed.
- [ ] Run them and confirm the current parallel economic paths permit the gap.
- [ ] Integrate the official windows and policies into one promotion command; remove pre-2023 and every duplicate economic path from blocking CI.
- [ ] Keep 2023 drawdown `<= 0.2725`, 2024 wealth `>= 1.7314`, drawdown `<= 0.18`, turnover `<= 2.9965`, A/B/C bull wealth `>= 12.827` and orders `<= 11`, D/E bull wealth `>= 12.933`, orders `<= 13`, turnover `<= 14.451`, and acute return `>= -0.03` unless the existing frozen gate is stricter.
- [ ] Run the CLI fail-closed tests and full AI-Era Performance Gate.
- [ ] Commit the unified blocking gate only after the strategy already passes it.

### Task 9: Bind Final Evidence and Synchronize Documentation

**Files:**
- Generate after checkout (Git-ignored CI artifact): `benchmarks/ai_era_performance.json`
- Modify: `benchmarks/promotion_baseline.json`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/DEVELOPMENT.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PERFORMANCE.md`
- Modify: `docs/QUALITY.md`
- Modify: `docs/STRATEGY.md`
- Test: `tests/test_validation.py`

**Interfaces:**
- Produces: final artifact fields `production_commit`, `production_source_sha256`, `effective_config_sha256`, `data_snapshot_id`, `data_manifest_sha256`, `python_full_version`, `numpy_version`, `pandas_version`, `uv_version`, `uv_lock_sha256`, `generated_at`.

- [ ] Write failing promotion-generator contract tests that require every runtime-artifact binding field, bind commit/source to the clean checked-out HEAD, and reject dirty or source-mismatched inputs; final-job readback must assert the serialized `production_commit == HEAD`.
- [ ] Run it and confirm historical artifacts cannot prove the candidate.
- [ ] Generate the final Git-ignored artifact from the passing checked-out candidate, mark incompatible older artifacts historical, validate content-addressed evidence, and upload it from CI; do not commit the self-referential runtime artifact.
- [ ] Update all user documentation and examples to the six-window AI-era contract, explicit default configuration, Python 3.12, and one CI truth.
- [ ] Search for stale dates, scenario names, hidden-override claims, and unbound current-performance claims; fix every inconsistent statement.
- [ ] Run documentation-adjacent CLI tests and provenance validation.
- [ ] Commit the evidence contract and documentation, then regenerate the runtime artifact from that exact HEAD; any later HEAD change requires another regeneration.

### Task 10: Final Review, Verification, and Main Publication

**Files:**
- Review: all branch changes relative to `origin/main`.

**Interfaces:**
- Consumes: the report's 19-item acceptance checklist and all fresh gate outputs.
- Produces: verified main commit SHA and final before/after metrics.

- [ ] Run Ruff, strict mypy, frozen-data integrity, full pytest with branch coverage, compileall, package build, Bandit, pip-audit, and explicit determinism tests under Python 3.12.
- [ ] Run the complete AI-Era Performance Gate and capture 2023, 2024, bull/crash, acute, and continuous metrics.
- [ ] Review the full diff for correctness, security, causality, documentation consistency, and accidental pre-2023 economic logic.
- [ ] Re-read the phase-one checklist and map every item to fresh evidence; fix and rerun if any item fails.
- [ ] Rebase/fast-forward the branch onto the latest remote `main`; if HEAD changes, rerun both complete gates and regenerate the Git-ignored artifact, requiring `binding.production_commit == HEAD` without creating another commit afterward.
- [ ] Push the verified branch, merge only if all checks pass, push `main`, and read back remote `main` SHA and CI status.
