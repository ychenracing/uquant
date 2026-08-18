# 2026-08-18 Future Holdout operations review

## Frozen boundary

The original contract remains unchanged: `last_in_sample=2026-08-05`,
`first_holdout=2026-08-06`, review milestones `20/40/60`, next-open execution, and
`parameter_changes_from_observation=false`. Historical replay, deterministic Future
Holdout replay, and the manual execution Journal are separate evidence classes.

## Lane registry

The append-only registry currently contains only `champion_pre_sentinel`, activated on
2026-08-06 and bound to the reviewed strategy anchor commit, production/Sentinel source,
effective configuration, data contract and directory, Python/NumPy/pandas/uv runtime,
and `uv.lock`. No Sentinel behavior or concentration policy is introduced. An observed
Lane cannot be deleted or have its activation, commit, source, configuration, runtime,
parent, or economic behavior rewritten. A new Lane must start after the last imported
session and cannot receive earlier results.

## Current observation state

No real files exist in `data/holdout/phase2-future-v1` in this checkout. Therefore the
authoritative observed-session count is 0, the next milestone is 20, status is
`NON_REVIEWABLE`, and all seven formal economic scores remain `null`. Calendar dates
that have elapsed are not observations and are not substituted for missing market data.

## Manual execution integrity

Journal schema v2 lives in the validation/observation boundary and is append-only and
hash chained. Every row carries the decision and
planned-order identity plus observed execution fields, broker order ID, timestamps, and
the current/previous record hashes. Legacy v1 rows remain readable. Manual fills and
skips are observational only: replay reads the Journal solely to bind an external tail
checkpoint, and tests prove that appending a fill leaves model decisions and Decision
Digests byte-for-byte unchanged.

## Operator gates

CI recomputes the lane report from the sealed contract, registry, and isolated holdout
data. The release remains blocked if the observation prefix changes, a Lane backfills,
formal scores appear before 20 sessions, data history is overwritten, the Journal chain
is altered, or any production economic file or decision output diverges.

The observation-only Python overlay is independently sealed by
`benchmarks/future_holdout_observation_overlay.json`. Phase 2 source validation permits
only those exact four path hashes while continuing to require every non-observation
production byte to match the reviewed post-Task-8 source contract.
