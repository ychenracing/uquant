# Risk Sentinel Consolidation Design

## Scope

Phase 8 starts from remote `main` at
`711af1179aa72ce48ca3a6af58ecddb3a029a7ce`. Phase 7 is a rejected research
branch and is never merged or cherry-picked as a unit. Every Phase 7 path is
classified as merge, rewrite, archive, or discard before any content is
recovered.

The final production topology remains one `ProductionEngine`, one
`RiskAssessment`, one `AccountState`, one portfolio/execution path, and one
Daily Report. Sentinel contributes diagnostics to the existing report. It does
not gain independent authority.

## Boundaries

- Keep `risk_sentinel_causal_confirmation_enabled=false` in production.
- Keep `risk_sentinel_mode=FREEZE_ONLY`; only `SHADOW` and `FREEZE_ONLY` are
  valid modes.
- Reject `LIMITED_GROSS_CAP` and every unrecognized mode explicitly.
- Do not change risk thresholds, strategy, positions, gross caps, symbol caps,
  targets, orders, fills, SELL authority, or account economic fields.
- Do not recover the Phase 7 candidate lock, authority path, state machine, or
  trading-path changes.
- Recover only generic observation/validation behavior, tests that defend
  existing boundaries, compact evidence, and rejection documentation.

## Evidence closure

`research/sentinel_evidence_closure.py` consumes the immutable Phase 6
`RiskEvidenceTimeline`. It records the first Sentinel trigger for each trusted
market family and classifies it without changing any threshold:

- `DUPLICATE`: base risk has the same family active on the trigger date.
- `EARLIER`: Sentinel's first date is before the base first date on the same
  point-in-time market sequence.
- `INCREMENTAL`: Sentinel has a family the base sequence has not yet observed.

Forward 5/10/20-session tech-index returns are diagnostic outcomes. A
Sentinel-only warning with a positive 20-session return is labelled
`FALSE_POSITIVE`; insufficient future observations remain `DATA_NOT_READY`.
Because production authority stays disabled, actual production opportunity
cost is always zero. A separate diagnostic missed-return field is never called
accounting PnL.

## Daily Report

The existing `render_daily_report()` remains the only report entry point. Its
compact `Risk Sentinel` section displays mode, observed level, coverage,
confidence, owner, comparable market families, weakest AI subindustries, and a
bounded conclusion. Conclusions are limited to normal execution, no new risk,
or data review. The report never recommends a sell, position reduction, or
single-symbol action.

## Verification

Tests cover all evidence classifications, incomplete forward data, all report
owner states, safe conclusions, Phase 7 recovery boundaries, allowed modes,
and rejected legacy modes. The final committed tree must pass the complete
Engineering gate. An isolated cross-commit Phase 1 equivalence replay must
produce identical decision payload and economic-account digests, which cover
targets, pending orders, fills, account economics, and the reported return,
drawdown, turnover, order-count, Sharpe, and acute-return inputs.
