# Cross-vintage account recovery: R26 evidence

All fixed economic gates in the accompanying evaluations passed. Release completion is recorded separately by remote original receipts and the normal-merge readback; this report alone does not assert a merge.

Baseline B is `3af6aba18f133f4e22612769c703dac080d940d9`. All candidate economics in this report were rerun on R26 producer `0beca9d1b3f991b633621219770f9da7a389b72b`; published source `2b1279e70c21d697e3fa325211db86753e5af973` has the identical full tree `e8342eb5be19a89ee87e0e2560be7ed64bc612ab`. Its production subtree is `69f5e67cabc55b2d53918bd623afb31f6f8695d5`; later documentation/provenance commits retain it. Relative baseline B0 remains `28a4d4ead1e4dd3c98119eec0c57b9f294edf9ef`; no extra improvement over current B was imposed. C9-to-B equivalence is recorded separately. Prior engineering results retain their original identities and are reused only for unchanged behavior.

R24 passed the task matrix but failed the existing champion preservation check: final wealth 15.3520465505x versus current B 27.9731339989x. R25 holding-based repair cadence restored champion but failed the legacy sentinel (G 2.9623959385); it was rejected. R26 restores R24 cadence and corrects strategic-cohort membership. Champion now passes at 28.1387451775x with 27.133435% maximum drawdown. No acceptance threshold, input or scenario was relaxed.

## Causes and the unified correction

The original capital repair gate required historical high-water recovery before allowing cash to deploy. The actual 2024-offset0 B checkpoint on January 16, 2025 had cash 1,755,651.0013816166, lifetime peak 2,024,610.1332918145 and capital tier 1. Its 13.28449% historical loss prevented repair while frozen cash could not earn back that loss. The candidate measures current continuous deployment drawdown in one risk owner. Actual flatness ends the deployment cycle; held legacy accounts conservatively inherit their true capital peak. Lifetime capital and operating peaks, cash, holdings, cost bases, fills and obligations remain intact. The existing healthy-market confirmation releases at most one tier each session after its existing five-day confirmation, without restarting the clock for every tier. Higher-tier escalation still combines historical loss with independent current damage.

Admission recovery does not restore lost risk capacity. The existing overlay retains the existing 50% crisis cap while the new actual deployment peak remains below the lifetime peak's existing 20% crisis line. Market freezes, outstanding orders, live owners, late fills, lot sizes and final funding checks remain effective. No additional cash book, user parameter, date/account-age/ticker rule or rescue route was added.

The recovery scanner now retains a bounded existing five-day breakout observation for unowned additions to an existing cohort. Initial admission and owned unfilled remnants still need a current breakout. Current price/MA20, depth and liquidity remain mandatory. Fresh opportunities retain the original depth/strength priority; older valid observations only supplement them. Existing members remain owned until the existing lifecycle releases them. The scanner owns ordering once, eliminating repeated selection sorting.

Two further causal failures emerged in the first registered confirmation. First, ordinary protection snapshots from completely closed May 2023 trades survived after their final strategic owner closed in August. In the different-recovery legacy account they blocked January 10, 2024 despite the same economic book as native. The existing authority normalizer now retires those snapshots only when the book is cash, no execution or strategic obligation is live, current-episode protection is absent, and complete attributed ordinary fills prove net zero shares ending in a sell. Unknown or ambiguous rights remain. The actual January 8 checkpoint counterfactual clears the residue January 9, permits the January 10 instruction, and fills 59,900 sh688041 shares January 11 without changing any economic history. An initially later checkpoint missed that instruction and is retained as an unsuccessful diagnostic.

Second, nominal 80% ordinary capital was divided among qualified peers before applying an actual 50% risk cap, consuming the cap on the first peers and starving the last qualified peer. One line now intersects the existing entry allowance with the existing current cap before allocation. The actual May 8, 2025 checkpoint retains the 50% cap and allocates one-sixth to each of three qualified peers on May 9. Real May 12 fills are sh688019 2,700, sh688233 10,500 and sz300666 3,900 shares. Final cash, concentration, lot and execution checks are unchanged. These mechanism diagnostics support causation; the full economic tests below decide acceptance.

The champion trace contained one actual strategic member and one ordinary LEADER holding. The synchronized cohort-break predicate counted both as cohort breadth, forcing a strategic sell. R26 requires two actual live strategic members for this route. Existing single-name tail, capital and market protections remain active. R24 and R26 champion cash and shares match until October 15, 2025, the first affected fill; July recovery sizing and timing are unchanged. Public-risk regressions retain the two/three-member break and reject ordinary holdings as second strategic members. See CROSS_VINTAGE_COHORT_MEMBERSHIP_PROOF.json.

## Six native accounts

The common window is January 16, 2025 close through August 5, 2026 close. G normalizes measurement only; it does not reset an account. Full precision JSON decides every threshold.

| Account/version | Full W | Full DD | Window G | Window DD | Flat closes | Mean gross | End cash |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C-native-2024-offset0 | 12.916708872 | 22.148649% | 13.531142893 | 18.220637% | 18.4492% | 71.9150% | 25833417.74 |
| B-2024-offset0 | 0.871566861 | 14.236694% | 0.992870292 | 3.467886% | 86.6310% | 2.6146% | 1743133.72 |
| C-native-2024-offset5 | 13.207458982 | 28.145955% | 14.116258319 | 18.480007% | 18.4492% | 71.8088% | 26414917.96 |
| B-2024-offset5 | 1.028028705 | 13.477472% | 0.991517459 | 3.615665% | 86.6310% | 2.6683% | 2056057.41 |
| C-native-2024-offset10 | 14.010007973 | 22.191663% | 13.507136502 | 18.214133% | 18.4492% | 71.8210% | 28020015.95 |
| B-2024-offset10 | 0.955141258 | 11.747038% | 0.992240254 | 3.532026% | 86.6310% | 2.6355% | 1910282.52 |
| C-native-2025-offset0 | 15.241230373 | 18.840109% | 13.830486382 | 18.840109% | 20.5882% | 72.8973% | 30482460.75 |
| B-2025-offset0 | 12.394418813 | 17.253037% | 11.247178634 | 17.253037% | 21.3904% | 66.3052% | 24788837.63 |
| C-native-2025-offset5 | 12.157717696 | 18.843711% | 12.157717696 | 18.843711% | 20.5882% | 71.1208% | 24315435.39 |
| B-2025-offset5 | 11.249229820 | 17.234169% | 11.249229820 | 17.234169% | 21.3904% | 66.2498% | 22498459.64 |
| C-native-2025-offset10 | 12.157717696 | 18.843711% | 12.157717696 | 18.843711% | 20.5882% | 71.1208% | 24315435.39 |
| B-2025-offset10 | 11.249229820 | 17.234169% | 11.249229820 | 17.234169% | 21.3904% | 66.2498% | 22498459.64 |

## Three real B-prefix upgrades

Each starts from B's actual processed close and continues on the next session. Full DD includes B's entire prefix and true high water. Migration economic hashes and prefix equity curves are checked unchanged.

| Account/version | Full W | Full DD | Window G | Window DD | Flat closes | Mean gross | End cash |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C-legacy-2024-offset0 | 9.452311082 | 18.429163% | 10.767870237 | 17.051425% | 21.3904% | 65.1628% | 18904622.16 |
| C-legacy-2024-offset5 | 11.531230507 | 17.857416% | 11.121689818 | 17.405038% | 21.3904% | 64.8506% | 23062461.01 |
| C-legacy-2024-offset10 | 10.655562310 | 17.383726% | 11.069438955 | 17.383726% | 21.3904% | 64.7338% | 21311124.62 |

Higher new-account reference G: 13.830486382246367. Minimum native-old ratio: 0.9766204982728293; minimum legacy-old ratio: 0.7785604887332337. New-account C/B retention: {"0": 1.2296849577010984, "5": 1.0807600067109642, "10": 1.0807600067109642}. Suffix-only doubled-cost retention: {"C-legacy-cost2": 0.9980722889463757, "C-new-cost2": 1.0475853501026835}. Prefix costs remain unchanged in suffix stress; this is distinct from the two original full-period cost scenarios.

## Original 23 scenarios

The original 70% cluster, 80% strong-scenario, 1.20 weak geometric/0.90 individual, 30% DD and 90% cost gates retain B0 as their relative baseline. This table also discloses change against current B.

| Scenario | B W | C W | C/B | B DD | C DD |
| --- | --- | --- | --- | --- | --- |
| 2024-offset0 | 0.871566861 | 12.916708872 | 14.820100952 | 14.236694% | 22.148649% |
| 2024-offset10 | 0.955141258 | 14.010007973 | 14.667995808 | 11.747038% | 22.191663% |
| 2024-offset5 | 1.028028705 | 13.207458982 | 12.847364012 | 13.477472% | 28.145955% |
| 2025-offset0 | 12.394418813 | 15.241230373 | 1.229684958 | 17.253037% | 18.840109% |
| 2025-offset10 | 11.249229820 | 12.157717696 | 1.080760007 | 17.234169% | 18.843711% |
| 2025-offset5 | 11.249229820 | 12.157717696 | 1.080760007 | 17.234169% | 18.843711% |
| d-continuous_ai_era | 18.695267713 | 30.947537942 | 1.655367466 | 27.139915% | 27.139915% |
| full | 32.316992795 | 32.662634383 | 1.010695351 | 27.133916% | 27.133916% |
| full-cost2 | 32.142973910 | 32.483773762 | 1.010602624 | 27.241168% | 27.241168% |
| full-offset1 | 32.310905018 | 32.654354934 | 1.010629536 | 27.147058% | 27.147058% |
| full-offset10 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset2 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset3 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset4 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset5 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset5-cost2 | 31.382797965 | 31.726984995 | 1.010967379 | 27.218525% | 27.218525% |
| full-offset6 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset7 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset8 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| full-offset9 | 31.574706881 | 31.886421420 | 1.009872286 | 27.122244% | 27.122244% |
| loo-sz300308 | 1.863365061 | 4.371938529 | 2.346259796 | 27.108751% | 27.602569% |
| loo-sz300502 | 1.803770937 | 8.664817419 | 4.803723821 | 25.389366% | 25.624110% |
| remove_all_three | 1.499275008 | 3.410074014 | 2.274481997 | 22.561481% | 28.462388% |

Weak-pool geometric retention against B0: 4.684368859123599. Full-period cost retention: {"full": 0.9945239989495676, "full-offset5": 0.9949998645962268}.

## Confirmation and its evidence boundary

R22 passed core and original 23 development but failed both valid registered confirmation cases. Its different-recovery legacy ratio was 0.09711540671799086; remove308 native ratio was 0.6395487232587204. These failures are retained, and both cases became development evidence when used for R23/R24 correction. R23 fixed the stale-rights path but remove308's exact same-candidate ratio remained 0.6224480186501945. No failed case was replaced or discarded. Earlier C6/R19 confirmation outcomes were not inspected for tuning.

R24's registered January 23 reserve passed before the later champion correction. R26 reruns every original case and the same registered reserve with the same higher-reference 70% old, 90% new retention and 30% full-DD gates. Actual affected-path coverage is separately checked through paired B/C releases, real instructions and subsequent fills. Neither initial cases nor the already observed reserve are untouched R26 evidence. No case was replaced. R26 was driven by the separate champion trace, not reserve optimization.

| Evidence role | Pair | Account | W | G | Full DD |
| --- | --- | --- | --- | --- | --- |
| Initial confirmation, now development | different-recovery | C_old_native | 37.957922911 | 10.143836354 | 21.246261% |
| Initial confirmation, now development | different-recovery | C_new_native | 13.656024231 | 13.656024231 | 27.774316% |
| Initial confirmation, now development | different-recovery | C_old_legacy | 37.957922911 | 10.143836354 | 21.246261% |
| Initial confirmation, now development | different-recovery | B_old | 5.338157716 | 1.426563788 | 19.470303% |
| Initial confirmation, now development | different-recovery | B_new | 0.979759184 | 0.979759184 | 13.164030% |
| Initial confirmation, now development | remove308 | C_old_native | 2.054306603 | 2.170769123 | 26.877170% |
| Initial confirmation, now development | remove308 | C_new_native | 2.132045197 | 2.132045197 | 19.658762% |
| Initial confirmation, now development | remove308 | C_old_legacy | 2.246558376 | 2.166770094 | 19.367984% |
| Initial confirmation, now development | remove308 | B_old | 1.028028705 | 0.991517459 | 13.477472% |
| Initial confirmation, now development | remove308 | B_new | 1.561984378 | 1.561984378 | 17.101022% |
| Previously observed registered reserve | reserved-dates | C_old_native | 14.010007973 | 13.335373969 | 22.191663% |
| Previously observed registered reserve | reserved-dates | C_new_native | 12.157717696 | 12.157717696 | 18.843711% |
| Previously observed registered reserve | reserved-dates | C_old_legacy | 10.655562310 | 11.069438955 | 17.383726% |
| Previously observed registered reserve | reserved-dates | B_old | 0.955141258 | 0.992240254 | 11.747038% |
| Previously observed registered reserve | reserved-dates | B_new | 11.249229820 | 11.249229820 | 17.234169% |

Exact ratios and references are in CROSS_VINTAGE_GATE_SUMMARY.json. The reserve establishes limited calendar/state transfer on this frozen dataset, not independent future profitability or a new untouched confirmation after the champion correction.

## Costs, workload and opportunity trade-offs

| Account | Instructions | Fill records | Operation days | Daily-equity turnover | Fees | Slippage |
| --- | --- | --- | --- | --- | --- | --- |
| C-native-2024-offset0 | 18 | 18 | 11 | 3.755800 | 23836.04 | 34090.76 |
| B-2024-offset0 | 4 | 4 | 4 | 0.765300 | 684.78 | 1348.50 |
| C-legacy-2024-offset0 | 9 | 9 | 6 | 2.290175 | 15153.90 | 21316.93 |
| C-native-2024-offset5 | 17 | 17 | 9 | 3.351560 | 23869.68 | 34111.81 |
| B-2024-offset5 | 4 | 4 | 4 | 0.785201 | 828.70 | 1633.10 |
| C-legacy-2024-offset5 | 9 | 9 | 5 | 2.287146 | 18096.78 | 25207.41 |
| C-native-2024-offset10 | 18 | 18 | 11 | 3.753542 | 25840.38 | 36949.59 |
| B-2024-offset10 | 4 | 4 | 4 | 0.773380 | 758.34 | 1493.89 |
| C-legacy-2024-offset10 | 9 | 9 | 5 | 2.279823 | 16712.05 | 23277.07 |
| C-native-2025-offset0 | 11 | 11 | 7 | 3.049369 | 26832.44 | 38080.69 |
| B-2025-offset0 | 10 | 10 | 6 | 2.836129 | 20955.40 | 29345.25 |
| C-native-2025-offset5 | 9 | 9 | 5 | 2.353524 | 20374.73 | 29023.88 |
| B-2025-offset5 | 9 | 9 | 5 | 2.200832 | 18063.06 | 25381.20 |
| C-native-2025-offset10 | 9 | 9 | 5 | 2.353524 | 20374.73 | 29023.88 |
| B-2025-offset10 | 9 | 9 | 5 | 2.200832 | 18063.06 | 25381.20 |

Instructions use unique durable orders; fill records and operation days are separate. Fees include commission, stamp duty and transfer fees; slippage is separate. Unfilled orders and actual cancellation reasons remain in the core result. Improved participation consumes capital, incurs costs and changes later opportunities. The complete C/B comparison includes deterioration as well as gains. Mechanism-tag returns are not treated as portfolio-level causal gains. A doubled-cost ratio above one can result from lot-size and path changes, not a claim that costs improve investing.

## Engineering scope and limits

On the identical affected-file scope, economic production physical lines changed from 5523 to 5605; validation lines changed from 1064 to 1081. Research, tests and documentation are excluded. Account fields changed 84 to 85 for the derived continuous-deployment peak. New user settings: zero; new parallel cash/reserve books: zero. Recovery selection shrank 60 to 54 lines; scanning grew 33 to 49; capital overlays grew 66 to 73. The authority normalizer grew 39 to 49 lines and 8 to 11 branch points, with maximum nesting unchanged at three. A focused eight-line pure closure-proof helper has 13 branch points and nesting one. This corrects the intermediate 24-branch function without changing its proof or weakening the 20-branch repository limit. Ordinary budget sizing adds no line, branch or state. The synchronized cohort predicate adds a live-member cardinality check without new state or configuration. Full per-function counts and the identical counting method are in CROSS_VINTAGE_COMPLEXITY.json. We do not claim an overall line-count reduction.

The actual convergence is removal of a circular historical-loss admission dependency, one current-deployment risk owner, one continuous confirmation clock, one recovery ordering owner, terminal release of proven closed rights, and sizing from the actual shared risk cap. History and live obligations are retained.

Engineering evidence distinguishes current-candidate checks and byte-identical reuse. It includes risk owners, migration/integrity, current authority, ordinary funding, actual partial fills and cancellation, same-session idempotence, failed-grant successor authorization, lint and strict typing. Earlier timed-out broad batches are not passes; queued GitHub Actions are not passes. Full 34-LOO and full Absolute matrices are not additional task requirements absent actual protection.

Maximum single-name weight among the original scenarios remains 98.37%. Concentration and the existing frozen-data/cost-accounting limitations remain. Complete corporate-action/tax research, full-role no_optical and future out-of-sample profitability are not established. The system remains daily research and manual decision support with no live or broker trading.

Final remote preservation, current protection/check status and normal merge are recorded in the delivery receipt. Historical failures retain their own original producers and archives.
