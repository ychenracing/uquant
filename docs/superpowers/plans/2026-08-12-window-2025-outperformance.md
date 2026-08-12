# 2025-2026 Target-Window Outperformance Implementation Plan

> **Execution mode:** Follow the executing-plans workflow in this session, with a verification checkpoint after every task.

**Goal:** Produce and pass a reproducible 20-cell A-E comparison for 2025-01-02 through 2026-07-31, then fix the causal D-path and acute-defense gaps until uquant meets the strict pairwise definition in the design.

**Architecture:** Keep all competitor execution in research scripts. Extend uquant's causal trace before changing strategy behavior. Make the smallest generic state-machine changes to ownership/rearm and risk budgeting, guarded by unit tests and exact-window economic tests.

**Stack:** Python 3.11+, pandas, numpy, pytest, uv, JSON benchmark artifacts.

---

## Task 1: Freeze and expose reproducible evidence

**Files:**
- Reuse: `scripts/run_pareto_evidence.py`
- Modify: `uquant/engine.py`
- Test: `tests/test_engine_evidence_trace.py`

1. Reuse the already tested evidence-runner and causal-trace commits from the prior research branch.
2. Run the focused tests and confirm they are green on this branch.
3. Record the frozen main SHA and data manifest in the target artifact.

## Task 2: Restore the common competitor adapter for the exact interval

**Files:**
- Create: `scripts/run_window_competitor_adapter.py`
- Create: `tests/test_window_competitor_adapter.py`
- Create: `benchmarks/window_competitor_contract.json`

1. Write failing tests for the exact start/end dates, A-E pools, source locks, account-order netting, ambiguous-link failure, and 15-cell completeness.
2. Port only the reusable adapter machinery from historical commit `7990c44705467465c480866aa5045b87a035b87e` to current `uquant` data/provenance modules.
3. Add fail-closed checkout/source validation and a deterministic JSON schema.
4. Run focused tests to green.
5. Run all three frozen competitors against every A-E pool and write the 15-cell artifact.

## Task 3: Build the exact 20-cell matrix and strict winner gate

**Files:**
- Create: `scripts/run_window_outperformance.py`
- Create: `tests/test_window_outperformance.py`
- Create: `benchmarks/window_2025_2026_outperformance.json`

1. Write failing tests that require 20 unique cells and all four pairwise predicates.
2. Add the five uquant runs under the identical execution contract.
3. Compute final wealth, maximum drawdown, account orders, turnover, and one common acute-window return.
4. Emit per-predicate pass/fail details and fail the CLI when any cell loses.
5. Run once before strategy changes to establish the exact deficit matrix.

## Task 4: Diagnose and fix D-pool ownership/rearm

**Files:**
- Modify: `uquant/engine.py` and the smallest relevant strategy/state module identified by the trace
- Test: the matching unit-test module plus `tests/test_engine_evidence_trace.py`
- Update: `benchmarks/window_2025_2026_outperformance.json`

1. Generate A, D, and E daily causal traces through the first missed D entry.
2. Record the first divergent date and suppression cause before editing production code.
3. Write a failing unit test reproducing that generic lifecycle state without pool names, symbols, or target dates.
4. Implement the minimum stale-owner release or evidence-driven rearm transition.
5. Run the unit tests, A-E target-window uquant runs, and strict order-count gates.
6. Revert the candidate if it damages any required cell.

## Task 5: Add acute defense while retaining Alpha ownership

**Files:**
- Modify: the risk-budget/state module identified by causal trace
- Test: the corresponding risk/state tests
- Update: `benchmarks/window_2025_2026_outperformance.json`

1. Identify the first acute-window excess-loss divergence using only observable-at-the-time evidence.
2. Write failing tests proving both exposure reduction during the shock and ownership/rearm continuity after it.
3. Implement one minimal de-risking transition; do not change signal ownership or erase recovery eligibility.
4. Run focused tests and the exact A-E economic gate.
5. Remove the candidate if final wealth, order count, or acute return fails the strict pairwise predicate.

## Task 6: Verify, document, and review

**Files:**
- Create: `docs/WINDOW_2025_2026_OUTPERFORMANCE_2026-08-12.md`
- Update benchmark artifacts only from fresh runs

1. Run formatting/static checks configured by the project.
2. Run focused adapter, provenance, PIT, daily/batch, state-machine, and economic tests.
3. Run the complete test suite from a clean process.
4. Regenerate the exact 20-cell artifact and validate source/data fingerprints.
5. Inspect the diff for forbidden pool-length, symbol, or date-specific behavior.
6. Write the final report with exact commands, commits, metrics, remaining failures if any, and an explicit achieved/not-achieved verdict.

## Required command evidence

```bash
UV_CACHE_DIR=/tmp/uquant-window-uv-cache uv run pytest -q
UV_CACHE_DIR=/tmp/uquant-window-uv-cache uv run python scripts/run_window_competitor_adapter.py --help
UV_CACHE_DIR=/tmp/uquant-window-uv-cache uv run python scripts/run_window_outperformance.py --help
git diff --check
git status --short --branch
```

No completion claim is allowed until these commands and the strict pairwise matrix have passed in the final branch state.
