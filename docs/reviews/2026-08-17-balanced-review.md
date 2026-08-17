# Balanced repository review ledger

**Status:** CODE AND DOCUMENT REVIEW CLEAN; TASK 8 SOURCE REBIND PENDING
**Review date:** 2026-08-17
**Branch:** `agent/balanced-review`
**Approved design:** `docs/superpowers/specs/2026-08-17-balanced-code-and-documentation-review-design.md`

## Scope and decision rules

This review covers all tracked Python, test, Markdown, and workflow files. It
may improve cohesion, naming, reuse, and readability, but preserves historical
decisions, orders, account state, replay output, and economic results. A
demonstrated behavioral defect requires a deterministic failing test, the
smallest coherent fix, and affected plus complete economic validation when the
boundary can affect results.

The following are frozen: strategy parameters, official windows, pools, seeds,
universe, data, market rules, policy thresholds, compatibility/provenance/audit
code, historical evidence, fail-closed validation, atomic persistence,
execution-journal continuity, and future-holdout protections. `AGENTS.md` is
preserved byte-for-byte. No force-push or remote overwrite is permitted.

| Severity | Meaning | Disposition |
|---|---|---|
| Critical | Corruption, unsafe execution, fabricated evidence, security exploit, or strategy/economic regression that can invalidate release. | Fix immediately. |
| Important | Incorrect behavior, fail-open validation, misleading current documentation, materially tangled responsibility, or an untested high-risk edge. | Fix before proceeding. |
| Minor | Local readability or style improvement with no correctness impact. | Fix only when the diff is small and behavior-neutral. |

## Immutable authenticated baseline

| Item | Identity |
|---|---|
| Baseline commit | `e2663695fd008fb960b86f33bc36309a2f525b68` (`Bind final P0 evidence to reviewed source`) |
| Baseline tree | `437d8df325edceb59cf5d5c76f9b1cbce392f947` |
| Runtime | CPython `3.12.13`; NumPy `2.5.1`; pandas `3.0.5`; uv `0.11.33` |
| Runtime constraint | Python `>=3.12,<3.13`; lock requires `==3.12.*` |
| Lockfile | `uv.lock` SHA-256 `4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61` |
| Frozen data | snapshot `20260809T094222Z-causal-tech-index-rebase`; 36 files; manifest SHA-256 `343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d`; checksums SHA-256 `ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29` |
| Green test baseline | `1198` tests passed |
| Engineering | [green run 32032791507](https://github.com/ychenracing/uquant/actions/runs/32032791507) |
| AI-Era Performance | [green run 32032791429](https://github.com/ychenracing/uquant/actions/runs/32032791429) |
| AI-Era Generalization | [green run 32032791460](https://github.com/ychenracing/uquant/actions/runs/32032791460) |

## Review rounds and findings

| Round | Boundary | Result | Critical / Important / Minor |
|---|---|---|---|
| 0 | Authenticated baseline | Commit, tree, runtime lock, frozen manifest, `1198`-test baseline, and three green workflow runs recorded. | 0 / 0 / 0 |
| 1 | Mechanical input | Ruff, strict mypy, and Bandit pass; tracked inventory captured. | 0 / 0 / 0 |
| 1.1 | Ledger completion | Important ledger omission corrected: canonical final commands added and shell syntax checked. | 0 / 1 resolved / 0 |
| 2 | Durable account and execution | Schema migration, order non-reuse, attribution chains, broker idempotency, cash/T+1/lot controls, atomic replacement, and journal/checkpoint continuity traced; short journal writes now complete or roll back fail-first. | 0 / 1 resolved / 0 |
| 3 | Strategy, portfolio, risk, and engine | Complete: all named ownership and transition invariants traced; no production source change justified. | 0 / 0 / 0 |
| 4 | Attribution, validation, holdout, and evidence | Complete after two internal fix-round reviews: fail-closed paths, broad-exception boundaries, subprocesses, holdout continuity, recoverable evidence carriers, canonical JSON, Git/data identity, attribution, and shard aggregation traced; thirteen Important findings resolved fail-first. | 0 / 13 resolved / 0 |
| 5 | CLI, adapters, and remaining tests | Complete after three fix-round reviews: command parsing, managed paths, output identity and permissions, external-data staging, deterministic adapter ordering, and all previously unmapped tests reviewed; four Important safety findings and one Minor error-clarity finding resolved fail-first. | 0 / 4 resolved / 1 resolved |
| 6 | Workflows and documentation | Complete after fix round 1: all 20 tracked Markdown files, Dependabot, and three blocking workflows reviewed; four Important documentation contradictions resolved without changing historical evidence. | 0 / 4 resolved / 0 |
| 7 | Independent final review | Initial pass: every path in the 38-path candidate diff against `e2663695` was reread independently; thirteen Important findings and three low-risk Minor findings were resolved, one safety-helper deduplication was deferred, and the then-approved exact-twelve provisional gate passed. Root review later found that those twelve test nodes mixed identity checks with independent assertions. | 0 / 13 resolved / 3 resolved, 1 deferred |
| 7.1 | Root-review fix round 1 | Complete after splitting four identity-only Task 8 nodes from every independent contract assertion, replacing the synthetic coverage scaffold with real Git/reviewed-source fixtures, and assigning Task 8 authenticated economics/rebinding/affected suites while Task 9 owns the post-metadata zero-deselection proof. | 0 / 2 resolved / 3 deferred |

### Resolved findings

| ID | Severity | Finding | Resolution |
|---|---|---|---|
| I-001 | Important | The final-validation summary named the gates but did not provide a repeatable command set. | Added the canonical engineering, economic/provenance, publication, and remote-tree verification commands; validated every `bash` fenced block with `bash -n`. |
| I-002 | Important | The append-only execution journal originally assumed one `os.write` persisted the complete JSONL record; the first fix completed eventual short writes but still left a truncated prefix when a later write returned zero or raised. | Added deterministic eventual-success, partial-then-zero, and partial-then-error regressions. The append now records its starting EOF and, under the existing exclusive lock, either writes the complete encoded record or truncates and flushes back to that EOF before re-raising the original failure. |
| I-003 | Important | A later daily-decision or journal-checkpoint failure could leave a newly replaced future-holdout replay beside older evidence carriers; interruption bypassed selected-exception rollback, unconditional rollback could overwrite a foreign generation, and an output could replace a repository-local lock inode. | Added canonical exact-byte snapshots, stable external repository and per-carrier locks protected from output aliasing, atomic generation claims, no-replace restoration, and fail-first ordinary/interruption/foreign-replacement/lock-alias regressions. Replay, daily decision, and checkpoint are a serialized recoverable update among cooperating writers; rollback never overwrites a concurrently installed generation, and unsafe pre-existing symlinks are rejected without deletion. |
| I-004 | Important | Frozen-data manifests, ablation registries, Phase 2 checkpoint envelopes, and Phase 1 diagnostic traces could accept ambiguous duplicate-key or non-canonical JSON even though downstream evidence treated them as sealed, singular objects. | Added duplicate-key and canonical-encoding regressions. All four readers now reject ambiguity before accepting hashes, seals, or replay evidence. |
| I-005 | Important | Phase 1 decision equivalence authenticated `HEAD` labels but executed working-tree Python, so untracked runtime hooks, dirty bytes, or a source/commit change during replay could be attributed to a Git commit that did not contain them. | Added real temporary-Git fail-first tests. Both caller trees must be completely clean at stable commits; every replay executes with isolated Python from private detached worktrees materialized at those commits, which are rechecked clean and unchanged afterward. |
| I-006 | Important | Phase 1 equivalence could compare both commits on a substituted self-consistent data directory, read expected provenance from a caller-mutable checkout, or let different cases observe changing private market-data bytes. | Reads expected provenance only from the detached frozen commit, binds the caller input to it, copies a read-only private replay snapshot for the whole matrix, revalidates both private and caller data afterward, and includes the authenticated identity in the result. Substitution, private/caller drift, and detached-provenance regressions cover the boundary. |
| I-007 | Important | A noncanonical `missing/../carrier` output could snapshot a missing raw path, replace an existing canonical carrier, then delete that pre-existing evidence during rollback. | Resolves and symlink-checks each replay, decision, and checkpoint carrier exactly once before locking, then passes only those canonical absolute identities through validation, snapshot, write, verification, checkpoint binding, and rollback. The missing-component and working-directory-drift reproductions preserve one lock/write identity and the pre-existing bytes. |
| I-008 | Important | Rollback compared owned bytes and then separately unlinked or replaced the path, while repository-root-only locks did not serialize different repositories sharing external carriers. | Uses globally ordered locks for every canonical carrier and atomically renames the current generation to a same-directory claim before comparison. Successful restoration publishes prior bytes only if the canonical name is still absent; restoration errors retain exact claimed and prepared bytes at named recovery paths rather than deleting the only copies. |
| I-009 | Important | Holdout and detached-worktree cleanup could mask a primary replay/interruption failure, stop later recovery, or skip cleanup after a partial worktree add; one Phase 2 context also relabeled caller failures as materialization errors. | Cleanup now attempts every carrier, lock descriptor, and worktree; retains the original `BaseException`; attaches secondary recovery, unlock, close, and removal failures as notes; checks removal subprocesses; and keeps materialization exception translation outside the caller's `yield` boundary. |
| I-010 | Important | Phase 2 baseline and historical-evidence checkouts authenticated only before replay, so a clean reset to another commit could still be attributed to the original checkout. | Revalidates baseline HEAD, tree, source fingerprint, and cleanliness plus evidence HEAD and cleanliness after `yield`, before accepting replay output. |
| I-011 | Important | Phase 1 isolated mode still initialized global site packages before inserting the detached source, allowing `.pth` or `sitecustomize` code to pre-import production. | Starts trace children with `-I -S`, injects only explicit dependency directories without processing `.pth` files, rejects any pre-bound `uquant` module, and verifies every imported `uquant` module originates below the detached checkout. |
| I-012 | Important | The compiled Phase 2 trusted-evidence manifest still used last-key-wins JSON parsing. | Adds duplicate-key rejection before the compiled canonical mapping digest or envelope seal is evaluated. |
| I-013 | Important | Relative replay/decision paths were canonicalized for locks and then resolved again for writes, so a working-directory change could lock one carrier and update another. | Captures all canonical absolute carrier paths once in the public transaction entrypoint and passes them unchanged into the locked implementation; a deterministic mid-lock `chdir` regression proves both writes retain the locked identities. |
| I-014 | Important | A hard-link error after atomic rollback claim could reach unconditional quarantine/temp cleanup and delete every copy of an owned or foreign carrier generation. | Unexpected publish/link errors retain the exact prepared rollback bytes and claimed carrier as recovery files and annotate the raised error with both paths; owned and foreign post-claim failure regressions inventory the surviving generations. |
| I-015 | Important | A lock-descriptor close error could replace the transaction failure and stop cleanup of later descriptors. | Unlock and close errors are collected while every descriptor is attempted; they annotate an existing primary exception or produce one deterministic aggregate cleanup failure only when no primary exists. |
| I-016 | Important | CLI reports and evidence runners used direct truncating writes; the first fix still checked daily account hardlinks only after account replacement and left adapter output unbound from caller competitor/data trees. | Added fail-first exact-path, hardlink, containment, and external-victim tests. Daily validates output/account/snapshot identity before engine or account side effects and rechecks at publish. Pareto, outperformance, and competitor-adapter outputs are atomic; the adapter rejects containment in or hardlink aliasing to every consumed caller input tree. |
| I-017 | Important | The Tencent backfill accepted symlinked managed paths; after the first fix its tech-proxy CSV and both metadata files still truncated existing inodes, so interruption could corrupt them and hardlinks could mutate an external name. | Every complete CSV, manifest, and checksum payload now publishes through a staged same-directory atomic replacement. Symlinks still fail before network work; publish-failure and hardlink regressions prove prior/external inode bytes survive. The docstring states the exact per-file atomic boundary. |
| I-018 | Important | Protected hardlink detection treated `os.path.samefile()` errors as proof of non-aliasing, so identity uncertainty authorized a write. | `_aliases()` now converts identity-check `OSError` into a chained fail-closed `ValueError`; the shared validator is used both for invocation-time preflight and final atomic publish. The engineering-edge regression preserves destination bytes under an injected identity-check failure. |
| I-019 | Important | Atomic replacement published `mkstemp()`'s private `0600` mode, silently narrowing existing `0640`/`0644` managed files and making new outputs ignore the caller's umask policy. The first regression also assumed the ambient umask produced `0644` and asserted POSIX modes on Windows. | Same-directory staging creates new inodes with normal `0666 & ~umask` semantics and reapplies an existing POSIX destination's exact mode before fsync and rename. Text, byte, hardlink-backfill, and controlled-umask regressions prove content safety and permission continuity; the backfill fixture explicitly establishes its POSIX source mode before linking and keeps only byte/inode assertions cross-platform. |
| I-020 | Important | Current guidance still named the removed `strategic_partial_universe_max_size` configuration field, treated `strategic_cohort_symbols` as configuration, described `RISK_OFF` as 75% instead of 66%, omitted the 20-session holdout milestone, and implied every frozen replay error was necessarily a new policy failure. | Aligned the current configuration, strategy, holdout, and Phase 2 prose with `SystemConfig`, account state, the signed holdout contract, and champion-relative replay-error policy. No configuration, account, replay, or policy byte changed. |
| I-021 | Important | `artifacts/phase2/final-acceptance.md` could be mistaken for current release truth even though every commit, metric, decision, and path in it is a historical Task 11 snapshot. | Added a prominent historical-snapshot notice and links to current operational documentation while leaving the original evidence, numbers, hashes, and acceptance decision unchanged. |
| I-022 | Important | The first README cleanup grouped missing records with replay-error state and could still be read as making recovery of an authenticated baseline replay error fail automatically, contradicting the frozen recovery-envelope policy described in `PERFORMANCE.md`. | Scoped the blocking statement to missing records or new/changed replay errors and linked authenticated baseline-error recovery explicitly to the common-support and recovery-envelope checks. |
| I-023 | Important | The strategic configuration table and strategy guide presented the retained 3-day two-member and 4-day one-member fields as effective constraints even though partial cohorts require synchronized reversal, which selects the 2-day cohort branch before member-count routing. | Documented the effective two-day confirmation for current single/double partial cohorts and labeled the 3/4-day fields as retained for configuration compatibility and governance inventory, but not selected by current routing. |
| I-024 | Important | A partial execution-journal append interrupted by `KeyboardInterrupt` bypassed rollback because the handler caught only `OSError`. | A deterministic partial-write/interruption regression failed first. The locked append now rolls back on every `BaseException`, preserving the original interruption and prior journal bytes. |
| I-025 | Important | If truncation or fsync failed while rolling back a partial journal append, that recovery failure was silently suppressed even though a corrupt audit tail remained. | A partial-write plus injected truncate-failure regression failed first. Rollback failures now annotate, rather than mask, the primary append error so audit corruption cannot be hidden. |
| I-026 | Important | Future-holdout rollback restored prior bytes through a new `0600` inode and lost the carrier's authenticated pre-transaction POSIX mode. | Exact artifact snapshots now retain bytes plus mode; no-replace rollback reapplies the prior mode. A real-filesystem `0640` regression failed first and now passes. |
| I-027 | Important | Atomic replacement of an existing private file wrote and flushed its payload before tightening the staging inode from the caller umask to the existing restrictive mode. | Mode application moved into same-directory temporary creation before any payload write; failure closes and removes the empty temporary. A deterministic fchmod-time payload probe failed first and now observes an empty staging file. |
| I-028 | Important | The new CLI, Pareto, and outperformance atomic writers protected selected exact files but could still overwrite consumed market-data or production-source trees after replay/build work began. | One shared boundary preflight now rejects containment and hardlink aliasing, inventories consumed trees, runs before replay/build side effects, and is rechecked at publication. Fail-first CLI, smoke, and two outperformance-runner regressions preserve all consumed bytes. |
| I-029 | Important | Phase 1 equivalence verified the frozen manifest, then copied the whole caller data directory with symlink following; unrelated unauthenticated entries could be copied into the private replay snapshot. | The snapshot copies only the authenticated manifest, checksum file, and captured CSV inventory with symlink following disabled, then re-verifies the private copy. An external-secret symlink regression failed first and is excluded. |
| I-030 | Important | Tencent backfill rejected a symlink at the selected directory or managed leaf but followed a symlinked ancestor, beginning network work before atomic publication eventually rejected it. | The complete selected path is symlink-checked before globbing or download dispatch. A deterministic ancestor-symlink regression proves `_backfill_one` is never called. |
| I-031 | Important | Current README, development, and operations gates documented Bandit over `uquant` only, omitting the reviewed `research` and `scripts` scope used by CI and the approved gate. | All three current command blocks now use `uv run bandit -q -r uquant research scripts`; the expanded command passes. |
| I-032 | Important | `QUALITY.md` required complete Engineering, Phase 1, and Phase 2 reruns in every review loop, contradicting the approved bounded workflow and behavior-neutral no-repeat rule. | The guide now requires focused affected checks per iteration, one complete final production-tree gate, and repetition only after material result-affecting changes. |
| I-033 | Important | The publication ledger required push-triggered GitHub workflows to be green before the direct push that starts them. | The sequence now requires a locally verified matching tree before non-forced push, then green PR/push workflows before release, merge, or handoff completion. |
| I-034 | Important | `STRATEGY.md` applied stricter per-name score/confidence thresholds to both one- and two-member synchronized-reversal cohorts, while the routed two-member branch does not select those thresholds. | The guide now assigns synchronized reversal to the two-member 85% route and the additional `leader_score`, `secular_score`, and `leader_confidence` checks only to the one-member 50% route. No strategy byte or parameter changed. |
| I-035 | Important | The approved plan required Task 7's full pytest command to exit zero even though the same plan reserved twelve fail-closed source identities for Task 8, and its Task 8 affected suite omitted the three engineering-edge anchors. | The initial Task 7 pass documented those exact failures and added the engineering-edge file; I-037 later refined the mixed twelve-node set to four identity-only nodes. Task 8 reruns affected contracts after rebinding, before Task 9's zero-deselection final gate. |
| I-036 | Important | An interrupted partial journal append could lose its primary error if rollback raised `KeyboardInterrupt`/`SystemExit`, and final unlock or descriptor-close failure could likewise replace the append failure. | Deterministic rollback-interruption and close-failure regressions failed first. Rollback and cleanup now attempt every operation across `BaseException`, attach secondary failures to the primary, and raise cleanup directly only when no primary exists. |
| I-037 | Important | The twelve provisional deselections were whole mixed-purpose nodes: stale source binding stopped execution before independent binding/JSON/Git, session/score/manifest/data/account-tamper, market-safety, checkout, mutation, and deterministic-runner assertions. | Reduced the provisional set to four identity-only nodes. Every independent assertion now runs against a synthetic signed-contract binding or real Git bytes at the sealed reviewed commit; the runner uses current runner/research code with only its production inventory restored from the reviewed Git object. Removed the two mock-heavy omnibus coverage tests and retained their material cases in focused real-boundary tests. |
| I-038 | Important | Handoff prose assigned Task 8 the complete zero-deselection Engineering result even though Task 8 must still commit derived identities and evidence metadata before a final exact-HEAD proof. | Task 8 now unambiguously owns authenticated Phase 1/Phase 2 economics, Git-derived identity binding, affected contracts, and its metadata commit. Task 9 owns the complete zero-deselection Engineering/provenance proof after that last commit and must rerun it after any later tracked metadata change. |
| M-001 | Minor | `run_window_competitor_adapter.py --workers 0` validated source checkouts and staged data before surfacing a late process-pool error. | Validates a positive worker count immediately after argument parsing and reports the offending option through `argparse`. |
| M-002 | Minor | Invalid UTF-8 at strict frozen-manifest, ablation-registry, source-contract, checkpoint, worker, and evidence-manifest readers escaped as raw codec errors rather than their local data/ablation domain errors. | Added `UnicodeError` only at those trust-boundary exception translations. Parameterized invalid-byte regressions fail first and now retain the original cause under `DataContractError` or attributable `ValueError`. |
| M-003 | Minor | The backfill managed-symlink regression returned a successful fake download, so it did not directly prove rejection happened before network work. | Replaced the fake downloader with a fail-on-call stub; both managed-leaf and ancestor-symlink tests now prove acquisition is never entered. |
| M-004 | Minor | Atomic text and byte publication retain similar write/flush/replace bodies. | Deferred after explicit Task 7 triage: path, alias, mode, temporary, cleanup, and durability primitives are already shared, while extracting the remaining safety-critical bodies adds churn without a demonstrated behavioral benefit. |
| M-005 | Minor | A `Path.resolve()` symlink-loop `RuntimeError` escaped the atomic-output domain boundary even though ordinary resolution failures were attributable `ValueError`s. | A focused regression failed first. Both destination and protected-root resolution now translate `OSError` and symlink-loop `RuntimeError` to the same chained fail-closed domain error. |
| M-006 | Minor | The reviewed-source test fixture reads the source-contract commit with ordinary JSON before production validation authenticates the contract. | Deferred: every consumer immediately invokes the production validator, so malformed, unsealed, or invalid identities fail closed; adding a second test-only contract validator would duplicate the trust boundary. |
| M-007 | Minor | The current-runner fixture explicitly lists the production paths restored from the sealed reviewed commit. | Deferred: the explicit hybrid is test setup, and any future inventory addition makes the observed source fingerprint mismatch and fails closed; deriving private production internals would reduce test independence. |
| M-008 | Minor | Two new temporary-Git fixtures follow existing repository precedent and do not explicitly disable global commit signing. | Deferred: the locked gate environment passes, existing Git-fixture tests share this convention, and changing only the new commands would not establish a coherent repository-wide policy. |

## Mechanical review record

The initial tracked inventory contains 136 paths: 114 Python files, 19 Markdown
files, and three workflow files. The initial `git status --short` output was
empty.

| Command | Result |
|---|---|
| `uv run ruff check .` | Pass: `All checks passed!` |
| `uv run mypy uquant scripts research` | Pass: `Success: no issues found in 66 source files` |
| `uv run bandit -q -r uquant research scripts` | Pass (exit 0); only existing `# nosec B603` acknowledgement warnings, with no failed Bandit test. |
| `git ls-files '*.py' '*.md' '.github/workflows/*.yml' \| sort` | Captured the 136-path review inventory. |
| `git status --short` | Empty before this ledger was created. |

Targeted textual scans found no bare `except:`, `shell=True`, `eval(`, `exec(`,
or mutable-default function-parameter finding in `uquant`, `research`,
`scripts`, or `tests`. Advisory complexity counts are review-routing signals,
not automatic refactor instructions.

## Final validation and publication state

Task 8 ran the authenticated economic/provenance section, derived and committed
source bindings, and reran affected contracts. Task 9 then ran the Engineering
section with zero deselections plus final provenance readback on the published
candidate. The sections below retain the commands and exact evidence identities.

### Engineering — Task 9 final zero-deselection proof

```bash
export UV_CACHE_DIR=/tmp/uquant-balanced-review/uv-cache
uv run ruff check .
uv run mypy uquant scripts research
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run pytest --cov=uquant --cov-report=term-missing --cov-report=xml
uv run python -m compileall -q uquant scripts research tests
uv run python -m build
uv run bandit -q -r uquant research scripts
uv export --frozen --no-dev --no-emit-project --no-hashes \
  --output-file /tmp/uquant-balanced-review/requirements.txt
uv run pip-audit --cache-dir /tmp/uquant-balanced-review/pip-audit-cache \
  --requirement /tmp/uquant-balanced-review/requirements.txt
```

### Economic and provenance — Task 8 authenticated execution and rebinding

```bash
mkdir -p /tmp/uquant-balanced-review/final
uv run python -m uquant.validation promotion \
  --data-dir data/frozen --profile full \
  --output /tmp/uquant-balanced-review/final/phase1.json
uv run python -m uquant.validation.ci_artifacts phase1 \
  --artifact /tmp/uquant-balanced-review/final/phase1.json \
  --report-output /tmp/uquant-balanced-review/final/phase1-validation.json \
  --upstream-result success --data-dir data/frozen
prefix=balanced-final
shard_root=/tmp/uquant-balanced-review/final/shards
for window in h1_2023 h2_2023 h1_2024 h2_2024 \
  bull_crash_2025_2026 continuous_ai_era; do
  artifact="$shard_root/${prefix}-${window}"
  mkdir -p "$artifact"
  uv run python -m uquant.validation generalization-matrix \
    --data-dir data/frozen --window "$window" \
    --output "$artifact/${window}.json"
done
uv run python -m uquant.validation.ci_artifacts generalization \
  --shard-root "$shard_root" --artifact-prefix "$prefix" \
  --report-output /tmp/uquant-balanced-review/final/generalization-policy-report.json \
  --merged-output /tmp/uquant-balanced-review/final/generalization-matrix.json \
  --upstream-result success --data-dir data/frozen
uv run pytest -q tests/test_future_holdout.py tests/test_future_holdout_runtime.py \
  tests/test_phase2_ablation.py tests/test_phase2_ci_contract.py \
  tests/test_engineering_gate_edges.py
git diff --check
git diff --exit-code e2663695fd008fb960b86f33bc36309a2f525b68 -- AGENTS.md
```

If production or holdout source bytes changed, rebind the immutable holdout and
post-Task-8 source contracts from Git objects before the final affected-test
run; never hand-enter a score or reuse a digest from another tree.

### Publication and remote verification

```bash
# Pre-publication guard and fast-forward used by Task 9.
git fetch origin main
test "$(git rev-parse origin/main)" = \
  "e2663695fd008fb960b86f33bc36309a2f525b68"
git push origin HEAD:main
git fetch origin main
test "$(git rev-parse HEAD^{tree})" = "$(git rev-parse origin/main^{tree})"
```

Task 9 fast-forwarded `main` from
`e2663695fd008fb960b86f33bc36309a2f525b68` through the recoverable remote
Git-object chain `c47367bba64c827fe18f788c9a3650e13ece306f` ->
`42f6cbdfcf3c3e396200758f80485b49b9e245bf` ->
`5568ac5df8f0fa96fd1ff3a2b5922da248426606` ->
`941a430794231aec22177fe293fdfa3a2618023f`. The final ledger correction is a
documentation-only direct descendant; it does not change any reviewed source,
binding, strategy, economic result, or `AGENTS.md` byte.

## Task 1 integrity checks

```text
git diff --exit-code e2663695fd008fb960b86f33bc36309a2f525b68 -- AGENTS.md
git diff --check
```

Task 1 executes these checks immediately before its scoped ledger commit.

## Task 2 durable account and execution review

Reviewed `uquant/account.py`, `uquant/atomic_io.py`, `uquant/broker.py`,
`uquant/execution.py`, `uquant/execution_journal.py`, and `uquant/types.py`
against all seven mapped test modules. The trace confirmed explicit schema-v5
migration, exact durable order-sequence non-reuse, prose-independent attribution
identity and native BUY/lot origin closure, duplicate broker-fill rejection and
idempotency, snapshot all-or-nothing mutation, sell-before-buy cash release,
minimum lots and STAR initial lots, T+1 sellability, exact sold-lot and fee
allocation, temp-file cleanup after atomic replacement, journal lifecycle and
hash-chain continuity, and externally retained checkpoint continuity.

The only production change is I-002 in the observational execution journal. It
does not feed strategy, execution planning, account persistence, or replay, so
the behavioral change cannot affect economic results and did not require an
economic replay expansion.

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_execution_journal.py::test_journal_append_completes_after_short_os_writes` (before fix) | Fail as intended: `OSError: short execution journal append`. |
| Same focused test after the fix | Pass: `.`. |
| Fix-round partial-then-zero and partial-then-error tests before rollback | Fail as intended: both prior-journal byte-equality assertions detected a retained 10-byte JSON prefix. |
| Same two focused rollback tests after the fix | Pass: `..`. |
| `uv run pytest -q tests/test_execution_journal.py` | Pass: `14 passed` (`.............. [100%]`). |
| Complete seven-file boundary pytest command from the Task 2 brief | Pass: 238 collected tests, progress reached `[100%]`. |
| Task 2 Ruff command from the brief | Pass: `All checks passed!` |
| Task 2 mypy command from the brief | Pass: `Success: no issues found in 6 source files` |

## Task 3 strategy, portfolio, risk, and engine review

Reviewed `uquant/config.py`, `uquant/config_governance.py`, `uquant/data.py`,
`uquant/features.py`, `uquant/industry.py`, `uquant/leader.py`,
`uquant/opportunity.py`, `uquant/portfolio.py`, `uquant/portfolio_core.py`,
`uquant/portfolio_leaders.py`, `uquant/portfolio_recovery.py`,
`uquant/portfolio_strategic.py`, `uquant/reference.py`,
`uquant/reference_registry.py`, `uquant/risk.py`, `uquant/risk_sector.py`, and
`uquant/engine.py` against the ten mapped strategy test modules. No production
source change or parameter change was justified.

The ownership trace confirmed the following invariants:

- `SystemConfig` remains immutable, completely serialized, and fail-closed on
  invalid relationships; compile-anchored governance classifies every live
  field exactly once without changing the frozen economic search freedom.
- `ProductionEngine.decide()` is the single daily/replay path. It checks schema,
  chronology, broker boundary, data-prefix provenance, and code identity before
  advancing state; resolves point-in-time reference membership; builds only
  causal features and reference/leader evidence; runs risk before opportunity;
  applies opportunity alpha and leader tenure once; delegates one target vector
  to the allocator; attaches stable attribution; then plans, merges, and
  atomically reconciles next-open intents before recording durable provenance.
- Data and feature consumers use date-bounded prefixes. Reference membership has
  inclusive starts and exclusive ends, future rows are excluded from features,
  correlation, leader, risk, and recovery evidence, and stable reference
  coverage fails closed. Cross-section, membership, candidate, target, and order
  collections use deterministic symbol/rank tie-breaks.
- `assess_risk()` remains the sole aggregate gross-cap authority. It owns shock,
  sector-guard, drawdown, capital-budget, chronic, anchor, and repair state, but
  emits no order. `PortfolioAllocator.allocate()` remains the sole target-vector
  owner and applies the returned cap on every strategy route with one sparse,
  no-buy risk reducer and lifecycle-aware lot retention.
- Strategic, recovery, and generic-leader ownership is mutually ordered. Live
  strategic epochs retain first refusal; freezes preserve durable reductions and
  cancel additions; recovery anchors preserve saved restoration/substitution
  rights; generic leader admission, dynamic K, additions, scouts, rotation, and
  lifecycle exits use their own persistent confirmation keys. Owner handoffs
  clear or transfer stale anchors, restore rights, and rearm state explicitly.
- Replacement tenure is pair-scoped and reset when candidates or evidence gaps
  change. Ordinary rotation, recovery substitution, leader lifecycle exits,
  strategic qualification, strategic trails, recovery admission, and risk
  repair each retain their configured confirmation and minimum-tenure contracts.
- Protected and strategic restoration targets survive partial/blocked execution
  but are retired on final strategy exit, failed restoration, completed
  transition-impulse liquidation, or completed restore. Gross, symbol, position,
  unknown-industry, industry, recovery, strategic, and risk caps remain layered
  without adding an alternate cap or order owner.
- Decisions require strictly increasing common index sessions. Dynamic-K,
  rotation, recovery, and shock-rearm distances use causal market-session clocks.
  Orders are emitted after the close and cannot execute until a later open;
  sells precede buys and tranche sellability enforces T+1. Backtest uses that
  same open-execute, close-decide transition and persists the complete account
  state and replay trace.

The brief's decision-equivalence command could not reach replay: the requested
detached baseline is `e2663695fd008fb960b86f33bc36309a2f525b68`, while the
existing validator is intentionally hard-bound to the older immutable Phase 1
champion `cf8fecff76564fd4ed87faa0da336a06d433fd93`; it therefore rejected the
baseline commit before evaluating a case. The validator was not weakened or
changed. Because Task 3 made no production-source change, the plan owner
authorized an exact byte-identity gate over every named Task 3 production file
against `e2663695fd008fb960b86f33bc36309a2f525b68`. That gate is stronger than
sampled replay equivalence for this unchanged boundary and passed with no diff.

| Command | Result |
|---|---|
| Complete ten-file focused pytest command from the Task 3 brief | Pass: progress reached `[100%]` (exit 0). |
| `uv run ruff check uquant tests` | Pass: `All checks passed!` |
| `uv run mypy uquant` | Pass: `Success: no issues found in 46 source files` |
| Exact Task 3 decision-equivalence command with the required `e266369...` detached baseline | Precondition failure before replay: `RuntimeError: frozen equivalence tree does not match the Phase 1 champion commit`. |
| `git diff --exit-code e2663695fd008fb960b86f33bc36309a2f525b68 --` plus all 17 Task 3 production paths | Pass (exit 0, no output): every reviewed production byte is identical to the authenticated current baseline. |

## Task 4 attribution, validation, holdout, and evidence review

Reviewed `uquant/attribution.py`, `uquant/report.py`, every Python and JSON
resource below `uquant/validation/`, every Python file below `research/`, the
three named Phase 1/Phase 2 scripts, and all 16 mapped attribution, holdout,
generalization, Phase 1, Phase 2, and validation test modules. The supporting
atomic persistence helper in `uquant/atomic_io.py` was reviewed and extended
only to restore exact evidence bytes.

The fail-closed trace confirmed the following invariants:

- Attribution validates exact event, symbol, quantity, value, fee, strategy,
  and origin identities against authenticated raw engine results. Reconciliation
  closes exactly against economic totals, and policy consumes the canonical
  reconciled values rather than caller summaries.
- Promotion, generalization, holdout, universe, competitor, CI, and ablation
  contracts reject duplicate keys where JSON is a trust carrier, non-finite
  economic values, malformed fields, changed canonical seals, missing or extra
  path inventory, symlinks, source/data mutation, and unverified Git identities.
  Frozen-data manifests, ablation registries, Phase 2 checkpoint envelopes, and
  Phase 1 diagnostic traces now reject the ambiguity found in I-004.
- Source identities are length-delimited over sorted exact path inventories.
  Historical reads use validated 40-hex commits and fixed-argument `git
  ls-tree`/`show` calls. Phase 1 equivalence now rejects any dirty or untracked
  checkout byte, captures both commit identities, and executes isolated Python
  from private detached worktrees at those commits. It also binds the exact
  frozen-baseline data provenance, replays the full matrix from one private
  verified data snapshot, and rechecks both source trees and caller data after
  replay.
- A first holdout update contains exactly one reviewed session. A consecutive
  update preserves the checkpointed session, decision-digest, and data-prefix
  histories exactly and adds at most one session. The externally retained
  journal checkpoint remains bound through replay, daily decision, and carrier
  checkpoint validation.
- Future-holdout replay, daily decision, and checkpoint writes now run under a
  globally ordered set of stable, external repository and canonical-carrier
  locks with exact pre-update snapshots. Ordinary failures and process-control
  interruptions attempt recovery for every carrier while retaining the primary
  failure. Atomic generation claims and no-replace restoration preserve a
  concurrently installed successor instead of overwriting it. Unsafe
  pre-existing symlinks are rejected without being treated as newly created
  output.
- Generalization shards accept exactly the six official directories and 39
  records per shard, reject duplicate cell identities, and aggregate exactly
  234 records. They recompute provenance, replay errors, attribution,
  concentration, and the frozen policy instead of trusting shard summaries.

All five broad `except Exception` sites remain intentional outer boundaries.
Promotion and generalization immutable-input guards convert every possible
post-replay read/fingerprint failure into a chained deterministic error;
generalization-matrix and Phase 2 replay capture exception type, message, and
cell/date context while retaining partial diagnostic trace and excluding failed
cells from metrics; the frozen-competitor process boundary converts custom
exceptions into a built-in, attributable error safe for process-pool transport.
The Phase 2 materialization handler was narrowed so it no longer spans caller
execution, while cleanup handling was expanded only to preserve a primary
`BaseException` and attach secondary recovery errors. Every reviewed subprocess
uses a resolved or fixed executable with an argument array, never a shell, and
`check=True` with captured output. Detached-worktree removals are checked;
failures are raised when primary and otherwise attached as notes, never used to
authenticate or fabricate evidence.

No strategy, parameter, policy, window, pool, seed, universe, data, market rule,
or frozen threshold changed. I-003 and I-007 through I-009 change only failure
persistence and evidence recovery; I-004/I-012 reject ambiguous encodings;
I-005/I-006/I-010/I-011 prevent source or data attribution to mutable,
substituted, or globally pre-imported bytes. They do not change decisions,
orders, accounts, fills, or economic formulas. They do change reviewed
production and holdout source identities, so the immutable holdout and
post-Task-8 Git-object contracts and the complete Phase 1/Phase 2 economic
validation remain correctly deferred to Task 8; no digest was hand-entered or
weakened during this round.

| Command | Result |
|---|---|
| Eighteen focused fail-first regressions before their fixes | Failed as intended: retained replay bytes, interruption bypass, foreign-generation overwrite, lock aliasing, a rejected symlink removed, duplicate keys accepted (`DID NOT RAISE`), mutable/untracked equivalence inputs reached replay, substituted/private-mutated data was accepted, caller provenance was read, and caller paths were executed directly. |
| `uv run pytest -q tests/test_future_holdout_runtime.py` | Pass: 28 tests, `............................ [100%]`. |
| `uv run pytest -q tests/test_phase1_decision_equivalence.py` | Pass: ten tests, `.......... [100%]`. |
| `uv run pytest -q tests/test_phase1_diagnostic_runner.py` | Pass: seven tests, `....... [100%]`. |
| Complete 420-test Task 4 pytest command | 412 pass. Eight fail closed solely at the planned Task 8 provenance preconditions: one frozen prior-close code hash and seven post-Task-8 source-contract cases. |
| Same Task 4 command with exactly those eight Task 8 preconditions deselected | Pass: 412 tests, progress reached `[100%]` (exit 0). |
| Task 4 Ruff command | Pass: `All checks passed!` |
| Task 4 mypy command | Pass: `Success: no issues found in 40 source files` |
| Task 4 Bandit command | Pass (exit 0); only existing fixed-argument `# nosec B603` acknowledgement warnings. |
| `git diff --check` and authenticated-baseline `AGENTS.md` diff | Pass (exit 0, no output); `AGENTS.md` SHA-256 remains `640298ceac5187724d2cf769b13f4d7e2381cbcb10faf46e99bdac378547f808`. |

Internal fix-round validation tightened, rather than superseded, those results:

| Command | Result |
|---|---|
| Four focused holdout regressions before the fix | Four failures: canonical evidence deletion, foreign-generation TOCTOU deletion, primary failure masking, and missing shared-carrier lock identity. |
| Two focused Phase 1 regressions before the fix | Two failures: trace child lacked `-S`, and a partial worktree add received no removal attempt. |
| Three focused Phase 2 regressions before the fix | Three failures: clean baseline/evidence commit switches were accepted and a duplicate-key trusted manifest was accepted. |
| `uv run pytest -q tests/test_phase1_decision_equivalence.py tests/test_future_holdout_runtime.py` | Pass: 44 tests, `............................................ [100%]`. |
| Focused Phase 2 post-yield, cleanup, and manifest command | Pass: seven tests, `....... [100%]`. |
| Complete updated 428-test Task 4 command | 420 pass. The same eight Task 8 provenance preconditions fail closed; no provenance contract was rebound. |
| Same updated Task 4 command with exactly those eight preconditions deselected | Pass: 420 tests, progress reached `[100%]` (exit 0). |
| Updated Task 4 Ruff / mypy / Bandit commands | Pass: `All checks passed!`; `Success: no issues found in 40 source files`; Bandit exit 0 with only existing `# nosec B603` acknowledgement warnings. |
| Isolated Phase 1 trace smoke with one real frozen case | Pass: `a/bull` produced 64-hex decision and economic-account digests under `python -I -S`. |

Internal fix round 2 validation:

| Command | Result |
|---|---|
| Four focused canonical-identity, post-claim, and lock-cleanup regressions before the fix | Four failures: writes followed a changed working directory, owned and foreign generations were both deleted after link errors, and the first close error replaced the primary exception and stopped descriptor cleanup. |
| Same focused command after the fix | Pass: four tests, `.... [100%]`. |
| `uv run pytest -q tests/test_future_holdout_runtime.py` | Pass: 36 tests, `.................................... [100%]`. |
| Holdout and validation adjacent suite with the one Task 8 prior-close precondition deselected | Pass: 107 tests, progress reached `[100%]`. |
| Updated Task 4 suite with exactly eight Task 8 provenance preconditions deselected | Pass: 424 tests, progress reached `[100%]`. |
| The eight Task 8 provenance nodes run directly | Fail closed as planned: `FFFFFFFF [100%]`; one frozen prior-close code hash and seven post-Task-8 source-contract checks reject the changed source. |
| Updated Task 4 Ruff / mypy / Bandit commands | Pass: `All checks passed!`; `Success: no issues found in 40 source files`; Bandit exit 0 with only existing `# nosec B603` acknowledgement warnings. |

## Task 5 CLI, adapters, data acquisition, and remaining-test review

Reviewed `uquant/__init__.py`, `uquant/__main__.py`, `uquant/cli.py`,
`scripts/backfill_tencent_history.py`,
`scripts/run_five_window_outperformance.py`, `scripts/run_pareto_evidence.py`,
`scripts/run_window_competitor_adapter.py`, and
`scripts/run_window_outperformance.py`. The remaining-test inventory was:
`tests/test_ai_era_contract.py`, `tests/test_ai_universe_contract.py`,
`tests/test_backfill_security.py`, `tests/test_ci_artifact_validation.py`,
`tests/test_cli_and_report.py`, `tests/test_competitor_validation.py`,
`tests/test_conviction_guard.py`, `tests/test_engineering_gate_edges.py`,
`tests/test_first_divergence.py`, `tests/test_five_window_outperformance.py`,
`tests/test_pareto_evidence.py`, `tests/test_research.py`,
`tests/test_window_competitor_adapter.py`, `tests/test_window_matrix.py`, and
`tests/test_window_outperformance.py`.

The trace confirmed that command choices and frozen adapter contracts remain
explicit; matrix cells and evidence rows retain deterministic system, pool,
window, symbol, and date ordering; Tencent requests are HTTPS-only,
same-origin across redirects, response-bounded, retry-bounded, and validated
before any managed write; bounded competitor data is copied into temporary
snapshots that clean up on every context exit; worker exceptions retain their
system/pool/window identity; and no shell command, caller-controlled executable,
strategy parameter, market rule, window, pool, seed, universe, data byte,
threshold, decision, order, account, replay result, or economic formula changed.

I-016 through I-018 close real overwrite boundaries rather than changing report
or economic contents. Outputs use one crash-safe atomic writer, identity errors
fail closed, daily aliases are rejected before account side effects, adapter
outputs cannot name or alias consumed input trees, and every complete backfill
payload is atomically replaced after managed symlinks are rejected before network
work. M-001 moves worker-count validation to the user-input boundary. The remaining
tests exercise behavioral boundaries, deterministic fixtures, negative
contracts, and frozen identities; exact frozen constants are retained only
where they authenticate an external or economic contract.

The brief's initial non-economic command collected 1,215 pre-Task-5 nodes and
failed only at eleven Task-8-owned source identities: the prior-close account
hash, seven Phase 2 post-Task-8 source-contract checks, and three holdout
strategy-anchor checks in the previously unmapped engineering-edge suite. The
Task 5 change to authenticated `uquant/cli.py` correctly added one more
fail-closed CLI-anchor node. No contract, digest, score, or historical artifact
was rebound. With exactly those twelve source-anchor nodes deselected, all
1,208 selected non-economic nodes pass.

| Command | Result |
|---|---|
| Initial brief pytest command | Expected fail-closed result: 1,204 pass and eleven Task 8 source-anchor preconditions fail. |
| Six focused output/worker regressions before production edits | Fail as intended: `FFFFFF [100%]`; direct writes overwrote protected inputs or symlink targets and worker validation reported the wrong prerequisite. |
| Backfill managed-symlink regression before its fix | Fail as intended: the command completed and replaced the managed symlink instead of rejecting it. |
| Same seven focused regressions after the fixes | Pass: six-node command `...... [100%]` and backfill node `. [100%]`. |
| Six complete adjacent changed-file suites | Pass: 43 tests, `........................................... [100%]`. |
| Complete fifteen-file remaining-test inventory with only its three pre-existing Task 8 engineering-edge nodes deselected | Pass, progress reached `[100%]` (exit 0). |
| First post-edit non-economic run with only the initial eleven provenance nodes deselected | One fail-closed CLI source-anchor precondition remained; every other selected node passed. |
| Final non-economic run with exactly all twelve Task 8 source-anchor nodes deselected | Pass: 1,208 selected nodes, progress reached `[100%]` (exit 0). |
| `uv run ruff check .` | Pass: `All checks passed!` |
| `uv run mypy uquant scripts research` | Pass: `Success: no issues found in 66 source files` |
| `uv run bandit -q -r uquant research scripts` | Pass (exit 0); only existing fixed-argument `# nosec B603` acknowledgement warnings. |

Task 8 owns the twelve exact source-identity preconditions and the complete
economic/provenance rebind after all production edits. Task 5 deliberately did
not weaken or relabel them.

### Task 5 fix round 1

Review found that the initial output-safety fix was incomplete at hardlink and
identity-error edges. Seven deterministic regressions failed before the fix:
the shared alias helper allowed an indeterminate identity; exact and hardlinked
daily outputs mutated or escaped the account guard; backfill metadata truncated
before publication and tech-proxy output mutated an external hardlink; and the
adapter replaced a competitor source pathname or accepted a hardlink to caller
trade data.

The minimal correction introduces one shared invocation-time atomic-output
validator, reuses it at final publish, makes identity-check errors fail closed,
preflights daily output before account loading or saving, protects the adapter
against containment and aliases across complete consumed input trees, and uses
same-directory atomic replacement for every backfill-managed file. No payload,
ordering, source lock, data row, or economic result changed.

| Command | Result |
|---|---|
| Seven focused fix-round regressions before the fix | Fail as intended: `FFFFFFF [100%]`; each failure preserved the reviewed root cause. |
| Same seven regressions after the fix | Pass: `....... [100%]`. |
| Four requested suites with exactly the three Task 8 engineering-edge nodes deselected | Pass: 62 tests, `.............................................................. [100%]`. |
| Atomic-writer adjacent suite plus backfill, CLI, and adapter suites | Pass: 37 tests, `..................................... [100%]`. |
| Focused Ruff and strict mypy | Pass: `All checks passed!`; `Success: no issues found in 4 source files`. |
| Final non-economic command with exactly the same twelve Task 8 source-identity nodes deselected | Pass: 1,213 selected of 1,225 collected nodes, progress reached `[100%]`; no source identity was rebound. |

### Task 5 fix round 2

Review found that routing managed files through `mkstemp()`-backed replacement
published the temporary inode's fixed `0600` mode. Existing group-readable CSV
and authentication metadata silently lost permissions, while new files ignored
the process umask's normal creation policy.

Two real-filesystem regressions failed before the fix: atomic text/byte
replacement changed existing `0640`/`0644` files to `0600`, and backfill changed
its CSV and metadata modes to `0600`. The shared staging helper now securely
creates unique same-directory files with exclusive `0666 & ~umask` semantics;
on POSIX, an existing destination's exact mode is applied to the staged inode
before fsync and rename. Symlink, alias, cleanup, byte, and durability behavior
is unchanged.

| Command | Result |
|---|---|
| Atomic mode and backfill permission regressions before the fix | Fail as intended: `FF [100%]`; observed mode was `0600` instead of `0640`/`0644`. |
| Same two regressions after the fix | Pass: `.. [100%]`. |
| Atomic/backfill/execution-journal/CLI/adapter/holdout adjacent suites with only three Task 8 engineering anchors deselected | Pass: 113 tests, progress reached `[100%]`. |
| Focused Ruff and strict mypy | Pass: `All checks passed!`; `Success: no issues found in 2 source files`. |
| Final non-economic command with exactly the same twelve Task 8 source-identity nodes deselected | Pass: 1,214 selected of 1,226 collected nodes, progress reached `[100%]`; no source identity was rebound. |

### Task 5 fix round 3

Review under an inherited `077` umask found a portability defect in the
backfill permission regression: its external CSV inherited `0600` before the
test installed a controlled umask, but the assertion assumed `0644`. The same
exact-mode assertions also had no Windows boundary even though production uses
native Windows creation semantics.

The fixture now explicitly sets the external inode to `0644` on POSIX before
hardlinking. Byte and external-inode preservation remain cross-platform; only
the controlled umask setup and exact POSIX mode assertions are platform-gated.
Production code and the twelve Task 8 source identities are unchanged.

| Command | Result |
|---|---|
| Backfill hardlink regression inherited under umask `077` before the test fix | Fail as intended: `F [100%]`; managed mode was `0600`, not the fixture's assumed `0644`. |
| Same node under inherited umask `077` after the test fix | Pass: `. [100%]`. |
| Complete backfill and engineering-edge adjacent suites with only three Task 8 engineering anchors deselected | Pass: 45 tests, `............................................. [100%]`. |
| Focused Ruff and strict mypy | Pass: `All checks passed!`; `Success: no issues found in 2 source files`. |

## Task 6 workflow and documentation review

Reviewed all 20 tracked Markdown files. `AGENTS.md` remains the protected current
policy. Current operational material is `README.md`, the seven topic guides under
`docs/`, and this ledger. Historical/evidentiary material is the three tracked
artifact reports, three implementation plans, two approved designs, and the two
tracked `.superpowers/sdd` task reports. No document was redundant and
non-evidentiary, so none was deleted.

Dependabot and all three workflows are valid YAML and retain the reviewed blocking
topology: unconditional PR/`main` triggers, Python 3.12 plus locked uv, pinned actions,
independent stable summaries, non-cancelling Phase 2 shards, always-running evidence
aggregation, and diagnostic artifact retention. No workflow change was justified.

Current documentation now matches the removed configuration-field inventory,
account-state ownership, the base and narrow-anchor `RISK_OFF` caps, all three holdout
milestones, and champion-relative replay-error handling. The Phase 2 final-acceptance
report is explicitly labeled as a historical snapshot; all original commits, hashes,
metrics, decisions, paths, and evidence prose remain intact.

| Command/check | Result |
|---|---|
| Current `uquant` and validation command `--help`; build, uv export, and pip-audit option help | Every documented operational option is accepted by the current parser. |
| Fenced-code-aware tracked Markdown checker | 20 files, 20 unique semantic H1 titles, ten valid local links, and 48 syntactically valid Bash fences. |
| YAML safe parse for Dependabot and three workflows | Pass: all four roots are mappings. |
| Required two-file Task 6 pytest command | Expected fail-closed result: exactly three Task 8 strategy-source anchors fail with `strategy source bytes drifted from the Task 8 anchor`. |
| Same command with exactly those three node IDs deselected | Pass: 59 passed, three deselected. |
| Required stale-marker scan | Two contextual hits only: one historical negation and the implementation plan's scan command. |

No strategy, executable source, test, economic result, policy, data, workflow, or
source contract changed. Task 8 still owns the three exact anchor rebindings.

### Task 6 fix round 1

Follow-up review corrected two current-document contradictions without changing
runtime behavior or frozen evidence. The README now distinguishes missing records
and new/changed replay errors from an authenticated baseline replay error that
recovers under the common-support and recovery-envelope checks. The configuration
and strategy guides now state that synchronized-reversal partial cohorts select the
two-day `strategic_cohort_confirm_days` branch; the retained three-day and four-day
member-count fields remain part of configuration compatibility and the governance
inventory but are not selected by current routing.

| Command/check | Result |
|---|---|
| Fenced-code-aware tracked Markdown checker | Pass: 20 files, 20 unique semantic H1 titles, 11 valid local links, and 48 syntactically valid Bash fences. |
| Required two-file Task 6 pytest command | Expected fail-closed result: exactly the same three Task 8 strategy-source anchors fail with `strategy source bytes drifted from the Task 8 anchor`. |
| Same command with exactly those three node IDs deselected | Pass: 59 passed, three deselected. |

No source, test, configuration, strategy, economic result, contract, policy, data,
workflow, or historical evidence changed. `AGENTS.md` remains byte-for-byte intact,
and Task 8 retains sole ownership of source-anchor rebinding.

## Task 7 independent whole-branch review

Task 7 reread all 36 paths committed between
`e2663695fd008fb960b86f33bc36309a2f525b68..4161cc9a6475c58e925bf0036e7cf9742836fe71`
plus both additional paths changed during the initial Task 7 pass and
`tests/test_future_holdout.py` added by root-review fix round 1, for a 39-path
final-candidate diff against the baseline. The pass was independent of task order and covered
current and historical documentation, production and
validation code, research/scripts, tests, changed callers, trust boundaries,
filesystem and subprocess error paths, cleanup ownership, compatibility,
audit/history retention, and Git/data provenance. Separate production/test and
documentation/workflow audits were followed by a final independent diff review.
No Critical or Important finding remains open.

The review did not change a strategy owner, parameter/default, policy threshold,
official window, universe, pool, seed, market-data byte, market rule, economic
formula, decision, order, account, replay result, or historical evidence
decision. `AGENTS.md` remains byte-identical at SHA-256
`640298ceac5187724d2cf769b13f4d7e2381cbcb10faf46e99bdac378547f808`.
Frozen data remains snapshot `20260809T094222Z-causal-tech-index-rebase`, 36
files, manifest SHA-256
`343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d`,
and checksums SHA-256
`ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29`.

The three carried Minor observations received explicit rulings:

- M-002 was fixed because local invalid-UTF-8 domain translation is small,
  fail-path-only, and preserves chained causes.
- M-004 remains deferred because extracting the last similar text/byte writer
  bodies would churn a safety-critical primitive without behavioral benefit.
- M-003 was fixed test-only because a fail-on-call acquisition stub directly
  proves backfill rejects symlinks before download.

One additional low-risk Minor, M-005, was fixed fail-first so symlink-loop
resolution remains attributable without weakening rejection. I-024 through
I-038 record all fifteen Important Task 7 findings and their minimal fixes.

### Task 7 source-anchor preconditions

Task 7 intentionally did not rebind source identities. The complete pytest
command now fails only at these four identity-only Task 8 preconditions:

1. `tests/test_engineering_gate_edges.py::test_current_holdout_binding_matches_exact_head_and_reviewed_strategy_anchors`
2. `tests/test_future_holdout.py::test_current_strategy_cli_matches_reviewed_anchor`
3. `tests/test_future_holdout.py::test_current_code_fingerprint_matches_frozen_candidate_account_code_hash`
4. `tests/test_phase2_ablation.py::test_current_source_matches_reviewed_post_task8_contract`

All formerly co-located behavior assertions remain selected. Task 8 must derive
the final holdout and ablation identities from committed Git objects and
authenticated economics, then run all affected nodes including the
engineering-edge file. After Task 8 commits those bindings and ledger metadata,
Task 9 must run the complete Engineering/provenance proof with zero deselections
on that exact HEAD. No identity may be copied from dirty working-tree bytes or
reused from another tree.

### Task 7 engineering gate

All commands used `UV_CACHE_DIR=/tmp/uquant-balanced-review/uv-cache`.

| Command | Final-tree result |
|---|---|
| `uv run ruff check .` | Pass: `All checks passed!` |
| `uv run mypy uquant scripts research` | Pass: no issues in 66 source files |
| `uv run python -m uquant.validation data-manifest --data-dir data/frozen` | Pass: exact snapshot, 36 files, unchanged manifest and checksum identities |
| Full `pytest --cov=uquant` with no deselection | Expected fail closed: 1,272 passed, exactly the four identity-only nodes above failed, no other node failed; coverage 85.37% |
| Full `pytest --cov=uquant` with exactly those four nodes deselected | Pass: 1,272 passed, 4 deselected, coverage 85.28%; every formerly co-located independent assertion remained selected |
| `uv run python -m compileall -q uquant scripts research tests` | Pass, exit 0 |
| `uv run python -m build` | Pass: sdist and wheel built |
| `uv run bandit -q -r uquant research scripts` | Pass, exit 0; fixed-argument `nosec` acknowledgements only |
| Frozen `uv export` | Pass: locked runtime requirements exported |
| `pip-audit` against the frozen export | Pass: no known vulnerabilities |
| Baseline `AGENTS.md` diff and SHA-256 | Pass: no diff; exact hash above |
| `git diff --check` | Pass: no whitespace errors |

The final self-review rechecked the Task 7 brief, complete baseline diff, Task 7
working diff, finding dispositions, deterministic red/green evidence, source
anchor non-rebinding, canonical output and compatibility surfaces, and the
absence of accidental tracked paths. No remote write or publication occurred.

## Task 8 authenticated economics and provenance binding

Task 8 ran the complete frozen Phase 1 and all six Phase 2 windows from the clean
pre-binding source anchor
`9b2f665eb23f1deafba8a1f6a686a47fbc3436b5`. Remote source checkpoint
`c47367bba64c827fe18f788c9a3650e13ece306f` has the identical tree
`9f218d1d4b6222282095c777655b5bcce2a985e1`. No strategy owner, parameter,
policy threshold, window, pool, seed, universe, data byte, market rule, economic
formula, decision, order, account, replay result, or historical score was tuned
or relabeled. The authenticated evidence is retained under
`/tmp/uquant-balanced-review/final`.

### Task 8 economics

| Evidence | SHA-256 | Result |
|---|---|---|
| `phase1.json` | `3943258459115ad68a11752030a7d48e793297d1a7d321c860a848ccfb688f8b` | Pass: 30 official and 15 protected cells; median wealth `4.14785416600156`, 340 account orders, worst drawdown `0.2786861829563525` |
| `phase1-validation.json` | `c0ac47fc7e3c18dd4d322d1af9ed42b84a24b963cf350db3af4d3aaa574175c3` | Pass with no failures |
| `generalization-matrix.json` | `62cafaa16b3ccbeaa1704c19ab4b7fa1073d5d300a96afe62eef19e973ac9406` | Pass: 234 records, 192 economic cells, 42 insufficient-sample records, zero replay errors |
| `generalization-policy-report.json` | `7a7e2a58046eb9e69e215a59e74647b6591e0f58d5e588e5bd9ae43ff8b5e7aa` | Frozen policy pass with no failures |

Each Phase 2 window contributed exactly 39 records: 32 economic and seven
insufficient-sample records. All 192 economic attributions reconciled, with
maximum absolute reconciliation error
`7.450580596923828e-09`. Aggregate median wealth was
`1.1151492647234051`, p10 wealth `0.9936017483997773`, p90 drawdown
`0.21151575819020615`, worst drawdown `0.2825468776869108`, median orders
`6`, p90 orders `19.80000000000001`, median annual turnover
`2.720569961945872`, and p90 annual turnover `6.964561529134746`.

Phase 1's 30 official and 15 protected metric mappings are exactly equal to the
committed baseline artifact at `e2663695fd008fb960b86f33bc36309a2f525b68`
(blob SHA-256
`d81bb2f67413f58ca255f51bf42702fe547ee51460ed8bb269760b89f11d836b`),
including its summary and validation fingerprint. Phase 2 compared against the
unchanged committed champion blob
`926ea8419ab8aad7a05577eee56aeefa90c33cc7faa4e1ee1d2bbbaac77439cc`.
The frozen policy authenticated the existing configuration migration from
`023d709731196a325d9cd03e95ece92e4baf63d2c5c66bb9f7d0e7a190e7bf20`
to `ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13`,
so exact champion equality was not required. All 30 intrinsic checks and all 24
random-tail checks passed; every random-tail result passed its frozen
non-regression comparison. The prior champion's sole replay error,
`continuous_ai_era/random__20__0000`, became a reconciled economic cell, taking
valid cells from 191 to 192 and replay errors from one to zero. Thus no
unauthorized economic deterioration occurred.

### Task 8 exact source binding

The holdout identities were re-derived from remote committed Git object
`c47367bba64c827fe18f788c9a3650e13ece306f`, not from dirty working-tree
bytes. The 42-path strategy inventory hashes
to `f9c78557e38342c5a994f19fde63352f635ac37c5d2d7a187ba410b98caa1aed`;
the Git-object CLI semantic hash is
`fb3da89b7bb8ec745e2249d10173855edc5976a6d1d5f4fd952552d7a2e7e427`;
and the Git-object account-code identity is
`de361ef93a218449df927f5aab14e5013110cc3141a89f94686156bed37a66fc`.
The authentic `continuous_ai_era/full` final account has prior close
`2026-08-05`, the same account-code identity, 36 frozen symbols, data digest
`9a73ed7e19d34ab8876c7ddb9e974147e1c43d8dcfcaa73abe85c7f9a3ee492e`,
and canonical account digest
`251c90cef356821547c633c69595371aa857a704d8ea21e5119be16136ac0fc8`.
`validate_prior_close_account` accepted that exact artifact. The rebound holdout
contract canonical seal is
`f1555d2f5527b83899ade8f934f67de8df6050aa2ebc7453d0d4245c618e2aeb`.

The stable remote sequence is source checkpoint
`c47367bba64c827fe18f788c9a3650e13ece306f`, holdout-only anchor
`42f6cbdfcf3c3e396200758f80485b49b9e245bf`, and post-source binding
`5568ac5df8f0fa96fd1ff3a2b5922da248426606`. From the holdout anchor,
`_production_paths_at_commit`, `_source_fingerprint_at_commit`, and
`_source_delta` reproduced a 48-path base fingerprint
`9bedfd5fb2bed6d3a1624efcca6f1d442c765abdee9e4749170fbb2e89536d6b`,
a 53-path reviewed fingerprint
`3356a4e2e99da02ed215cc98163fe83f04c9bddc21c96b37850287599784b26a`,
and 14 exact deltas. Their post-Task8 canonical seal is
`09b8e9709bb09a31dddc79659faf725afc616956364ec5324e354b6e83fb2b44`.
The three remote trees are respectively `9f218d1d4b6222282095c777655b5bcce2a985e1`,
`856ed05df998e00b1406a20ff40077964e054fe3`, and
`e92a0518e1015de7125b811717552b5da8526403`. This avoids circular
self-binding and leaves historical evidence and economics unchanged.

### Task 8 affected gate

All commands used `UV_CACHE_DIR=/tmp/uquant-balanced-review/uv-cache`.

| Command/check | Result |
|---|---|
| Five-file affected pytest command | Pass: all 188 collected tests, including the four formerly deferred identity-only nodes |
| `uv run ruff check .` | Pass: `All checks passed!` |
| `uv run mypy uquant scripts research` | Pass: no issues in 66 source files |
| `git diff --check` | Pass: no whitespace errors |
| Baseline `AGENTS.md` diff and SHA-256 | Pass: no diff; exact SHA-256 `640298ceac5187724d2cf769b13f4d7e2381cbcb10faf46e99bdac378547f808` |

The remote Git-object checkpoint chain is readable by exact commit identity.
Task 9 completed the zero-deselection Engineering/provenance suite, exact-HEAD
readback, and non-forced fast-forward described below.

## Task 9 final verification and publication

Task 9 verified detached remote candidate
`941a430794231aec22177fe293fdfa3a2618023f` with tree
`75cbc992f85d5a704182d03632caa1f781a3fda8`, then fast-forwarded `main`
without force. The candidate was four commits ahead of the baseline, zero
commits behind, with the baseline as its merge base.

| Final check | Result |
|---|---|
| Full `pytest --cov=uquant` | Pass: 1,276 passed; zero failed, errored, skipped, or deselected; 85.39% branch coverage against an 85% gate |
| Four exact identity nodes | Pass: 4 passed independently |
| Ruff and strict mypy | Pass; mypy checked 66 source files |
| Frozen manifest | Pass: 36 files; manifest `343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d`; checksums `ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29` |
| Compile, build, Bandit, frozen export, pip-audit | Pass; sdist and wheel built; no known dependency vulnerabilities |
| Strategy and repository integrity | Pass: 17 strategy/risk/portfolio files and `AGENTS.md` are byte-identical to the baseline |
| Documentation integrity | Pass: 20 Markdown files, 20 unique H1 headings, 11 valid local links, and 49 valid Bash fences |

The first published candidate completed all three push workflows successfully:

- [Engineering gates](https://github.com/ychenracing/uquant/actions/runs/32078660793)
- [AI-Era performance](https://github.com/ychenracing/uquant/actions/runs/32078660789)
- [AI-Era generalization](https://github.com/ychenracing/uquant/actions/runs/32078660787)

This ledger-only correction is deliberately outside the reviewed production and
binding inventories. Its own exact-HEAD identity, documentation, Engineering,
performance, and generalization checks must also remain green before handoff.
