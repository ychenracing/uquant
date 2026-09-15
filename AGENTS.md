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

## Git/GitHub: chunked large-original preservation

- Large originals are not optional when the active task or evidence contract requires them. Do not drop, summarize away or leave them unpreserved merely because direct one-shot transfer is risky.
- Avoid direct large-payload transfer through the model/agent boundary. Do not print an entire large file, full Base64 body, huge JSON or complete archive into command/tool output and then reuse truncated output as upload input.
- For large originals, read bounded chunks and verify each chunk's offset/order plus expected versus actual byte length. When encoding is involved, distinguish raw byte length from encoded character length. Verify aggregate length before upload and verify final reconstructed/raw byte identity and a cryptographic hash afterward.
- Prefer an already-authorized file-aware path that reads the local file directly, such as native Git/Git LFS where appropriate or a programmatic request executed inside the runtime, so the model does not reproduce the entire payload. If native Git/API lacks write permission or is unavailable, switch to the authorized GitHub connector rather than stopping or asking the user to recover the transfer manually. Never extract or expose connector credentials.
- When using a connector, never rebuild a large upload from one truncated read. Use verified chunks and the safest supported transport. If a final single request is still unsafe, use a deterministic multipart/resumable representation with a manifest containing part order, raw/encoded lengths and hashes, then verify byte-for-byte reconstruction before claiming preservation. Multiple Git blobs are separate objects, not append operations on one file.
- If Base64 is used, verify decoded byte count and decoded hash, not only encoded text length. For Git object verification, compare the returned blob SHA with the expected Git object ID; a plain file SHA-1 is not the same calculation.
- Keep transfers serial and bounded. Reuse already-verified chunks/objects and a concise transfer ledger. After a cancelled, timed-out or missing response, read back remote state before retrying so a successful write is not duplicated.
- A created blob is not yet preserved evidence. Verify the final tree/commit/ref or storage manifest, then read back or reconstruct and verify original byte identity before reporting success.
- Large-original transfer must not unnecessarily block independent implementation or validation, but required originals remain an unmet preservation item until verified upload/reconstruction succeeds. Never fabricate evidence, weaken acceptance, or call a summary/hash a backup.
- Apply this rule to current task instructions, project briefs and handoffs. It supersedes blanket `no large uploads` directions without changing task goals, authorization or evidence contracts. Do not create or modify scheduled tasks without explicit scheduling authorization.
