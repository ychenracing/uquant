# Main-based local enhancement from PR #67

## Scope and authority

The user explicitly requested current main as the base, reuse of PR #67, and a local capability improvement with permissible economic compromises. This release does not claim broad or future out-of-sample generalization. The preregistered six-cell screen in PLAN.json remains unchanged; historical universal contracts and failed results remain historical evidence, not relabelled passes.

Main baseline: `ea695ac87fd2155aef55cd2c6911284f4f3ffaff`.
Reviewed producer: `0cca3daffe669f8b967831e58fe547ca734bed34`.
Reviewed production tree: `ef0b8abba9f0b694bfd32901cad272893de973e0`.
Original PR reference: `8de51ef14a2bfbc76853c08d5b8963f68893e1ea`.

## Adopted PR capabilities

- Synchronized ordinary restoration requires confirmed, complete current evidence for every actual holding instead of permission from a reason string. An independent review additionally found and fixed a protected-subset aggregation gap; each held price must rise from its valid prior close. Existing risk, Sentinel, cash and concentration constraints remain.
- Settled stale ordinary protection records no longer claim fresh recovery capital. History is preserved; actual ownership, holdings, pending orders and unsettled obligations retain their checks.
- Repeated scans on one session cannot advance sector recovery confirmations. Same-session deterioration still resets the count.
- Leader confidence is visible in decision diagnostics.
- Existing PR fixes for three stale engineering fixtures are retained: Chinese report headings, correct policy fingerprint ownership, and exact entry-check evidence. No assertions of capital or causal execution are removed.

No production parameter or threshold changed. Relative to current main there are four changed production files. Holding exits, mature-cycle sizing, tactical expansion, formation restrictions and alternative recovery weighting remain available in PR history and preserved research checkpoints; they are not silently promoted.

## Economic evidence

RESULT.json verifies the paired inputs, committed production hashes, data hashes, runtime, configuration, dates, universe, equity/wealth/drawdown reconciliation and a single candidate producer. All six reviewed cells equal current main in wealth, drawdown and order count. Nominal geometric wealth retention is 1.0; both stress retentions are 1.0; no drawdown increases.

The holding-plus-recovery selection failed offset-start retention. Its raw evidence and SELECTED_RESULT.json are preserved. Removing the holding group yielded the recovery-only combination; correcting the review issue did not change the six historical results. This is bounded selection evidence, not independent out-of-sample validation.

All 24 native outputs (baseline, rejected combination, pre-review recovery and reviewed recovery) are included under raw/, with byte counts and SHA256/Git blob identities in RAW_MANIFEST.json. They retain original producer identities. Changes after the reviewed producer are tests, documentation, archival evidence and a pre-existing lint exclusion for two frozen research scripts; production, lockfile and runtime dependencies are unchanged.

## Verification and remaining limits

The focused final recovery/custody/import/complexity suite passed 89 tests. Ruff, strict mypy over 328 source files, and 36-file frozen-data integrity passed. The independent review cleared its one Important finding after the new declining-peer regression passed. Full architecture verification and exact integration checks are recorded separately when complete.

Current main already has red full CI: four stale engineering assertions, strategic ownership diagnostics and broad generalization checks. The three fixture fixes above address the four engineering assertions. This limited release does not assert that all historical economic requirements pass. Platform protections remain authoritative and are not bypassed.

## Reproduction

Use the pinned Python 3.12.13, NumPy 2.5.1, pandas 3.0.5 and uv 0.11.33 environment. replay.py is the byte-identical original runner. FROZEN_SCENARIOS.json preserves the exact scenario contract supplied to all runs. Its older economic thresholds do not replace PLAN.json for this limited release.

Run compare.py with --runs pointing at raw, --repo pointing at a checkout whose Git object database contains the producer commits, --candidate recovery-reviewed, and --output pointing at a new report path. The reviewed.patch and reviewed-commits.json preserve the producer tree and raw commit objects if local producer history must be reconstructed. Never rewrite a raw source_head to the merge commit.
