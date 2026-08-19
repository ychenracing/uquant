# Sentinel freeze-only Phase 4 review

Date: 2026-08-19  
Outcome: Freeze-only accepted; production default is `FREEZE_ONLY`

## Authority and de-duplication

Sentinel remains evidence-only. `uquant.assess_risk()` is the sole mapping boundary and can
only OR an eligible Sentinel opinion into the existing `RiskAssessment.freeze_new_risk`.
It cannot change `Risk.state`, `target_gross_cap`, reduction level, shock state or severity.
`LIMITED_GROSS_CAP` is deliberately unavailable in Phase 4.

The six canonical families are combined with boolean OR, so correlated base and Sentinel
observations never create two votes. The risk summary records base, Sentinel and combined
family flags, incremental and earlier diagnostics, confirmation count,
raw Sentinel assessment and whether Sentinel received freeze authority. Eligibility requires
READY coverage, confidence at least 0.80, two independent families, two-session confirmation
or the narrow severe-direct exception, and a genuinely incremental same-day family. Phase 4
has no trustworthy point-in-time per-family first-date carrier, so earlier evidence fails closed
with `sentinel_earlier_supported=false` rather than inferring history from today's state.
Today's holdings, leaders, drawdown and industry mapping are likewise never replayed backward;
production reports `confirmation_history_trusted=false`, so ordinary multi-session confirmation
fails closed and only the narrow severe-direct exception can bypass it.

The Sentinel evaluation uses `default_ai_universe()` and its point-in-time industry mapping;
it verifies that the production reference registry has the same point-in-time membership.
No static trade risk basket is copied.

## Freeze behavior

The allocator obtains the ordinary strategy counterfactual on a deep account copy. Sentinel-only freeze then
removes new symbols and clamps existing targets to current economic weights. This covers
ordinary entries, strategic cohorts, ADD1, ADD2, SATELLITE, new RECOVERY, post-shock
restoration and active rotation. Replacement-funded exits are held back so an incumbent is
not sold merely to fund a replacement that freeze prevents. Independent lifecycle exits and
all base-uquant reductions remain available; Sentinel alone does not reduce a healthy holding.

Unsubmitted incremental BUY intent is cancelled with reason `sentinel_freeze_new_risk`.
Broker-visible OPEN or PARTIALLY_FILLED orders retain broker-authoritative status, filled and
remaining quantities while the ledger records `CANCEL_REQUESTED`; later snapshots remain
authoritative. The external order remains durable in the nonterminal ledger, leaves the local
executable pending set, and accepts late broker fills until `CANCELLED` confirmation or final
fill. New same-symbol BUYs are blocked meanwhile, but an independent SELL is not. Carried-forward
BUY risk is not renewed.

The behavioral gate checks the formal `RiskAssessment.freeze_new_risk` first. Sentinel evidence
can attribute an already-authorized freeze but cannot activate allocator or cancellation behavior
when the formal flag is false. Counterfactual admissions, cohorts and rotations therefore cannot
mutate the durable account, while independent lifecycle exits still pass through. Only monotonic,
route-owned exit cleanup is committed from the planning copy, including tactical cooldown/rearm
and final strategic-epoch completion; admission and rotation state is never copied back.

## Economic acceptance

The corrected `a/h1_2024` candidate probe has no economic divergence. Four dates supplied
incremental same-day Sentinel evidence, but base uquant had already frozen new risk on every
date, so Sentinel-exclusive authority did not activate. The candidate changed no cap and
created no SELL.

| Metric | Shadow | Freeze-only | Delta |
|---|---:|---:|---:|
| Final wealth | 1.9042531401 | 1.9042531401 | 0 |
| Wealth retention | 100% | 100% | 0 pp |
| Max drawdown | 0.1567427757 | 0.1567427757 | 0 |
| Acute return | 0.0639067990 | 0.0639067990 | 0 |
| Account orders | 8 | 8 | 0 |
| Gross turnover | 2.0503083590 | 2.0503083590 | 0 |
| Annual turnover | 4.2048696854 | 4.2048696854 | 0 |

An earlier 97.860322% result was invalid: the integration layer had rewritten base CAUTION's
formal freeze evidence as Sentinel authority even when Sentinel was ineligible. The corrected
integration preserves base evidence semantics; a regression test covers this boundary. Wealth
retention is 100%, MDD and Acute do not worsen, and orders do not increase, so the fixed
promotion gates pass without lowering a baseline or changing parameters.

## Promotion validation

Phase 1 full profile passed all 30 official a-e/six-window cells and 15 protected cells with
no failures. Generalization passed all 234 cells: no-optical 6, remove-all-core 6, remove-one
18, subindustry 72, industry-balanced 6, full 6 and fixed-random 120. There were no replay
errors. Explicit Shadow and Freeze-only have zero economic/status differences across all 234
cells. Random nearest-rank p10 wealth was 0.9982387916 and p90 drawdown was 0.2117666082.

The old Phase 1/2 and Future Holdout contracts remain content-addressed historical evidence;
Phase 4 does not reseal them around new production bytes. Existing accounts require the
explicit code-identity-only migration described in the evidence README. Migration tests
prove the pre/post economic-state SHA is identical and fail if any economic field changes.

Full artifacts and hashes are under `artifacts/sentinel/freeze_only/`.
