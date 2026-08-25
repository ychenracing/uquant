# Sentinel Evidence Closure

> **权威级别：历史证据** — 本目录记录截至 2026-08-05 的冻结增量证据闭合，不授予当前
> 生产权限。当前边界见 [Risk Sentinel](../../../docs/RISK_SENTINEL.md)，证据目录见
> [历史证据索引](../../README.md)。

`evidence_closure.json` compares the immutable, account-free Phase 6 market
timeline through 2026-08-05. It uses only `breadth_structure`,
`covariance_stress`, and `market_velocity`; current book and capital damage are
excluded from history.

The first-family comparison found three duplicate capabilities and no
Sentinel-only incremental or earlier first family. The exact first dates are:

| Family | Base first | Sentinel first | Relationship |
|---|---|---|---|
| `market_velocity` | 2014-02-25 | 2014-02-25 | DUPLICATE |
| `breadth_structure` | 2014-02-25 | 2014-02-26 | DUPLICATE |
| `covariance_stress` | 2015-08-18 | 2024-09-30 | DUPLICATE |

`FALSE_POSITIVE` is reserved for a Sentinel-only incremental or earlier first
warning followed by a positive 20-session tech-index return. Duplicate
capabilities are not relabelled as Sentinel false positives. Missing future
sessions remain `DATA_NOT_READY`.

Forward returns and diagnostic missed returns are not accounting PnL. Since
production causal-confirmation authority remains disabled, actual production
opportunity cost is zero for every row. The artifact uses the existing
configuration without overriding thresholds or confirmation parameters and
does not read or write an account.

`economic_equivalence.json` binds the Stage 8 comparison to production main
`711af117` and the authenticated 45-case Phase 1 matrix. Its baseline and
candidate trace digests are identical. The trace contract covers canonical
daily decisions plus the final economic account, including order and fill
history; only the production code identity is excluded from the economic
account hash.

The resumable comparison is available as:

```bash
uv run python -m research.committed_economic_equivalence \
  --baseline-root /path/to/clean-main \
  --candidate-root /path/to/clean-candidate \
  --data-dir data/frozen \
  --checkpoint /tmp/uquant-equivalence-checkpoint.json \
  --output /tmp/uquant-equivalence.json \
  --jobs 4
```

Each completed side/case trace is atomically checkpointed and bound to both
commit SHAs, the data manifest, and the exact matrix digest. A mismatched
checkpoint is rejected instead of silently reused.

`account_code_identity_migration.json` records the mandatory explicit account
code-identity migration. Schema and every economic field remain equal; only
`code_hash` and the appended `code_identity_only` audit event change.
