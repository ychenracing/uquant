# Alpha Recovery and Generalization Design

## Purpose

Implement the approved 2026-08-11 audit without adding another production engine or reintroducing symbol-specific priors. The production priority remains return, drawdown, then account orders. A feature is enabled by default only after shared-configuration replay shows no material economic regression.

## Chosen approach

Use staged structural repair with economic gates. First identify the earliest decision divergence in A/2024, C/2023, and B/continuous. Then remove correlated risk-vote double counting, make same-day opportunity drive same-day alpha without double tenure mutation, share one group-balanced reference context, simplify strategic admission to SECULAR and EMERGING_SECULAR evidence, add evidence-conditioned cohort size, upgrade unknown-industry inference, and close the research/promotion loop.

Rejected alternatives are a big-bang strategy rewrite, which destroys attribution, and parameter-only optimization, which preserves the known structural defects and increases event overfitting.

## Architecture

- `ReferenceContext` is the single causal daily reference summary consumed by opportunity and risk.
- Leader scoring has a structural stage with no opportunity dependency and an alpha stage that accepts the current opportunity explicitly. Tenure mutates exactly once.
- Risk votes are keyed by six evidence families; each family contributes at most one vote. Continuous severity remains available but does not create duplicate votes.
- Strategic admission exposes two production states. Historical route names survive only as attribution tags during migration.
- Unknown industries use tech-beta residual correlations over 60/120-day windows and shrink sparse industry strength toward a parent technology factor.
- Research uses one shared `SystemConfig` across all cells and reports walk-forward instability, PBO and deflated Sharpe penalties before promotion.
- Point-in-time reference membership is versioned and fail-closed; historical replay never sees a future-effective member.

## Economic gates

- Bull A-E wealth is at least 99% of the frozen baseline, or 98% only with at least two percentage points of drawdown improvement.
- Drawdown cannot worsen by more than 0.5 percentage points.
- Orders cannot increase by more than `max(1, 5%)`.
- A/2024 wealth is at least 1.7314x; C/2023 is at least 3.3290x; B/continuous targets at least 35.54x, drawdown at most 27.78%, and at most 65 orders, with wealth taking priority if the three cannot be achieved together.
- Through-July urgent return remains at least -3%.
- Full promotion, generalization-by-regime, and competitor matrices fail closed when reviewed references are absent or incomplete.

## Safety and testing

Every behavior change starts with a failing unit or integration test. Sentinel replays run after each structural phase. Full static, unit, manifest, promotion, generalization and competitor gates run before publication. Negative economic candidates are reverted instead of documented as accepted defaults.
