### Task 3: Centralize the sole linear quantile authority

**Files:**
- Create: `uquant/validation/statistics.py`
- Modify: `uquant/validation/generalization_matrix_evidence.py`
- Modify: `uquant/validation/generalization_policy/tail_evaluation.py` and direct callers using duplicate percentile logic
- Create: `tests/test_validation_statistics.py`
- Modify: affected existing generalization tests

**Interfaces:**
- Consumes: finite numeric sequences and a probability in `[0, 1]`.
- Produces: `linear_quantile(values: Sequence[float], probability: float) -> float`, using sorted values and interpolation at `(n - 1) * probability`.

- [ ] **Step 1: Write RED quantile tests**

Cover n=1, n=2, odd/even samples, p=0.10, p=0.90, p=0, p=1, order independence, empty input, out-of-range probability, booleans, NaN, and Infinity.

- [ ] **Step 2: Implement the minimal finite linear interpolation owner**

Reject invalid inputs before sorting; calculate lower/upper indices from `(n - 1) * p`; interpolate without nearest-rank/midpoint/lower/higher behavior.

- [ ] **Step 3: Replace duplicate production-validation implementations**

Make matrix and old policy tails import the single function. Preserve public compatibility aliases only where existing API contracts require them; remove copied formulas.

- [ ] **Step 4: Verify and checkpoint**

Run focused statistics, old matrix/policy tests, exact affected architecture/import/public-API gates, Ruff, strict MyPy, `compileall`, and diff check; commit, push, and verify remote SHA.

---

