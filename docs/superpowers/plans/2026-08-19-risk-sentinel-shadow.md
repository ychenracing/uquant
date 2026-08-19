# Independent Risk Sentinel Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a causal, read-only Independent Risk Sentinel and offline calibration lane without changing any production economic behavior.

**Architecture:** Add an isolated `uquant/risk_sentinel/` package whose pure service consumes bounded canonical data and emits immutable assessments. A separate CLI reads a validated account and atomically writes standalone evidence. Offline calibration and the observational source overlay remain outside the production import graph.

**Tech Stack:** Python 3.12, pandas, NumPy, dataclasses, SHA-256 canonical JSON, pytest, Ruff, mypy, Bandit, existing uquant DataStore/universe/account/atomic-I/O contracts.

**Spec:** `docs/superpowers/specs/2026-08-19-risk-sentinel-shadow-design.md` and the user-supplied `03_阶段3_Risk_Sentinel_Shadow_Mode.md`.

## Global Constraints

- Do not implement P0-1 or modify single-name or strategic concentration policy.
- Do not modify ProductionEngine, formal RiskAssessment, PortfolioAllocator, ExecutionPlanner, AccountState, or economic configuration.
- Do not add RiskAction, sell shares, Pending Signal, EngineAdapter, account peak, cooldown, recovery, or stop-loss execution.
- Use only causal prefixes of canonical uquant data, universe, industries, references, and common index calendar.
- Low coverage never lowers risk; calibration is offline-only and bounded by an explicit end date.
- Every independently reviewable task ends in a commit and push.
- Run one final complete Engineering, Phase 1, Phase 2, current-head, and zero-behavior-equivalence validation.

---

### Task 1: Immutable models, coverage, and warmup

**Files:**
- Create: `uquant/risk_sentinel/__init__.py`
- Create: `uquant/risk_sentinel/models.py`
- Create: `uquant/risk_sentinel/coverage.py`
- Create: `tests/test_risk_sentinel_models.py`
- Create: `tests/test_risk_sentinel_coverage.py`

**Interfaces:**
- Produces: `SentinelLevel`, `WarmupStatus`, `CoverageHealth`,
  `SentinelAssessment.to_dict()`, and `assess_coverage()`.

- [ ] Write tests whose named mutations are invalid finite/range handling, nondeterministic ordering, unsafe NOT_READY output, missing/stale indices, new members, missing industries, and unmapped holdings.
- [ ] Run the new tests and verify RED because `uquant.risk_sentinel` does not exist.
- [ ] Implement immutable validation and the exact 0.45/0.35/0.20 coverage formula.
- [ ] Implement READY/DEGRADED/NOT_READY warmup so missing evidence cannot produce a safer conclusion.
- [ ] Run focused pytest, Ruff, and mypy; commit and push `feat: add sentinel coverage health`.

### Task 2: Causal equal-subindustry evidence and Risk Opinion

**Files:**
- Create: `uquant/risk_sentinel/evidence.py`
- Create: `uquant/risk_sentinel/opinion.py`
- Create: `uquant/risk_sentinel/service.py`
- Create: `tests/test_risk_sentinel_evidence.py`
- Create: `tests/test_risk_sentinel_opinion.py`

**Interfaces:**
- Consumes: causal OHLCV frames, PIT industries, held/leader symbols, existing capital drawdown, and same-day base-risk evidence.
- Produces: `build_market_evidence()`, `build_risk_opinion()`, and the report-specified `evaluate_sentinel()`.

- [ ] Write RED tests for unequal industry sizes, extreme-member caps, input-order invariance, future-row invisibility, PIT identity, dual-index velocity, family de-duplication, earliest evidence, and NOT_READY behavior.
- [ ] Implement per-name causal metrics, robust within-industry aggregation, equal-industry aggregation, covariance/volatility diagnostics, held/leader/capital evidence, and six-family mapping.
- [ ] Implement deterministic levels/confidence/reasons and observation-only gross/freeze suggestions.
- [ ] Verify the service imports no engine, portfolio, execution, broker, or calibration module.
- [ ] Run focused pytest, Ruff, and mypy; commit and push `feat: add causal sentinel opinion`.

### Task 3: Offline event calibration

**Files:**
- Create: `benchmarks/risk_sentinel_calibration_contract.json`
- Create: `uquant/risk_sentinel/calibration.py`
- Create: `tests/test_risk_sentinel_calibration.py`

**Interfaces:**
- Produces: strict `CalibrationContract`, `calibrate_events()`, and `summarize_calibration()`.

- [ ] Write RED tests for preregistered thresholds, 1/3/5/10/20 outcomes, evaluation-end truncation, precision/recall, lead time, false-positive cost, missed-shock depth, CAUTION cost, and bull silence.
- [ ] Implement post-event outcomes only in `calibration.py`; refuse rows beyond `evaluation_end` and incomplete/malformed contracts.
- [ ] Add an import-graph test proving service/opinion/CLI production evaluation does not import calibration.
- [ ] Run focused pytest, Ruff, and mypy; commit and push `feat: add offline sentinel calibration`.

### Task 4: Read-only CLI, deterministic artifacts, and documentation

**Files:**
- Create: `uquant/risk_sentinel/__main__.py`
- Create: `uquant/risk_sentinel/cli.py`
- Create: `tests/test_risk_sentinel_cli.py`
- Create: `docs/RISK_SENTINEL.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/QUALITY.md`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `uquant-sentinel` / `python -m uquant.risk_sentinel`, deterministic JSON/Markdown output, and `latest_success` readback.

- [ ] Write RED tests that snapshot account bytes, protect input/data paths from output aliasing, verify deterministic reruns and provenance, and preserve latest_success after a failed run.
- [ ] Implement canonical data/universe/calendar loading, strict read-only account use, base-risk difference diagnostics, atomic output, and source/data/config/runtime identities.
- [ ] Add focused CI integrity and import-isolation checks without changing economic commands or thresholds.
- [ ] Document evidence, coverage, base-risk differences, calibration limits, and manual daily use with neutral Independent Risk Sentinel terminology.
- [ ] Run focused pytest, CLI smoke, Ruff, mypy, compileall, and Bandit; commit and push `feat: add read-only sentinel shadow cli`.

### Task 5: Observation overlay, Holdout Lane, and zero-behavior proof

**Files:**
- Create: `benchmarks/risk_sentinel_shadow_overlay.json`
- Create: `artifacts/sentinel/shadow_equivalence.json`
- Create: `tests/test_risk_sentinel_shadow_equivalence.py`
- Modify: `research/ablation_registry.py`
- Modify: `tests/test_phase2_ablation.py`
- Modify: `benchmarks/future_holdout_lane_registry.json`
- Modify: `artifacts/holdout/lane_validation.json`
- Modify: `tests/test_future_holdout_lanes.py`

**Interfaces:**
- Consumes: the committed Task-4 Sentinel source, Phase-2 reviewed source endpoint, original lane registry, and protected production files.
- Produces: exact Sentinel overlay seal, appended `sentinel_shadow` lane activated 2026-08-19, and byte/economic equivalence evidence.

- [ ] Write RED tests for overlay path/hash drift, any previous production-byte change, lane deletion/mutation/backfill, sentinel source/commit binding, and protected economic-file changes.
- [ ] Seal only committed Sentinel paths and extend ablation validation to accept the exact union of Phase-2 and Sentinel observation overlays.
- [ ] Append `sentinel_shadow` with `IDENTICAL` behavior, 2026-08-19 activation, Task-4 source commit, complete source/config/data/runtime provenance, and null pre-milestone scores.
- [ ] Generate an equivalence artifact proving protected bytes and root production code fingerprint match `87f4366683e4531d0744d78380bf5c336fce2f57`.
- [ ] Run focused Phase-2/holdout/equivalence tests, Ruff, and mypy; commit and push `test: seal sentinel shadow equivalence`.

### Task 6: Final gates, PR, and non-force main integration

**Files:**
- Verification only after the final candidate commit.

**Interfaces:**
- Produces: final local/remote gate results and remote main readback.

- [ ] Verify protected production hashes, configuration, frozen data, universe, runtime lock, lane identities, and all null pre-20 formal Holdout scores.
- [ ] Run complete Engineering commands including 85% branch coverage, build, Bandit, and dependency audit.
- [ ] Run complete Phase 1 and Phase 2 official gates and current-head matrix on the exact committed candidate.
- [ ] Run the zero-behavior matrix covering a-e, six official windows, no-optical, remove-core, fixed random pools, and current-account sample; compare Decision Digest, targets, orders, fills, metrics, and final account exactly.
- [ ] Push the candidate, open a PR, and require Engineering, Phase 1 Performance, and Phase 2 Generalization to succeed.
- [ ] Confirm remote `main` is an ancestor, update it with `force=false`, then read back the exact commit and tree.
