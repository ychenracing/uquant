# uquant working agreement

## Entry and authority

- Follow the current task's explicit scope and acceptance criteria. Read applicable nested `AGENTS.md` before editing that directory.
- For continuation work, read `PROJECT_STATE.md` after this file, resolve mutable branch/PR/SHA/CI/artifact facts from GitHub, then load only the active PR/diff and affected files needed for the next decision. Read `.github/CHATGPT_PROJECT_BRIEF.md` only for stable architecture, commands or boundaries. Historical plans and evidence are context, not new authority; do not preload unrelated history, skills or logs.
- Skills provide methods, not additional authority or approval gates. Continue already-authorized bounded work unless a platform/safety limit or a material decision not resolvable from repository state blocks it.

## Git and recovery

- Resume the matching branch/PR. New work uses a feature branch unless a direct default-branch update is explicitly authorized; preserve required reviews/checks.
- Save coherent recoverable milestones as commits, push when authorized, and verify the remote SHA. Keep mutable task state in the existing PR rather than permanent instructions.
- Without explicit authorization, do not reset, clean, rebase, force-push, rewrite history, discard unknown work, or commit secrets. Keep one writer per shared runtime or evidence identity.

## Verification and economic evidence

- Verify affected behavior first and expand by risk. Run the complete required engineering/economic acceptance only for a stable final candidate or when the active contract explicitly requires it earlier.
- Before expensive matrices/replays, validate runner, schema, attribution, failure retention and readback with a small representative sentinel set.
- Reuse deterministic evidence only when the covered behavior plus production tree, configuration, data manifest, universe, runtime lock and runner are equivalent. A new message/handoff alone does not invalidate evidence; a new SHA still needs applicable exact-HEAD checks. A partial matrix is not full acceptance.
- Behavior-neutral documentation/provenance changes need relevant documentation and exact-HEAD checks, not unrelated economic recomputation once neutrality is established. Executable-input or runtime changes invalidate affected evidence.
- Never weaken frozen thresholds, inputs, scenarios, attribution or guards to obtain a pass. Preserve failed and historical evidence.

## Documentation boundary

`README.md`, current `docs/` guides and accepted ADRs describe the current system. Frozen `artifacts/**` evidence remains historical and must not be rewritten as current results.
