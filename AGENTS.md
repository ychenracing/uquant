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

## Git/GitHub: chunked reads and verified uploads

- Large files are not categorically prohibited. When transferring file content, use bounded chunk reads, check each chunk's offset/order and expected versus actual byte length, then verify the total against the original before uploading. Keep byte assembly in the runtime or transport layer, not in model-generated payloads.
- Check content integrity as well as length. If Base64 is needed, distinguish encoded character count from decoded byte count and verify the decoded file hash. Compare the returned Git blob SHA with the expected Git object ID, not a plain file SHA-1. Never upload truncated or unverified content.
- Prefer existing authorized native Git or a program that reads local files and sends requests directly. Do not print entire large files, full Base64 or huge JSON into tool output and then reconstruct upload arguments through the model. Chunked reads followed by a giant model-generated create_blob argument do not solve that risk. Never extract or expose connector credentials.
- Chunked reading is not multipart upload. Use only the transport's supported upload semantics; multiple blobs are not append operations on one file. If genuine multipart or separate-part storage is needed, retain order, lengths and checksums and verify reconstruction. Do not probe an already failed large-payload path; use a verified supported route, or report that transfer as blocked while continuing independent work.
- Use small serial operations and one writer per branch. Reuse the existing transfer/recovery ledger and verified objects. After a missing or cancelled response, read back before retrying. A created blob is not a committed file: verify the final commit and remote branch, and verify byte identity before claiming original evidence is preserved.
- Keep task/recovery instructions concise; record source identity, key results, actual artifact locations, preservation status and next action rather than embedding archives. A summary, hash or reproduction command is not a backup. Report unpreserved originals and temporary-runtime loss risk honestly; do not delete originals, rewrite historical evidence or weaken acceptance.
- Apply this rule to current task instructions, project briefs and handoffs. It replaces blanket no-large-upload directions, not the task goal, authorization or evidence contract. Do not create or modify scheduled tasks without explicit authorization for that scheduling change.
