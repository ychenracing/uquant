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

## Authority and Context

- The user's current task, explicit acceptance criteria, this file, and any more specific nested `AGENTS.md` form the execution contract. Repository-specific rules take precedence over generic guidance.
- Accept GitHub tasks in natural language without requiring a fixed prompt, manual template, branch name, PR number, or Issue when facts can be resolved safely.
- GitHub live state is authoritative for branches, SHAs, commits, PRs, reviews, checks, and merge status; history, memory, plans, summaries, and handoffs are leads only.
- Search for a matching open PR, branch, or Issue before creating work and continue a unique match in place. Use the PR body as dynamic state for ordinary single-PR work; create an Issue only for genuinely multi-PR, long-lived phased or backlog work, or when requested.
- Load the smallest authoritative context first: applicable `AGENTS.md`, `.github/CHATGPT_PROJECT_BRIEF.md` when present, the matching PR and diff, then directly related code, tests, configuration, and workflows. Expand only when evidence is insufficient, contradictory, or impact grows.
- Do not load whole repositories, conversations, all PRs/Issues/Actions, or large logs by default. Never lossy-compress prohibitions, exceptions, AND/OR logic, thresholds, dates, versions, paths, branches, SHAs, exact results, risks, or unknowns.
- If no local worktree exists, mark local fields not applicable; never invent them. Use `context-budget-router` and `conversation-continuity-guard` when available.

## Remote Task Bootstrap

These rules create durable remote recovery points and never replace or weaken the repository-specific business, security, quantitative, testing, CI, release, evidence, or Git-safety rules above.

- After minimum read-only verification and before substantive modification, establish a remote task-start checkpoint. Create a new feature branch from the verified remote default-branch SHA, or continue a matching PR/branch in place after refreshing the PR, pushing recoverable state, and verifying the remote head.
- Prefer a structured empty bootstrap commit recording: Objective; Acceptance criteria; Included and excluded scope; Non-negotiable constraints; Default branch and baseline SHA; Feature branch; Related PR, branch, or Issue; Current verified state; Risks and unknowns; and Next action.
- Push the bootstrap commit and verify the remote feature-branch head SHA before editing. If empty commits are unsupported, use temporary branch-only `.github/task-bootstrap/<task-slug>.md` and delete it before merge.
- Every formal checkpoint and important milestone must run minimum necessary verification, commit one coherent atomic state, push, verify the remote SHA, update the PR, and continue. Chat, a local workspace, a local commit, or a temporary container alone is not a complete checkpoint. Do not commit trivial edits separately.
- Never push secrets, unrelated changes, or incomplete atomic work. Without explicit authorization, never push the default branch or force-push. If push or verification fails, report the exact blocker and do not claim a completed checkpoint.

## Continuous Execution and Recovery

- Continue complex, multi-step, long-running, GitHub, batch, research, debugging, and multi-tool work while a safe, clear, authorized next step exists. Milestones, checkpoints, commits, pushes, PR creation, partial validation, progress updates, and prepared handoffs are not completion.
- Progress updates are non-blocking. At meaningful milestones, use the formal checkpoint procedure above, refresh the PR with verified current state and next action, and proceed without asking the user to say “continue”.
- In a batch, checkpoint targets independently and continue past one blocked target. While required checks are pending, do other executable work; long-running non-required checks are not blockers.
- If state may be stale, re-read authoritative repository and PR state, head/base/default SHAs, commits, diff, reviews, checks, and remaining work; resolve discrepancies read-only and resume rather than restart.
- Do not stop merely because the conversation is long, many files/tools were used, a phase is large, or a handoff could be prepared. Do not claim remaining context capacity without accurate platform telemetry.

## Completion, Handoff, and Git Safety

- Finish only after explicit acceptance passes with necessary final verification, the user stops, safety policy requires termination, a required tool is unavailable, or a verified blocker prevents all remaining safe authorized work. If safe executable work remains, continue; do not promise background completion.
- Handoff blockers are limited to: a hard tool limit; permission, branch protection, required approval, or external authorization blocking all remaining work; a material decision that cannot be inferred safely; an unresolved live-state conflict; critical context unrecoverable from authoritative sources; material correctness, security, privacy, data-integrity, economic, or irreversible risk; or an explicit user request.
- Task length, many milestones/files/logs/tools, a large next phase, an existing handoff, pending non-required CI, or one blocked repository in an actionable batch are not handoff conditions.
- Before a required handoff, finish the smallest safe atomic action, save and verify a recoverable checkpoint when possible, refresh authoritative state, state the blocker exactly, and report only verified repository, branch, SHA, worktree, tests, CI, commits, push, risks, and next steps.
- Without explicit authorization, do not run `reset`, `clean`, or `rebase`; force-push or rewrite history; delete branches/worktrees; discard tracked, staged, unstaged, or untracked work; overwrite unrelated changes; or redo completed verified work.
- Before merge, handoff, or completion, verify the live branch, HEAD, remote feature SHA, default-branch SHA, merge base, working state, commits, push state, changed files, reviews, required checks, and exact test results. Mark unavailable fields not verified or not applicable.
