# Agent Working Agreement

These repository-wide instructions apply to ChatGPT Work, Codex, and other coding
agents. Historical plans and evidence documents describe past work; they do not
override this file.

## Primary Rule: Minimal Sufficient Verification

Optimize for confidence in the final production tree, not proof of every
intermediate state. Preserve one reliable final verification and remove repeated
full proof around transient commits, task checkpoints, reviews, and metadata-only
changes.

### Verification Boundaries

- During implementation, run only focused tests, static checks, or the smallest
  reproduction directly related to the current change.
- Batch related edits. Do not run the full test, build, security, or economic suite
  after every commit, task, refactor, or review round.
- Run the complete required validation once for each final candidate production
  tree immediately before its PR, release, or merge.
- If that validation leads to a production or economic-input change, validate the
  new final candidate once. Never rerun the same complete validation when the
  relevant content is unchanged.
- A failed check should trigger a fix and rerun of the affected check. Escalate to
  the complete suite only when the fix changes production behavior or an economic
  input covered by that suite.

### Risk-Based Checks

- Documentation, comments, formatting, evidence prose/metadata, commit SHAs,
  branch names, tags, PR text, and behavior-neutral packaging: inspect the diff
  and run formatting or contract checks only. Do not run full pytest or economic
  replay.
- Tests, evidence tooling, or developer utilities: run their focused tests and
  directly affected contracts.
- Non-economic production code: run focused regressions during implementation and
  the full engineering suite once at the batch or final-candidate boundary.
- Strategy behavior, defaults, portfolio/risk/evaluation logic, data processing,
  universe selection, or result-affecting dependencies: run focused regressions
  and affected windows while iterating. Run the complete Phase 1 and Phase 2
  economic gates only for a promoted final candidate and the final merge tree.

### Economic Evidence Reuse

Economic evidence is invalidated only by changes to result-affecting production
code, strategy configuration/defaults, data or its processing, universe selection,
portfolio/trading/risk/evaluation logic, or result-affecting runtime dependencies.

README edits, comments, report layout, evidence metadata, commit identity, branch
or tag names, CLI wording that cannot affect execution, file moves, and equivalent
publication commits do not invalidate economic evidence.

Reuse successful results by immutable content identity, including the production
tree, configuration, data manifest, universe, runtime lock, and runner version.
Do not make commit SHA alone the identity of an economic result, and do not create
self-invalidating evidence chains in which updating an anchor requires replaying
unchanged economics.

### Long-Running Work

- Before a full matrix, replay, or ablation, validate the runner, schema,
  attribution, failure retention, and readback with one to three sentinel cases.
- Checkpoint and reuse completed deterministic shards. Parallelize independent
  read-only shards when resource-safe.
- Exact-HEAD evidence branches have one writer. Do not allow concurrent writers to
  invalidate an in-progress evidence binding.

### Reviews, Skills, and Stop Conditions

- Use one targeted independent review at a phase or PR boundary. Do not require
  separate specification and quality reviews for every small task.
- Skills may assist the work, but must not expand verification beyond this policy.
  Loading a skill does not require executing an entire ceremony chain. Use
  systematic debugging for an observed failure and final verification at the
  batch/PR boundary; do not automatically chain brainstorming, planning, TDD,
  repeated reviews, and full verification for already-specified low-risk work.
- Do not use open-ended instructions such as "repeat until no issue can be found."
  Stop when the explicit acceptance criteria pass and no known P0/P1 issue remains.
- At the start of a task, state its risk class and the checks that will be run. Do
  not silently broaden the validation scope.
