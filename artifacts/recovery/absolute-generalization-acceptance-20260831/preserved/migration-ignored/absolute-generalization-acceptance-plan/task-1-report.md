# Task 1 — authoritative baseline evidence

Status: **DONE**

Executed in `/workspace/scratch/1a8f428176e6/uquant-base/.worktrees/absolute-generalization-acceptance` on the current checkout. No tracked production code, contract, test, or documentation file was edited. The sole report file is ignored by `.superpowers/sdd/.gitignore`.

## Branch and tree identity

Initial and final identity checks were identical:

```text
$ git status --porcelain=v2 --branch
# branch.oid d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5
# branch.head codex/absolute-generalization-acceptance
# branch.upstream origin/main
# branch.ab +0 -0

$ git rev-parse HEAD HEAD^{tree} origin/main
d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5
d718c3f3c2c31659c12f72f7f206184ca2810ef4
d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5

$ git merge-base HEAD origin/main
d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5
```

Thus `HEAD == origin/main == merge-base == d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`; the tree object is `d718c3f3c2c31659c12f72f7f206184ca2810ef4`. The porcelain status contains only branch headers (no changed, staged, or untracked paths).

## Commands and exact outcomes

| Command | Exit code | Exact result |
|---|---:|---|
| `git status --porcelain=v2 --branch` | 0 | clean; branch and upstream shown above; `+0 -0` |
| `git rev-parse HEAD HEAD^{tree} origin/main` | 0 | values shown above |
| `git merge-base HEAD origin/main` | 0 | `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5` |
| `uv run python -m uquant.validation data-manifest --data-dir data/frozen` | 0 | 36 frozen files verified; see frozen-data section |
| grant JSON schema assertion + `validate_contract(load_contract())` for ownership | 0 | both contracts `PASS`; exact output below |
| `uv run pytest tests/test_strategic_grant_model.py tests/test_strategic_grant_observation.py tests/test_strategic_grant_identity.py tests/test_strategic_grant_recovery.py tests/test_account_schema_v3_integrity.py tests/test_broker_sync.py tests/test_strategic_forced_owner.py` | 0 | `85 passed in 48.47s`; no skipped or failed tests |
| `uv run pytest tests/test_flat_book_capital_repair.py tests/test_strategic_cash_rearm_state.py tests/test_strategic_cash_rearm.py tests/test_strategic_capital_authority.py tests/test_strategic_universe_quorum.py tests/test_strategic_epoch.py tests/test_strategic_owner_continuity.py tests/test_strategic_successor.py tests/test_strategic_ownership_acceptance.py` | 0 | `95 passed in 1.48s`; no skipped or failed tests |
| `uv run python scripts/run_strategic_grant_acceptance.py --output /tmp/absolute-generalization-task1.tW6T53/strategic-grant-acceptance.json` | 0 | artifact status `PASS` |
| `uv run python scripts/run_strategic_ownership_acceptance.py --shard champion --output /tmp/absolute-generalization-task1.tW6T53/strategic-ownership-champion.json --cache-dir /tmp/absolute-generalization-task1.tW6T53/strategic-ownership-cache` | 0 | artifact status `PASS`; both scenarios cache miss / newly recomputed |
| `uv run python scripts/run_strategic_ownership_acceptance.py --shard critical --output /tmp/absolute-generalization-task1.tW6T53/strategic-ownership-critical.json --cache-dir /tmp/absolute-generalization-task1.tW6T53/strategic-ownership-cache` | 0 | artifact status `PASS`; both scenarios cache miss / newly recomputed |
| `git diff --check` | 0 | no output (pass) |
| `git diff --cached --check` | 0 | no output (pass) |
| `git diff --quiet` | 0 | no unstaged tracked diff |
| `git diff --cached --quiet` | 0 | no staged diff |

The two focused test lists are the exact lists from `.github/workflows/strategic-grant-acceptance.yml` and the `ownership-tests` job of `.github/workflows/strategic-ownership-acceptance.yml`, respectively. No tests were skipped and no test failures occurred. The first test command’s runner transport yielded before its final shell wrapper printed `EXIT_CODE=0`; the pytest completion line is `85 passed in 48.47s`, which denotes pytest exit status 0. All other listed commands returned their exit codes directly (the three runner wrappers each printed `EXIT_CODE=0`).

Explicitly not run (outside this task’s authorized scope): Extended Performance Matrix, Extended Economic Matrix, and the slow `architecture-portfolio` set.

## Frozen data and contracts

Frozen-data validation output:

```json
{
  "checksums_sha256": "ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29",
  "files_verified": 36,
  "manifest_sha256": "343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d",
  "snapshot_id": "20260809T094222Z-causal-tech-index-rebase"
}
```

Contract-validation output:

```json
{"grant_contract":"PASS","grant_symbols":["sz300308","sz300502","sz300394","sh688008","sh603986"],"ownership_contract":"PASS","ownership_shards":["champion","continuity","critical","ghost-a","ghost-b"]}
```

The grant validator asserted its exact top-level fields, exact baseline fields, five-symbol baseline, and three native-eligibility entries. The repository ownership `validate_contract` validator accepted the bounded 34-symbol universe, exact shard set and scenario IDs, thresholds, and window. The production grant runner subsequently revalidated the baseline economic hashes and metrics.

## Runner artifacts and compact facts

All runner artifacts and caches were written only under `/tmp/absolute-generalization-task1.tW6T53`.

| Artifact | SHA-256 | Key facts |
|---|---|---|
| `/tmp/absolute-generalization-task1.tW6T53/strategic-grant-acceptance.json` | `6aa5f4c95aeb08ac6c63fd09194545c414e4a0ea706ea1494253bea84f597aee` | `status=PASS`; baseline path hashes and all metric values match the frozen grant contract; three native-eligibility cells are `SUCCESS` |
| `/tmp/absolute-generalization-task1.tW6T53/strategic-ownership-champion.json` | `70c8185cc8192011c346e42449d18e25520a8c528f28c097a3efc4e8ff20b11d` | `status=PASS`; `contract_sha256=72e6b510c3bcf44ac77d2c13613f4d72a14ae8dab0d60a19e5947055ae7cbf08`; `production_source_identity=d1ef7977ae482e46a920381e6af58791199ec8e1a02586dbe8df451e7d4696c9` |
| `/tmp/absolute-generalization-task1.tW6T53/strategic-ownership-critical.json` | `dabac9a6595cebc97fc074236b9098cf293586fa9acf78147d412cccb2ae3d4a` | `status=PASS`; same contract and production-source identities; both critical removal scenarios are newly recomputed |

Native-eligibility provenance facts: `sz300308` at `2024-02-27` (`final_account_sha256=029f6392d7ebbad80a77d68c3ee8f41b9c6eef7b667f8d0783086a0234576afc`, `trace_sha256=7ae92d50875804f450ac422659875350e24c4cd46e3952f7f72e6a7f3a48ce36`); `sz300502` at `2024-03-04` (`8598b32fd4ff97355107d3a7403d9f936ab5267feafa51e8fa845e417b69c1f0`, `66265e99e34ddff4faddffa293a8a460c68ca3e8e7a8c5759f8de9d575931095`); `sz300394` at `2023-02-13` (`a8bf59dc15ebd7b2d801f1fe3cadff2f8aeb76a89053866fae5f004a0640eb18`, `8e8e38385013157bbee90c62c1915aa144aae803553c6fcd0c716cb558fc4b63`).

## Historical-invariant reconciliation

| Required invariant | Observed fact | Result |
|---|---|---|
| Champion wealth `24.509661802900865` | grant baseline and champion-5 both report exactly `24.509661802900865` | MATCH |
| Champion MDD `0.27146973146234554` | grant baseline and champion-5 both report exactly `0.27146973146234554` | MATCH |
| Report-13 continuity | `final_wealth=24.509661802900865`, MDD `0.27146973146234554`, 227 positive target sessions, owner `sz300308`, one active epoch | MATCH / PASS |
| remove-`sz300308` wealth `1.2632209078168604` | exactly `1.2632209078168604` | MATCH |
| remove-`sz300308` owner `sh601869` | one realized epoch owned by `sh601869`; `2025-08-04` target/order/grant/qualification, `2025-08-05` fill/active; `authorization_id=rearm_91b6b9cae96bbb92b4cccd5508a13798df96c1a500908dc646d1fc502b505a94`; closed `2026-05-28` for `owner_exit` | MATCH |
| remove-`sz300308` repair `60/60` | repair ready `2024-12-12`, level 3, `healthy_session_count=60`, `required_healthy_sessions=60`, repair ID `repair_f2d832f4f3a4837e425a06457f2e717cee1fb28f0a83d7e9ed0cb58009624d4d` | MATCH |
| remove-`sz300394` wealth `7.809998638641716` | exactly `7.809998638641716` | MATCH |
| remove-`sz300394` owner `sz300502` | one realized epoch owned by `sz300502`; `2025-06-30` target/order/grant/qualification, `2025-07-01` fill/active; no authorization/rearm provenance; closed `2026-06-25` for `owner_exit` | MATCH |

Additional critical facts: remove-`sz300308` MDD `0.23851539613956552`, 137 positive target sessions, longest healthy zero-target streak 7; remove-`sz300394` MDD `0.1985706825003003`, 225 positive target sessions, longest streak 0. Both are accounting reconciled and contain exactly one actual strategic epoch.

## Independent review and remote anchor

The independent Task 1 reviewer returned `READY` with no Critical, Important,
or Minor findings after checking the brief, report, workflow test lists, all
three artifact hashes and facts, the zero-commit review package, and the clean
working tree.

The controller then created the remote branch anchor through the authorized
GitHub connector after the shell HTTPS remote rejected unauthenticated push.
No Git object or remote ref was changed by that failed shell authentication
attempt. Read-only remote verification returned:

```text
d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5 refs/heads/codex/absolute-generalization-acceptance
d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5 refs/heads/main
```

After fetching that new ref and setting the local upstream, `HEAD`,
`origin/codex/absolute-generalization-acceptance`, and their merge base are all
exactly `d7fd3bf8f23ae9c66eb27f5046dedb9f7f980be5`; branch divergence is `+0 -0`.

## Final working-tree checks

`git check-ignore -v .superpowers/sdd/absolute-generalization-acceptance-plan/task-1-report.md` reports `.superpowers/sdd/.gitignore:1:*`, so this report is intentionally ignored. Final porcelain status remains clean and both diff checks passed.

No concern, divergence, failure, or skipped test remains.
