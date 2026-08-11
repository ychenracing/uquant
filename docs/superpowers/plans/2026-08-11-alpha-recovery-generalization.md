# Alpha Recovery and Generalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the identified choppy, mixed, and continuous economics while improving causal leader discovery and completing generalization validation.

**Architecture:** Preserve the single `ProductionEngine`. Introduce shared causal reference evidence, two-stage leader scoring, family-level risk arbitration, simplified strategic admission, residual industry inference, point-in-time reference governance, and a production-backed research runner.

**Tech Stack:** Python 3.11+, pandas, NumPy, pytest, Hypothesis, uv, ruff, mypy.

## Global Constraints

- Return improvement is prioritized over drawdown improvement, then account-order reduction.
- All production parameters are shared across pools and regimes.
- New production behavior is default-on only after economic gates pass.
- No symbol-specific alpha, risk, recovery, or reference exception for the historical optical trio.
- No black-box ML, multiple production engines, or high-turnover sleeve architecture.

---

### Task 1: First-divergence and attribution runner

**Files:** Create `research/candidate_runner.py`; modify `research/__init__.py`; test `tests/test_research.py`.

**Interfaces:** Produce `DecisionTrace`, `CandidateRunner.trace_cell`, and `first_divergence` over immutable daily decision records.

- [x] Write tests proving canonical traces include date, opportunity, risk, family votes, guard, budget, leaders, strategic tag, targets, orders, fills, and equity.
- [x] Run focused tests and confirm the missing API fails.
- [x] Implement the smallest production-backed trace runner without adding a production route.
- [x] Compare current, imported baseline, and intermediate checkpoints for A/2024, C/2023, and B/continuous.
- [ ] Commit the independently testable runner.

### Task 2: Shared group-balanced ReferenceContext

**Files:** Create `uquant/reference.py`; modify `uquant/engine.py`, `uquant/opportunity.py`, `uquant/risk.py`; test `tests/test_industry_rotation.py`, `tests/test_risk_transitions.py`.

**Interfaces:** Produce immutable `ReferenceContext` with name/group breadth, dispersion, correlation, industry strength, global strength, coverage, and evidence details.

- [x] Write failing tests for unequal industry sizes and point-in-time coverage.
- [x] Verify the current opportunity result is name-count biased.
- [x] Implement one daily reference calculation and inject it into opportunity and risk.
- [x] Verify risk and opportunity report identical breadth values.
- [x] Run sentinel replays and retain only non-regressive behavior.

### Task 3: Evidence-family risk arbitration

**Files:** Modify `uquant/risk.py`, `uquant/types.py`, `uquant/report.py`; test `tests/test_risk_transitions.py`, `tests/test_lifecycle_and_risk.py`.

**Interfaces:** `RiskAssessment.evidence` exposes `evidence_families`, `family_votes`, and `family_vote_count`; legacy `votes` equals the family count.

- [x] Write failing tests showing correlated breadth/MA/sector signals yield one structure-family vote.
- [x] Write a failing test showing a true crash across independent families still escalates.
- [x] Replace raw indicator counting with six capped families.
- [x] Keep continuous transition severity but remove its duplicate authority.
- [x] Re-run A/2024, C/2023, B/continuous, E/bull, E/July, and D/continuous.

### Task 4: Two-stage same-day leader pipeline

**Files:** Modify `uquant/leader.py`, `uquant/engine.py`, `uquant/opportunity.py`; test `tests/test_data_and_leader.py`, `tests/test_engine_contracts.py`.

**Interfaces:** `compute_structural_leaders(...)`, `apply_opportunity_alpha(..., opportunity=...)`, and `apply_leader_tenure(...)` are separate; compatibility `compute_leaders` remains available.

- [x] Write failing tests that same-day TREND to CHOPPY and CHOPPY to RECOVERY use the current factor profile.
- [x] Write a failing test proving tenure increments once per decision.
- [x] Implement structural scoring, classify with structural leaders, apply current alpha, then tenure once.
- [x] Update cache keys to exclude stale account opportunity.
- [x] Run focused and sentinel regression tests.

### Task 5: Strategic admission simplification and adaptive cohort size

**Files:** Modify `uquant/portfolio_strategic.py`, `uquant/config.py`, `uquant/account.py`; test `tests/test_lifecycle_and_risk.py`, `tests/test_config_contracts.py`.

**Interfaces:** Admission state is `SECULAR` or `EMERGING_SECULAR`; attribution tags describe evidence. Cohort selection may return one to three members with evidence-conditioned gross caps.

- [x] Write failing tests for removal of route-specific production ownership.
- [x] Write failing tests for qualified 3-, 2-, and exceptional 1-name cohorts and weak-leg rejection.
- [x] Implement unified admission scores and evidence tags.
- [x] Apply stricter confirmation and 80-90% gross for two names, 45-55% for one exceptional leader.
- [ ] Run no-optical and industry-only sentinel replays before accepting defaults.

### Task 6: Industry inference 2.0 and hierarchical shrinkage

**Files:** Modify `uquant/leader.py`, `uquant/industry.py`; test `tests/test_industry_rotation.py`, `tests/test_data_and_leader.py`.

**Interfaces:** Unknown-industry inference returns residual-correlation confidence with 60/120-day stability; industry scores expose shrunk and raw strength.

- [x] Write failing tests where common tech beta would otherwise misclassify a symbol.
- [x] Write failing tests for 60/120 disagreement and small-group shrinkage.
- [x] Residualize stock and basket returns against the tech index.
- [x] Require stable window evidence and apply `n / (n + k)` shrinkage.
- [x] Re-run arbitrary-universe and industry-only tests.

### Task 7: Point-in-time reference registry

**Files:** Create `benchmarks/reference_registry.json`, `uquant/reference_registry.py`; modify `uquant/leader.py`, `uquant/engine.py`, manifest/provenance validation; add tests.

**Interfaces:** Resolve reviewed members by `effective_from <= as_of < effective_to`, validate source and review status, and reject future leakage.

- [x] Write failing registry schema, date-boundary, duplicate, and leakage tests.
- [x] Encode the current stable universe with historically valid effective dates.
- [x] Use registry membership in daily and replay reference loading.
- [x] Fingerprint registry content in validation provenance.
- [ ] Verify current-window behavior is unchanged before promotion.

### Task 8: Research statistics and production-backed candidate matrix

**Files:** Create `research/statistics.py`, `research/candidate_runner.py`; modify `research/candidate_search.py`, `research/__init__.py`; test `tests/test_research.py`.

**Interfaces:** Produce walk-forward folds, fold instability, PBO and deflated-Sharpe diagnostics; execute one config through `ProductionEngine` over the declared matrix.

- [x] Write failing deterministic fold/PBO/DSR tests including insufficient-sample errors.
- [ ] Add penalties to candidate evaluation without hard-coding experimental weights into production.
- [x] Enforce complete matrix cells and shared `SystemConfig` construction.
- [ ] Attach trade attribution and generalization observations.
- [x] Verify research never writes production configuration or references.

### Task 9: Generalization-by-regime and reviewed evidence

**Files:** Modify `uquant/validation/generalization.py`, CLI and tests; create reviewed reference only from completed observations.

**Interfaces:** Fixed diagnostics run all seven regimes; random 6/12/24 sets run 50 seeds across bear, choppy, bull, acute, and continuous windows.

- [ ] Write failing completeness and cross-regime aggregation tests.
- [ ] Add deterministic scenario expansion and tail metrics.
- [ ] Run remove-one, remove-all-three, no-optical, industry-only, balanced, leave-top-k, and random matrices.
- [ ] Freeze a provenance-complete baseline only if every observation is real and reviewed.
- [ ] Reject placeholder or self-referential references.

### Task 10: Competitor 105-cell and final promotion

**Files:** Modify competitor/promotion validators, benchmarks, workflows, and synchronized docs.

**Interfaces:** Require 5 pools x 7 regimes x 3 competitors, calculate Pareto non-inferiority rate, and enforce bull/bear/choppy/acute/order policy.

- [x] Write failing 105-cell completeness and Pareto-rate tests.
- [ ] Generate same-execution competitor observations from pinned commits.
- [x] Run full 35-cell production promotion and economic hard gates.
- [ ] Run ruff, strict mypy, full pytest/coverage, compileall, build, Bandit, dependency audit, manifest, generalization, and competitor checks.
- [x] Synchronize code comments, configuration, README, architecture, strategy, performance, quality, development, and operations docs.
- [ ] Commit, publish directly to `main`, and verify GitHub status.
