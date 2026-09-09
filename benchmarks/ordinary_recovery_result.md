# Ordinary recovery: paired evidence

Historical screening, not a future-return claim or full production acceptance.
The preregistered mechanism sequence and unchanged gates are in
`ordinary_recovery_continuation.md`. Config, frozen inputs, execution and fees
are unchanged. Initial cash is 2,000,000; wealth includes initial capital.
Continuous interval: 2023-01-03 through 2026-08-05, 869 sessions. The no-optical
sentinel ends 2023-06-30, 118 sessions. Exclusions apply to the native role sets.

| Producer | Full wealth / DD / orders | Remove three | Also remove sz300666 | No optical H1 |
|---|---|---|---|---|
| Main baseline | 30.74331173 / .271470 / 20 | 2.39761599 / .202292 / 14 | .91093260 / .168164 / 18 | 1.44769470 / .244243 / 6 |
| A: absolute exit | 30.74331173 / .271470 / 20 | 2.39761599 / .202292 / 14 | .98732640 / .205508 / 24 | 1.44769470 / .244243 / 6 |
| B: mature-only ordinary admission | 30.74331173 / .271470 / 20 | 2.38489707 / .196173 / 12 | 2.89545684 / .153326 / 29 | 1.46926229 / .232086 / 4 |
| C: retire fresh long-pullback, native trend admission | 30.74331173 / .271470 / 20 | 2.39761599 / .202292 / 14 | 2.44859500 / .158389 / 32 | 1.44769470 / .244243 / 6 |

A fails strict-removal wealth and orders. B fails remove-three wealth retention
and strict-removal orders. Neither is accepted; these rows must not be replaced
by later successful runs. B's .5% wealth regression still fails the fixed floor.
Its large strict-removal gain is not an independent out-of-sample result.
C preserves all three control results and improves strict-removal wealth and
drawdown, but 32 orders exceed 22. C is also rejected by the fixed screen.
More profitable recovery can create more real subsequent trades: removing an
entry route does not mechanically reduce whole-account orders.

Each completed baseline/A/B/C case passed native raw/account readback, including
seals, source/config/runner, chronological role sets, next-session execution,
complete session schedule and recomputed accounting/performance. A/B expected
economic source identities were derived from their Git commits, not copied from
the artifacts being checked. Local and remote producer trees are byte-equal.

| Producer | Local commit | Economic source SHA256 | Raw directory |
|---|---|---|---|
| Baseline | 960539a89408cc7c1fc3937bda19c9f760095012 | 86d3541617b4f3185c94bf0f5ad2bbeedfaddecabdcf1fabd593196223459fdd | recovery-baseline-960539a |
| A | 3840444 | 8f0a6b302d37baf256cb242208de46bea3c792cd3656be34cd5533fc59089c12 | structural-exit-3840444 |
| B | 815092c26d20f9bb791d31048b03a7eb567a7a43 | 7c1218aae6ee89d8f5bdd5ace216f3a6c14ec4b6a9c39198101388a79ea4ff3f | mature-ordinary-b |
| C | bc10398d815d8e7c52b0d8c727e3139fae862235 | b928db51ea6a7bbdfbe79588408717241200d44b3d98b2c39d7668b28e90259d | retire-pullback-c |

## Mechanism evidence and limitations

Production already has real account-owned cash repair. The earlier standalone
comparator's persistent flat book does not prove production repair is missing.
A removes a relative-maturity veto that delayed an existing absolute-damage
exit; it preserves consecutive confirmation, minimum holding time, winner
protection and active strategic-owner boundaries.

B changes the reinvestment path, not merely one rejected trade. For example,
removing losing sh688200 changes account equity and later concentration trims
of profitable sh688256. Summing isolated trade PnL would miss that effect.

C, preregistered before its replay, returns to the original early-slot trend
qualification and retires only the separate fresh long-pullback allocation.
Legacy orders, proofs, real inventory and exit protection remain supported.
No candidate has passed all screens, and no full L4 acceptance is claimed.
Production main is not updated with these failed candidates. Removing the
22-order gate or silently accepting B's control regression is not authorized
by a general request to improve the strategy. Further adaptations on these
same observed intervals are research, not fresh evidence of generalization.

## Recoverable evidence and engineering scope

`ordinary_recovery_20260909_evidence.tar.gz`, 262,161,580 bytes, SHA256
`07ec18faecae109242ad69a4b7d4b5f798609e489bffebedbf5854812658c9d4`,
contains all 16 case identities, sealed results, final accounts and complete
`observations.jsonl.gz` streams. Duplicate per-day envelopes are omitted from
the archive; original local per-day observations are not deleted. Each result
still verifies the exact raw/account file digests after extraction.

C affected checks passed: 30 admission/execution/holding/exit tests, 274 ordinary
allocation/recovery/report/lifecycle/risk tests, 143 proof/risk/reentry tests,
and 10 focused architecture/projection tests after the reviewed docstring
projection correction. Ruff and mypy (317 source files) passed. Independent
review found no code blocker; review is not economic acceptance. An earlier
broad A architecture run was not a completed final verification and is not
counted here. Full L4 was not repeated for failed screening candidates.
