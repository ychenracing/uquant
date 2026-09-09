# Capital, holding and information-source round — 2026-09-09

Decision: both preregistered native arms rejected. No production replacement,
third arm, tuning or merged PR. The useful new finding is an input-classification
problem affecting the economic interpretation of cross-industry results.

## Native results

All four paths: recorded no_optical case, 2023-01-03 through 2023-06-30,
actual next-open/partial fills, account costs and risk interactions. Native
readback succeeded; exact sources, producers, seals and fills are in
`capital_holding_receipts.json`. The original case excludes recorded optical
labels; it does NOT reliably exclude all economically optical businesses.

| Path | Final wealth | Maximum drawdown | Orders | Decision |
|---|---:|---:|---:|---|
| Existing C | 1.447695 | 24.4243% | 6 | Reused control |
| Prior simple | 1.272228 | 23.5531% | 8 | Previously rejected |
| S: existing entry cap | 1.198683 | 17.7654% | 8 | Wealth fail |
| H: original ordinary exits | 1.288300 | 23.3414% | 8 | Wealth fail |

Screen: wealth >=1.3753099655116032, drawdown <=.2442425185317515,
orders <=12. Both treatments start from the SAME rejected simple producer.
S reuses cfg.core_admission_weight=.20 instead of .30: this REDUCES the cap;
it does not test increasing position sizes or reproduce C's whole allocation.
H changes the exit function only, but downstream capital, risk sales and actual
entry dates may change. These are full-account policy effects, not isolated
per-stock hypothetical PnL. Runtime about 96 and 99 seconds respectively.
No economic matrix is justified by these failed screens.

## Exact accounting attribution

Simple minus C account net PnL: -350934.38 CNY. Symbol differences:
sh688200 -43608.85; sh688256 -211304.53; sh688766 -120165.23;
sz002281 +24144.23. They reconcile to the difference in ending wealth.

For sh688256, C buys 13300 shares on March 7 and sells 9000 on May 8;
simple buys 9900 on February 28 and sells 6100 on May 8. Same CRISIS sale
price. Symmetric three-factor accounting decomposition of -211304.53:
quantity -249517.49, purchase price +44820.78, sold fraction -6968.67,
lower cash fees +360.85. Slippage is already in fills, not deducted twice.

Smaller quantity dominates this accounting difference; earlier purchase price
helps. Hypothetical combinations are not executable account counterfactuals,
and this does not establish that uniformly raising allocations improves returns.
The two fixed treatments fail; close them without another constant search.

## Input correctness takes priority

`industry_mapping_review.json` preserves original labels and independent source
URLs, publication dates, evidence status and proposed corrections. In the fixed
six-company sample, pre-2023 issuer documents substantiate three issues:

- sh688498: recorded advanced_packaging, business optical chips.
- sh688110: recorded foundry, business Fabless memory IC design.
- sz002281: recorded datacenter, business optical components/modules; serving
  data centers does not remove optical exposure.
- sh688037: recorded design, likely equipment; 2023Q1 issuer explanation
  supports this, but pre-cutoff business evidence remains pending.

Do not claim all 34 companies audited. Classification does not prove historical
AI eligibility or pool membership. Do not silently edit frozen inputs or sealed
results. Current numeric receipts remain valid for their recorded definitions;
claims of economically non-optical generalization require qualification.

Reclassifying ONLY sh688498 in the unchanged full C account raises the optical
net-profit share from 78.5522% to 91.2213%. Total profit stays 59486623.46 CNY.
This is accounting reclassification, NOT a corrected-input strategy replay.
Prior industry-score diagnostics also need re-evaluation under a valid taxonomy.

## Independent revenue information feasibility

`revenue_source_feasibility.json` contains verified original 2022Q3 quarterly
revenue and YoY values for 4/6 fixed sampled companies, and one later 2023Q1
update. Disclosure date is distinct from period end and board date; availability
uses the next exchange session conservatively. Original issuer reports on Sina
are mirrors, not analyst forecasts. Two records remain unverified (IPO/Q3
coverage and table extraction); missing is not zero. No later annual-report
backfill, invented timestamp, alpha claim or production ingestion.

Conclusion: the field is obtainable for part of the sample, but the source
check does not establish complete coverage, historical pool validity or a useful
investment signal. Do not add a revenue filter until input correctness is fixed.

## Next bounded direction

Prioritize a separate versioned research taxonomy and point-in-time provenance
review of the existing 34-symbol pool, starting with these documented errors.
Keep the original frozen baseline alongside it. First check set exclusions and
recompute cheap attribution/ranking summaries; only then preregister the smallest
native sentinel needed to measure the input correction. Do not modify strategy
parameters in that comparison. A corrected historical sample is still research,
not a fresh out-of-sample test. Budget the next source/input round to 2–3 hours;
stop with explicit unresolved records instead of turning it into a data platform.
The long-term profitability/generalization goal remains unproven.

## Reproducibility and verification

- Per-arm focused lifecycle/cash-rearm tests: 31 passed for S and 31 for H.
- Attribution tests: 2 passed; Ruff passed. Four native account readbacks and
  PnL reconciliations passed. These are focused checks, not full acceptance.
- Production unchanged from e7bf286; main not merged and PR56 remains draft.
- Exact treatment patches are in `capital_holding_producers/`.
- Native archive: uquant_capital_holding_evidence_20260909.tar.gz,
  10302158 bytes, SHA256
  `0e7d65e26e55f27bf1878802b686f0b23821f5e043f9a5886a692a9fa6133907`;
  264 archived members verified against SHA256SUMS.
  Durable ID: `libfile_3976db10c4948191bfec214b245798ff`.
- Producer bundle requires original simple commit 226ef63, preserved in the
  preceding archive `libfile_0089fab2c0188191b1d344ff42e8f5bf`.
  Local experimental producers are not claimed to be PR production commits.
- Five source PDFs were downloaded (including Guangxun annual); other cited
  sources were verified through web retrieval. SSE prospectus byte download
  returned HTTP502; no local PDF checksum is claimed for it.
- Future Holdout market data not accessed. No new fees/data contract changes.
