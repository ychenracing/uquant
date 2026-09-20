# UQUANT recovery handoff — PAUSED BY USER, 2026-09-20

The latest instruction was: “停下所有工作。把任务、目标、状态、进度、数据全部保存到GitHub远端，并给我一份能从远端恢复工作的handoff prompt，我需要随时恢复工作。”

Do not implement, run acceptance, launch replays, or merge until the user explicitly resumes. The user then explicitly authorized storing large originals in persistent personal storage instead of GitHub, provided recovery survives container deletion or migration. Saving this checkpoint does not mean economic acceptance. PR remains unmerged. On an explicit resume request, continue the unfinished work below; do not restart completed evaluations merely because the session changed.

## Recovery entry point

Repository: `ychenracing/uquant`. Existing PR: https://github.com/ychenracing/uquant/pull/75 . Branch: `fix/cross-vintage-recovery`. This handoff and the adjacent `SNAPSHOT.json` and `restore_snapshot.py` are the GitHub recovery carrier. The 61 archive parts are saved in the user’s persistent personal ChatGPT Library; the manifest records exact stable Library IDs, filenames, lengths, offsets and SHA-256 hashes. They are not dependent on this conversation’s container. The user expressly authorized this storage split. Use the immutable commit containing this file (not an assumed latest branch) and verify the manifest before trusting results.

Clone this repository at the checkpoint commit. Read `SNAPSHOT.json` and materialize every part using its exact `library_file_id` into one local directory. Use the Library skill and `prepare_materialize` with at most20 items per call (known references, not guessed paths). Parts may also be downloaded from personal Library by their exact filenames `UQUANT_PAUSED_WORKSPACE_20260920.part001` through `part061`. Verify all61 parts; never treat partial downloads as complete. No signed temporary URL is needed as a durable reference. From the repository root run:

```sh
python artifacts/branch-integration/start-date-research/pause-20260920/restore_snapshot.py --parts /absolute/path/to/downloaded-parts --output /absolute/path/to/recovered-workspace
```

This reconstructs the archived workspace into a new directory, verifies every part, the full archive and every restored file. It does not run any strategy. Verification completed: all61 saved parts were downloaded again and the full34,248-file workspace was restored with byte length and SHA-256 checks. See adjacent `VERIFICATION.json`. The archive stores deduplicated byte-exact source snapshots of all task worktrees, frozen input data, evidence, pending patches, runner scripts, journals, original result files and historical preservation receipts. `MANIFEST.json` inside the archive lists all paths and exclusions. Git internals, installed environments/caches, redundant transport ZIP/readback copies and oversized redundant/interrupted R20 transport bundles are excluded. Their useful source/data are included separately. Archive SHA-256: `472528b1d4a9a26820dbd2f8964d3e717d38306ad9504dc9f37df5de162c54fc`; 445,563,802 bytes, 34,248 restored paths, 4,112 unique byte objects. The handoff and final manifest are adjacent remote files created after the archive snapshot, not recursively embedded in it. Source snapshots are not initialized Git worktrees; keep the cloned repository as the authoritative remote Git history, or reconstruct a worktree using the included bundles and recorded parent prerequisite. No original working copy was reset or deleted.

The original complete task prompt is restored at `inputs/UQUANT_CROSS_VINTAGE_RECOVERY_GENERALIZATION_PROMPT_20260920.md`. Read it, `inputs/READ_FIRST.md`, the fixed contract under `uquant-r24/artifacts/branch-integration/CROSS_VINTAGE_CONTRACT.json`, `pause-save/PR75_HISTORY.md`, and `pause-save/WORKTREES.json`. This handoff supersedes older PR instructions to keep working.

## Exact source identities

- Baseline main B: `3af6aba18f133f4e22612769c703dac080d940d9` (last verified, refresh on resume).
- Frozen economic producer: `47d830ace565d8f659b99103fff41cf765c65466`, full tree `49dedf21d05ba44d3dc0dc4c3e8a6130db463838`, production tree `b925a8e3d994a034ae5a9d6f667a3b89dd9a8fed`. Workspace `uquant-r24`, branch `research/cross-vintage-r24`.
- Delivery local commit: `f65f86df56a0139f73b684ec61db133bc2b92ffe`, full tree `c0ba175bf205797e7ae6b958aa361128dcbae244`, production tree `5a7eb89d36d94c491ccfd612629cd8f6b2f2a6d3`. Workspace `uquant-r24-delivery`.
- Remote delivery source before this preservation-only commit: `3b27fc422a5fb4bd5f21b5ce34af9a746919de87`. Its tree exactly matches the local delivery tree; file readback verified.
- The only production change between the economic producer and delivery is extraction of `_ordinary_deployment_closed(account, symbol)` in `uquant/portfolio/strategic/authority.py`. Inlining it produces an exactly equal complete module AST. Proof: `cross-vintage/r24-delivery-equivalence.json`. Do not casually rerun its generator and overwrite appended source/readback metadata.
- Bundles `cross-vintage/r24-source-since-main.bundle` and `r24-delivery-source-since-main.bundle` require the baseline main commit above. Use `git bundle verify` before importing.
- Pending delivery change: `docs/ACCEPTANCE.md` links a not-yet-created final delivery report. It remains uncommitted and is preserved in the source snapshot and `pause-save/uquant-r24-delivery.patch`; do not publish that broken link as final acceptance. All worktree statuses and pending binary patches are preserved.

## Objective and fixed acceptance

Repair cross-vintage recovery/generalization causally, with the same real account/owner/cash history and unchanged risk constraints. No date/ticker/age-specific bypass, reset of fills/equity/history, parallel cash book, live trading, new scheduler, forced push or protection changes. Do not lower the registered thresholds or broaden the evaluation suite by default.

B is the baseline above. Common date 2025-01-16; end 2026-08-05. Old starts 2024-01-02/09/16, new starts 2025-01-02/09/16. G = end equity / common-date equity; Gref is max across all three B new and all three C new runs. Each old native and actual B-prefix upgrade ratio must be >= 0.70. Each candidate new/B new >= 0.90. Full drawdown including original prefix <= 0.30. Suffix 2x cost retention >= 0.90 for legacy old0 and native new5.

Original 23 contract includes full 2023-01-03 plus offsets1..10, 2024 offsets0/5/10, 2025 offsets0/5/10, full2xcost, offset5cost2, d-continuous_ai_era, loo308, loo502, remove_all_three. Cluster >=70%, strong W/B0 >=80%, weak geometric >=1.20 and each>=.90, DD<=30%, cost>=90%. B0 original baseline/evidence identity and file map are in existing contract/proofs. Full34LOO, expanded no_optical and full Absolute are not authorized default expansions.

## Completed and evidenced

All paths below are inside the restored workspace.

| Gate | Status at stop | Evidence |
|---|---|---|
| R24 cross-vintage core | CORE_CROSS_VINTAGE_MET | cross-vintage/r24-core.json |
| R24 original23 | ECONOMIC_MATRIX_MET; all23 complete | cross-vintage/r24-original23.json; cross-vintage/native/r24/ |
| Initial two confirmation groups | CONFIRMATION_ECONOMICS_MET | cross-vintage/r24-confirmation.json |
| Initial affected-path coverage | AFFECTED_PATHS_COVERED | cross-vintage/r24-confirmation-coverage.json |
| Registered reserve | STARTED, INTERRUPTED; not accepted | partial attempts and stop receipt |
| Final acceptance report/archive | NOT GENERATED | guarded scripts retained |
| Merge | NOT DONE | existing PR75 |

Core Gref 13.830486382246367; native old minimum ratio .9766204982728293; legacy minimum .7785604887332337. New retention 1.2296849577010984 / 1.0807600067109642 / 1.0807600067109642. Old/new5 suffix cost retention .9980722889463757 / 1.0475853501026835.

Different-recovery confirmation native/legacy ratio .7428103657746651, Gref13.656024231379753. Remove308 native ratio1.0181628073026667, legacy1.0162871302435124, Gref2.1320451965927174. Both initial confirmation groups are DEVELOPMENT evidence after earlier failures, not untouched holdouts. Coverage linked real fills (14 and24), with228 and207 release events, respectively; outside-known-signals true.

Original23 strict evaluator passed before the stop. Example full W32.66263438259412, DD.27133916084354315; 2xcost W32.48377376240514. The original dispatcher operation `20260920T075643-4b8b3660a6b0` exited1 due to full-offset9/10 “Replay already running”. A separate exclusive worker completed both (`20260920T081603-c10b00de8b0b` PASS), and strict23 evaluation passed. Preserve the dispatcher failure as contention, not an economic failure or a falsely successful dispatch.

Engineering evidence: `cross-vintage/r24-engineering-verification.json`. Focused funding19, owner2, actual fill/cancel/scan/sentinel4 passed. Ruff and strict mypy330 passed. Earlier byte-identical covered checks are explicitly identified in the report. R24 original complexity failed24>20 branch points; the exact helper extraction fixed the existing gate without relaxing it. Delivery authority/complexity passed operation `20260920T081024-fa44e0ffb3c8`; delivery Ruff/mypy passed `20260920T081100-e73bd9cfa6fa`. Earlier broad timeout runs remain NOT PASS. Production line change4916→4987, validation1064→1081, account84→85; no new user knobs or parallel books.

## Mechanism and history

R22 carries a derived continuous-deployment peak: held positions only raise it; actual flat resets to actual equity; held legacy state seeds conservatively. Current risk/repair use deployment drawdown while historical capital peak remains. Higher tiers still require history plus independent damage. Existing healthy5day recovery releases at most one tier/day. Fresh10day breakout required for initial/owned remainders; unowned additions to existing cohort use the existing5day window with current MA/depth/liquidity. Fresh candidates retain original priority, older valid supplements follow, existing owned members retained. Existing50% crisis cap remains under the existing20% peak condition.

R23 clears stale ordinary protected weights only after proven ordinary deployment closure: flat cash, no positions/pending/unsettled/late fills/grants/epochs, no active-episode protection, and complete attributed net-zero buy/sell history. The real different-recovery legacy state retained closed May2023 weights after August closure and blocked January2024 entries. Actual account counterfactual demonstrates causal order/fills; no synthetic reset.

R24 computes positive nonrepair ordinary admission budget as min(configured entry gross, actual book gross cap) BEFORE peer sharing. The old .8 nominal budget shared then clipped at .5 starved the third peer. Actual May8 state preserves .5 cap and produces three1/6 orders and real May12 fills. Proofs: r22/r23/r24-mechanism-proof.json and r23-jan8/r24-may8-counterfactual originals.

Historical R22 initial-confirm failures and R23 remove308 failure are retained. C6/R19 candidate confirmation outcomes were not inspected for tuning. See full PR history and original evidence; do not relabel failures or reuse old source outcomes as current.

## Interrupted reserve and stop evidence

Session52225 was interrupted with Ctrl-C and exited130. `cross-vintage/USER_STOP_RECEIPT.json` records free exclusive supervisor/native locks. Original supervisor journal still says RUNNING because interruption prevented terminal recording; retained verbatim. No replay worker remained in process verification.

Reserve remains the REGISTERED old2024-01-23/new2025-01-23/common2025-01-23/fullpool cases. B actual prefixes and continuations completed and are preserved in `confirmation-prefixes/` and `reserve-baseline/`. Candidate reserve old/new native and legacy had started, but no completed canonical candidate reserve results or outcomes were inspected.

Partial native attempts:
- old: `cross-vintage/confirmation-native/r24/attempts/reserved-dates-old/ebc4a828bd274608a37431b6f6ff4ccb/`
- new: `cross-vintage/confirmation-native/r24/attempts/reserved-dates-new/6b2217aa08984c53b244e543899363a7/`

Identity and replay logs are preserved, not results. Legacy partial logs are under `cross-vintage/confirmation/r24/`. On resume, first inspect actual files/locks/identities, preserve interrupted logs under immutable attempt names, and continue only missing registered cases. `run_r24_reserve.py` uses exclusive file creation; blindly restarting `finish_r24_economics.py`/reserve dispatcher can collide with existing logs. Do not delete originals or register replacement dates because of interruption.

At stop, GitHub Actions on source3b27fc4 had Engineering gates35498927748, Ownership35498927802, Absolute35498927864 queued; Grant35498927758 succeeded. The available connector exposes no cancellation operation, so cancellation is NOT claimed. Refresh live state on resume. Preservation commit uses [skip ci]; it does not change workflows/protection and may not suppress every event type.

## Ordered continuation ONLY after explicit resume

1. Read original prompt, contract, this handoff, full history, source identities and source-equivalence proof. Verify snapshot hashes and fresh remote PR/main/checks/reviews before changing anything. Preserve source identities for all reused evidence.
2. Restore runtime with Python3.12.13, recorded dependencies/lockfile (original numpy2.5.1,pandas3.0.5,uv0.11.33). Reconstruct Git worktrees/bundles as needed; scripts assumed workspace sibling paths and `uquant/.venv/bin/python`. Do not treat restored snapshots as ready initialized Git repositories.
3. Resume ONLY the three incomplete registered candidate reserve runs with preserved interrupted attempts and a collision-safe new attempt identity. Keep the frozen economic source47d/b925; delivery has exact AST equivalence. Inspect driver commands before launch. Do not rerun already verified core/23/initial-confirm solely because time passed.
4. Evaluate with `evaluate_r24_reserve.py`; then `build_r24_confirmation_coverage.py reserve` if allowed gates hold. Failure is failure: diagnose causally and preserve holdout lineage; no gate lowering or silent holdout replacement.
5. If all six economic/coverage statuses pass, `build_r24_delivery.py` and guarded `archive_r24_delivery.py` build final report/evidence. Current engineering/equivalence JSON contain appended metadata that older generators would overwrite: merge carefully, not blindly regenerate. B original map spans checkpoints4 AND5: `r22-current-B-original-map.json` is authoritative.
6. Complete current-source required engineering/CI and applicable review/integration requirements, address pending ACCEPTANCE link only when report exists, verify exact remote tree and evidence. Long Actions do not block independent authorized work, but required merge gates still matter. No merge until actual acceptance and authorization permit it; this checkpoint itself authorizes no resumed work.

Long jobs use `python -m tools.cloud_guard run --name ... --timeout ... -- ...`. External writes use begin/finish verified receipts. Original operation journals contain the exact commands, source identities and returncode. Current environment can disappear; read the committed handoff/manifest and the persistent personal-space archive parts. GitHub holds the source and small recovery metadata; the manifest resolves every large original without requiring old conversation context.
