# Causal Divergence and Generalization Sprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reproducible causal decision tracing, test one evidence-backed strategic-exit coalescing candidate, and freeze a deterministic 24-scenario generalization smoke report without weakening any existing economic gate.

**Architecture:** Research-only modules wrap the existing `ProductionEngine` decision path and reuse the reviewed generalization scenario builder; they never create a second strategy implementation or write production configuration. The only production candidate changes order planning so ordinary partial strategic exits accumulate to two existing no-trade bands before execution, while risk-priority exits and full exits keep their current behavior.

**Tech Stack:** Python 3.12, pandas, pytest, frozen CSV data, `ProductionEngine`, existing promotion/generalization validators.

## Global Constraints

- Base all work on reviewed remote `main` commit `8e4ffafbb928e7c485a1d74c8288fc535820b99c` in an isolated branch.
- Priority is return, then drawdown, then trade count.
- Preserve one shared production parameter set and the single inherited `ProductionEngine` decision path.
- Preserve causal close-to-next-open timing, cash-only execution, T+1, price limits, lot sizes, capacity, fees, and slippage.
- Never add symbol, date, scenario, profile, or configured-pool-size special cases.
- Do not retry sparse acute-damage defense (`ba2bf73`) or sparse leader admission (`4a5719e`, `43b6790`).
- Never rewrite `benchmarks/promotion_baseline.json` or create an unreviewed `generalization_baseline.json` to make a result pass.
- A candidate is rejected immediately if a key wealth result falls more than 2%, drawdown rises more than 2 percentage points, or orders rise more than 10%.
- Final promotion requires no new 35-cell failures, at least two fewer than the known 23 violations, bull and continuous wealth at least 99% of current when feasible, drawdown regression no more than 0.5 percentage points, and order growth no more than `max(1, 5%)`.
- The 24-case smoke snapshot is diagnostic evidence, not a promotion baseline or competitor gate.
- Failed production experiments remain off `main`; reviewed research tooling and diagnostic evidence may still ship when production behavior is unchanged.

---

### Task 1: Research-only daily decision trace

**Files:**
- Create: `research/first_divergence.py`
- Create: `tests/test_first_divergence.py`
- Modify: `research/__init__.py`

**Interfaces:**
- Produce `trace_backtest(engine: ProductionEngine, *, symbols: Iterable[str], start: str, end: str) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]`.
- Produce `first_economic_divergence(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None`.
- Trace rows must contain date, opportunity, risk state, independent family votes, sector-guard state, capital damage/budget, ranked leaders, strategic targets, target gross, actual gross, new fills, and pending orders.

- [ ] **Step 1: Write failing real-behavior tests**

  Add tests proving the wrapper returns the exact same `decision_digests`, final wealth, drawdown, orders, fills, and final account as a normal `ProductionEngine.backtest`; proves every trace date is causal and sorted; and proves the comparator returns the first changed economic action rather than a later metric difference.

- [ ] **Step 2: Run RED**

  Run `uv run pytest tests/test_first_divergence.py -q` and confirm failure because the module/functions do not exist.

- [ ] **Step 3: Implement the minimum wrapper**

  Wrap one engine instance's real `decide` method only for the duration of its existing `backtest`, capture the returned `Decision` and already-executed fills/account state, restore the original method in `finally`, and never copy strategy or execution logic.

- [ ] **Step 4: Run GREEN and adjacent checks**

  Run `uv run pytest tests/test_first_divergence.py tests/test_engine_contracts.py tests/test_attribution.py -q`, `uv run ruff check research/first_divergence.py tests/test_first_divergence.py`, and `git diff --check`.

- [ ] **Step 5: Commit**

  Commit with message `Add causal first-divergence tracing`.

### Task 2: Strategic exit coalescing candidate

**Files:**
- Modify: `uquant/execution.py`
- Modify: `tests/test_execution.py`

**Interfaces:**
- `plan_orders` remains the sole target-to-order translator.
- Ordinary partial SELL targets with `reason_code == "strategic_cohort"`, `exit_kind == "strategy"`, and positive target weight use an execution threshold of `max(min_trade_value, 2 * min_trade_weight * equity)`.
- BUYs, zero-weight full exits, `RISK_PRIORITY` reductions, crisis/risk/capital-budget exits, and every non-strategic target retain the existing threshold and semantics.

- [ ] **Step 1: Write failing tests**

  Add one test where a 7% ordinary partial strategic reduction produces no order, one where an 11% reduction produces one SELL to the exact allocator target, and one where the same 7% reduction marked `RISK_PRIORITY` still produces a SELL. Use literal weights and real `plan_orders`; do not assert implementation text.

- [ ] **Step 2: Run RED**

  Run only the three new tests and verify the 7% ordinary strategic case fails because current code emits an order.

- [ ] **Step 3: Implement the minimum condition**

  Derive the doubled band from existing `min_trade_weight`; add no new configuration field and do not change `merge_pending_orders` or allocator state.

- [ ] **Step 4: Run GREEN and full engineering tests**

  Run the three new tests, all of `tests/test_execution.py`, Ruff on changed files, `git diff --check`, and then full `uv run pytest`.

- [ ] **Step 5: Commit isolated candidate**

  Commit with message `Coalesce ordinary strategic exit orders`.

### Task 3: Fail-closed economic decision

**Files:**
- Create diagnostic report under this plan's `.superpowers/sdd/` workspace only.
- Do not modify any benchmark in this task.

**Interfaces:**
- Compare candidate commit against `8e4ffaf` with the same frozen data, pool definitions, `ProductionEngine`, and baseline file.

- [ ] **Step 1: Run sentinels**

  Replay D/choppy_2024, C/mixed_2023, A and E/bull, A and E/through_july, and A/D/continuous. Record final wealth, max drawdown, account orders, annual turnover, and urgent return.

- [ ] **Step 2: Apply hard rejection**

  Reject on any Global Constraint regression or if D/choppy does not reduce orders without lowering wealth. If rejected, reset no files: preserve the candidate commit for audit, create a fresh evidence branch from the last reviewed research commit, and skip production promotion.

- [ ] **Step 3: Run complete promotion only for an eligible candidate**

  Run `uv run python -m uquant.validation promotion --data-dir data/frozen --baseline benchmarks/promotion_baseline.json --profile full`. Candidate must reduce the known 23 failures by at least two and add none.

- [ ] **Step 4: Record verdict**

  Append exact commands, SHAs, results, and PASS/REJECT to the task report.

### Task 4: Deterministic 24-case generalization smoke

**Files:**
- Create: `research/generalization_smoke.py`
- Create: `tests/test_generalization_smoke.py`
- Create: `benchmarks/generalization_smoke_reference.json`
- Modify: `research/__init__.py`
- Modify: `docs/PERFORMANCE.md`

**Interfaces:**
- Produce `build_smoke_scenarios(...)` by calling the existing `compute_pre_window_evidence` and `build_generalization_scenarios`, then selecting exactly: base; three remove-one priors; remove-all priors; no-optical; eleven real industry-only cases; balanced industries; and random 6/12/24 with seeds 0 and 1 (24 total).
- Produce `run_generalization_smoke(...)` using only `ProductionEngine.backtest`, `observation_from_result`, `aggregate_metrics`, `prior_dependence`, and immutable data/source provenance.
- Snapshot must state `diagnostic_only: true`, bind the production commit/source hash, data manifest/checksums, scenario fingerprint, pre-window evidence date/membership, and all 24 wealth/drawdown/order/deployment observations.

- [ ] **Step 1: Write failing construction/provenance tests**

  Prove exact count/family membership, deterministic fingerprint, no future-ranked evidence, result coverage, immutable provenance, and explicit diagnostic-only labeling.

- [ ] **Step 2: Run RED**

  Run `uv run pytest tests/test_generalization_smoke.py -q` and confirm the missing module/functions fail.

- [ ] **Step 3: Implement the minimum smoke runner**

  Reuse existing public validation helpers and one production engine; do not add a baseline-writing API, thresholds, competitor values, or automatic promotion.

- [ ] **Step 4: Run GREEN and generate the real snapshot**

  Run focused tests, Ruff, and `git diff --check`. Then execute Pool E, priors A, window `2018-01-02` through `2026-07-20`, and write canonical JSON to `benchmarks/generalization_smoke_reference.json` only after the production tree is committed.

- [ ] **Step 5: Document and commit**

  Document how to reproduce the diagnostic and why it is not the full generalization gate. Commit with message `Freeze 24-case generalization smoke evidence`.

### Task 5: Whole-branch verification, review, and direct main push

**Files:**
- Modify only files already listed by Tasks 1, 2, and 4, plus task reports in the ignored SDD workspace.

- [ ] **Step 1: Independent whole-diff review**

  Review for future leakage, decision-path duplication, hidden pool/date/symbol specialization, altered execution semantics, stale provenance, benchmark tampering, and insufficient tests. Fix every Critical/Important finding and re-run affected checks.

- [ ] **Step 2: Fresh full verification**

  Run full pytest with coverage >=85%, Ruff, strict mypy, compileall, Bandit, build, data-manifest verification, exact smoke reproduction, and full promotion when the production candidate remains eligible.

- [ ] **Step 3: Confirm intended scope**

  Verify the original dirty checkout is untouched; inspect `git status`, commit list, diff, and benchmark hashes. Do not stage any unrelated file.

- [ ] **Step 4: Publish preselected integration choice**

  The user already selected direct `main` publication. Fetch `origin/main`; require it still equals the branch base or fast-forward it safely. Push the exact reviewed HEAD to `origin/main` without force, then verify remote `refs/heads/main` equals that SHA. If production candidate was rejected, publish only Tasks 1 and 4 evidence commits.
