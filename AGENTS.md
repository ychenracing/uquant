# uquant working agreement

## Scope, context and methods

Follow the current task's explicit scope and acceptance criteria within platform permissions. Preserve project-specific business, security, data, economic, CI and release contracts; read applicable nested `AGENTS.md` before editing that directory. Historical plans and evidence describe past work, not new authority. Analysis-only and approval-before-edit requests remain read-only until authorized.

Resolve current branches, SHAs, PRs and checks from GitHub and inspect local changes when a worktree exists. Resume a matching task instead of creating a replacement PR or redoing verified work. Start with `.github/CHATGPT_PROJECT_BRIEF.md` when present, relevant README/topic documentation, the matching PR/diff and directly affected code, tests, configurations and workflows. Expand only to resolve uncertainty or impact. Preserve exact constraints, frozen contracts and evidence identities; do not preload all history, skills or logs.

Use skills as task-specific methods, not additional task authorities. For an already authorized, bounded task, do not add repeated design approvals, execution-mode questions, mandatory full-mode workflows or ceremonial announcements. Respect platform-required skill use and genuine approval gates. Missing optional skills are not blockers when available tools suffice. Prefer the smallest sufficient change, existing dependencies and plans limited to material decisions, dependencies and acceptance. Batch related edits; parallelize independent tasks, with one writer per shared file, runtime or evidence identity.

## Authorization and durable recovery

- Continue safe, clearly authorized work without asking for another "continue". Resolve factual ambiguities by reading; ask only for a material decision that cannot be resolved safely. Do not infer permission for spending, real trading, credential/permission changes, irreversible actions or unrelated external writes.
- Resume existing task branches. For new work use a feature branch unless a direct default-branch update is explicitly authorized. Preserve required reviews/checks and any PR-only policy.
- Save the first coherent result and meaningful later milestones as verified commits; push authorized checkpoints and verify remote SHA. No empty bootstrap commit, temporary bootstrap file or new Issue is required for routine work. Use the existing PR for dynamic task state, not permanent instructions.
- Without explicit authorization, do not `reset`, `clean`, `rebase`, force-push, rewrite history, delete branches/worktrees, discard unknown work or overwrite unrelated changes. Never commit secrets or claim an unverified push succeeded.

## Progressive verification

Verification must establish the final result rather than repeatedly prove transient edits. Start at the lowest sufficient level and expand because of impact, uncertainty or explicit acceptance:

- **L1:** directly affected unit/regression tests, a failing node/module, compile, lint, static checks, schema checks or minimal reproduction.
- **L2:** affected module, strategy, subsystem or integration plus a representative small smoke replay/backtest.
- **L3:** affected benchmarks, regimes, windows, cohorts or universes and broader portfolio/risk/execution/runner integration.
- **L4:** the complete required engineering and economic acceptance and robustness matrix for the stable final candidate. Complete performance and generalization gates belong to L4 unless the acceptance contract explicitly classifies them otherwise.

Shared strategy, portfolio, risk, execution, runner, schema, runtime, security, concurrency, build or data changes justify earlier escalation to the next useful level, not automatically to L4. Resolve engine/cross-window uncertainty progressively unless lower levels cannot bound impact safely or the contract explicitly requires an early full run.

Run required L4 once after the candidate is stable. Repeat only when a result-affecting fix follows a failed run, a later material change invalidates covered evidence, or new evidence shows the prior scope/assumptions insufficient. After a later material fix, verify locally first, then complete affected required acceptance on the new stable candidate. Do not rerun valid evidence simply because a message, agent, branch label or handoff changed.

On failure, diagnose the root cause and run the failing item plus directly affected checks first. Batch related fixes before expanding coverage. Reuse successful deterministic shards and rerun failed/cancelled jobs when their independence is established; restart the full run only when its evidence has been invalidated. A partial matrix is not full acceptance.

## Economic evidence and exact-HEAD checks

- Reuse economics only when the production tree, configuration, data manifest, universe, runtime lock and runner version are equivalent for the claimed behavior. Do not infer equivalence from passing unrelated tests.
- For commit/branch/tag/provenance-only changes, rebind provenance and run applicable exact-HEAD contract checks without recomputing unchanged economics. Never present an old CI result as the new HEAD's status.
- Before an expensive full matrix, replay or ablation, validate the runner, schema, attribution, failure retention and readback with one to three representative sentinel cases.
- Checkpoint deterministic shards and preserve failed evidence. Exact-HEAD evidence branches have one writer at a time.
- Documentation, comments, formatting, renames and packaging avoid economic replay only after establishing behavior neutrality; changes to executable inputs, generated contracts or runtime behavior invalidate affected evidence. Do not weaken thresholds, frozen inputs, scenarios, attribution or guards to obtain passing results.

## Review, documentation and completion

Use one focused review of the complete phase/PR diff by default; repeat focused review for material fixes or additional risk, not unchanged content. Instruction edits need trigger, authorization and completion-boundary review. Documentation-only work uses relevant link, terminology, command and governance checks unless an explicit contract requires more.

`README.md`, current topic guides under `docs/` and accepted ADRs describe the current system. Frozen `artifacts/**/README.md` and adjacent analyses describe their historical evidence; never rewrite them as current results. Keep temporary task plans/reports/handoffs outside the tracked production tree after durable decisions enter canonical documentation. Update live links and the current governance inventory when moving historical material; never rewrite immutable baseline inventories to erase history.

Finish when explicit acceptance and required checks pass and no known material correctness, security, data-loss or economic-regression issue remains. Do not add marginal optimization afterward. A checkpoint, PR, partial pass or prepared handoff is not completion. Continue independent targets past a blocker; if no safe authorized action remains, preserve recoverable progress and report the exact blocked/unverified work separately. Do not promise background completion. Before delivery or merge, verify the applicable remote SHA, full task diff, reviews, required checks and evidence; mark unavailable local fields as not applicable rather than inventing them.
