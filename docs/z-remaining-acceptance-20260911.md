# Z remaining acceptance — 2026-09-11

Z does not meet the complete financial acceptance contract. The user's cost exception has been applied: doubled-cost no-optical trading costs were CNY 46,002.71695088 against the original CNY 40,000 ceiling; the CNY 6,002.71695088 overrun is ACCEPTED_EXCEPTION. Other applicable trading-cost overruns strictly below CNY 10,000 are also authorized. Original costs, ceilings and failures remain recorded.

The selected Z production fingerprint remains 18912bdf6b98826b86fc49c4ad4ff5c7cc29382fa11b71b0fbb7cbb2d589fd3f. The running producer was never changed during these replays. Main remains unchanged and the PR is Draft.

| Evidence | Final result |
| --- | --- |
| Native nominal accounts | 14/14 PASS, including required disjoint pre-2025 improvement |
| Frozen robustness tasks | All 64 ended: 53 COMPLETE, 6 CONTROL_DELETED, 5 old-source ERROR |
| New-source robustness judgments | 37 PASS, 1 ACCEPTED_EXCEPTION, 1 financial FAIL, 5 UNAVAILABLE_OLD_PAIR |
| Actual-best removal, no optical (exclude sh688498) | New wealth 1.408092; old 1.010395; PASS |
| Actual-best removal, remove all three (exclude sh688256) | New wealth 1.385696; old 1.011638; PASS |
| Full fixed Performance | All 45 native units complete; 13 PASS, 32 FAIL |
| Operator account-code migration rehearsal | PASS on a copied historical native account; financial digest preserved |

The confirmed initial-condition failure is remove_all_three with start-session offset20. Z wealth is 0.8718030613 against paired old wealth1.8445673338; the existing effective floor is1.5771050704. This is a substantive wealth shortfall, not a small trading-cost overrun.

Full Performance failures include independent-window wealth, acute-return and drawdown regressions. For example, a/h1_2024 wealth0.9153624963 is below1.512. The complete artifact was revalidated: its findings reproduce the economic failures, with no additional identity or schema finding. Complete L4 is NOT_MET; dependent Ownership/Absolute stages were not rerun after the failed Performance prerequisite and are not declared passed.

Five old-source initial-condition runs are unavailable: four failed canonical pending-order event identity/finalization, one failed final account schema validation. Their raw errors are preserved, and they are not treated as paired passes. The descriptive population of all44 actual new-source accounts is complete; it does not erase the frozen evaluator's missing-pair/aggregate failures.

A separate AC branch fixes a real retired-cohort damage-guard residue after successful native epoch settlement.21 targeted tests and focused review passed, but its a/h1_2024 wealth0.8925906197 still fails; its offset20 economics exactly equal Z. AC remains separately preserved and has not replaced Z. See branch codex/settled-guard-repair-20260911, remote ae9ef72b6cd6da6c87f54d02620509b93d1d582d.

All scheduled replays and raw-evidence preservation are finished. Archive hashes, source mappings, original judgments and upload receipts are in benchmarks/z_remaining_acceptance_result.json and the preceding nominal result. Engineering/style/coverage/full CI remains secondary; no Actions run above10 minutes was awaited. No merge, live operation or post-2026-08-05 holdout was performed. The remaining obstacle is financial generalization, not the waived CNY6003 cost excess.
