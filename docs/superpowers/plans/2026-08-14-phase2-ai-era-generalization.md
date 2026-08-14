# Phase 2 AI-Era Generalization Implementation Plan

> **For Codex:** Use `superpowers:subagent-driven-development` to execute this
> plan continuously. Every behavior change follows RED/GREEN/REFACTOR and every
> task receives both specification and quality review.

**Goal:** Add production-grade AI-universe generalization, reconciled economic
attribution, independent subsystem ablation and an immutable future holdout,
while preserving the frozen Phase 1 champion and making all three gates block
CI and `main`.

**Architecture:** Extend the existing causal Phase 1 validation contracts
rather than creating a parallel research stack. A canonical PIT AI-universe
manifest feeds all generalization and attribution behavior. Generalization is
an outer six-window runner over deterministic scenario construction. Stable
economic metadata propagates through production decisions and a reconciled
attribution ledger. Research-only ablations use a frozen registry and exact
config/patch carriers. Holdout data is physically isolated from frozen
in-sample data.

**Tech Stack:** Python 3.12, NumPy/Pandas locked by `uv.lock`, pytest,
Ruff, strict mypy, GitHub Actions, canonical JSON/SHA-256 provenance.

## Global Constraints

- Frozen Phase 1 champion: `cf8fecff76564fd4ed87faa0da336a06d433fd93`.
- Economic dates are never earlier than 2023-01-01. Earlier prices are feature
  warm-up only and never account replay or performance input.
- AI supply-chain stocks only; no scenario-specific strategy parameters.
- Random base seed is `20260810`; indexes are exactly `0,1,2,3,4`; sizes are
  exactly `5,9,15,20`; failing pools are never replaced.
- Official windows are only the six entries in `AI_ERA_WINDOWS`.
- Phase 1 thresholds, accounting and scenarios cannot be removed or weakened.
- T+1, price-limit/suspension/volume, lot, fee/slippage, cash, PIT,
  determinism and fail-closed rules cannot be ablated.
- Only one major subsystem is disabled or deleted in an experiment.
- Every accepted strategy deletion is followed by Phase 1 Performance, the
  affected Generalization cells, then the final complete three-gate run.
- All generated evidence binds exact HEAD, source, effective config, data,
  universe, industry map, Python/NumPy/Pandas, uv and `uv.lock`.

### Task 1: Freeze champion and canonical PIT AI universe

**Files:**
- Create: `benchmarks/phase1_frozen_champion.json`
- Create: `benchmarks/ai_universe_manifest.json`
- Create: `uquant/validation/universe.py`
- Modify: `uquant/industry.py`
- Modify: `uquant/reference.py`
- Test: `tests/test_ai_universe_contract.py`

1. Write failing tests for the exact champion identity, exactly 34 unique AI
   symbols, exact reference coverage, PIT intervals, industry taxonomy,
   deterministic canonical hash and rejection of the stale `sh688205` entry.
2. Run the focused tests and confirm failures are caused by missing contracts.
3. Add the reviewed champion metadata and universe manifest, then implement a
   fail-closed loader with immutable dataclasses and canonical SHA-256.
4. Make production industry/reference helpers derive from this one manifest;
   eliminate independent mapping drift without changing decisions.
5. Prove existing Phase 1 decisions are byte-identical and run focused tests,
   Ruff and strict mypy.
6. Commit: `Freeze Phase 1 champion and AI universe`.

### Task 2: Build the six-window generalization contract and matrix

**Files:**
- Create: `uquant/validation/generalization_contract.py`
- Create: `uquant/validation/generalization_matrix.py`
- Modify: `uquant/validation/generalization.py`
- Modify: `uquant/validation/ai_era.py`
- Modify: `uquant/validation/__init__.py`
- Modify: `uquant/validation/cli.py`
- Delete or reduce to adapter: `research/generalization_smoke.py`
- Delete or reduce to adapter: `research/universe_stress.py`
- Test: `tests/test_generalization_contract.py`
- Test: `tests/test_generalization_matrix.py`
- Modify: `tests/test_generalization.py`
- Modify: `tests/test_generalization_smoke.py`

1. Write failing contract tests for the six official windows; exact fixed
   seeds/sizes; remove-three, remove-all-core, tradable-no-optical,
   industry-balanced and sufficient-industry cases; explicit
   `INSUFFICIENT_SAMPLE`; no duplicated/missing cells; no pre-2023 account
   replay; and deterministic scenario fingerprints.
2. Confirm RED, then implement a small immutable contract over the existing
   causal `PreWindowEvidence` and scenario engine.
3. Write failing tests that champion equality passes, mutation/non-finite/
   stale provenance fails, and every result preserves its raw cell.
4. Implement outer window execution, aggregate median/worst/p10 wealth,
   p90 drawdown, p90 orders, turnover and concentration. Do not interpret
   tradable-no-optical as removal from the reference context.
5. Replace hard-coded smoke counts and duplicate universe rules with thin
   adapters to the canonical contract.
6. Add `python -m uquant.validation generalization-matrix` with fixed manifest,
   frozen reference, window sharding and canonical JSON output.
7. Run focused tests, Ruff and strict mypy.
8. Commit: `Add complete AI-era generalization matrix`.

### Task 3: Capture untouched champion matrix and freeze gate policy

**Files:**
- Create: `benchmarks/ai_era_generalization_baseline.json`
- Create: `benchmarks/ai_era_generalization_policy.json`
- Create: `artifacts/phase2/champion-generalization-matrix.json`
- Modify: `tests/test_generalization_contract.py`
- Modify: `tests/test_generalization_matrix.py`

1. Run the complete matrix against the untouched frozen champion in the locked
   Python 3.12 environment.
2. Review every tail, including remove-all-core, tradable-no-optical and all
   fixed random seeds. Store all cells; never replace a failing seed.
3. Before any strategy change, derive and freeze intrinsic floors and
   non-regression policy. The policy must not require a Pareto improvement for
   champion equality and cannot be self-signed after compilation.
4. Add negative tests for empty baseline, edited/resealed cells or policy,
   missing scenarios, changed seeds, threshold weakening and provenance drift.
5. Run the full untouched matrix again and require equality with the frozen
   reference.
6. Commit: `Freeze AI-era generalization reference`.

### Task 4: Propagate stable economic attribution identity

**Files:**
- Modify: `uquant/types.py`
- Modify: `uquant/account.py`
- Modify: `uquant/execution.py`
- Modify: `uquant/portfolio.py`
- Modify: `uquant/portfolio_core.py`
- Modify: `uquant/portfolio_leaders.py`
- Modify: `uquant/portfolio_recovery.py`
- Modify: `uquant/portfolio_strategic.py`
- Modify: `uquant/engine.py`
- Modify: `uquant/broker.py`
- Test: `tests/test_attribution_identity.py`
- Modify: `tests/test_account_broker_schema.py`
- Modify: `tests/test_account_schema_v3_integrity.py`

1. Write failing round-trip tests for stable event ID, origin subsystem,
   mechanism, origin/current lifecycle, replacement link, industry-at-entry
   and industry-manifest hash from Target through Order/Tranche/Fill.
2. Add backward-safe readers only where existing account files require them;
   production writes use the single canonical schema.
3. Populate metadata at the causal decision point. Never parse human-readable
   `reason` strings to recover economic identity.
4. Ensure later lifecycle changes do not overwrite immutable origin fields.
5. Prove serialization, deterministic Decision equality and Phase 1 economic
   decisions remain unchanged.
6. Run focused schema/execution tests, Ruff and strict mypy.
7. Commit: `Add stable attribution identity`.

### Task 5: Implement reconciled PnL and risk attribution

**Files:**
- Create: `uquant/attribution.py`
- Modify: `uquant/engine.py`
- Modify: `uquant/report.py`
- Replace/adapt: `research/trade_attribution.py`
- Modify: `uquant/validation/generalization.py`
- Test: `tests/test_economic_attribution.py`
- Modify: `tests/test_attribution.py`
- Modify: `tests/test_cli_and_report.py`

1. Write failing accounting tests covering realized and open lots, fees,
   slippage, lifecycle/origin subsystem, industries, replacement and costs.
2. Implement Top-1/Top-3 positive contribution, signed/absolute contribution,
   PnL HHI, industry HHI, lifecycle and mechanism contribution, turnover and
   trading-session holding period. Define undefined denominators explicitly.
3. Require symbol and aggregate attribution to reconcile to final equity minus
   initial cash within a strict tolerance.
4. Add a causal daily ledger for cash/gross/weights/PnL/caps and binding owner.
   Label cash drag as diagnostic and risk avoidance as paired-counterfactual,
   never exact realized PnL.
5. Prevent post-exit horizons from reading beyond `economic_end`.
6. Attach concentration and attribution to every matrix cell and reports.
7. Run focused tests, full Phase 1 Performance and affected Generalization.
8. Commit: `Add reconciled economic attribution`.

### Task 6: Classify and constrain all configuration freedom

**Files:**
- Create: `benchmarks/config_parameter_governance.json`
- Create: `uquant/config_governance.py`
- Modify: `uquant/config.py`
- Modify: `research/candidate_search.py`
- Modify: `research/parameter_stress.py`
- Modify: `uquant/validation/promotion.py`
- Test: `tests/test_config_governance.py`
- Modify: `tests/test_config_contracts.py`

1. Write failing coverage tests that every `SystemConfig` field appears exactly
   once under `MARKET_RULE`, `SAFETY`, `ECONOMIC`, `DERIVED`, or
   `COMPATIBILITY`, with one subsystem owner and no unknown fields.
2. Restrict candidate search and stress tools to declared ECONOMIC fields;
   reject MARKET_RULE/SAFETY/DERIVED/COMPATIBILITY overrides.
3. For derived values, add equivalence tests before removing independent
   override paths. Do not invent formulas from historical outcomes.
4. Prove the seven statically unread fields have no production behavior or
   serialized-state role, then remove them and migration-only code one at a
   time. Re-run Phase 1 and affected Generalization after each deletion.
5. Test and remove false alternative-path compatibility flags only when exact
   decision traces and state hashes prove identity.
6. Record before/after total and ECONOMIC counts in the governance artifact.
7. Run focused tests, Ruff, strict mypy and complete Phase 1 Performance.
8. Commit: `Govern production parameter freedom`.

### Task 7: Build fail-closed independent ablation infrastructure

**Files:**
- Replace: `research/ablation.py`
- Create: `research/ablation_registry.py`
- Create: `scripts/run_phase2_ablation.py`
- Create: `artifacts/phase2/ablations/registry.json`
- Test: `tests/test_phase2_ablation.py`

1. Write failing registry tests for every report-mandated subsystem, unique
   carrier, one-at-a-time delta, market/safety exclusions, exact patch/config
   hash, immutable seed/window contract and required first divergence.
2. Implement config-carrier and content-addressed patch-carrier execution from
   isolated clean checkouts. A patch cannot touch another subsystem or safety
   code. No behavior divergence is an invalid experiment.
3. Run every ablation against complete Phase 1 Performance and complete
   Generalization from fresh account state with identical inputs.
4. Emit per-cell and aggregate deltas for wealth, drawdown, orders, acute risk,
   turnover, concentration and tail generalization; store exact replay commands
   and provenance.
5. Add deterministic rerun and mutation/stale-source negative tests.
6. Run focused tests, Ruff and strict mypy.
7. Commit: `Add fail-closed subsystem ablation`.

### Task 8: Execute ablations and remove only proven valueless complexity

**Files:**
- Create: `artifacts/phase2/ablations/results.json`
- Create: `artifacts/phase2/ablations/conclusions.md`
- Modify/delete: only files owned by one accepted subsystem per commit
- Modify: relevant tests and documentation for each deletion

1. Execute all registry entries exactly once per carrier, with fixed seeds and
   no parameter tuning. Record failures as results, not reasons to change seeds
   or policy.
2. For each subsystem, classify `KEEP`, `DELETE`, or `INCONCLUSIVE` using all
   required dimensions. Explain any unique tail or generalization protection.
3. For each `DELETE`, remove only that subsystem's production code, config,
   state, serialization, tests, comments and docs in a dedicated commit.
4. After each deletion run focused tests, Phase 1 Performance and related
   Generalization. Revert the candidate deletion if Phase 1 is harmed or unique
   protection disappears; do not compensate with a new rule.
5. After all accepted deletions rerun the complete registry against the new
   minimal candidate to ensure conclusions remain valid.
6. Commit final evidence: `Record subsystem ablation conclusions`.

### Task 9: Freeze future holdout and manual execution journal

**Files:**
- Create: `benchmarks/future_holdout_contract.json`
- Create: `uquant/validation/holdout.py`
- Create: `uquant/execution_journal.py`
- Modify: `.gitignore`
- Modify: `uquant/cli.py`
- Modify: `uquant/report.py`
- Test: `tests/test_future_holdout.py`
- Test: `tests/test_execution_journal.py`

1. Write failing tests that find the maximum observed economic market date,
   freeze `2026-08-05`, start on `2026-08-06`, isolate future data under
   `data/holdout/phase2-future-v1`, carry the prior close account/pending state,
   and reject non-null metrics when no sessions exist.
2. Implement a tracked immutable contract and ignored post-checkout manifest
   with exact production/source/config/universe/industry/environment/lock/
   account hashes, 40/60-session milestones and null score fields.
3. Fail if holdout data enters `data/frozen`, Phase 1 windows expand, the
   observation period is used for parameter changes, or the manifest is stale.
4. Implement append-only PLANNED/FILLED/SKIPPED journal records for planned
   price, next open, actual time/price/shares, manual skip and derived slippage.
   The journal has no dependency path into strategy decisions or state.
5. Run focused tests, Ruff and strict mypy.
6. Commit: `Freeze future holdout protocol`.

### Task 10: Make all three gates blocking and align documentation

**Files:**
- Create: `.github/workflows/strategy-generalization.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/strategy-performance.yml`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/DEVELOPMENT.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PERFORMANCE.md`
- Modify: `docs/QUALITY.md`
- Modify: `docs/STRATEGY.md`
- Test: `tests/test_phase2_ci_contract.py`

1. Write failing static workflow tests for Engineering, Phase 1 Performance
   and Phase 2 Generalization as independent blocking checks on PR and `main`.
2. Add window-sharded Generalization jobs and an always-running aggregator.
   Avoid required checks that become skipped due to path filters.
3. Require artifact readback to match exact HEAD and all provenance fields;
   upload failed artifacts for diagnosis without converting failure to success.
4. Document the canonical universe, fixed random pools, all metrics,
   attribution semantics, parameter categories, ablations, separate holdout and
   manual journal without changing the manual daily-trading positioning.
5. Remove obsolete generalization smoke/baseline descriptions and verify no
   pre-2023 economic, non-AI, fake holdout or adjustable-seed instructions.
6. Run focused tests and documentation/static checks.
7. Commit: `Make AI-era generalization blocking`.

### Task 11: Generate final exact-HEAD evidence and run all gates

**Files generated post-checkout:**
- `benchmarks/ai_era_performance.json` (ignored)
- `benchmarks/ai_era_generalization.json` (ignored)
- `benchmarks/future_holdout_manifest.json` (ignored)
- Create tracked summary: `artifacts/phase2/final-acceptance.md`

1. Ensure the tracked tree is clean and record exact candidate HEAD.
2. Run the complete Engineering Gate: Ruff, strict mypy, manifest validation,
   full pytest with coverage, compile, build, Bandit and dependency audit.
3. Run full Phase 1 Performance and verify 30 official plus 15 protected cells,
   no failures and exact candidate-HEAD provenance.
4. Run all Generalization cells and verify scenario completeness, fixed seeds,
   tails, concentration, policy and exact candidate-HEAD provenance.
5. Generate the null-score holdout manifest and verify exact candidate HEAD,
   frozen state and 2026-08-06 start.
6. Run deterministic repeat checks and compare canonical artifact hashes after
   removing timestamps only where the contract explicitly allows them.
7. Check source, tests, config, comments, README, Markdown and workflows for
   consistency. Any fix creates a new HEAD and requires steps 2--6 again.
8. Commit only the tracked acceptance summary, then repeat steps 2--6 against
   that new exact HEAD. Do not commit self-binding generated artifacts.

### Task 12: Independent review, publish and verify `main`

1. Run a specification-compliance review against the Phase 2 report, approved
   design and every task/checklist entry.
2. Run a separate whole-diff Python/quant/provenance review. Fix every
   load-bearing finding with TDD, then rerun all three gates.
3. Push the Phase 2 branch and open a pull request against `main`.
4. Wait for and inspect all GitHub blocking checks. Address any failure from
   logs; never weaken a gate or baseline.
5. Merge only after Engineering, Performance and Generalization are green.
6. Fetch `origin/main`, verify the merged SHA and rerun/read back final artifacts
   if merge changes the production commit.
7. Report final main SHA, preserved Phase 1 metrics, every generalization tail,
   attribution/concentration, each ablation conclusion, deletions, configuration
   count change, three gate states, future-holdout manifest and remaining risks.
