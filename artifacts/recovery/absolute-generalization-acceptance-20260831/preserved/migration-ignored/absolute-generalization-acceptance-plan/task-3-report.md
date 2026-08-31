# Task 3 report — sole linear quantile authority

Status: `READY_FOR_REREVIEW`

Base checkpoint: `85e03f83612892e7efdfa8ee38eb73d3513dc291`

Base tree: `dff35c0a7abd5176b212ca01205f0139f80cf14d`

Review-input tree staged by the controller:
`7664718d921deb6f7f681f1f6b01100b1b55cc3b`

Fix-round-1 candidate tree (temporary-index projection; the real index was
not modified): `7421ee4feae98d8e2ceeba871a8f185045ca7983`

Candidate binary diff SHA-256:
`75ebc3d4fda864379144a2fed6f134baa12a447c8b5aca9d1bfeb4a10ae66701`

No commit or push was performed by the implementer.

## Scope delivered

- Added `uquant.validation.statistics.linear_quantile` as the only owner of
  sorted `(n - 1) * probability` interpolation.
- Validates probability and every value as finite built-in numeric input,
  rejects booleans, rejects empty input, and validates all values before
  sorting.
- Replaced copied formulas in:
  - `uquant/validation/generalization/metrics.py`
  - `uquant/validation/generalization_matrix_evidence.py`
  - `uquant/validation/generalization_policy/cell_policy.py`
- Preserved the existing `quantile`, `matrix_quantile`, and `policy_quantile`
  public signatures as one-call compatibility transports. The metrics facade
  no longer aliases the central function object, so its legacy module metadata
  rewrite cannot mutate the central owner's pickle identity.
- Registered the new module only in `validation_runner_v1` and
  `full_package_v1`; `economic_decision_v1` remains unchanged.
- Classified the new module as `validation_runner` in the explicit
  architecture authority map.
- Added finite/interpolation/compatibility tests and a regression for frozen
  contract loading across validation-only registry evolution.

## Rulings

1. The plan names `generalization_policy/tail_evaluation.py`, but the live
   duplicate formula is in `generalization_policy/cell_policy.py`.
   `tail_evaluation.py` only calls the latter. I migrated the real owner and
   did not create an unrelated edit merely to match the stale path. Cost if
   wrong: a hidden direct formula in the named file would remain; the source
   scan below shows it does not.
2. The Task 2 contract's `source_surface_registry_sha256` is freeze-time
   provenance, not a requirement that every future validation-only registry
   revision reseal the frozen contract. The contract bytes and compiled seal
   remain exactly `af3882c...`; the current registry independently validates
   at `5d6e2a...`, while the economic surface still hashes to `d1ef7977...`.
   The loader no longer compares the whole current registry seal to the
   frozen provenance, but it still loads the strict current registry through
   `source_surface_fingerprint` and rejects any economic source mismatch.
   Cost if wrong: a requirement for whole-registry immutability would instead
   require pre-registering nonexistent future files, which conflicts with the
   exact-physical-path source registry gate.

## TDD evidence

### Initial RED

Command:

```text
.venv/bin/python -m pytest -q tests/test_validation_statistics.py
```

Result: `26 failed`; every failure was the expected missing feature:
`ModuleNotFoundError: No module named 'uquant.validation.statistics'`.

Covered n=1, n=2, odd/even samples, p10, p90, p=0, p=1, order independence,
empty values, booleans, non-numeric values/probability, NaN, Infinity, and
legacy-owner transport.

### Initial GREEN

Same command after the minimal owner and migrations:

```text
26 passed
```

### Frozen-registry coupling RED/GREEN

After registering the new validation module while restoring the immutable
Task 2 contract bytes/seal, the two focused regressions produced:

```text
FF
ValueError: absolute generalization source registry identity differs
2 failed
```

The first proved a validation-only registry revision could not load the
frozen contract; the second proved this stale check masked the intended
economic-source mismatch error.

After removing only the whole-registry equality from the independent runtime
authority check:

```text
..  [100%]
2 passed
```

The current economic source mismatch remains rejected with
`absolute generalization candidate source identity differs`.

### Compatibility transport RED/GREEN

The frozen public-API gate and the delegation test first failed because a
direct alias was not attributed to the two legacy public modules. Thin
one-call transports then produced:

```text
..  [100%]
2 passed
```

The transport test verifies each module's private imported owner is the
central function, its public compatibility function has only
`_linear_quantile` in `co_names`, valid output is delegated, and central
bool rejection propagates.

### Architecture helper-name RED/GREEN

The final non-portfolio architecture group produced 63 passes and two
failures:

- `test_architecture_duplicate_private_helper_debt_is_zero_without_generic_utils`
- `test_architecture_current_blockers_match_empty_acceptance_allowlist`

Both reported the same new duplicate helper group for `_finite_number`.
Renaming it to the domain-specific `_finite_quantile_number` was the only
change. Exact failed-node rerun:

```text
..  [100%]
2 passed
```

No allowlist or architecture baseline was changed.

### Review fix round 1

The reviewer raised three Important findings.

1. Source-surface transport RED:

   ```text
   .venv/bin/python -m pytest -q \
     tests/architecture/test_risk_boundaries.py::test_risk_source_surface_migration_is_exact_and_requirements_stay_bound
   1 failed
   Extra items in the left set: 'uquant/validation/statistics.py'
   ```

   The minimal fix registered the physical statistics owner in
   `_VALIDATION_ADDITIONS`. The exact risk and execution source-surface nodes
   then passed together: `2 passed`.

2. Central-owner metadata RED:

   ```text
   .venv/bin/python -m pytest -q \
     tests/test_validation_statistics.py::test_generalization_facade_preserves_central_quantile_owner_metadata
   1 failed
   assert 'uquant.validation.generalization' == 'uquant.validation.statistics'
   ```

   The direct metrics alias let the legacy facade rewrite the central
   function's `__module__`. Replacing only that alias with a one-call wrapper
   preserved the legacy API while keeping the central function pickleable at
   `uquant.validation.statistics.linear_quantile`. The metadata/pickle and
   shared-owner transport tests then passed together: `2 passed`.

3. Two requested validation-order regressions were added without changing
   production code: heterogeneous `[1.0, "bad"]` is a controlled `ValueError`,
   and invalid probability is rejected before a sentinel `Sequence` receives
   any `len`, `getitem`, or `iter` call. Both tests passed immediately on the
   review-input implementation (`2 passed`), so this was truthfully classified
   as a missing-coverage finding rather than fabricating a RED.

## Final verification evidence

Focused statistics, absolute contract, source contract, and source
provenance:

```text
.venv/bin/python -m pytest -q \
  tests/test_validation_statistics.py \
  tests/test_absolute_generalization_contract.py \
  tests/test_source_surface_contracts.py \
  tests/test_source_surface_provenance.py
64 passed
```

Legacy generalization, matrix, and contract behavior after compatibility
wrappers:

```text
.venv/bin/python -m pytest -q \
  tests/test_generalization.py \
  tests/test_generalization_matrix.py \
  tests/test_generalization_contract.py
173 passed
```

Exact source/import/public-owner/public-API architecture nodes:

```text
.venv/bin/python -m pytest -q \
  tests/architecture/test_source_surface_contracts.py \
  tests/architecture/test_import_contracts.py::test_every_internal_module_has_an_explicit_authority \
  tests/architecture/test_import_contracts.py::test_cross_module_private_import_debt_is_exact_and_can_only_shrink \
  tests/architecture/test_import_contracts.py::test_internal_import_cycles_are_exact_and_can_only_disappear \
  tests/architecture/test_validation_public_owners.py \
  tests/architecture/test_public_api_contracts.py::test_public_names_signatures_dataclasses_enums_and_runtime_contracts_match_current_contract \
  tests/architecture/test_risk_boundaries.py::test_risk_source_surface_migration_is_exact_and_requirements_stay_bound \
  tests/architecture/test_execution_application_boundaries.py::test_execution_source_surface_migration_is_exact_for_all_five_v1_surfaces
14 passed
```

Additional non-portfolio architecture governance/import/validation-debt
group: 65 collected; 63 passed before the domain-specific helper rename, the
two exact failures then passed as shown above. The rename was behavior-neutral
and touched only the helper identifier.

Full static gates before that identifier-only rename, followed by exact
touched-file static gates after the rename:

```text
.venv/bin/ruff check .
All checks passed!

.venv/bin/mypy uquant scripts research
Success: no issues found in 286 source files

.venv/bin/python -m compileall -q uquant scripts research tests
exit 0

git diff --check
exit 0

.venv/bin/ruff check uquant/validation/statistics.py tests/test_validation_statistics.py
All checks passed!

.venv/bin/mypy uquant/validation/statistics.py
Success: no issues found in 1 source file

.venv/bin/python -m compileall -q \
  uquant/validation/statistics.py tests/test_validation_statistics.py
exit 0
```

Fix round 1 reran every full static gate on the final working tree:

```text
.venv/bin/ruff check .
All checks passed!

.venv/bin/mypy uquant scripts research
Success: no issues found in 286 source files

.venv/bin/python -m compileall -q uquant scripts research tests
exit 0

git diff HEAD --check
exit 0
```

Formula-owner source scan:

```text
rg -n '\(len\(ordered\) - 1\) \*|math\.floor\(location\)|math\.ceil\(location\)' \
  uquant/validation
uquant/validation/statistics.py:37:    location = (len(ordered) - 1) * selected_probability
uquant/validation/statistics.py:38:    lower = math.floor(location)
uquant/validation/statistics.py:39:    upper = math.ceil(location)
```

Identities on the final candidate:

```text
frozen absolute contract seal = af3882c594372ae0f5d4665990f5ead6bea99faaf0916f803239256c8ec6baf6
current source registry seal   = 5d6e2a30c1a7c20a136d20d2741f1043e752db7ef567d5cff8499d3ed21fa6b1
economic_decision_v1 source    = d1ef7977ae482e46a920381e6af58791199ec8e1a02586dbe8df451e7d4696c9
```

The user-excluded slow local `architecture-portfolio` shard was not run.
Neither Extended Performance Matrix nor Extended Economic Matrix was run.

## Files in candidate tree

- `benchmarks/source_surface_registry.json`
- `tests/architecture/_analysis_authorities.py`
- `tests/architecture/_owner_transport.py`
- `tests/test_absolute_generalization_contract.py`
- `tests/test_validation_statistics.py`
- `uquant/validation/absolute_generalization/contract.py`
- `uquant/validation/generalization/metrics.py`
- `uquant/validation/generalization_matrix_evidence.py`
- `uquant/validation/generalization_policy/cell_policy.py`
- `uquant/validation/statistics.py`

Candidate diff: 10 files, 269 insertions, 41 deletions.

## Self-review

- No threshold, economic behavior, production authority, universe, config,
  frozen baseline, or economic source surface changed.
- No copied floor/ceil interpolation formula remains outside the new owner.
- Matrix and policy compatibility APIs keep their prior public signatures.
- Invalid inputs are checked before sorting; bool, NaN, Infinity, empty, and
  out-of-range probability fail closed.
- Contract bytes and compiled seal remain unchanged from Task 2.
- The controller's review-input index remains exactly
  `7664718d921deb6f7f681f1f6b01100b1b55cc3b`; all three fix-round file changes
  remain unstaged (` M`, `AM`, and `MM`) for the controller to freeze.

Concerns: none. Ready for independent Task 3 re-review.

## Checkpoint

- Independent fix-round re-review of snapshot
  `3271cce28fd37cdfb255205a7f6907cb39a93594` (tree
  `7421ee4feae98d8e2ceeba871a8f185045ca7983`) returned `READY` with no
  Critical, Important, or Minor findings.
- GitHub commit: `5d2fc07e8d69878b78f2e482a7026a62abd126e5`.
- All 10 uploaded blob SHAs matched the local staged blobs, and the GitHub tree
  matched the reviewed tree exactly.
- The remote feature ref was advanced non-force, fetched, and verified. Local
  `HEAD`, upstream, and remote ref all resolve to the checkpoint; the worktree
  is clean.
