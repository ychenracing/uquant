# uquant working agreement

## Scope and working method

Follow the current task's explicit scope and acceptance criteria within platform permissions. Preserve business, security, data, economic and release contracts. Read applicable nested `AGENTS.md` before editing that directory. Historical plans are context, not new authority; analysis-only and approval-before-edit requests remain read-only until authorized.

For continuation work, read `PROJECT_STATE.md`, resolve mutable branch/PR/SHA/check/artifact facts from GitHub, and inspect existing local changes. Resume matching work. Consult `.github/CHATGPT_PROJECT_BRIEF.md` only for relevant architecture, commands or boundaries; read the active diff and affected files, expanding for uncertainty or impact. Reuse unchanged context rather than preloading history, skills or logs.

Skills provide methods, not additional authorization, approval or stopping gates. Respect platform requirements, but do not impose repeated design confirmations, mandatory full workflows or ceremonial announcements on bounded authorized work. Prefer existing implementation/dependencies and the smallest sufficient change. Plan material decisions, batch related edits, and keep one writer per shared file, branch, runtime or evidence identity.

## Testing and economic evidence

- Use risk-based, minimum sufficient verification based on changed behavior and real call paths. Do not enforce Superpowers TDD or fresh-verification-per-message workflows. Tests may precede or follow implementation; never delete working code solely because it was written before a test. For defects, prefer a minimal reproduction and necessary regression coverage; reuse existing tests rather than adding redundant or brittle implementation-detail assertions.
- Run the failing case and directly affected checks first. Expand for concrete cross-module risk, uncertain impact or an explicit applicable acceptance requirement. Neither each small edit nor each final delivery automatically requires every tool, the full suite or a complete economic matrix.
- Reuse evidence while covered behavior, tests, dependencies, configuration, data and relevant environment remain equivalent. For economic evidence, assess the relevant production tree, data manifest, universe, runtime lock and runner. Only affected evidence is invalidated. Messages, handoffs, commits, pushes or new SHAs alone do not require rerunning unchanged checks. Record the actual tested source and reuse scope; never present an old CI result as a new HEAD result. Actual required checks and explicit exact-revision acceptance still apply.
- Behavior-neutral documentation/provenance changes need only relevant format, link, command or documentation-contract checks. Instruction edits also require ambiguity, duplication, conflict, authorization and completion-boundary review. Do not recompute unrelated economic results. Executable inputs, generated configuration and parsed documentation are not automatically behavior-neutral.
- For strategy, funds, orders, risk, security or persistent-data changes, verify affected invariants and necessary integration/replay paths. Before an expensive matrix, validate runner, schema, attribution, failure retention and readback with a small representative sentinel set. Run full/expensive validation on a stable candidate only when impact or the applicable delivery contract requires it; a partial matrix is not full acceptance.
- Review the task diff once and repeat focused review after material changes. Reuse existing tests and logs, not a new verification framework. Report verified, reused, unrun and failed items with their scope. Never weaken frozen thresholds, inputs, scenarios, attribution or guards to pass; preserve failed and historical evidence and actual repository protections.

## Execution, Git and recovery

Continue safe authorized work without repeated requests to continue. Read to resolve factual ambiguity; ask only for a necessary decision that cannot be resolved safely. Authorization does not imply spending, live trading, credential/permission changes, irreversible actions, unrelated writes or scheduled-task changes.

Resume the matching branch/PR; new work uses a feature branch unless a direct default-branch update is explicitly authorized. Preserve required reviews/checks and any PR-only policy. Save coherent milestones, push authorized changes and verify the remote SHA. Keep mutable recovery state in the existing PR; do not create empty bootstrap commits or routine process files/Issues. Without explicit authorization, do not reset, clean, rebase, force-push, rewrite history, delete branches/worktrees, discard unknown work or overwrite unrelated changes. Never commit secrets.

A failed check or candidate calls for diagnosis and an evidence-supported correction or bounded alternative, not task termination or repetitive trials. Stay within scope and budget, retain failures and continue independent work past local blockers. Finish when applicable acceptance and actual required checks are satisfied; do not add marginal optimization afterward. A checkpoint, PR or partial pass alone is not completion. If no safe authorized action remains, preserve recoverable progress and report completed, failed, blocked and unverified items without promising background completion.

## Documentation boundary

`README.md`, current `docs/` guides and accepted ADRs describe the current system. Frozen `artifacts/**` evidence remains historical and must not be rewritten as current results.

## Git/GitHub transfer and preservation

- Inspect object identities, sizes and outgoing paths before transfers; select necessary files and reuse unchanged references, not entire working directories. Prefer authorized native Git or file-aware programmatic transfer; when unavailable, use the authorized connector without extracting credentials or asking the user to perform the transfer. Do not repeat a confirmed-failed transport.
- Keep large bodies out of model/tool output and arguments: no complete Base64, huge JSON, archives, full logs or chunk-then-giant-request uploads. Prefer file-to-file transfer, selective/range reads and bounded excerpts. String-only connectors are for small recoverable batches.
- Required large originals still need preservation. Use supported resumable or deterministic multipart transport where necessary, recording order, offsets, raw/encoded lengths and hashes. Verify byte-for-byte reconstruction and expected Git/LFS object identities where applicable. Separate blobs are not append operations; a summary/hash is not a backup.
- Keep large transfers bounded and serial. Reuse a concise ledger of local/remote locations, identities and verification status; inspect state after interruption before retrying. Verify final local bytes and remote tree/commit/ref or storage manifest, not merely a created blob. Report unpreserved originals and temporary-runtime loss risk while continuing independent work. Never replace required evidence with summaries or rewrite historical evidence.

## Cloud execution continuity

For cloud engineering tasks, use [the cloud runner](tools/cloud_guard/README.md) by default for long local commands and external writes. On first execution or recovery, run `python -m tools.cloud_guard inspect`; reconcile any same-name unfinished operation before retrying. Run long commands through `python -m tools.cloud_guard run --name <stable-operation> --timeout <appropriate-seconds> -- <command>`. This automatically records the command and preserves a verified local checkpoint; it does not automatically upload it. Use `begin`/`finish` around connector writes, then record actual remote readback. Ordinary short reads need no wrapper.

Reuse the existing PR/handoff for task state and add only the journal location, last verified operation and unconfirmed writes. Preserve required originals through an authorized channel before environment loss; never publish private command logs without reviewing their content. The Absolute and Ownership shard workflows record commands automatically. AGENTS instructions are not a platform hook: before-first-tool model failures and an already-lost container remain unobservable here. Do not claim to prevent or cure Thinking failed, infer OOM from SIGKILL alone, or replay unknown writes automatically. Keep this machinery outside strategy behavior and economic acceptance.
