# Phase 4 Sentinel freeze-only evidence

Phase 4 implemented the complete freeze-only path, but the economic candidate was rejected
and the production default was returned to `SHADOW`. No baseline was weakened and `main`
must not be advanced from this evidence set.

## Candidate result

The first observed economic divergence is `a/h1_2024` on `2024-02-06`: Shadow submitted
the existing strategy's `sz300502` restoration buy, while Freeze-only retained the live book
and submitted no order. Sentinel never created a SELL and the formal base and integrated
`target_gross_cap` remained exactly `1.0`.

The candidate retained only `97.860322%` of Shadow final wealth (`1.8635082599` versus
`1.9042531401`), below the mandatory 99% floor. MDD and Acute were unchanged, orders were
unchanged at 8, and gross turnover decreased by `0.00506709685`. The exact rejection record
is in `candidate_rejection.json`.

## Shadow fallback validation

- Phase 1: 30 official a-e/six-window cells and 15 protected cells, `passed=true`, no failures.
- Generalization: 234/234 cells, including 6 no-optical, 6 remove-all-core, 18 remove-one,
  72 subindustry and 120 fixed-random cells, `passed=true`, no replay errors.
- Fixed random pools: nearest-rank p10 wealth `0.9982387916`; p90 drawdown `0.2117666082`.
- Phase 1 artifact SHA-256: `5d958213d0fd3b150771087c6a35039afeab2bf962bda153865dc7a12e65bee5`.
- Raw 86MB generalization artifact SHA-256:
  `213868e52fbffb31aba0218db67ad6ba2a881c66530e7214af00c89d7f68da00`.

`phase1_shadow_promotion.json` contains the complete Phase 1 metrics. The compact
`generalization_shadow_summary.json` binds the full raw matrix without committing its
large per-day/per-lot payload.

## Account migration

`uquant account-code-migrate --account ACCOUNT --acknowledge-code-change` performs the only
authorized migration: it updates `code_hash` and appends one `code_identity_only` audit
event. `economic_state_sha256` excludes only those two identity/audit fields and must remain
identical before save, after save, and after strict reload. Cash, positions, tranches,
pending orders, order ledger, fills, peaks, opportunity/risk and lifecycle state are not
modifiable by this migration.
