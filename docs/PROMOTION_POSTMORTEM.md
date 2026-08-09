# Promotion Holdback Postmortem

## Status

The preregistered single-use promotion holdback for 2026-07-21 through
2026-08-05 was consumed exactly once by candidate `336355b49fc`. It failed the
K2 loss/drawdown gate. The failure is preserved in
`benchmarks/promotion_holdback_result.json` and the lock remains
`CONSUMED_FAIL`; neither artifact is resealed or overwritten.

The corrected candidate (`f8fdd2ab989f`) passes all 73 non-holdback acceptance
checks. Replaying the consumed dates against the correction is diagnostic only:
it does not convert the historical K2 result into a pass. A new candidate freeze
and a genuinely future, preregistered single-use window are required for final
promotion.

## Observed K2 failure

The holdback contained 12 sessions, 36 files, and 432 rows. Coverage,
canonical hashes, candidate hashes, determinism, and positive finite wealth all
passed before evaluation.

| Pool | Final wealth | Period loss | Max drawdown | Orders | Gate |
|---|---:|---:|---:|---:|---|
| a | 0.9271x | 7.29% | 12.48% | 2 | PASS |
| b | 0.8357x | 16.43% | 24.74% | 8 | FAIL DD |
| c | 0.8416x | 15.84% | 24.16% | 8 | FAIL DD |
| d | 0.8300x | 17.00% | 24.62% | 8 | FAIL loss + DD |
| e | 0.9176x | 8.24% | 21.60% | 8 | FAIL DD |

The preregistered limits were strictly less than 17% for both per-pool period
loss and maximum drawdown.

## Root cause

On 2026-07-22 the independent risk radar entered `CAUTION` with four risk
votes, but `CAUTION` still allowed 100% gross. A 60% controlled rebound probe
was therefore permitted. On 2026-07-24, while the radar still reported
`CAUTION`, the allocator exited that probe and initialized the full strategic
cohort. `RISK_OFF` was not confirmed until 2026-07-29 and `CRISIS` until
2026-07-30; next-open exits on 2026-07-31 arrived after the portfolio had
already crossed 24% drawdown in pools b-d.

The failure was a control-composition defect: cohort entry and the risk radar
were individually causal, but a fresh secular cohort could deploy while several
independent risk axes disagreed with the entry.

## Correction

Two causal protections were added without changing the single-account or
single-target architecture:

1. A new strategic cohort cannot initialize in `RISK_OFF`/`CRISIS`, or in
   `CAUTION` with two or more independent risk votes. A benign one-vote
   `CAUTION` transition remains eligible, preserving the validated 2023 secular
   entry.
2. A confirmed four-vote `CAUTION` caps gross at 60%, while preserving already
   held strategic gross when the separate strategic lifecycle requires it.

The entry guard applies only when the requested universe contains the complete
fixed cohort. Incomplete stress universes retain their original initialization
state transitions.

## Diagnostic replay after correction

The consumed dates were replayed only to test the causal hypothesis. These
numbers are not promotion evidence and are not written into the K2 result.

| Pool | Final wealth | Period loss | Max drawdown | Orders | Old K2 limits |
|---|---:|---:|---:|---:|---|
| a | 0.9024x | 9.76% | 10.06% | 2 | within limits |
| b | 0.8703x | 12.97% | 12.97% | 2 | within limits |
| c | 0.9632x | 3.68% | 10.73% | 3 | within limits |
| d | 0.9052x | 9.48% | 10.79% | 2 | within limits |
| e | 1.0461x | 0.00% | 0.68% | 2 | within limits |

The full 2018-2026 continuous pool-a replay remains unchanged at 38.1378x
wealth, 28.67% maximum drawdown, and 95 orders. The corrected candidate has now
passed all 73 non-holdback checks. Its 963-scenario stress artifact and
180-experiment plus 54-cell robustness artifact are current and signed against
production `f8fdd2ab989f`, validation `e35831e29770`, configuration
`5821f769d963`, and the data bounded at 2026-07-20. Long replays also compare
their start and end signatures and reject mixed-version evidence.

## Promotion rule

Do not reuse 2026-07-21 through 2026-08-05, do not relabel its result, and do
not claim Production from the diagnostic replay. Freeze the corrected candidate,
preregister a future window before its data is observed, consume it once, and
promote only if all 74 gates pass on the same signed candidate.

That freeze and preregistration are now recorded in
`benchmarks/PROMOTION_HOLDBACK_NEXT.json`. The new window is 2026-08-06 through
2026-08-21 with 12 expected sessions and the unchanged strict per-pool 17%
loss/drawdown limits. Local frozen data currently ends on 2026-08-05, so the
lock is `PENDING_FUTURE_DATA`; it has no canonical data hash and has not been
evaluated.
