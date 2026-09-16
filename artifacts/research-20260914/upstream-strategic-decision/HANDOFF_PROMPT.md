# HANDOFF_PROMPT — Continue uquant PR #67 from preserved H8 and rejected H9

Continue the original task in `ychenracing/uquant`, original Draft PR #67,
branch `codex/targeted-generalization-research`. This is continuation, not a
new project. Do not create a replacement PR, repeat completed recovery, or ask
again for authorization already recorded here.

## Goal and immutable scope

Deliver one default production candidate that satisfies every applicable
frozen economic, generalization, engineering, and repository requirement.
Normally merge PR #67 into `main` only after the same candidate passes all
applicable gates, then verify merged `main`.

The user retains authorization for ordinary deployment, selection/sizing,
ordinary holding/exit and eligible re-entry, and allocation within existing
recovery-cohort responsibilities. The user additionally authorizes targeted
redesign of prospective strategic economic choices only where contracts leave
them discretionary: whether/when to initiate an eligible strategic commitment,
selection and initial exposure within fixed grant/capital constraints, and
holding/planned exit decisions where the lifecycle permits them.

Do not change frozen strategic or obligation rules, grant eligibility,
mandatory owner rights/restoration duties, risk/freeze/recovery conditions,
pending-order obligations, settlement rules, or acceptance thresholds. Do not
erase duties after a grant/order/fill. Do not use symbol/date/window special
cases, hindsight winner selection, threshold sweeps, a shadow account, a new
predictive model, or a new tunable-parameter family. Preserve cash-long-only,
daily post-close decisions, next-session fills, manual review, and no automatic
orders. Preserve every failed hypothesis as evidence.

## Live repository state at handoff

- Repository: `ychenracing/uquant`
- PR: `#67`, Draft/open
- Branch: `codex/targeted-generalization-research`
- `main`: `df45b4d7d9ea290ae953115afbb73140270537c2` at last verification
- Exact recovered H8 producer: `613cce5c8e724b2988e731640029370a774cbe72`
- Exact recovered H8 tree: `d8e8a159be0b3ef91db632fd8e9380a11d4ff371`
- Remote H8 preservation commit: `cbaead21d8610fcd5b025ed5f0776ce60fd760ab`
  with the exact same tree `d8e8a159be0b3ef91db632fd8e9380a11d4ff371`;
  it is a different commit SHA because it has the remote PR parent.
- First upstream decision commit: `db5635f895a5a534c62d6edd8f07b924e6d5fb01`,
  tree `6ae1a7f32e04e2f35edce17c10abb988f4e01730`
- Rejected H9 code commit: `2be82f589d389013518d341080a8f9d09b335a7b`,
  tree `c3d58002f95f5fcbed1b884c0cf36f0fa19d8dc4`
- Rejected H9 evidence commit: `1d25f621774635af33d32f681ac622f70a680f6a`,
  tree `51ee32c0fa0aec6dd2ffe31aa5087b8451105ea1`
- This handoff is committed after that evidence commit; verify the live branch
  SHA and this file before continuing.
- Local producer SHAs can differ from the remote API-created commits while the
  trees match. Do not call those different commit SHAs identical.
- The originating scratch checkout was reclaimed while this handoff was being
  saved, after the H9 evidence reached GitHub. There is no recoverable local Git
  status to inherit; begin from the verified remote branch. No replay processes
  were left running. No merge was performed.

The original H8 workspace was kept read-only at
`/workspace/scratch/1d33cdae5a4f/uquant` in the originating container. Do not
depend on that transient path in a new session; the complete exact H8 tree and
evidence are now in Git history at `cbaead21`.

## Completed recovery and evidence audit

The exact H8 recovery reconciled the two inventories: 43 exact changed files
in the H8 delta, and 34 sealed evidence files containing 31 metric traces.
Remote readback verified the full tree byte-for-byte and `git fsck` passed.

H8 remains the strongest protected comparison, not an accepted final candidate:

| Cell | H8 result | Status |
|---|---:|---|
| A bull | 11.8706585867x, DD 16.0048175923%, 12 orders | PASS |
| D bull | 8.8774550752x, DD 24.2806120730%, 18 orders | PASS |
| E bull | 13.8210227236x, DD 17.2530367369%, 13 orders | PASS |
| E 2024-H2 | 1.3955925424x, DD 8.84926952%, 10 orders | FAIL; floor 1.6930928947x |
| E acute Aug 1–Sep 2 | -2.8553764161% | FAIL; floor +4.61081079% |
| Native remove308 | 1.1190839397x, DD 22.8889194976%, 16 orders, 2 epochs/owners | PASS |
| Native remove502 | 2.3851418754x, DD 23.8124234993%, 20 orders, 2 epochs/owners | PASS |

The compact bound audit is
`artifacts/research-20260914/upstream-strategic-decision/ACUTE_BOUND_AUDIT.md`.
Its verified conclusion is important: the reported optimistic no-ordinary-loss
bound of -1.2155759139% is valid only for H8's already committed account path.
It does not cover the earlier lawful prospective decision that created the
2024-07-22 `sh688268` strategic grant/epoch and July 23/24 fills. The later
August sell duties were mandatory after fill and must not be changed. Therefore
the old bound disproves nearby post-commit ordinary sizing variants, not all
authorized upstream strategic policies.

## Implemented H9 and why it is rejected

H9 kept a nondecisive `reversal_industry` `FULL_COHORT` certificate eligible
but deferred its prospective grant when both `MARKET_CONFIRMATION` and
`OWNER_ABSOLUTE_QUALITY` were `FAILED`. It also prevented the exact same-day,
same-symbol, same-signature, same-evidence-hash certificate from bypassing that
decision through ordinary CORE admission. Existing grants, holdings, orders,
fills, rights, duties, risk rules, eligibility, and thresholds were untouched.

Focused TDD evidence:

- The corrected test first failed because a positive ordinary target still
  appeared after strategic deferral, then passed after cross-entry arbitration.
- 34 related tests passed, including protection that an independent current
  `FULL_COHORT` certificate may still fund an ordinary core beside a healthy
  existing strategic owner.
- Targeted Ruff passed.
- Mypy passed for `uquant/portfolio/pipeline.py` and
  `uquant/portfolio/strategic/discovery.py`.

H9 results on the same remote production source `2be82f5`:

| Cell | H9 result | Status |
|---|---:|---|
| E acute | +4.6108107936% | PASS |
| E 2024-H2 | 1.8168767799x, DD 9.8540242179%, 7 orders | PASS |
| A bull | 11.8706585867x | unchanged PASS |
| D bull | 8.8774550752x | unchanged PASS |
| E bull | 13.8210227236x | unchanged PASS |
| Native remove308 | 0.8949076697x, 13 orders, 3 epochs/owners | FAIL |
| Native remove502 | 2.4868745924x, 16 orders, 1 epoch/owner | FAIL lifecycle |

H9 is not eligible for merge. Its broad rule deferred the protected lawful
2023-01-19 initial `sz300223` reversal cohort in both removals. H8 had a
four-session confirmation streak at that decision. The losing E certificate
first became ready on 2024-07-22 at the existing three-session minimum and
disappeared on 2024-07-23. This is the key causal contrast discovered before
the pause. Do not repeat unconditional weak-certificate deferral.

All H9 raw traces and their SHA-256 manifest are under
`artifacts/research-20260914/upstream-strategic-decision/`. Read
`RESEARCH_LEDGER.md` and verify `SHA256SUMS.json`; do not rerun H9 merely to
recover already preserved evidence and do not relabel it canonical.

## Next execution order

1. Fetch the live PR branch and verify its SHA, tree, this handoff, PR Draft
   state, `main`, local status, and current checks. Preserve unknown remote work;
   do not reset, clean, rebase, force-push, or discard the H9 commits.
2. Read root `AGENTS.md`, `PROJECT_STATE.md`, this handoff, the acute audit, and
   the upstream research ledger. Do not restart the completed H8 recovery or
   bound audit.
3. Start from the causal contrast, not parameter tuning. The next smallest
   falsifiable hypothesis is prospective deployment timing: a weak but eligible
   nondecisive FULL_COHORT may be observed at its existing first READY session,
   while commitment is deferred until the same certificate persists into a
   later distinct session. This leaves qualification and its existing minimum
   unchanged; it changes only discretionary grant timing. This is proposed,
   not proven.
4. Use TDD. Add a behavior test that distinguishes first READY observation from
   a later distinct-session persistence of the same certificate. Protect
   decisive reversal, independently qualified ordinary entry, active/partial
   grants, pending orders, fills, and existing owner rights. Keep the exact
   cross-entry arbitration only while that same certificate is currently
   deferred.
5. Implement the minimum causal change. Prefer the already recorded
   `qualification_streak` and existing
   `strategic_cohort_confirm_days`; do not add a configurable threshold or
   symbol/date exception. Confirm from code/contracts that this is deployment
   timing, not a silent eligibility-threshold change.
6. Run the focused tests, Ruff, mypy, and `git diff --check`. Commit and remotely
   preserve the exact candidate before economic replay so runner identities are
   immutable.
7. Screen E acute from native 2024-07-01 initialization, then full E 2024-H2.
   Next run native point-in-time remove308 and remove502 immediately; both must
   preserve applicable numeric and lifecycle requirements. If the one-session
   timing hypothesis fails either E or the removals, preserve its raw failure
   and move to a distinct evidence-supported allocation/selection mechanism;
   do not tune a new threshold.
8. Only after those pass, replay A/D/E bull, then no_optical,
   remove_all_three, and relevant protections. For a stable final candidate,
   complete all applicable native economic and engineering acceptance,
   including full/champion long account (at least 15x wealth and at most 40
   orders), fixed 34 LOO/generalization, ownership/grant/recovery, costs,
   start-date, tail, and continuation obligations.
9. Keep PR #67 Draft/open until the same production candidate passes every
   applicable requirement. Do not splice best cells across candidates. Then
   merge normally and verify merged `main`; otherwise preserve status and do
   not merge.

GitHub Actions for `2be82f5` had 24 checks when last inspected: architecture
foundation, Windows smoke, security/dependency audit, ownership model tests,
and Absolute input preflight had succeeded; other tests/ownership/grant shards
were running or queued. These checks belong to rejected H9 and do not establish
acceptance for a successor. Do not wait on an unchanged workflow beyond 10
minutes; continue independent work and inspect later. Any current check state
after the handoff commit is unverified until fetched live.
