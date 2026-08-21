# Agent Working Agreement

These repository-wide instructions apply to ChatGPT Work, Codex, and other coding
agents. Historical plans and evidence describe past work and do not override this
file.

## Verification Policy

- Final correctness, safety, and economic integrity take priority over speed.
  Minimal sufficient verification is the default, not a hard cap; never skip a
  check needed to establish the acceptance criteria.
- Before implementation, state the change risk and intended checks. During
  iteration, use focused tests, static checks, or the smallest useful reproduction,
  and batch related edits instead of proving every transient state.
- Run the complete required engineering and economic validation once for each final
  candidate production tree before PR, release, or merge. If a material change
  follows, rerun affected checks and validate the new final candidate. Never repeat
  full validation when result-affecting content is unchanged.
- After a failure, rerun affected checks first. Expand coverage when the impact is
  uncertain, cross-cutting, or broader than focused checks can establish.

## Risk and Evidence

- Broaden validation for strategy or economic behavior, data or backtest runners,
  portfolio/trading/risk logic, authentication or security, concurrency, migrations
  or destructive actions, and result-affecting build, release, or runtime changes.
  High-risk or cross-domain work may receive wider tests and an additional review;
  state the reason and avoid duplicate checks.
- For strategy iterations, run focused regressions and affected windows. Run the
  complete Phase 1 and Phase 2 gates for a promoted final candidate, or earlier when
  an engine-level change has uncertain impact across all windows.
- Reuse economic results only when the production tree, configuration, data
  manifest, universe, runtime lock, and runner version are equivalent. For
  commit/branch/tag/evidence-metadata-only changes, rebind provenance and run any
  required exact-HEAD contract checks without recomputing unchanged economics.
- Documentation, comments, formatting, renames, and packaging avoid full economic
  replay only after confirming they are behavior-neutral.

## Long Work, Review, and Stop Conditions

- Before a full matrix, replay, or ablation, validate the runner, schema,
  attribution, failure retention, and readback with one to three sentinel cases.
  Checkpoint deterministic shards. Exact-HEAD evidence branches have one writer.
- Use one targeted phase or PR review by default. Add a focused review after a
  material fix or for justified high-risk or cross-domain work.
- Apply skills proportionately. They may add checks when risk warrants, but must not
  create unbounded loops or repeat full validation against identical content.
- Stop when explicit acceptance criteria pass and no known correctness, security,
  data-loss, or material economic-regression issue remains. Do not continue marginal
  optimization unless the user explicitly requests it.

## Progressive Verification Strategy

Use progressive, impact-based verification. Do not repeatedly run the full test suite or other expensive validation after small edits.

### During implementation

- For every incremental change, identify the smallest affected scope from the diff and dependency path.
- Run only directly relevant unit tests, targeted integration tests, compilation, lint, static checks, or the smallest useful reproduction.
- Prefer test method, test class, module, package, or subproject scope over repository-wide commands.
- Batch logically related edits before broader verification.
- Do not rerun checks whose covered behavior has not changed.
- Do not run the full repository test suite after every edit.

### Milestone verification

- After a meaningful logical milestone, run the affected module or subsystem suite when cross-component behavior may have changed.
- Expand coverage when impact is uncertain, cross-cutting, or shared infrastructure has changed.

### Final verification

- Run the complete required test/validation suite once after implementation is finished and the final candidate is stable.
- Repeat the complete suite only when:
  1. the previous full run failed and behavior-affecting code was changed to fix it;
  2. material behavior-affecting changes were made after the full run;
  3. shared build/configuration/dependency/schema/core infrastructure changed; or
  4. evidence shows the previous scope was insufficient.
- Documentation, comments, formatting, renames, and metadata-only changes do not require repeating expensive full validation unless explicitly required.

### Failure handling

1. Diagnose the root cause.
2. Rerun the failed test/check and directly affected tests first.
3. Expand coverage only after targeted verification passes or when the impact cannot be bounded safely.
4. Do not restart the entire expensive suite after every diagnostic edit.

### Expensive backtests, benchmarks, or simulations

When applicable, use staged validation:

- **L1:** targeted unit/regression tests;
- **L2:** affected strategy/module plus a small smoke run;
- **L3:** affected benchmark/regime/data cohort;
- **L4:** complete acceptance/robustness matrix.

Use L4 as a final acceptance gate, not as the inner development loop. Repeat L4 only after a material result-affecting change.

### Principle

Verification should produce new evidence. The goal is to establish final correctness with the minimum test scope that safely proves the current change; full validation is a final acceptance gate, not a per-edit ritual.
