# Crisis cap cleanup and continuation decision — 2026-09-11

**复杂度降低，盈利增量未建立。** This branch removes duplicated crisis-cap rules and repairs current engineering checks. It does not include the failed financial ordering or expand production membership.

The complete continuation study is preserved on [the research branch](https://github.com/ychenracing/uquant/blob/3ce21689e2943671ac7b87d23ad01fa87218f167/docs/continuation-research-20260911.md). Its five-account quarter-growth candidate failed: target paired wealth geometric mean0.9352315, only one condition improved. It was not taken into the remaining55-account acceptance matrix.

Historical23-to24 coverage produced a positive same-input full-account pair (wealth0.9196 to2.0203). That is a single conditional research result. The added company is absent from the original frozen55-account input; replacing that input would violate the current scope. Daily historical exit eligibility and real-share corporate-action accounting also remain incomplete. The result does not justify a production pool change or reopen the rejected sorting mechanism.

## What changes

- `recovery_state` forwards its existing public/private crisis-cap interface to the already-used `protected_recovery` implementation. All severity branches, reserve distinction, fallback, configuration and call order remain. Production code is10 lines smaller and contains five fewer duplicate conditional branches.
- Two existing type-narrowing false positives are resolved with runtime-identity `typing.cast`; research readback imports the universe function from its declared owner.
- The current holding-evidence architecture guard now binds the entire already-merged main module, including tactical/recovery fill ownership. Its old two-function expectation was stale. Immutable historical artifacts and economic contracts are untouched.
- The crisis-cap guard binds the retained original body, exact argument forwarding, relative import and unique alias binding. It rejects changed import targets and alias reassignment; independent review verified both mutation cases.

## Verification

Production source: `1b70eb948dcb3ffece7a6c4f2b54a58b3abe02d4fee9e1d427e2fc27c04986f2`.

The whole-module AST proof covers every changed production module: unchanged retained risk implementation, exact forwarding and import resolution, and the two identity casts. All36 severity/reserve/configuration comparisons pass without configuration mutation. Previous results retain their original producer identities.

A fresh fixed-runtime **869-session full account** passes native raw/account/ledger readback and is exactly equal to the main-equivalent baseline across every reported metric, including the complete equity curve, order/submission ledgers, fees, slippage and turnover:

| Metric | Baseline and cleanup |
| --- | ---: |
| Wealth |29.717003593409824|
| Maximum drawdown |0.27147361646156465|
| Filled account orders |22|
| Fees |65,928.056280352|
| Slippage |101,142.05689999806|

Producer local789206f and remotee137365 have the same source tree. The final evidence uses Python3.12.13, numpy2.5.1, pandas3.0.5, uv0.11.33 and the original lock/data identities. One earlier replay was interrupted by disk exhaustion; a second completed with host uv0.12.8 after cache cleanup broke the pinned tool path. Both are retained and excluded from the fixed-runtime final claim. The actual pinned executable was restored before the accepted replay; no report was relabeled.

Focused risk/public-owner, ordinary-entry/rearm, immutable-body, mutation-rejection and complexity checks pass. Ruff and strict mypy cover the project;336 typed source files pass. Frozen data, static holdout lanes, empty manual Journal and sentinel contracts pass. A deterministic wheel was built from the committed source using build1.5.0 and pinned setuptools84.0.0. No dependency or frozen runtime lock was changed.

Original Absolute source-binding and Ownership economic failures remain **NOT_MET**. This engineering acceptance is not a full historical/generalization promotion or an all-green CI claim. Already-running/queued Actions are not cancelled, retried or awaited; new Actions observation is bounded to10 minutes.

## Future Holdout preparation

The staged [preparation manifest](../benchmarks/future_holdout_preparation_20260911.json) freezes the source/runtime and the exact reviewed2026-08-05 account: schema8, cash/equity59,434,007.18681965, no open holdings or pending orders. This historical anchor is **not** an assumed2026-09-11 closing account.

Activation remains null. Before entry, a reviewed continuous bridge must establish the actual prior-close account; any pre-freeze reconstruction stays retrospective and cannot be counted or used for tuning. Existing holdout contracts, old source identities and observed prefixes are unchanged. New observations remain **0**, all scores **null**, milestones20/40/60 sessions. No broker connection or unsupported background execution was scheduled.

## Evidence preservation

All14,080 research evidence files were verified by SHA-256 and saved in two archives. These include valid and rejected panels, incomplete attempts, raw native observations/accounts and original business/distribution sources. Only verified archived daily cache copies were released after saving; authoritative aggregate observations, accounts and reports remain available. See the [storage receipts](../benchmarks/continuation_evidence_storage_20260911.json).

The research branch, rejected candidate branch and this engineering branch have separate purposes. Only the verified neutral engineering change is proposed for main.
