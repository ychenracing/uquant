# Cloud execution continuity

This is a foreground Linux/POSIX command recorder, not a ChatGPT model-service
hook. It reuses the supplied cloud_guard recorder and adds operation admission,
automatic byte-verified local checkpoints and repository/CI integration. It does
not change strategy, risk, acceptance, permissions, or scheduled tasks.

## Automatic coverage and limits

After a repository checkout, agents following AGENTS.md use this entry point for
long commands. This is an instruction-level default, not forced interception.
Absolute and Ownership CI shard commands are mechanically wrapped in workflow
configuration. The focused Cloud guard workflow runs its regression tests and
preserves exact source plus its own execution evidence.

A successfully started command records a durable start, process identity,
heartbeats, resource observations, exit/signal and private log hashes. At a
normal or controlled-error exit it automatically builds and byte-verifies
`<operation>/checkpoint.zip` with the original journal and logs. It preserves
incomplete journals on unexpected failures; no exception is silently passed.

A per-name lock is inherited by the child. A concurrent duplicate command or an
unreconciled write with the same name is refused. This is not a lock on the entire
repository; retain the existing single-writer discipline for shared resources.
Renaming an operation is not a safe retry strategy.

No recorder process runs between tasks. No Python code can catch a model failure
before the first tool call. Loss of a whole container can lose unuploaded logs.
A recorded local checkpoint is not remote storage, and a checksum is not a backup.

## Agent usage (in the cloud checkout, no user laptop needed)

Run from the repository root; there are no dependencies to install.

```bash
python -m tools.cloud_guard inspect
python -m tools.cloud_guard run --name focused-tests --timeout 600 -- \
  uv run pytest tests/test_absolute_generalization_artifacts.py
```

The default journal is `.cloud-task-journal`, ignored by Git. Set
`UQUANT_CLOUD_JOURNAL` or `--root` to an existing private persistent volume when
one is available. Root must be stable across resumption. Do not point it at a
shared/untrusted directory. Names must be stable, short and non-sensitive.

Timeouts are explicit engineering budgets, not platform limits. Keep the outer
tool-call limit distinct: a synchronous tool can terminate earlier than this
supervisor. Use the existing CI runner for long acceptance, or launch a bounded
supervised process and inspect it within the current task. Never promise a new
model turn will run by itself. Heartbeat defaults to five seconds; the disk and
log limits are polling protections, not hard OS quotas.

External connectors cannot be intercepted by this script. Record their boundary:

```bash
python -m tools.cloud_guard begin --name github-publish --kind external_write
# Perform the authorized connector call; inspect remote state if its result is unknown.
python -m tools.cloud_guard finish --id <returned-operation-id> --outcome success
# Only after real remote readback, save the program's verification receipt:
python -m tools.cloud_guard finish --id <returned-operation-id> \
  --outcome verified --receipt /path/to/actual-readback.json
```

The receipt is hashed, not semantically authenticated. Never create a receipt
claiming an unperformed verification. For unrelated work use its own operation
name; a blocked write does not stop independent work.

## Preservation and recovery

```bash
python -m tools.cloud_guard export --output /private/path/metadata.zip
python -m tools.cloud_guard export --include-private-logs \
  --output /private/path/originals.zip
```

Exports never upload or overwrite prior bundles. They stream files, retain their
order/path/length/SHA-256 in MANIFEST.json and compare every archived byte with the
source. A partial/growing file fails export rather than receiving a valid receipt.
`inspect` trusts the durable journal, not a potentially stale latest.json.

Reuse the task's existing PR/handoff: reference the verified source SHA, journal
location, last successful operation, unresolved writes and saved bundle receipt.
At a meaningful milestone, preserve originals through the authorized file-aware
channel and read back remote bytes. Review private output before public GitHub
publication; metadata export intentionally excludes raw log bodies. Do not add a
second task-state ledger. The fixed Absolute and Ownership CI commands publish
metadata and byte-verified original stdout/stderr in separate bundles inside
`cloud-execution-*` artifacts, retained for 30 days. This is restricted to these
controlled historical acceptance commands; arbitrary agent/private logs still
require review before publication. Existing strategy artifact names and checks
are unchanged. The focused Cloud guard workflow also preserves its lint and test
originals, exact source, and a commit/length/SHA-256/Git-blob manifest. Download
artifacts through the file-aware connector and compare bytes, not tool text.
Artifacts expire: preserve required long-term originals through the authorized
repository/storage channel before expiry. The CI workflow still needs a live
runner to upload; abrupt machine loss is not solved by an `always()` step.

On resumption: inspect first, verify live process identity and remote writes, then
resume only missing work. COMMAND_NONZERO_EXIT, SUPERVISOR_TIMEOUT and PROCESS_SIGNAL
are distinct from model errors. Memory events are cgroup correlation, not proof
that this child was OOM-killed. All reports keep platform_root_cause=NOT_OBSERVED.

## Platform boundary

Account/project instructions can request this default, but cannot install a
server-side hook. The current integration cannot read OpenAI request traces,
reconfigure routing, restart a failed model turn, or cure Thinking failed. Those
require platform support to associate the actual conversation/message and time
with scheduler, generation, tool-dispatch and result-persistence logs. Local
operation IDs and GitHub run IDs must never be reported as OpenAI request IDs.
