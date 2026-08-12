# Pareto Sprint v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce random-universe tail drawdown, historical-winner dependence, small-pool acute losses, and local choppy drawdown without materially degrading the frozen bull, drawdown, or order baselines.

**Architecture:** Preserve the single `ProductionEngine` and reuse the existing research/validation stack. Freeze immutable evidence, diagnose the first causal divergence for the approved failure cells, admit only two to four evidence-driven candidates through independent TDD and Pareto gates, then run the complete promotion/generalization/competitor evidence chain.

**Tech Stack:** Python 3.12, pandas, NumPy, pytest, Hypothesis, uv, ruff, mypy, Git.

## Global Constraints

- Start from `05104007b1c58c12192a8fb88c76ed323531455d` on `main`.
- Execute Phase 0 through Phase 4 in order and stop after three consecutive non-Pareto candidates.
- Never branch on symbol, calendar date, or requested universe size.
- Never use full-history returns to select references or refresh a baseline to admit a candidate.
- Never apply a constant global bull exposure haircut, copy trade's three-sleeve architecture, or run an unbounded parameter search.
- Every production change starts with a failing behavior test and is independently committed only after focused tests and economic sentinels pass.
- Preserve the sole daily/backtest path, PIT inputs, close-t/next-open execution, T+1, costs, limits, suspensions, lots, and partial fills.

---

### Task 1: Freeze Phase 0 evidence

**Files:** Create local untracked evidence under `artifacts/pareto_sprint_v2/start/`; do not modify `benchmarks/*.json`.

**Interfaces:** Consume committed promotion/smoke references; produce reports containing commit, source/data/config hashes, universe, window, and execution contract.

- [ ] **Step 1: Record immutable identities**

```bash
mkdir -p artifacts/pareto_sprint_v2/start
git rev-parse HEAD > artifacts/pareto_sprint_v2/start/commit.txt
sha256sum benchmarks/*.json > artifacts/pareto_sprint_v2/start/benchmark-sha256.txt
git status --porcelain=v1 > artifacts/pareto_sprint_v2/start/status.txt
```

- [ ] **Step 2: Run manifest and the full 35-cell promotion baseline**

```bash
uv run python -m uquant.validation data-manifest --data-dir data/frozen \
  > artifacts/pareto_sprint_v2/start/data-manifest.json
uv run python -m uquant.validation promotion --data-dir data/frozen \
  --baseline benchmarks/promotion_baseline.json --profile full \
  --output artifacts/pareto_sprint_v2/start/promotion-full.json
```

- [ ] **Step 3: Reproduce the 24-case smoke and audit missing reviewed references**

```bash
uv run python scripts/run_pareto_evidence.py smoke \
  --output artifacts/pareto_sprint_v2/start/generalization-smoke.json
uv run python scripts/run_pareto_evidence.py reference-audit \
  --output artifacts/pareto_sprint_v2/start/reference-audit.json
```

- [ ] **Step 4: Commit the evidence runner only after its red/green cycle**

```bash
git add tests/test_pareto_evidence.py scripts/run_pareto_evidence.py
git commit -m "Add reproducible Pareto evidence runner"
```

### Task 2: Produce First Divergence diagnostics

**Files:** Modify `research/candidate_runner.py` only if a trace layer is missing; test `tests/test_first_divergence.py`; write local JSON under `artifacts/pareto_sprint_v2/divergence/`.

**Interfaces:** `CandidateRunner.trace_cell(...) -> CellTrace`; diagnostics expose risk/reference evidence, regime, leaders, cohort, targets, orders, fills, and equity.

- [ ] **Step 1: Write a failing trace-completeness test**

```python
def test_decision_trace_exposes_reference_and_risk_evidence() -> None:
    names = {field.name for field in fields(DecisionTrace)}
    assert {"reference_evidence", "risk_evidence", "leaders", "targets", "orders", "fills"} <= names
```

- [ ] **Step 2: Verify RED, add only immutable evidence already emitted by production, then verify GREEN**

```bash
uv run pytest tests/test_first_divergence.py::test_decision_trace_exposes_reference_and_risk_evidence -q
uv run pytest tests/test_first_divergence.py tests/test_research.py -q
```

- [ ] **Step 3: Generate the approved A/B/C acute, A choppy, remove-core/no-optical, and worst-random diagnostics**

```bash
uv run python scripts/run_pareto_evidence.py divergence \
  --baseline artifacts/pareto_sprint_v2/start \
  --output-dir artifacts/pareto_sprint_v2/divergence
```

- [ ] **Step 4: Commit the trace extension independently**

```bash
git add research/candidate_runner.py tests/test_first_divergence.py
git commit -m "Expose causal evidence in divergence traces"
```

### Task 3: Candidate 1 — independent-evidence confidence

**Files:** Modify `uquant/reference.py`, `uquant/risk.py`, `uquant/config.py`; test `tests/test_industry_rotation.py`, `tests/test_risk_transitions.py`.

**Interfaces:** A bounded confidence score uses coverage, group coverage, residual correlation, breadth agreement, and freshness. It affects gross only when low confidence and active deterioration coincide.

- [ ] **Step 1: Write failing size-invariance and healthy-bull tests**

```python
def test_independent_evidence_confidence_is_size_invariant() -> None:
    sparse = _reference_context(coverage=1.0, groups=("a", "b"), correlation=0.80)
    duplicated = _duplicate_names_within_groups(sparse, copies=4)
    assert evidence_confidence(sparse) == pytest.approx(evidence_confidence(duplicated))

def test_low_confidence_alone_does_not_cut_healthy_bull_gross() -> None:
    result = _assess(reference=_reference_context(coverage=0.55), deteriorating=False)
    assert result.target_gross_cap == pytest.approx(DEFAULT_CONFIG.max_gross)
```

- [ ] **Step 2: Verify RED, implement the minimum conjunction, then run focused GREEN**

```bash
uv run pytest tests/test_industry_rotation.py tests/test_risk_transitions.py -q
```

- [ ] **Step 3: Run A/B/C acute, A choppy, bull A-E, and smoke sentinels**

```bash
uv run python scripts/run_pareto_evidence.py candidate --candidate confidence-risk \
  --output artifacts/pareto_sprint_v2/candidates/confidence-risk.json
```

- [ ] **Step 4: Commit only if independently Pareto-admitted**

```bash
git add uquant/reference.py uquant/risk.py uquant/config.py tests/test_industry_rotation.py tests/test_risk_transitions.py
git commit -m "Condition risk on independent evidence confidence"
```

### Task 4: Candidate 2 — evidence-conditioned strategic admission

**Files:** Modify `uquant/config.py`, `uquant/portfolio_strategic.py`; test `tests/test_config_contracts.py`, `tests/test_lifecycle_and_risk.py`.

**Interfaces:** Cohort size and gross derive from qualified-member confidence, independent industries, dispersion, correlation, and reference confidence; no comparison uses requested-universe length.

- [ ] **Step 1: Write failing irrelevant-universe and same-industry tests**

```python
def test_strategic_admission_is_invariant_to_irrelevant_universe_expansion() -> None:
    base = _dynamic_cohort_inputs(extra_weak_symbols=0)
    expanded = _dynamic_cohort_inputs(extra_weak_symbols=12)
    assert _admission_signature(base) == _admission_signature(expanded)

def test_two_name_admission_requires_independent_group_evidence() -> None:
    account = _run_strategic_candidate(groups=("optical", "optical"), confidence=0.95)
    assert account.strategic_cohort_targets == {}
```

- [ ] **Step 2: Verify RED, replace size ownership with causal evidence, then run focused GREEN**

```bash
uv run pytest tests/test_config_contracts.py tests/test_lifecycle_and_risk.py -q
```

- [ ] **Step 3: Run remove-one/remove-all/no-optical, bull A-E, and full smoke**

```bash
uv run python scripts/run_pareto_evidence.py candidate --candidate evidence-cohort \
  --output artifacts/pareto_sprint_v2/candidates/evidence-cohort.json
```

- [ ] **Step 4: Commit only if independently Pareto-admitted**

```bash
git add uquant/config.py uquant/portfolio_strategic.py tests/test_config_contracts.py tests/test_lifecycle_and_risk.py
git commit -m "Make strategic admission evidence conditioned"
```

### Task 5: Candidate 3 — causal concentration risk budget

**Files:** Modify `uquant/portfolio.py`, `uquant/risk.py`, `uquant/config.py`; test `tests/test_conviction_guard.py`, `tests/test_lifecycle_and_risk.py`.

**Interfaces:** A guard reacts to projected top-contributor share only when evidence diversification is low and residual correlation is high; risk exits bypass it and diversified high-confidence bulls retain gross.

- [ ] **Step 1: Write failing conjunction and protected-exit tests**

```python
def test_concentration_budget_reduces_only_uncorroborated_top_share() -> None:
    guarded = _allocate(top_share=0.60, evidence_groups=1, residual_correlation=0.90)
    corroborated = _allocate(top_share=0.60, evidence_groups=3, residual_correlation=0.30)
    assert sum(guarded.values()) < sum(corroborated.values())

def test_concentration_budget_never_delays_risk_exit() -> None:
    assert _exit_orders(concentration_guard=True) == _exit_orders(concentration_guard=False)
```

- [ ] **Step 2: Verify RED, implement the smallest conjunction, then run focused GREEN**

```bash
uv run pytest tests/test_conviction_guard.py tests/test_lifecycle_and_risk.py -q
```

- [ ] **Step 3: Run random-6/12, remove-core, A/B/C acute, and bull A-E**

```bash
uv run python scripts/run_pareto_evidence.py candidate --candidate concentration-budget \
  --output artifacts/pareto_sprint_v2/candidates/concentration-budget.json
```

- [ ] **Step 4: Commit only if independently Pareto-admitted**

```bash
git add uquant/portfolio.py uquant/risk.py uquant/config.py tests/test_conviction_guard.py tests/test_lifecycle_and_risk.py
git commit -m "Guard uncorroborated portfolio concentration"
```

### Task 6: Candidate 4 — economic hysteresis only when attribution proves need

**Files:** Modify the existing coalescing owner identified by `research/trade_attribution.py`; test `tests/test_execution.py`.

**Interfaces:** Coalesce only same-direction sub-material lifecycle changes whose expected benefit is below cost; every risk reduction bypasses it.

- [ ] **Step 1: Run attribution and admit this task only if repeated small orders have negative net economics**

```bash
uv run python scripts/run_pareto_evidence.py attribution \
  --output artifacts/pareto_sprint_v2/divergence/order-attribution.json
```

- [ ] **Step 2: Write a failing protected-exit bypass test**

```python
def test_risk_exit_bypasses_economic_coalescing() -> None:
    order = _target_change(delta=-0.01, exit_kind="RISK_REDUCTION")
    assert coalesce_order(order, expected_benefit=0.0) is order
```

- [ ] **Step 3: Run RED/GREEN and retain only if p90 orders improve without economic regression**

```bash
uv run pytest tests/test_execution.py -q
uv run python scripts/run_pareto_evidence.py candidate --candidate economic-hysteresis \
  --output artifacts/pareto_sprint_v2/candidates/economic-hysteresis.json
```

### Task 7: Phase 3 Universe Stress

**Files:** Reuse `research/generalization_smoke.py`, `research/universe_stress.py`, and `uquant/validation/generalization.py`; write local reports under `artifacts/pareto_sprint_v2/final/`.

**Interfaces:** Run 100-300 deterministic cases spanning random-6/12/24, remove-one/pair/all, no-optical, industry-only, balanced, leave-top-k, add-one, and order invariance with immutable provenance.

- [ ] **Step 1: Run deterministic stress and compare against the frozen start**

```bash
uv run python scripts/run_pareto_evidence.py stress --seeds 40 \
  --output artifacts/pareto_sprint_v2/final/universe-stress.json
uv run python scripts/run_pareto_evidence.py compare \
  --baseline artifacts/pareto_sprint_v2/start \
  --candidate artifacts/pareto_sprint_v2/final \
  --output artifacts/pareto_sprint_v2/final/pareto-comparison.json
```

- [ ] **Step 2: Confirm p90/worst drawdown improve without p90 orders or p10/median wealth regression**

```bash
uv run pytest tests/test_generalization.py tests/test_generalization_smoke.py tests/test_research.py -q
```

### Task 8: Phase 4 verification and direct publication

**Files:** Synchronize `README.md`, `docs/STRATEGY.md`, `docs/CONFIGURATION.md`, `docs/PERFORMANCE.md`, and `docs/DEVELOPMENT.md` only for retained behavior.

**Interfaces:** Produce fresh full-suite, PIT, daily/backtest, promotion, stress, competitor, and documentation-consistency evidence from the final committed candidate.

- [ ] **Step 1: Run static, full test, coverage, build, security, manifest, and full promotion gates**

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts research tests
uv build
uv run bandit -q -r uquant
uv run pip-audit
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run python -m uquant.validation promotion --data-dir data/frozen \
  --baseline benchmarks/promotion_baseline.json --profile full \
  --output artifacts/pareto_sprint_v2/final/promotion-full.json
```

- [ ] **Step 2: Run daily/backtest equivalence, PIT, stress, and competitor reference audit**

```bash
uv run pytest tests/test_engine_contracts.py tests/test_properties.py \
  tests/test_generalization.py tests/test_competitor_validation.py -q
uv run python scripts/run_pareto_evidence.py final \
  --output-dir artifacts/pareto_sprint_v2/final
```

- [ ] **Step 3: Inspect scope and rejected candidates**

```bash
git status -sb
git diff --check origin/main...HEAD
git log --oneline origin/main..HEAD
```

- [ ] **Step 4: Push the fully verified commit directly to main and verify the remote SHA**

```bash
git push origin HEAD:main
git ls-remote origin refs/heads/main
```

