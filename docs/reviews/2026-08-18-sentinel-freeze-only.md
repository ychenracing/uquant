# Sentinel freeze-only Phase 4 review

Date: 2026-08-19  
Outcome: Freeze-only rejected; production default returned to Shadow

## Authority and de-duplication

Sentinel remains evidence-only. `uquant.assess_risk()` is the sole mapping boundary and can
only OR an eligible Sentinel opinion into the existing `RiskAssessment.freeze_new_risk`.
It cannot change `Risk.state`, `target_gross_cap`, reduction level, shock state or severity.
`LIMITED_GROSS_CAP` is deliberately unavailable in Phase 4.

The six canonical families are combined with boolean OR, so correlated base and Sentinel
observations never create two votes. The risk summary records base, Sentinel and combined
family flags, incremental and earlier families, first evidence dates, confirmation count,
raw Sentinel assessment and whether Sentinel received freeze authority. Eligibility requires
READY coverage, confidence at least 0.80, two independent families, two-session confirmation
or the narrow severe-direct exception, and genuinely incremental or earlier evidence.

The Sentinel evaluation uses `default_ai_universe()` and its point-in-time industry mapping;
it verifies that the production reference registry has the same point-in-time membership.
No static trade risk basket is copied.

## Freeze behavior

The allocator first obtains the ordinary strategy counterfactual. Sentinel-only freeze then
removes new symbols and clamps existing targets to current economic weights. This covers
ordinary entries, strategic cohorts, ADD1, ADD2, SATELLITE, new RECOVERY, post-shock
restoration and active rotation. Replacement-funded exits are held back so an incumbent is
not sold merely to fund a replacement that freeze prevents. Independent lifecycle exits and
all base-uquant reductions remain available; Sentinel alone does not reduce a healthy holding.

Unsubmitted incremental BUY intent is cancelled with reason `sentinel_freeze_new_risk`.
Broker-visible OPEN or PARTIALLY_FILLED orders retain broker-authoritative status, filled and
remaining quantities while the ledger records `CANCEL_REQUESTED`; later snapshots remain
authoritative. Carried-forward BUY risk is not renewed. SELL orders are never blocked.

## Economic rejection

The first economic divergence in the candidate probe was `a/h1_2024` on 2024-02-06. Shadow
submitted the existing strategic-restoration BUY for `sz300502`; Freeze-only held the current
book and submitted no order. The candidate changed no cap and created no SELL.

| Metric | Shadow | Freeze-only | Delta |
|---|---:|---:|---:|
| Final wealth | 1.9042531401 | 1.8635082599 | -0.0407448802 |
| Wealth retention | 100% | 97.860322% | -2.139678 pp |
| Max drawdown | 0.1567427757 | 0.1567427757 | 0 |
| Acute return | 0.0639067990 | 0.0639067990 | 0 |
| Account orders | 8 | 8 | 0 |
| Gross turnover | 2.0503083590 | 2.0452412622 | -0.0050670968 |
| Annual turnover | 4.2048696854 | 4.1944778427 | -0.0103918427 |

Wealth retention violated the fixed 99% Bull floor. Per the promotion contract, no further
parameter search was performed: default mode was returned to `SHADOW`, the baseline was not
lowered, and `main` is not eligible for fast-forward.

## Validation after fallback

Phase 1 full profile passed all 30 official a-e/six-window cells and 15 protected cells with
no failures. Generalization passed all 234 cells: no-optical 6, remove-all-core 6, remove-one
18, subindustry 72, industry-balanced 6, full 6 and fixed-random 120. There were no replay
errors. Random nearest-rank p10 wealth was 0.9982387916 and p90 drawdown was 0.2117666082.

The old Phase 1/2 and Future Holdout contracts remain content-addressed historical evidence;
Phase 4 does not reseal them around new production bytes. Existing accounts require the
explicit code-identity-only migration described in the evidence README. Migration tests
prove the pre/post economic-state SHA is identical and fail if any economic field changes.

Full artifacts and hashes are under `artifacts/sentinel/freeze_only/`.
