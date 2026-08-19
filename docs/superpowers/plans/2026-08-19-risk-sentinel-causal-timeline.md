# Risk Sentinel Causal Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a point-in-time base/Sentinel evidence timeline and daily diagnostics with zero economic behavior drift.

**Architecture:** Extract the existing base market-family thresholds into one pure function, build immutable full-history Sentinel/base rows in a new history module, and cache only data/config-derived timelines in `ProductionEngine`. Keep the Phase 6 causal-authority switch false and render only existing decision diagnostics.

**Tech Stack:** Python 3.12, pandas, immutable dataclasses, pytest, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-19-risk-sentinel-causal-timeline-design.md`

## Global Constraints

- Start at `b4717a65f1658d9ff2311362d1ad49d490d13a5d` from `origin/main`.
- Do not modify concentration policy, `target_gross_cap`, portfolio/execution economics, account economic fields, RiskAction, sells, cooldown, recovery, or high-water marks.
- Trusted history families are exactly `market_velocity`, `breadth_structure`, and `covariance_stress`.
- `risk_sentinel_causal_confirmation_enabled` defaults to `False` and grants no Phase 6 authority.
- Every independently meaningful milestone is committed and pushed.

---

### Task 1: Freeze the pre-timeline baseline

**Files:**
- Create: `artifacts/sentinel/phase6/pre_timeline_baseline.json`
- Create: `docs/superpowers/specs/2026-08-19-risk-sentinel-causal-timeline-design.md`
- Create: `docs/superpowers/plans/2026-08-19-risk-sentinel-causal-timeline.md`

**Interfaces:**
- Consumes: current Git tree, frozen data, existing Phase 1 and Phase 2 evidence.
- Produces: compact hashes for decisions, risk summaries, targets, event IDs, orders, fills, final economic account, and key metrics.

- [ ] Capture HEAD/tree/config/code/data identities and the existing Phase 1/Phase 2 evidence hashes.
- [ ] Replay `a/h1_2024` and record canonical component hashes plus exact economics.
- [ ] Run the complete baseline pytest suite and record its result.
- [ ] Commit as `test: freeze pre-timeline sentinel baseline` and push.

### Task 2: Expose causal base market evidence

**Files:**
- Modify: `uquant/risk.py`
- Test: `tests/test_risk_transitions.py`
- Test: `tests/test_phase1_decision_equivalence.py`

**Interfaces:**
- Produces: `BaseMarketFamilySnapshot` and `build_base_market_family_snapshot(...)`.
- Consumes: raw market metrics and `SystemConfig`; returns only market-family flags.

- [ ] Add a failing test comparing legacy expected flags and formal risk output.
- [ ] Run the focused test and observe failure for the missing API.
- [ ] Extract the three market-family thresholds without changing their expressions.
- [ ] Run focused risk and Phase 1 decision-equivalence tests.
- [ ] Commit as `refactor: expose causal base market risk evidence` and push.

### Task 3: Build the causal market timeline

**Files:**
- Create: `uquant/risk_sentinel/history.py`
- Modify: `uquant/risk_sentinel/models.py`
- Modify: `uquant/risk_sentinel/service.py`
- Create: `tests/test_risk_sentinel_history.py`
- Create: `tests/test_risk_evidence_timeline.py`

**Interfaces:**
- Produces: `SentinelMarketRow`, `BaseMarketRiskRow`, `SentinelCausalState`,
  `RiskEvidenceTimeline`, `build_risk_evidence_timeline(...)`, and
  `fold_sentinel_market_state(...)`.
- Consumes: complete feature-frame prefixes, canonical PIT universe/industry,
  shared base snapshot function, and config; never consumes AccountState.

- [ ] Add failing tests for future-row isolation, pre-listing membership, PIT
  industry, input order, account-field exclusion, and missing common sessions.
- [ ] Run the tests and observe the missing history API failures.
- [ ] Implement immutable per-session rows from the first common warm session.
- [ ] Add failing fold tests for two-day confirmation, three-day repair,
  NOT_READY/DEGRADED interruption, determinism, and full-rebuild/prefix identity.
- [ ] Implement the pure fold and trust reasons.
- [ ] Run the focused history, evidence, coverage, and model tests.
- [ ] Commit the timeline and fold as two independently reviewable commits and push each.

### Task 4: Cache and integrate diagnostics

**Files:**
- Modify: `uquant/engine.py`
- Modify: `uquant/risk_sentinel/integration.py`
- Modify: `uquant/config.py`
- Modify: `benchmarks/config_parameter_governance.json`
- Modify: `uquant/config_governance.py`
- Test: `tests/test_engine_contracts.py`
- Test: `tests/test_risk_sentinel_integration.py`
- Test: `tests/test_config_contracts.py`
- Test: `tests/test_config_governance.py`

**Interfaces:**
- `ProductionEngine` caches immutable data/config timelines only.
- Integration reads timeline diagnostics but ignores causal history for authority
  while `risk_sentinel_causal_confirmation_enabled` is `False`.

- [ ] Add failing tests for cache-prefix identity, no AccountState writes, default
  switch false, and no new Sentinel-only freeze.
- [ ] Implement the cache key and diagnostic attachment with no economic fields.
- [ ] Update governed configuration identities for the one new safety switch.
- [ ] Run engine, integration, config, and decision-equivalence tests.
- [ ] Commit and push.

### Task 5: Render the one daily report

**Files:**
- Modify: `uquant/report.py`
- Create: `tests/test_risk_sentinel_daily_report.py`
- Modify: `tests/test_cli_and_report.py`

**Interfaces:**
- Produces: deterministic `Risk Sentinel` markdown from `Decision.risk_summary`.
- Supports freeze owners `NONE`, `BASE_RISK`, `SENTINEL`, `BOTH`, and `DATA_NOT_READY`.

- [ ] Add failing owner/rendering, max-three, missing-field, byte-identity, and
  no-decision/account-mutation tests.
- [ ] Implement the compact section and bounded manual-action vocabulary.
- [ ] Run report and CLI tests.
- [ ] Commit as `feat: render sentinel evidence in daily report` and push.

### Task 6: Close the rejected gross-cap path

**Files:**
- Modify: `uquant/config.py`
- Modify: `uquant/risk_sentinel/integration.py`
- Modify: `uquant/risk_sentinel/cli.py`
- Modify: `tests/test_config_contracts.py`
- Modify: `tests/test_risk_sentinel_integration.py`
- Modify: `tests/test_risk_sentinel_cli.py`
- Create: `docs/reviews/2026-08-19-sentinel-gross-cap-rejection.md`
- Create: `artifacts/sentinel/gross_cap_rejection.json`
- Modify: `README.md`
- Modify: `docs/RISK_SENTINEL.md`
- Modify: `docs/STRATEGY.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PERFORMANCE.md`
- Modify: `docs/CONFIGURATION.md`

**Interfaces:**
- `SystemConfig` accepts only `SHADOW` and `FREEZE_ONLY`.
- The rejected legacy string fails with the exact economic-gate message.

- [ ] Add failing tests for legacy-value rejection and CLI/help exclusion.
- [ ] Remove the production type/help path and add explicit legacy rejection.
- [ ] Add the sealed compact rejection JSON and audit note; do not add equity curves.
- [ ] Update only current production documentation.
- [ ] Run config, CLI, integration, documentation-contract, and static checks.
- [ ] Commit as `docs: close rejected sentinel gross-cap path` and push.

### Task 7: Migrate code identity and verify the final candidate

**Files:**
- Create: `artifacts/sentinel/phase6/account_code_identity_migration.json`
- Create: `artifacts/sentinel/phase6/final_equivalence.json`

**Interfaces:**
- Consumes: a backed-up representative current-schema account and final
  `code_fingerprint()`.
- Produces: a migration audit proving only `code_hash` and one
  `code_identity_only` event changed.

- [ ] Run `account-code-migrate --acknowledge-code-change` on the backed-up sample.
- [ ] Strictly reload and compare economic hashes before/after.
- [ ] Re-run the frozen representative replay and compare every compact baseline hash.
- [ ] Run ruff, mypy, pytest with coverage, compileall, build, and bandit.
- [ ] Run Phase 1 full promotion and all six Phase 2 windows without changing gates.
- [ ] Perform a focused final code review and fix Critical/Important findings with
  affected-test reruns only.
- [ ] Commit final evidence metadata, push, then remotely read back branch and main.
- [ ] Fast-forward `main` only if every gate and exact equivalence check passes.
