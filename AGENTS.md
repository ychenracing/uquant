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

## Git/GitHub: bounded large-file transfer and preservation

- Large originals remain required whenever the active task or evidence contract requires them. This section applies to both downloads and uploads; do not substitute summaries merely because one-shot transfer is risky.
- Preflight before moving a potentially large body: resolve path/object identity and obtain metadata such as byte size, Git/blob SHA, LFS OID/size, artifact ID/size, or equivalent. If size is unknown, do not blindly fetch the full body first.
- For downloads, prefer file-to-file paths that write directly to runtime storage without returning the payload through model/tool output: native Git with partial clone/sparse checkout where useful, selective Git LFS fetch/pull, resumable HTTP/range download, or a connector download action that returns a file reference. If native/API read access is unavailable, use an authorized connector path that is bounded or file-aware; never extract connector credentials.
- For large text needed only for analysis, search first and read bounded line/range chunks; process or filter locally and return only small relevant excerpts. For binary files, archives, workflow artifacts and logs, avoid full inline fetches when a file reference, range read, job-step summary, manifest/listing or selective extraction can answer the task. Do not `cat`, print, Base64-dump, hex-dump or otherwise stream the entire large body into the conversation.
- When a download must be chunked, record and verify chunk offset/order plus expected versus actual byte length, use resumable/range semantics when supported, and reuse already verified chunks after interruption. After completion verify aggregate byte length and a cryptographic hash or expected Git/LFS object identity before treating the local copy as complete.
- For uploads, prefer an authorized file-aware path that reads the local file directly, such as native Git/Git LFS or a programmatic request inside the runtime, so the model does not reproduce the payload. If native Git/API lacks write permission or is unavailable, switch to the authorized GitHub connector rather than stopping or asking the user to recover the transfer manually. Never extract or expose connector credentials.
- Never rebuild a large upload from a single truncated read. Use verified chunks and the safest supported transport. If one final request is unsafe, use a deterministic multipart/resumable representation with a manifest of part order, raw/encoded lengths and hashes, then verify byte-for-byte reconstruction. Multiple Git blobs are separate objects, not append operations on one file.
- If Base64 is used anywhere, distinguish encoded character count from decoded byte count and verify the decoded hash. For Git object verification compare against the expected Git object ID rather than a plain file SHA-1.
- Keep large transfers serial and bounded and maintain a concise transfer ledger. After cancellation, timeout or missing response, inspect local/remote state before retrying so completed chunks, downloads or writes are not duplicated.
- A partial local file, downloaded archive reference or created Git blob is not completion. Verify the final local file or extracted target, and for uploads verify the final tree/commit/ref or storage manifest plus reconstructed byte identity before claiming preservation.
- Large-file transfer must not unnecessarily block independent implementation or validation, but required originals remain incomplete until their download/upload and integrity verification succeeds. Never fabricate evidence, weaken acceptance or call a summary/hash a backup.
- Apply this rule to current task instructions, project briefs and handoffs. It supersedes blanket `no large uploads` or one-shot-download directions without changing task goals, authorization or evidence contracts. Do not create or modify scheduled tasks without explicit scheduling authorization.
