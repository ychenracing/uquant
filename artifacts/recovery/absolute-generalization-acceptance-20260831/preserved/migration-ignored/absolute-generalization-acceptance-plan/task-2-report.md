# Task 2 — absolute contract and canonical scenarios

Status: `READY_FOR_FINAL_REREVIEW`

Worktree: `/workspace/scratch/1a8f428176e6/uquant-base/.worktrees/absolute-generalization-acceptance`

Baseline and current HEAD: `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`

No commit or push was performed. This follows the controller transport adjustment;
the controller will preserve these exact verified bytes and create the checkpoint.

## Outcome

Task 2 now provides one strict, immutable, compile-sealed Absolute Generalization
Acceptance contract and a deterministic builder for exactly 34 canonical
leave-one-out scenarios. The loader fails closed on non-canonical JSON, duplicate
keys, `NaN`, `Infinity`, numeric overflow to infinity, changed bytes, resealed
tampering, identity drift, unsafe/missing contract paths, and source/input
authority drift. The public package exposes no writer or auto-acceptance path.

The scenario builder accepts only the complete currently validated contract
instance: besides checking the 34/34 scenario projection, fixed shards, flags,
and window, it reloads and independently validates the sealed contract and
requires full dataclass equality. Thus `dataclasses.replace` tampering of
thresholds, candidate identity, baseline identity, or relaxation policy is
rejected even when scenario-facing fields and the original seal remain present.

No economic production behavior was changed.

## TDD evidence

Tests were written before any Task 2 production, contract, or registry
implementation.

### Initial RED

```text
uv run pytest -q tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py
```

Exit code `1`; exact summary: `14 failed in 0.44s`.

```text
FFFFFFFFFFFFFF                                                           [100%]
13 failures: ModuleNotFoundError: No module named 'uquant.validation.absolute_generalization'
1 failure: expected absolute-generalization paths are absent from validation_runner_v1
=========================== short test summary info ============================
14 failed in 0.44s
```

These were the expected missing-feature failures. The complete raw output was
retained at `/tmp/task2-red-focused.log`. At that point only the two new test
files existed; no production, contract, or registry file had been created or
modified.

### Minimal implementation GREEN

The same focused command then exited `0` with `15 passed`.

### Scenario-semantics self-review RED/GREEN

Tests were added for direct replacement of shard, critical, witness, and window
semantics before strengthening the builder:

```text
uv run pytest -q tests/test_absolute_generalization_scenarios.py::test_scenario_builder_rejects_replaced_contract_semantics
```

Exit code `1`; `4 failed`, each with
`Failed: DID NOT RAISE <class 'ValueError'>`. After the minimal
frozen-scenario validation was added, the focused suite exited `0` with
`19 passed`.

### Complete-contract self-review RED/GREEN

Tests were next added for replacement of non-scenario fields while retaining the
original seal: relaxation policy, candidate identity, thresholds, and frozen
baseline identity.

```text
uv run pytest -q tests/test_absolute_generalization_scenarios.py::test_scenario_builder_requires_the_complete_validated_contract_instance
```

Exit code `1`; `4 failed`, each with
`Failed: DID NOT RAISE <class 'ValueError'>`. The minimal fix reloads the
canonical contract through the strict loader and requires complete contract
equality. The focused suite then exited `0` with `23 passed`.

### JSON numeric-overflow RED/GREEN

The `1e999` rejection test was authored before production. To directly prove
that its finite-number guard is effective, the guard was temporarily removed,
the single test was executed, and the exact implementation was restored:

```text
uv run pytest -q tests/test_absolute_generalization_contract.py -k '1e999'
```

Without the guard: exit code `1`; `1 failed`. The expected loader error
`only finite numbers` was absent and canonical encoding instead reported an
out-of-range `inf`. After restoring the guard: exit code `0`; `1 passed`.
No transient mutation remains.

### Final focused GREEN

```text
uv run pytest -q tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py
```

Exit code `0`; `23 passed` (`....................... [100%]`).

## Frozen contract and identities

- Canonical contract seal, file / recomputed / compiled after review fix round 1:
  `af3882c594372ae0f5d4665990f5ead6bea99faaf0916f803239256c8ec6baf6`
- Source-surface registry seal:
  `da0418442020762272b3b5008c17b515794688270b4940313ccfdfd0b13877cb`
- Current candidate source identity, independently reproduced from both the
  `d7fd3bf8...` git authority and current worktree registry:
  `d1ef7977ae482e46a920381e6af58791199ec8e1a02586dbe8df451e7d4696c9`
- Historical frozen baseline source identity (separate and unchanged):
  `cacef64c25053a84e1aad073feec252d8cb9d2decb19576460642a3b6ec6573f`
- Strategic ownership contract identity:
  `72e6b510c3bcf44ac77d2c13613f4d72a14ae8dab0d60a19e5947055ae7cbf08`
- Canonical AI universe identity:
  `03f42c5066fb8e1c7b2f8e1b7dd38d508d8053f548ebb5596317ce587d7cffd0`
- Effective config identity:
  `dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5`
- `uv.lock` identity:
  `4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61`

The unavailable historical seal beginning `d85d875` was not reproduced,
claimed, embedded, or fabricated. It remains historical provenance outside the
new schema. The new contract has a real canonical preimage and the seal above.

The identity verification command exited `0` and reported canonical file bytes,
all three contract seals equal, candidate git/worktree identities equal, the
historical baseline and ownership identities unchanged, and exactly `34`
symbols across `loo-a` through `loo-f`.

## Frozen semantics

- Window: `2023-01-03` through `2026-08-05`.
- Exactly 34 canonical symbols/cells and six fixed, disjoint, exhaustive shards.
- Critical removals, fixed order: `sz300308`, `sz300502`, `sz300394`.
- Witnesses: `sh603688`, `sh688008`, `sh688082`, `sz002409`,
  `sz300666`.
- Seven required capability components.
- Champion wealth `24.509661802900865`; 95% floor
  `23.28417871275582`; maximum drawdown `0.30`.
- Strict positive is `final_wealth > 1.0`; positive fraction `0.90`; p10
  wealth `>= 1.0`; p90 MDD `<= 0.30`; witness fraction `1.0`.
- p90/worst healthy zero streak limits `60`/`120`; failed retry `20`;
  terminal SCC `60`.
- Repair tuples: `(1,0,20)`, `(2,1,40)`, `(3,2,60)`, `(4,3,60)`.
- Repeated crowns: `2` epochs and `2` distinct owners.
- `baseline_can_relax_absolute_limits=false`.

## Affected verification

### Contract, source, public API, and non-portfolio architecture

```text
uv run pytest -q tests/test_source_surface_contracts.py tests/test_source_surface_provenance.py tests/test_ai_universe_contract.py tests/architecture/test_source_surface_contracts.py tests/architecture/test_public_api_contracts.py
```

The first governance run found two integration gaps: the new modules lacked
architecture authority, and the contract resource had been projected onto
`full_package_v1` although that resource boundary is closed. After adding the
validation-runner authority and restricting the resource to
`validation_runner_v1`, the rerun exited `0`: `35 passed`.

```text
uv run pytest -q tests/test_engine_contracts.py tests/architecture/test_distribution_contracts.py tests/architecture/test_validation_boundaries.py tests/architecture/test_repository_governance.py tests/architecture/test_release_acceptance.py
```

Collected `111`; result: `2 failed, 108 passed, 1 skipped`. Both failures
were the exact validation-relocation registry resource projection gap. The skip
was the pre-existing conditional release-candidate check. After updating the
projection, both failed tests were rerun and passed (`2 passed`). The unchanged
108 passes were reused per repository gate guidance.

```text
uv run pytest -q tests/architecture/test_validation_boundaries.py::test_validation_policy_relocation_is_closed_and_source_bound tests/architecture/test_validation_boundaries.py::test_validation_policy_resigned_relocation_tamper_is_rejected
```

Exit code `0`; `2 passed`.

```text
uv run pytest -q tests/architecture/test_execution_application_boundaries.py::test_execution_source_surface_migration_is_exact_for_all_five_v1_surfaces tests/architecture/test_risk_boundaries.py::test_risk_source_surface_migration_is_exact_and_requirements_stay_bound
```

Exit code `0`; `2 passed`.

```text
uv run pytest -q tests/test_strategic_ownership_acceptance.py tests/test_source_surface_contracts.py tests/test_source_surface_provenance.py tests/test_ai_universe_contract.py tests/architecture/test_source_surface_contracts.py tests/architecture/test_public_api_contracts.py tests/architecture/test_import_contracts.py tests/architecture/test_validation_boundaries.py::test_validation_policy_relocation_is_closed_and_source_bound tests/architecture/test_validation_boundaries.py::test_validation_policy_resigned_relocation_tamper_is_rejected tests/architecture/test_execution_application_boundaries.py::test_execution_source_surface_migration_is_exact_for_all_five_v1_surfaces tests/architecture/test_risk_boundaries.py::test_risk_source_surface_migration_is_exact_and_requirements_stay_bound
```

Exit code `0`; `57 passed`.

```text
uv run pytest -q tests/architecture/test_complexity_budgets.py tests/architecture/test_validation_public_owners.py
```

Exit code `0`; `10 passed`.

### Static gates

```text
uv run ruff check uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture/_analysis_authorities.py tests/architecture/_owner_transport.py tests/architecture/_validation_relocation.py tests/architecture/test_execution_application_boundaries.py tests/architecture/test_portfolio_boundaries.py tests/architecture/test_risk_boundaries.py
```

Exit code `0`: `All checks passed!`

```text
uv run mypy --strict uquant/validation/absolute_generalization/__init__.py uquant/validation/absolute_generalization/contract.py uquant/validation/absolute_generalization/scenarios.py
```

Exit code `0`: `Success: no issues found in 3 source files`.

An earlier diagnostic attempt to pass architecture test files directly to strict
MyPy exited `2` before checking them because this repository invocation
discovers `tests/architecture/__init__.py` under both `architecture` and
`tests.architecture`. That is not a configured gate; the required strict check
of every touched production module is green above.

```text
uv run python -m compileall -q uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture
```

Exit code `0`; no output.

```text
git diff --check
```

Exit code `0`; no output.

## Changed files

New:

- `benchmarks/absolute_generalization_acceptance_contract.json`
- `uquant/validation/absolute_generalization/__init__.py`
- `uquant/validation/absolute_generalization/contract.py`
- `uquant/validation/absolute_generalization/scenarios.py`
- `tests/test_absolute_generalization_contract.py`
- `tests/test_absolute_generalization_scenarios.py`

Modified:

- `benchmarks/source_surface_registry.json`
- `tests/architecture/_analysis_authorities.py`
- `tests/architecture/_owner_transport.py`
- `tests/architecture/_validation_relocation.py`
- `tests/architecture/test_execution_application_boundaries.py`
- `tests/architecture/test_portfolio_boundaries.py`
- `tests/architecture/test_risk_boundaries.py`

This report is intentionally stored under the ignored SDD report directory.

## Self-review

- Confirmed strict canonical JSON and exact schema; no duplicate/nonfinite
  compatibility path and no contract writer/auto-accept export exists.
- Confirmed compile sealing prevents changed content plus a new self-consistent
  seal from being accepted.
- Confirmed the scenario builder rejects both scenario-field and non-scenario
  dataclass replacement with the old seal.
- Confirmed current candidate identity remains independent from the unchanged
  historical frozen baseline identity.
- Confirmed contract and ownership contract use the exact canonical AI universe,
  and all six shards are disjoint and exhaustive.
- Confirmed the resource is only on `validation_runner_v1`; Python sources are
  validation/full-package sources, never economic-decision sources.
- Reviewed the complete diff and found no unrelated production change.

## Review fix round 1

The independent review returned one Critical and two Important findings. Each
was verified against the repository, reproduced with a test written before its
production fix, and repaired independently.

### Critical — hostile equality bypass

RED command:

```text
uv run pytest -q tests/test_absolute_generalization_scenarios.py::test_scenario_builder_rejects_hostile_equal_contract_lookalikes
```

Exit code `1`; exact result: `2 failed`. Both the
`AlwaysEqualContract` subclass and hostile duck case reported
`Failed: DID NOT RAISE <class 'ValueError'>`.

Minimal fix: before reading any supplied attribute, the scenario builder now
requires `type(contract) is AbsoluteGeneralizationContract`. Only after this
exact-type gate does it compare against a freshly loaded trusted dataclass, so
the generated equality implementation is no longer attacker-controlled.

GREEN: the same command exited `0`; `2 passed`.

### Important — independent baseline/candidate source identities

RED command:

```text
uv run pytest -q tests/test_absolute_generalization_contract.py::test_contract_binds_candidate_and_frozen_inputs_to_independent_authorities tests/test_absolute_generalization_contract.py::test_loader_binds_baseline_and_evolving_candidate_sources_independently
```

Exit code `1`; exact result: `2 failed`.

- The canonical candidate object lacked the required
  `baseline_source_sha256` field.
- A controlled fixture with different baseline and candidate fingerprints was
  rejected by the old candidate identity comparison, demonstrating that the
  loader had coupled the two authorities.

Minimal fix: the candidate schema/dataclass now records
`baseline_source_sha256` separately from `production_source_sha256`.
`_BASELINE_SOURCE` and `_CANDIDATE_SOURCE` are distinct compiled
authorities. The frozen git commit fingerprint is compared only with the former;
the evolving worktree fingerprint is compared only with the latter. Their
initial values are both the independently reproduced `d1ef797...`; the
historical frozen baseline remains `cacef64...`.

The contract was canonically regenerated, producing the real seal
`af3882c594372ae0f5d4665990f5ead6bea99faaf0916f803239256c8ec6baf6`.
The source registry document did not change in this fix round, so its canonical
seal remains
`da0418442020762272b3b5008c17b515794688270b4940313ccfdfd0b13877cb`.

GREEN: the same command exited `0`; `2 passed`.

### Important — physical contract and ownership reads

RED command:

```text
uv run pytest -q tests/test_absolute_generalization_contract.py::test_contract_physical_reader_rejects_leaf_and_ancestor_symlinks tests/test_absolute_generalization_contract.py::test_ownership_authority_reader_rejects_symlinks
```

Exit code `1`; exact result: `3 failed`. Leaf contract, ancestor contract,
and ownership authority cases each failed the callable assertion because the
shared private physical reader (and ownership wrapper) did not yet exist. These
tests use real filesystem symlinks and no monkeypatch.

Minimal fix: `_read_physical_regular_file` rejects symlinks at every path
component, opens with `O_RDONLY | O_NOFOLLOW` (plus platform binary mode),
requires `fstat` to identify a regular file, and reads only through the held
descriptor. Open/fstat/read/close `OSError` paths become fail-closed
`ValueError`. Both strict contract loading and ownership authority loading now
use this same reader; the former `is_file()+read_bytes()` and direct ownership
`read_bytes()` paths are gone. The private helpers were not added to
`__all__`.

GREEN: the same command exited `0`; `3 passed`.

### Round 1 final verification

```text
uv run pytest -q tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py
```

Exit code `0`; `29 passed`.

```text
uv run pytest -q tests/test_strategic_ownership_acceptance.py tests/test_source_surface_contracts.py tests/test_source_surface_provenance.py tests/test_ai_universe_contract.py tests/architecture/test_source_surface_contracts.py tests/architecture/test_public_api_contracts.py tests/architecture/test_import_contracts.py tests/architecture/test_validation_boundaries.py::test_validation_policy_relocation_is_closed_and_source_bound tests/architecture/test_validation_boundaries.py::test_validation_policy_resigned_relocation_tamper_is_rejected tests/architecture/test_execution_application_boundaries.py::test_execution_source_surface_migration_is_exact_for_all_five_v1_surfaces tests/architecture/test_risk_boundaries.py::test_risk_source_surface_migration_is_exact_and_requirements_stay_bound
```

Exit code `0`; all `57` selected tests passed.

```text
uv run pytest -q tests/architecture/test_complexity_budgets.py tests/architecture/test_validation_public_owners.py
```

Exit code `0`; all `10` selected tests passed.

```text
uv run ruff check uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture/_analysis_authorities.py tests/architecture/_owner_transport.py tests/architecture/_validation_relocation.py tests/architecture/test_execution_application_boundaries.py tests/architecture/test_portfolio_boundaries.py tests/architecture/test_risk_boundaries.py
```

Exit code `0`: `All checks passed!`

```text
uv run mypy --strict uquant/validation/absolute_generalization/__init__.py uquant/validation/absolute_generalization/contract.py uquant/validation/absolute_generalization/scenarios.py
```

Exit code `0`: `Success: no issues found in 3 source files`.

```text
uv run python -m compileall -q uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture
```

Exit code `0`; no output.

```text
git diff --check
```

Exit code `0`; no output.

Identity/seal recomputation exited `0`: contract bytes are canonical; file,
compiled, and recomputed contract seals are all `af3882c...`; the registry is
`da041844...`; schema/git baseline source and schema/worktree candidate source
each independently reproduce `d1ef797...`; historical baseline remains
`cacef64...`; ownership remains `72e6b5...`; the universe is still exactly
34 unique symbols.

The review-round-only worktree diff changes:

- `benchmarks/absolute_generalization_acceptance_contract.json`
- `tests/test_absolute_generalization_contract.py`
- `tests/test_absolute_generalization_scenarios.py`
- `uquant/validation/absolute_generalization/contract.py`
- `uquant/validation/absolute_generalization/scenarios.py`

A temporary-index `git write-tree` over the complete intended Task 2 worktree
produced candidate tree
`dcfc13802df69ae56efa2d448a2f56e650b3c289`. The real index was not modified;
it remains the controller's prior review snapshot.

## Review fix round 2

Re-review identified a remaining hostile-equality path below the exact outer
contract type. An exact `AbsoluteGeneralizationContract` could contain a
candidate/threshold/input/baseline lookalike whose attacker-controlled equality
made the generated outer dataclass equality report a false match.

### Hostile nested RED

The new parameterized regression test constructs:

- an exact outer contract containing an altered, always-equal candidate
  dataclass subclass whose production source is `0 * 64`; and
- an exact outer contract containing an altered, always-equal thresholds duck
  whose minimum positive-return fraction is `0.1`.

RED command, executed before the production shape check:

```text
uv run pytest -q tests/test_absolute_generalization_scenarios.py::test_scenario_builder_rejects_hostile_equal_nested_contract_values
```

Exit code `1`; exact result: `2 failed`. Both parameters reported
`Failed: DID NOT RAISE <class 'ValueError'>`.

### Minimal fix and targeted GREEN

Before invoking generated dataclass equality, the builder now recursively
compares the supplied object with a freshly loaded trusted instance using only
runtime structure:

- every dataclass node requires exact `type(value) is type(trusted)`;
- every tuple node requires exact type, equal length, and recursive member
  checks; and
- every leaf requires exact type, rejecting subclasses of `str`, `int`,
  `bool`, `float`, and `date` as well as hostile nested dataclass/duck
  substitutions.

The shape walk performs no attacker-controlled value equality. Only after the
entire exact shape succeeds is the trusted generated dataclass equality used.
The outer exact-type gate remains before the first supplied attribute read.

GREEN: the same targeted command exited `0`; `2 passed`.

No public export, token, writer, schema, contract bytes, or contract seal changed
in round 2.

### Round 2 final verification

```text
uv run pytest -q tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py
```

Exit code `0`; `31 passed`.

```text
uv run pytest -q tests/test_source_surface_contracts.py tests/test_source_surface_provenance.py tests/architecture/test_source_surface_contracts.py tests/architecture/test_public_api_contracts.py tests/architecture/test_import_contracts.py
```

Exit code `0`; all `37` selected source/public/import tests passed. The
unchanged strategic ownership, AI-universe, relocation, execution, and risk
tests retain their fresh round-1 results; they were not rerun per the repository
guidance for unchanged long sets.

```text
uv run pytest -q tests/architecture/test_complexity_budgets.py tests/architecture/test_validation_public_owners.py
```

Exit code `0`; all `10` selected tests passed.

```text
uv run ruff check uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture/_analysis_authorities.py tests/architecture/_owner_transport.py tests/architecture/_validation_relocation.py tests/architecture/test_execution_application_boundaries.py tests/architecture/test_portfolio_boundaries.py tests/architecture/test_risk_boundaries.py
```

Exit code `0`: `All checks passed!`

```text
uv run mypy --strict uquant/validation/absolute_generalization/__init__.py uquant/validation/absolute_generalization/contract.py uquant/validation/absolute_generalization/scenarios.py
```

Exit code `0`: `Success: no issues found in 3 source files`.

```text
uv run python -m compileall -q uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture
```

Exit code `0`; no output.

```text
git diff --check
```

Exit code `0`; no output.

Fresh identity recomputation exited `0`. Canonical file, compiled, and
recomputed contract seals remain
`af3882c594372ae0f5d4665990f5ead6bea99faaf0916f803239256c8ec6baf6`;
registry remains
`da0418442020762272b3b5008c17b515794688270b4940313ccfdfd0b13877cb`;
baseline/git and candidate/worktree independently remain `d1ef797...`;
historical baseline remains `cacef64...`; ownership remains `72e6b5...`.

Round-2-only changed files:

- `tests/test_absolute_generalization_scenarios.py`
- `uquant/validation/absolute_generalization/scenarios.py`

A temporary-index `git write-tree` over the complete intended Task 2 worktree
produced the new candidate tree
`7c9eabb452c8cac296e08ba0f2b6391aea88dcca`. The real index was not modified;
it remains the controller's complete fix-round-1 candidate snapshot.

## Review fix round 3

Final regular re-review found that the exact outer-type gate existed before
supplied field access, but the fresh trusted load and recursive shape gate still
came after scenario-facing `len`, iteration, hashing, and equality. A hostile
tuple/string/date subclass could therefore execute before being rejected.

### Hostile early-access RED

The new parameterized test constructs exact outer contracts containing:

- a `canonical_universe` tuple subclass whose `__len__` records the call and
  raises a sentinel exception; and
- a `canonical_sha256` string subclass whose `__eq__`/`__ne__` records the
  call and raises a sentinel exception.

It requires fail-closed `ValueError` from the shape gate and asserts that the
hostile call log remains empty.

RED command, executed before production reordering:

```text
uv run pytest -q tests/test_absolute_generalization_scenarios.py::test_scenario_builder_shape_checks_before_touching_hostile_scenario_fields
```

Exit code `1`; exact result: `2 failed`.

- Tuple case escaped with
  `_HostileScenarioFieldAccess: hostile tuple length executed` from builder
  line 86.
- String case escaped with
  `_HostileScenarioFieldAccess: hostile string inequality executed` from
  builder line 89.

Neither failure was the required fail-closed `ValueError`.

### Minimal fix and targeted GREEN

The builder order is now strictly:

1. exact outer `type(contract) is AbsoluteGeneralizationContract` gate, before
   reading a supplied attribute;
2. fresh trusted contract load;
3. recursive exact runtime shape comparison;
4. trusted generated dataclass equality; then
5. the unchanged 34/34, canonical scenario semantics, hashing, and scenario
   construction checks.

The recursive helper still only uses exact `type`, trusted dataclass fields,
and tuple operations after the supplied tuple has passed exact-type checking.

GREEN: the same targeted command exited `0`; `2 passed`, and both hostile
call logs remained empty.

### Round 3 focused integration

The first focused run after production reordering exited `1` with `5 failed`
and all other tests passing. All five were expected-message ordering assertions:
the shortened universe now correctly fails at the earlier shape gate, while four
same-shape semantic changes now correctly fail at trusted equality. No hostile
exception escaped and every case remained fail closed. The tests were updated
only to name the newly mandated earlier gate; the later 34/semantic production
checks were retained.

Final focused command:

```text
uv run pytest -q tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py
```

Exit code `0`; `33 passed`.

### Round 3 affected and static gates

```text
uv run pytest -q tests/test_source_surface_contracts.py tests/test_source_surface_provenance.py tests/architecture/test_source_surface_contracts.py tests/architecture/test_public_api_contracts.py tests/architecture/test_import_contracts.py
```

Exit code `0`; all `37` selected tests passed.

```text
uv run pytest -q tests/architecture/test_complexity_budgets.py tests/architecture/test_validation_public_owners.py
```

Exit code `0`; all `10` selected tests passed.

The first Ruff run found one test-only `RUF043` because the intended regex end
anchor was not written as a raw string. After changing only that literal to a
raw regex, all final gates below were rerun:

```text
uv run ruff check uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture/_analysis_authorities.py tests/architecture/_owner_transport.py tests/architecture/_validation_relocation.py tests/architecture/test_execution_application_boundaries.py tests/architecture/test_portfolio_boundaries.py tests/architecture/test_risk_boundaries.py
```

Exit code `0`: `All checks passed!`

```text
uv run mypy --strict uquant/validation/absolute_generalization/__init__.py uquant/validation/absolute_generalization/contract.py uquant/validation/absolute_generalization/scenarios.py
```

Exit code `0`: `Success: no issues found in 3 source files`.

```text
uv run python -m compileall -q uquant/validation/absolute_generalization tests/test_absolute_generalization_contract.py tests/test_absolute_generalization_scenarios.py tests/architecture
```

Exit code `0`; no output.

```text
git diff --check
```

Exit code `0`; no output.

The final focused suite was also rerun after the raw-regex-only edit and remained
`33 passed`.

No contract JSON, schema, identity, or seal changed. Fresh recomputation exited
`0`: canonical file, compiled, and recomputed contract seals remain
`af3882c594372ae0f5d4665990f5ead6bea99faaf0916f803239256c8ec6baf6`;
registry remains
`da0418442020762272b3b5008c17b515794688270b4940313ccfdfd0b13877cb`;
baseline git and candidate worktree independently remain `d1ef797...`;
historical baseline remains `cacef64...`; ownership remains `72e6b5...`.

Round-3-only changed files:

- `tests/test_absolute_generalization_scenarios.py`
- `uquant/validation/absolute_generalization/scenarios.py`

A temporary-index `git write-tree` over the complete intended Task 2 worktree
produced final regular-fix candidate tree
`dff35c0a7abd5176b212ca01205f0139f80cf14d`. The real index was not modified;
it remains the controller's complete round-2 candidate snapshot.

## Intentionally not run

- Full 34-cell economic matrix.
- Extended Performance/Economic Matrix.
- `architecture-portfolio` / portfolio architecture suite. Its one touched call
  site is mechanical signature propagation and was covered by import/compile/
  static checks; the prohibited suite itself was not executed.

These were explicitly prohibited for Task 2. No waiver or threshold relaxation
was used.

## Checkpoint

- Independent final re-review of frozen snapshot `30d9ce2ba728aa141a18c0a3247635f947199b9c`
  (tree `dff35c0a7abd5176b212ca01205f0139f80cf14d`) returned `READY` with no
  Critical, Important, or Minor findings.
- GitHub commit: `85e03f83612892e7efdfa8ee38eb73d3513dc291`.
- All 13 uploaded blob SHAs matched the local staged blobs, and the GitHub tree
  matched the reviewed tree exactly.
- The remote feature ref was advanced non-force, fetched, and verified. Local
  `HEAD`, the upstream tracking ref, and the remote ref all resolve to
  `85e03f83612892e7efdfa8ee38eb73d3513dc291`; the worktree is clean.
