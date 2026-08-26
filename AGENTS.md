# Agent Working Agreement

These repository-wide instructions apply to ChatGPT Work, Codex, and other coding
agents. Explicit task requirements and acceptance criteria remain authoritative for
the current task. Historical plans and evidence describe past work and do not
override this file.

## Core Execution Principles

- Final correctness, safety, data integrity, and economic integrity take priority
  over speed. However, redundant validation does not increase correctness. Prefer
  the smallest verification scope that produces sufficient new evidence.
- Validation is progressive and impact-driven. Expand scope because the change or
  evidence requires it, not merely because a broader check exists.
- Batch logically related edits before broader validation. Do not prove every
  transient implementation state.
- A milestone is not a final candidate. A **final candidate** is the stable tree that
  is actually intended for delivery, merge, release, or final acceptance.
- A successful broader validation remains valid until result-affecting content inside
  its covered scope changes. Do not rerun equivalent checks against unchanged
  behavior simply to obtain the same evidence again.
- Apply reviews, skills, and validation proportionately. They may broaden when risk
  warrants, but must not create unbounded loops or repeated full validation against
  materially identical content.

## Progressive Verification Ladder

Use the lowest level that can safely establish the current change, then escalate only
when additional evidence is needed.

### L1 — Targeted local verification

Use during normal iteration after small or well-bounded changes.

Examples:
- directly affected unit or regression tests;
- a specific test method, test class, package, or component;
- compilation, lint, static analysis, schema checks, or the smallest useful
  reproduction for the changed scope.

Do not run repository-wide tests after every small edit.

### L2 — Affected module or smoke verification

Use after a meaningful local milestone or when a change crosses nearby component
boundaries.

Examples:
- the affected module, strategy, subsystem, or integration path;
- a small representative smoke backtest or replay;
- a compact set of directly related scenarios or universes.

### L3 — Affected benchmark, regime, or cohort verification

Use when L1/L2 cannot sufficiently establish cross-component or economic behavior.

Examples:
- affected market regimes or benchmark windows;
- relevant data cohorts or universes;
- broader integration, portfolio, risk, execution, or runner checks that exercise
  the changed behavior without running the complete acceptance matrix.

### L4 — Complete acceptance and robustness verification

L4 is the complete required engineering and economic acceptance matrix for a final
candidate. When project acceptance documents define complete performance and generalization
gates, those complete gates are part of L4 unless the documents explicitly classify
them otherwise.

Use L4 as a final acceptance gate, not as the inner development loop.

## Escalation Rules

- Start at L1 for a bounded change unless its known impact clearly requires L2 or L3.
- Escalate from L1 to L2, L2 to L3, or L3 to L4 only when the current level does not
  provide sufficient evidence, such as when:
  - failures appear outside the assumed impact boundary;
  - dependency or data-flow impact cannot be bounded confidently;
  - the change affects shared strategy, portfolio, trading, risk, runner, schema,
    build, release, runtime, authentication, security, concurrency, migration, or
    destructive-action infrastructure;
  - acceptance criteria explicitly require broader coverage.
- High-risk or cross-domain work justifies earlier escalation to the next useful
  level; it does **not** automatically justify jumping directly to L4.
- Engine-level or cross-window uncertainty should normally be resolved progressively
  through L1 -> L2 -> L3. Run L4 early only when lower levels cannot safely bound the
  impact or an explicit acceptance contract requires complete validation at that
  point.
- Do not rerun a validation level when no covered behavior, configuration, data, or
  runtime input has changed and the prior result remains applicable.

## Final Validation Rules

- Run the complete required L4 validation once after implementation is finished and
  the final candidate is stable.
- Repeat L4 only when at least one of the following is true:
  1. the previous L4 run failed and a result-affecting change was made to fix it;
  2. a material behavior-affecting change was made after the successful L4 run;
  3. shared build, dependency, configuration, schema, data, runner, or core
     infrastructure changed in a way that can invalidate the prior L4 evidence;
  4. new evidence shows that the previous validation scope or assumptions were
     insufficient.
- After a material change made following L4, verify the changed scope locally first,
  then run L4 again only when the tree has become the new stable final candidate.
- Documentation, comments, formatting, renames, provenance metadata, branch/tag
  metadata, and other behavior-neutral changes do not require repeating expensive
  engineering or economic validation unless an explicit contract requires it.

## Failure Handling

When any test, backtest, benchmark, replay, or validation step fails:

1. Diagnose the root cause before expanding scope.
2. Rerun the failed item and directly affected checks first.
3. Confirm the fix at the smallest useful level.
4. Escalate only if the impact remains uncertain or the evidence requires broader
   coverage.
5. Do not restart the entire expensive suite after every diagnostic edit.
6. If a full matrix or CI run contains isolated failed or cancelled jobs, prefer
   rerunning the failed jobs or affected shard before restarting successful work,
   unless the failure invalidates the entire run.

## Economic Evidence and Result Reuse

- Reuse economic or backtest results only when the production tree, configuration,
  data manifest, universe, runtime lock, and runner version are equivalent for the
  behavior being claimed.
- For commit, branch, tag, evidence-metadata, or provenance-only changes, rebind
  provenance and run any required exact-HEAD contract checks without recomputing
  unchanged economics.
- Documentation, comments, formatting, renames, and packaging may avoid full
  economic replay only after confirming they are behavior-neutral.
- Before a full matrix, replay, ablation, or other expensive long-running job,
  validate the runner, schema, attribution, failure retention, and readback with one
  to three representative sentinel cases.
- Checkpoint deterministic shards so completed valid work can be reused rather than
  restarted after unrelated failures.
- Exact-HEAD evidence branches have one writer at a time.

## Review and Stop Conditions

- Use one targeted phase or PR review by default. Add another focused review after a
  material fix or when justified by high-risk or cross-domain changes; do not repeat
  reviews against materially unchanged content.
- Stop when explicit acceptance criteria pass and no known correctness, security,
  data-loss, or material economic-regression issue remains.
- Do not continue marginal optimization, broader experimentation, or additional
  validation after the stop condition is satisfied unless the user explicitly asks
  for it.

## Documentation Lifecycle

- `README.md`, the topic guides under `docs/`, and accepted ADRs describe the current
  system. Keep them timeless, concise, and consistent with executable defaults.
- `artifacts/**/README.md` and adjacent analyses explain frozen machine evidence. Do
  not rewrite historical results as if they described the current HEAD.
- Completed implementation plans, task reports, review transcripts, temporary
  handoffs, and generated diff packages are working material. Keep them outside the
  tracked production tree; Git history is the recovery mechanism after their durable
  decisions have been absorbed into canonical documentation or ADRs.
- When moving or deleting historical material, update live links and the current
  governance inventory. Never rewrite immutable baseline inventories to erase history.
- Documentation-only changes use link, terminology, command, and affected governance
  checks. They do not invalidate economic evidence unless they alter executable
  inputs, generated contracts, packaging, or runtime behavior.

## Governing Principle

Verification exists to produce new evidence about the correctness of the final
result. Use progressively broader checks as required by impact and uncertainty, but
reserve complete validation for the stable final candidate. Full validation is a
final acceptance gate, not a per-edit ritual.
