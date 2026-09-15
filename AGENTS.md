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
- Treat a failed check, rejected candidate or invalidated hypothesis as feedback, not task completion. Diagnose it and continue with an evidence-supported alternative or a bounded check that distinguishes plausible causes, within the authorized scope and any task budget. If attempts add no information, reassess other authorized paths rather than repeat them. Finish when acceptance is met; if no safe authorized action remains, preserve progress and report the specific blocker or evidence gap without requiring proof that the goal is impossible. Do not weaken acceptance criteria, suppress failed evidence, or bypass safety, authorization or frozen contracts.
- Before expensive matrices/replays, validate runner, schema, attribution, failure retention and readback with a small representative sentinel set.
- Reuse deterministic evidence only when the covered behavior plus production tree, configuration, data manifest, universe, runtime lock and runner are equivalent. A new message/handoff alone does not invalidate evidence; a new SHA still needs applicable exact-HEAD checks. A partial matrix is not full acceptance.
- Behavior-neutral documentation/provenance changes need relevant documentation and exact-HEAD checks, not unrelated economic recomputation once neutrality is established. Executable-input or runtime changes invalidate affected evidence.
- Never weaken frozen thresholds, inputs, scenarios, attribution or guards to obtain a pass. Preserve failed and historical evidence.

## Documentation boundary

`README.md`, current `docs/` guides and accepted ADRs describe the current system. Frozen `artifacts/**` evidence remains historical and must not be rewritten as current results.

## Git/GitHub: no large uploads

- Prevent interruption before a transfer; do not rely on recovery after the whole session stops. Do not upload large raw data, replay archives, evidence packages, Git bundles, compressed archives or complete working directories from an agent session. Native Git is not an exception.
- Never print or pass large file bodies, full Base64 or huge JSON through model/tool output or arguments. No chunk-and-reassemble requests, mass small-part uploads, compression or alternative transport to circumvent this rule. Do not probe large-payload limits or extract connector credentials.
- Remotely save only necessary small source changes, relevant tests, concise results and the existing recovery entry. Inspect file sizes and the full outgoing change/object set before writing; select explicit paths, not an indiscriminate `git add .`. Exclude large objects before the call without deleting originals or rewriting unrelated history.
- Keep large originals where they already exist. Record their actual location, size/hash, reproduction command and preservation status in a small manifest. A summary/hash is not a backup. Explicitly report originals not remotely preserved and temporary-runtime loss risk; never claim complete preservation without it.
- Reuse concise recovery state: objective, source identity, completed/unverified work, key results and next action. Do not embed full logs or historical archives. Use small serial saves, one writer per branch, and remote readback verification.
- A blocked large upload must not block independent implementation or validation. If acceptance requires an unavailable original, mark that requirement unmet; do not fabricate evidence or weaken acceptance.
- Apply this boundary to task instructions, project briefs and handoffs. It supersedes conflicting legacy upload/archival directions only, not the task goal, authorization or evidence contract. Do not rewrite historical evidence. Do not create or modify scheduled tasks without the user's explicit approval for that scheduling change.
