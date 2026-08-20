# Risk Sentinel Exclusive Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the reviewed Phase 6 causal confirmation as the sole new authority for Sentinel-exclusive `FREEZE_ONLY`, prove that it blocks incremental risk without gaining any sell, gross-cap, account-state, or position-reduction authority, and promote only if every economic gate passes.

**Architecture:** Pass the immutable, account-free `RiskEvidenceTimeline` through the existing `ProductionEngine -> assess_risk -> integrate_freeze_only` boundary. Non-severe Sentinel authority is computed only from the latest causal market row, trusted confirmation state, and comparable market-family deltas; the existing Portfolio and Execution freeze boundaries remain the only downstream behavior. A production-backed A/B runner compares the Phase 6 configuration with the locked candidate and preserves the first divergence, every exclusive-freeze event, and opportunity-cost diagnostics.

**Tech Stack:** Python 3.12, pandas, immutable dataclasses, pytest, uv, existing promotion/generalization validators, JSON evidence artifacts.

**Spec:** User-supplied `07_阶段7_Risk_Sentinel独立FREEZE_ONLY权限最终验证.md`.

## Global Constraints

- Start from remote `main` commit `711af1179aa72ce48ca3a6af58ecddb3a029a7ce`.
- The only candidate behavior switch is `risk_sentinel_causal_confirmation_enabled: False -> True`.
- Lock `FREEZE_ONLY`, confidence `0.80`, confirmation `2`, repair `3`, existing severe-direct `True`, and no gross cap before official economic replay.
- Historical authority families are exactly `market_velocity`, `breadth_structure`, and `covariance_stress`.
- `live_book_damage` and `capital_damage` remain current-day diagnostics and never establish historical confirmation or earlier evidence.
- Sentinel may only set the existing `RiskAssessment.freeze_new_risk`; it may not change risk state, gross cap, reduction level, shock state, capital budget, AccountState schema, targets, sells, quantities, orders, or fills directly.
- Preserve rotation atomicity, broker-visible cancellation truth, partial fills, healthy holdings, and all independent sell paths.
- Stop after the small gate if it fails. Do not search parameters or revisit gross-cap authority.
- Commit and push each independently meaningful milestone.

---

### Task 1: Freeze the Phase 6 baseline

**Files:**
- Create: `artifacts/sentinel/exclusive_freeze/phase6_baseline.json`
- Create: `docs/superpowers/plans/2026-08-20-risk-sentinel-exclusive-freeze.md`

**Interfaces:**
- Consumes: exact remote-main commit/tree, config and code fingerprints, data/universe identities, AccountState field list, and the complete Phase 6 test suite.
- Produces: an immutable comparison root for every Stage 7 artifact.

- [ ] Run `uv sync --frozen --extra dev` in the isolated worktree.
- [ ] Run `uv run pytest` and require zero failures.
- [ ] Record the exact starting commit/tree, baseline and enabled config fingerprints, code fingerprint, universe identity, locked parameter values, AccountState field names, and baseline test count.
- [ ] Validate the JSON with a focused artifact test or schema assertions.
- [ ] Commit as `test: freeze causal sentinel baseline` and push the target branch.

### Task 2: Lock the candidate and add the A/B evidence runner

**Files:**
- Create: `research/sentinel_exclusive_freeze.py`
- Create: `tests/test_sentinel_exclusive_freeze.py`
- Create: `artifacts/sentinel/exclusive_freeze/README.md`
- Create: `artifacts/sentinel/exclusive_freeze/candidate_lock.json`

**Interfaces:**
- Produces: `run_exclusive_freeze_comparison(...) -> dict[str, object]` and deterministic JSON containing first divergence, exclusive-freeze events, blocked buys, hard-authority counters, metrics, and 5/10/20-session counterfactual returns.
- Consumes: the sole production engine, `trace_backtest`, fixed symbols/window, and two `SystemConfig` objects that differ only in causal-confirmation authority.

- [ ] Write a failing test using a hand-built pair of daily traces; the expected first divergence must identify a blocked BUY and no SELL.
- [ ] Run `uv run pytest tests/test_sentinel_exclusive_freeze.py -q` and observe failure because the runner does not exist.
- [ ] Implement deterministic trace comparison and event summarization without mutating production state or treating counterfactual returns as accounting PnL.
- [ ] Add failing tests for non-comparable configs, unstable calendars, direct-sell detection, gross-cap drift, healthy-holding reduction, and missing non-severe value events.
- [ ] Implement fail-closed validation and literal `incremental_same_day`, `earlier_confirmed`, and `not_comparable` classifications.
- [ ] Create `candidate_lock.json` with the baseline commit, one enabled field, all fixed parameters, baseline/candidate fingerprints, forbidden-retuning flag, and prohibited authorities.
- [ ] Run runner tests plus `tests/test_phase6_evidence_artifacts.py`.
- [ ] Commit as `research: lock sentinel exclusive-freeze candidate` and push.

### Task 3: Wire causal history to the existing freeze-only authority

**Files:**
- Modify: `uquant/risk_sentinel/integration.py`
- Modify: `uquant/risk.py`
- Modify: `uquant/engine.py`
- Modify: `tests/test_risk_sentinel_integration.py`
- Modify: `tests/test_phase1_decision_equivalence.py`

**Interfaces:**
- `integrate_freeze_only(..., causal_timeline: RiskEvidenceTimeline | None = None) -> RiskAssessment`.
- `assess_risk(..., sentinel_causal_timeline: RiskEvidenceTimeline | None = None) -> RiskAssessment`.
- The engine passes its verified causal prefix before formal risk integration; disabled authority remains byte/economically equivalent to Phase 6.

- [ ] Write a failing test proving that a READY, confidence `0.90`, two-family, trusted two-day, non-severe incremental timeline freezes only with the enabled config.
- [ ] Run the focused test and verify the expected false-negative failure.
- [ ] Add failing table-driven tests for NOT_READY, confidence `0.79`, one family, untrusted history, one confirmation day, no incremental/earlier family, mismatched as-of date, base freeze, and bull-silent protection.
- [ ] Implement the minimal causal context wiring and fail-closed authorization using only the three comparable market families.
- [ ] Preserve the existing severe-direct narrow exception and every base field except `freeze_new_risk` plus evidence attribution.
- [ ] Add mutation-oriented assertions for unchanged state, gross cap, reduction, shock, capital budget, and no account carrier.
- [ ] Run integration, risk, engine, Phase 1 decision-equivalence, Phase 6 timeline, and config-governance tests.
- [ ] Commit as `feat: wire causal confirmation to freeze-only gate` and push while the default remains `False`.

### Task 4: Prove Rotation and Pending Buy atomicity

**Files:**
- Create: `tests/test_sentinel_rotation_atomicity.py`
- Create: `tests/test_sentinel_pending_buy_lifecycle.py`
- Modify only if a focused failing test proves a defect: `uquant/portfolio.py`, `uquant/execution.py`

**Interfaces:**
- Consumes: existing formal Sentinel freeze attribution.
- Produces: behavioral proof that blocked replacement buys suppress only their sell-funded rotation exits, while independent exits and broker-visible lifecycle truth remain intact.

- [ ] Write a failing-or-characterization test for one-to-one replacement and a multi-symbol replacement batch.
- [ ] Verify old leaders are retained when replacement buys are frozen and unrelated lifecycle/risk sells remain present.
- [ ] Write tests for unsubmitted BUY removal, submitted/open cancel requests, partial-fill preservation, stable ledger identity, duplicate-intent prevention, broker-confirmed cancellation, and SELL passthrough.
- [ ] Run the tests against real PortfolioAllocator/ExecutionPlanner behavior; modify production code only if a test exposes a report-defined violation.
- [ ] Run all Sentinel freeze, portfolio, execution, broker, and account-schema tests.
- [ ] Commit as `test: prove atomic risk admission under sentinel freeze` if existing boundaries pass unchanged, or `fix: preserve atomic risk admission under sentinel freeze` if a TDD-proven correction is required; push.

### Task 5: Enable the locked candidate and run the stop-early gate

**Files:**
- Modify: `uquant/config.py`
- Modify: `tests/test_config_contracts.py`
- Create: `artifacts/sentinel/exclusive_freeze/first_divergence.json`
- Create: `artifacts/sentinel/exclusive_freeze/exclusive_freeze_events.json`
- Create: `artifacts/sentinel/exclusive_freeze/small_gate.json`

**Interfaces:**
- `DEFAULT_CONFIG.risk_sentinel_causal_confirmation_enabled` changes from `False` to `True`; no other production default changes.
- The small gate emits a terminal `PASS` or `REJECT` decision and exact failure reasons.

- [ ] Write a failing config behavior test requiring the production default to authorize the already-tested causal path.
- [ ] Change only the one default value and run config/integration/governance tests.
- [ ] Commit as `feat: enable locked sentinel exclusive freeze candidate` and push.
- [ ] Run the A/B runner on one to three sentinel cells before any official full matrix.
- [ ] Require at least one non-severe, trusted, two-day, base-normal exclusive Freeze that blocks real incremental risk.
- [ ] Require identical base/candidate target gross cap, zero direct Sentinel sells, zero `RISK_GROSS_CAP`, zero healthy-holding reductions, zero new AccountState fields, and no forbidden state drift.
- [ ] Persist first divergence, every discovered exclusive event, blocked orders, and 5/10/20-session opportunity-cost fields.
- [ ] If any hard/small economic gate fails, skip Tasks 6 promotion replay and proceed directly to Task 7 REJECT evidence.

### Task 6: Run full economic promotion gates

**Files:**
- Create: `artifacts/sentinel/exclusive_freeze/phase1_summary.json`
- Create: `artifacts/sentinel/exclusive_freeze/phase2_summary.json`
- Create: `artifacts/sentinel/exclusive_freeze/economic_comparison.json`

**Interfaces:**
- Consumes: immutable candidate lock, frozen data, exact candidate HEAD, official Phase 1 and six-window Phase 2 contracts.
- Produces: recomputable promotion evidence with protected wealth, MDD, Acute, orders, turnover, generalization tails, and replay-error counts.

- [ ] Run the full `a-e` Phase 1 promotion matrix and its formal artifact validator.
- [ ] Compare protected bull wealth against Phase 6 and require at least `99%` retention; require no worse MDD or Acute and Account Orders delta no greater than `+1`.
- [ ] Run all six Phase 2 windows with full, no-optical, remove-one core, remove-all core, industry-balanced, subindustry, and fixed random 5/9/15/20 scenarios.
- [ ] Run formal generalization aggregation and require all economic cells valid, no replay errors, and no frozen-tail boundary regression.
- [ ] Persist compact summaries and hashes; do not copy large equity curves into the Stage 7 artifact directory.

### Task 7: Migrate identity, review, and issue PROMOTE or REJECT

**Files:**
- Create: `artifacts/sentinel/exclusive_freeze/account_code_identity_migration.json`
- Create: `artifacts/sentinel/exclusive_freeze/final_decision.json`
- Modify: `docs/RISK_SENTINEL.md`
- Modify: `docs/PERFORMANCE.md`
- Modify: `docs/OPERATIONS.md`
- On PROMOTE only, create: `artifacts/future_holdout/sentinel_exclusive_freeze.json`

**Interfaces:**
- Produces: exact economic-state migration proof and an unambiguous `PROMOTE` or `REJECT` result.
- PROMOTE alone authorizes non-force fast-forward of `main`, a stable tag, and a prospective non-backfilled Future Holdout Lane.

- [ ] Run explicit `account-code-migrate --acknowledge-code-change` on a representative current-schema account; prove only `code_hash` and one migration event differ.
- [ ] Run ruff, mypy, full pytest with coverage, compileall, build, bandit, dependency audit, frozen-data validation, and future-holdout contract validation.
- [ ] Request an independent focused review against the report; fix every Critical/Important finding with affected TDD and rerun affected gates.
- [ ] For PROMOTE, create a prospective Future Holdout Lane whose activation is the first real trading session after merge, with no historical rows; document the terminal Sentinel scope.
- [ ] For REJECT, keep remote `main` at Phase 6, retain rejection evidence on the Stage 7 branch, and do not create a lane or tag.
- [ ] Commit the final evidence as `docs: record sentinel exclusive-freeze promotion` or `docs: reject sentinel exclusive-freeze candidate`, then push.
- [ ] On PROMOTE only, non-force fast-forward remote `main`, create the stable tag, and remotely read back main/tag/tree identities.
