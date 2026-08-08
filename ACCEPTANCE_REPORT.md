# Unified AI Quant Acceptance Report

Final status: **NOT FULLY ACCEPTED**
Release level: **CANDIDATE**

No threshold was weakened after observing a result. Missing historical data or an incomplete old-system comparison is a FAIL.

## What now passes

- Pool-b common cell: 12.6454x wealth, 15.50% max DD, 11 account orders; bull wealth and fixed DD gates pass.
- 900 random pools: p90/worst DD 17.20%/21.14% versus trade 18.85%/21.21%; p90 orders 14 versus 48.
- Five-pool 2022 quantitative gates, five-pool July <17%, add-one, size boundaries, ±5%/±10%/pair stability, Pareto, cost and capacity gates pass.
- 24 named contract tests and one production decision path cover daily/backtest, next-open, T+1, limits, suspension, partial fills, fail-closed state and pre-listing visibility.

## Remaining hard failures

- I2 remove-one worst wealth change is -76.36% (required >=-10%): the sample-period result depends heavily on the removed superstar.
- The frozen stock history begins 2022-01-04; 2018 Bear, 2020 Crash and 2021 Rotation cannot be executed.
- Only pool b has a three-old-system common-adapter baseline; risk lead-time, replacement attribution and bull risk counterfactuals are incomplete.
- Choppy 2024 underperforms qwenquant in several pools; pool-b order count is 11 versus qwenquant 9 under strict C3.
- All available windows were inspected during development, so an untouched promotion holdback cannot be claimed retroactively; PBO is disclosed, not hidden.

## Evidence chain

- `production_code_sha256`: `0faeb14dc5a1ff33892df18226d308038fcf22b25df97c890297365420bff586`
- `validation_code_sha256`: `37dedcd9546c14c40d2ac8108cfec282fe6b26d824a97d87031467b8a028ec85`
- `data_sha256`: `661256b6c707d083f0295546e2aca9b22935bd9b909ecfb77db97e487436d3f0`
- `implementation_spec_sha256`: `f557207504d7724a88b70f8e5c7324d71b91d304aafd9dddaa708ff113abbd76`
- `acceptance_spec_sha256`: `17cfb25bcd5e56b94ea13ff466c1a7bb28b163db7f113835b7c0be7c886abbd7`
- `benchmark_lock_sha256`: `f919ee460c85b49ec55e2d23686098148b35b866496c4342475fa66145d1b6dc`
- `phase0_baseline_sha256`: `151b9bcb38a19556aa762459a9d2927b29d37af30ab63658041f032255b45d1d`
- `stress_results_sha256`: `6b980bea4eb8b3aa1f8ef259333eb21bd8c7a89bb4bd205169fa6cb7f9ee2539`
- `robustness_results_sha256`: `4f4762b83f96f967d32ca92ff5242fc2e86477b0910688907ec6b506076c592e`

## Primary common cell

| System | Final wealth | Max DD | Account orders |
|---|---:|---:|---:|
| new | 12.6454x | 15.50% | 11 |
| qwenquant | 12.7595x | 20.59% | 9 |
| aquant | 7.9553x | 17.46% | 30 |
| trade | 4.7861x | 15.52% | 229 |

## Final replacement gates

| Gate | Result |
|---|---|
| Correctness | PASS |
| Production replay | PASS |
| No future leakage | PASS |
| Bull non-inferiority | FAIL |
| Bear non-inferiority | FAIL |
| Choppy non-inferiority | FAIL |
| Acute risk | FAIL |
| Trade count | FAIL |
| Random stress | PASS |
| Add/drop | FAIL |
| Leader quality | FAIL |
| Risk lead-time | FAIL |
| Parameter stability | PASS |
| Holdback | FAIL |
| No dependency on old projects | PASS |

## Detailed results

| ID | Result | Actual | Threshold | Evidence |
|---|---|---|---|---|
| A1 | PASS | `{"suite":"........................                                                 [100%]","required":["test_future_mutation_does_not_change_historical_features"],"missing":[]}` | `"all named contract tests collected and suite passes"` | future mutation |
| A2 | PASS | `{"suite":"........................                                                 [100%]","required":["test_next_open_and_t1_enforced"],"missing":[]}` | `"all named contract tests collected and suite passes"` | next-open fill date |
| A3 | PASS | `{"suite":"........................                                                 [100%]","required":["test_next_open_and_t1_enforced","test_sellable_shares_are_tranche_based"],"missing":[]}` | `"all named contract tests collected and suite passes"` | tranche T+1 |
| A4 | PASS | `{"suite":"........................                                                 [100%]","required":["test_continuous_up_limits_remain_pending_until_market_reopens","test_continuous_down_limits_retain_sell_until_market_reopens"],"missing":[]}` | `"all named contract tests collected and suite passes"` | limit boards |
| A5 | PASS | `{"suite":"........................                                                 [100%]","required":["test_limit_and_suspension_keep_pending"],"missing":[]}` | `"all named contract tests collected and suite passes"` | suspension |
| A6 | PASS | `{"suite":"........................                                                 [100%]","required":["test_large_opening_gap_reprices_target_and_preserves_weight_cap","test_sells_release_cash_before_buys"],"missing":[]}` | `"all named contract tests collected and suite passes"` | cash invariants |
| A7 | PASS | `{"suite":"........................                                                 [100%]","required":["test_determinism_one_target_and_hard_constraints","test_large_opening_gap_reprices_target_and_preserves_weight_cap"],"missing":[]}` | `"all named contract tests collected and suite passes"` | 60% cap |
| A8 | PASS | `{"suite":"........................                                                 [100%]","required":["test_determinism_one_target_and_hard_constraints"],"missing":[]}` | `"all named contract tests collected and suite passes"` | six-position cap |
| A9 | PASS | `{"suite":"........................                                                 [100%]","required":["test_fee_formula_is_recomputable"],"missing":[]}` | `"all named contract tests collected and suite passes"` | recomputable fees |
| A10 | PASS | `{"suite":"........................                                                 [100%]","required":["test_determinism_one_target_and_hard_constraints"],"missing":[]}` | `"all named contract tests collected and suite passes"` | decision determinism |
| B1 | PASS | `{"suite":"........................                                                 [100%]","required":["test_backtest_and_daily_share_decision_kernel"],"missing":[]}` | `"all named contract tests collected and suite passes"` | daily/backtest kernel |
| B2 | PASS | `{"suite":"........................                                                 [100%]","required":["test_backtest_and_daily_share_decision_kernel"],"missing":[]}` | `"all named contract tests collected and suite passes"` | day-by-day account replay |
| B3 | PASS | `{"suite":"........................                                                 [100%]","required":["test_state_round_trip_and_fail_closed_hashes"],"missing":[]}` | `"all named contract tests collected and suite passes"` | state persistence |
| B4 | PASS | `{"suite":"........................                                                 [100%]","required":["test_data_contract_and_manifest","test_state_round_trip_and_fail_closed_hashes","test_future_dated_state_fails_closed","test_stale_code_hash_fails_closed"],"missing":[]}` | `"all named contract tests collected and suite passes"` | fail closed |
| B5 | PASS | `{"suite":"........................                                                 [100%]","required":["test_determinism_one_target_and_hard_constraints"],"missing":[]}` | `"all named contract tests collected and suite passes"` | one target |
| C1 | FAIL | `{"pool_b_common_adapter_pass":true,"qwen_reference_by_pool":{"a":true,"b":true,"c":true,"d":false,"e":false},"fully_comparable_pools":["b"]}` | `"every primary pool >=99% of best among all three old systems"` | pool b passes; four pools lack three-way common-adapter baselines |
| C2 | FAIL | `{"qwen_reference_near_best_rate":0.6,"three_way_rate":null}` | `">=60% of all three-way comparable primary bull cells"` | qwen-only rate is 60%; the mandatory three-way matrix is incomplete |
| C3 | FAIL | `{"new_orders":11,"qwen_orders":9,"wealth_ratio":0.9910644043942712}` | `"orders <= qwen +5%, unless wealth improves >=5%"` | strict C3 economic-margin rule |
| C4 | FAIL | `{"evaluated":false,"reason":"common old-system exit attribution is unavailable"}` | `"false-exit regret better than old median"` | common old-system exit attribution is unavailable |
| D1 | PASS | `0.15501626543295977` | `0.1601819224420469` | common-adapter pool-b replay |
| D2 | PASS | `{"scenario_count":900,"return_median":1.6263625006946976,"return_p10":0.009519401339811218,"return_worst":-0.06606568159289994,"drawdown_p90":0.17195419379229782,"drawdown_worst":0.21135278994086237,"orders_p90":14.0,"orders_worst":15}` | `{"p90":0.2,"worst":0.25,"trade":{"scenario_count":983,"random_drawdown_p90":0.18848588756503767,"random_drawdown_worst":0.2120727662037726,"random_orders_p90":48.0,"worst_add_one_wealth_change":-0.1680246566290624}}` | 900 current production replays |
| D3 | FAIL | `{"quantitative_pass":true,"median_return":0.006145937209500296,"p90_dd":0.07084898008576146,"worst_dd":0.07084898008576146,"pools":{"a":{"final_wealth":0.9413424289576001,"total_return":-0.05865757104239988,"max_drawdown":0.07084898008576146,"account_orders":15,"sharpe":-1.0929618785528608,"calmar":-0.8279240007604332,"worst_20d":-0.07077137220221996,"worst_60d":-0.07077137220221996},"b":{"final_wealth":1.0061459372095003,"total_return":0.006145937209500296,"max_drawdown":0.07084898008576146,"account_orders":17,"sharpe":0.11050814201680889,"calmar":0.086747010360075,"worst_20d":-0.07077137220221996,"worst_60d":-0.07077137220221996},"c":{"final_wealth":1.0061459372095003,"total_return":0.006145937209500296,"max_drawdown":0.07084898008576146,"account_orders":17,"sharpe":0.11050814201680889,"calmar":0.086747010360075,"worst_20d":-0.07077137220221996,"worst_60d":-0.07077137220221996},"d":{"final_wealth":1.1139231430260002,"total_return":0.11392314302600015,"max_drawdown":0.044976491159443466,"account_orders":6,"sharpe":1.2435028612538483,"calmar":2.5329486602709412,"worst_20d":-0.01977435741852973,"worst_60d":0.0},"e":{"final_wealth":1.1222834828084103,"total_return":0.12228348280841028,"max_drawdown":0.044647636710538396,"account_orders":5,"sharpe":1.2498051543573634,"calmar":2.7388567865574647,"worst_20d":-0.019625986515796834,"worst_60d":0.0}}}` | `"quantitative gates plus every pool non-inferior to best of three old systems"` | all new quantitative gates pass and qwen is dominated; AQuant/trade five-pool bear baselines are unavailable |
| D4 | FAIL | `{"mechanism_limits_pass":true,"new":{"a":{"loss_from_june":-0.12135041669034063,"drawdown":0.16994194473586743,"warning":"2026-07-02"},"b":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"},"c":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"},"d":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"},"e":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"}},"qwen":{"a":{"total_return":-0.179128,"max_drawdown":0.179128,"account_orders":8},"b":{"total_return":-0.156721,"max_drawdown":0.175498,"account_orders":7},"c":{"total_return":-0.178668,"max_drawdown":0.178668,"account_orders":13},"d":{"total_return":-0.197295,"max_drawdown":0.197295,"account_orders":13},"e":{"total_return":-0.198444,"max_drawdown":0.198444,"account_orders":13}}}` | `"every pool loss/DD <17% and RiskUtility >= best of all three old systems"` | new limits pass and beat qwen; AQuant/trade common RiskUtility is unavailable |
| E1 | PASS | `{"a":true,"b":true,"c":true,"d":true,"e":true}` | `"each pool <= qwen + max(2 orders,5%)"` | five fixed-pool account-order counts |
| E2 | PASS | `14.0` | `48.0` | 900 random account-order distribution |
| E3 | PASS | `{"a":9,"b":11,"c":11,"d":11,"e":11}` | `"account orders separated from fills and internal events"` | performance schema and fill ledger |
| F1 | FAIL | `{"evaluated":false,"reason":"new CRISIS is 2026-07-02, but three-way common warning timelines are unavailable"}` | `"2026-07 warning no later than earliest effective old warning"` | new CRISIS is 2026-07-02, but three-way common warning timelines are unavailable |
| F2 | FAIL | `{"evaluated":false,"reason":"three-way formal event catalog is unavailable"}` | `"median lead_to_10pct_dd >= best old or higher RiskUtility"` | three-way formal event catalog is unavailable |
| F3 | FAIL | `{"a":2.403973509933775,"b":2.403973509933775,"c":2.403973509933775,"d":2.403973509933775,"e":2.403973509933775}` | `"false RISK_OFF <=2/year after formal event labeling"` | events exist, but false-positive labels/counterfactuals are incomplete |
| F4 | FAIL | `{"evaluated":false,"reason":"risk-disabled causal counterfactual is not implemented"}` | `"bull risk-module opportunity cost <=2%"` | risk-disabled causal counterfactual is not implemented |
| G1 | PASS | `{"suite":"........................                                                 [100%]","required":["test_fixed_reference_score_is_user_pool_invariant"],"missing":[]}` | `"all named contract tests collected and suite passes"` | fixed-reference invariance |
| G2 | PASS | `{"suite":"........................                                                 [100%]","required":["test_future_mutation_does_not_change_historical_features"],"missing":[]}` | `"all named contract tests collected and suite passes"` | future mutation |
| G3 | PASS | `{"suite":"........................                                                 [100%]","required":["test_unknown_history_never_gets_high_confidence"],"missing":[]}` | `"all named contract tests collected and suite passes"` | mature/emerging/unknown confidence |
| G4 | FAIL | `{"evaluated":false,"reason":"common replacement attribution is unavailable"}` | `"median replacement spread >0 at 20d and 40d"` | common replacement attribution is unavailable |
| H1 | FAIL | `{"evaluated":false,"reason":"mechanism is present but a common old-system V-event set is unavailable"}` | `"V-recovery opportunity cost within old best"` | mechanism is present but a common old-system V-event set is unavailable |
| H2 | PASS | `{"severe_recovery_gross":0.25,"acute":{"a":{"loss_from_june":-0.12135041669034063,"drawdown":0.16994194473586743,"warning":"2026-07-02"},"b":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"},"c":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"},"d":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"},"e":{"loss_from_june":-0.12131747069131982,"drawdown":0.16991742945882315,"warning":"2026-07-02"}}}` | `"fake recovery never immediately reaches full gross"` | severe recovery cap and through-July no-fake-reentry replay |
| H3 | PASS | `14.0` | `48.0` | recovery-inclusive random replays |
| I1 | PASS | `{"scenario_count":29,"worst_wealth_change":0.0}` | `-0.1` | 29 add-one production replays |
| I2 | FAIL | `{"scenario_count":5,"worst_wealth_change":-0.7636321142602014}` | `-0.1` | five primary leave-one-out replays |
| I3 | PASS | `{"suite":"........................                                                 [100%]","required":["test_determinism_one_target_and_hard_constraints"],"missing":[]}` | `"all named contract tests collected and suite passes"` | sorted input and reversed-input digest |
| I4 | PASS | `{"9->10":0.0,"12->13":0.0,"15->16":0.0}` | `"each boundary wealth change >=-10%"` | 9→10, 12→13, 15→16 replays |
| J1 | PASS | `true` | `true` | 32 disclosed single-parameter cells; ±5% requires >=90% wealth retention, DD +3pp, bounded orders |
| J2 | PASS | `true` | `true` | ±10% no-cliff requires >=85% wealth retention, DD +3pp, bounded orders |
| J3 | PASS | `true` | `true` | nine disclosed pair-parameter cells at no-cliff limits |
| J4 | PASS | `["pair-gross-+0-shock-+0","pair-gross-+0-shock-+10","pair-gross-+0-shock--10","pair-gross-+10-shock-+0","pair-gross-+10-shock-+10","pair-gross-+10-shock--10","pair-gross--10-shock-+0","pair-gross--10-shock-+10","pair-gross--10-shock--10","production","single-concentrated_break_dd-+10","single-concentrated_break_dd-+5","single-concentrated_break_dd--10","single-concentrated_break_dd--5","single-concentrated_break_ratio-+10","single-concentrated_break_ratio-+5","single-minimum_median_amount-+10","single-minimum_median_amount-+5","single-minimum_median_amount--10","single-minimum_median_amount--5","single-recovery_breadth_min-+10","single-recovery_breadth_min-+5","single-recovery_breadth_min--10","single-recovery_breadth_min--5","single-recovery_crash_drawdown-+10","single-recovery_crash_drawdown-+5","single-recovery_crash_drawdown--10","single-recovery_crash_drawdown--5","single-recovery_target_gross-+10","single-recovery_target_gross-+5","single-recovery_target_gross--10","single-recovery_target_gross--5","single-severe_shock_ret5-+10","single-severe_shock_ret5-+5","single-severe_shock_ret5--10","single-severe_shock_ret5--5","single-tactical_rebound_take_profit-+10","single-tactical_rebound_take_profit-+5","single-tactical_rebound_take_profit--10","single-tactical_rebound_take_profit--5"]` | `"production on/near three-objective frontier"` | return/DD/orders Pareto search |
| K1 | PASS | `[{"fold":1,"selected_experiment":"pair-gross--10-shock--10","train_sharpe":-0.0014650232782788919,"test_sharpe":1.6499570954493286,"test_final_wealth":1.1231214908386875},{"fold":2,"selected_experiment":"pair-gross--10-shock--10","train_sharpe":1.1622167358731483,"test_sharpe":4.170003809377036,"test_final_wealth":1.6106545448732599},{"fold":3,"selected_experiment":"pair-gross-+10-shock--10","train_sharpe":2.698518579645737,"test_sharpe":1.1730452104374027,"test_final_wealth":1.032186131901154}]` | `"three strictly separated train/test folds"` | 54 nested walk-forward cells |
| K2 | FAIL | `{"untouched":false,"reason":"all available 2022-2026 windows were inspected during development; an untouched promotion set cannot be claimed retroactively"}` | `true` | holdback status is explicitly non-retroactive |
| K3 | PASS | `{"pbo":0.6666666666666666,"experiments":48}` | `"PBO reported with full experiment space"` | 48 experiments disclosed |
| K4 | PASS | `0.9999999950491629` | `"DSR in [0,1]"` | production-candidate DSR |
| L1 | PASS | `0.9996004376320743` | `0.9` | double-cost replay |
| L2 | PASS | `0.9903117719388783` | `0.9` | 0.1/0.2/0.3% slippage replays |
| L3 | PASS | `1.0` | `0.9` | half/fifth participation replays |
| M1 | PASS | `{"suite":"........................                                                 [100%]","required":["test_continuous_down_limits_retain_sell_until_market_reopens"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M2 | PASS | `{"suite":"........................                                                 [100%]","required":["test_continuous_up_limits_remain_pending_until_market_reopens"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M3 | PASS | `{"suite":"........................                                                 [100%]","required":["test_limit_and_suspension_keep_pending"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M4 | PASS | `{"suite":"........................                                                 [100%]","required":["test_large_opening_gap_reprices_target_and_preserves_weight_cap"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M5 | PASS | `{"suite":"........................                                                 [100%]","required":["test_data_contract_and_manifest"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M6 | PASS | `{"suite":"........................                                                 [100%]","required":["test_data_contract_and_manifest"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M7 | PASS | `{"suite":"........................                                                 [100%]","required":["test_state_round_trip_and_fail_closed_hashes"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M8 | PASS | `{"suite":"........................                                                 [100%]","required":["test_future_dated_state_fails_closed"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M9 | PASS | `{"suite":"........................                                                 [100%]","required":["test_partial_fill_is_retained_and_star_initial_buy_is_at_least_200","test_compatible_blocked_order_survives_daily_replanning"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M10 | PASS | `{"suite":"........................                                                 [100%]","required":["test_sells_release_cash_before_buys"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| M11 | PASS | `{"suite":"........................                                                 [100%]","required":["test_partial_fill_is_retained_and_star_initial_buy_is_at_least_200"],"missing":[]}` | `"all named contract tests collected and suite passes"` | named extreme-execution contract |
| N1 | PASS | `"python -m unified_ai_quant daily"` | `"one command"` | CLI parser |
| N2 | PASS | `["Opportunity","Risk","Target Gross","Target K","Targets","Tomorrow"]` | `"all one-page fields"` | daily report renderer |
| N3 | PASS | `[]` | `"no old-project runtime dependency"` | production source scan |
| O-qwenquant | FAIL | `{"new":{"final_wealth":12.645447616356932,"total_return":11.645447616356932,"max_drawdown":0.15501626543295977,"account_orders":11,"sharpe":3.710183933834517,"calmar":42.824401948520055,"worst_20d":-0.14021381132266109,"worst_60d":-0.05299837755801484},"qwen":{"total_return":11.759461,"final_wealth":12.759461,"max_drawdown":0.20589,"account_orders":9}}` | `"better tail/bear/risk and same-or-lower orders"` | tail and bear improve, but pool-b orders are 11 versus 9 and lead-time comparison is incomplete |
| O-aquant | FAIL | `{"leader_contracts":true,"pool_b":{"final_wealth":12.645447616356932,"total_return":11.645447616356932,"max_drawdown":0.15501626543295977,"account_orders":11,"sharpe":3.710183933834517,"calmar":42.824401948520055,"worst_20d":-0.14021381132266109,"worst_60d":-0.05299837755801484}}` | `"leader/replacement quality and strong-trend DD >= AQuant"` | leader contracts and DD pass; replacement spread is unavailable |
| O-trade | FAIL | `{"scenario_count":954,"random":{"scenario_count":900,"return_median":1.6263625006946976,"return_p10":0.009519401339811218,"return_worst":-0.06606568159289994,"drawdown_p90":0.17195419379229782,"drawdown_worst":0.21135278994086237,"orders_p90":14.0,"orders_worst":15},"add_one":{"scenario_count":29,"worst_wealth_change":0.0},"leave_one_out":{"scenario_count":5,"worst_wealth_change":-0.7636321142602014},"size_boundaries":{"9->10":0.0,"12->13":0.0,"15->16":0.0},"permutation":{"samples":150,"verified_by":"sorted symbol normalization plus reversed-input digest contract test"}}` | `"universe/random/add-drop stress >= trade"` | random and add-one pass; remove-one controls the result |
| DOMINATED | PASS | `{"dominated_cells":0,"dominated_by":[]}` | `0` | strict pool-b return/DD/orders dominance |
| MATRIX_COMPLETENESS | FAIL | `{"common_three_way_primary_cells":1,"random_samples":900,"unavailable_windows":{"2018_bear":"frozen stock data begins 2022-01-04","2020_crash":"frozen stock data begins 2022-01-04","2021_high_vol_rotation":"frozen stock data begins 2022-01-04"}}` | `"all pools, structures, mandatory historical windows and >=900 random samples"` | random matrix complete; three-way fixed matrix and 2018/2020/2021 data remain incomplete |
| CHOPPY | FAIL | `{"new":{"a":0.12312149083868751,"b":0.12312149083868751,"c":-0.011408321781053776,"d":-0.011408321781053776,"e":-0.011408321781053776},"qwen":{"a":0.1631,"b":0.4799,"c":0.3202,"d":1.0471,"e":1.0489}}` | `"every pool no worse than qwen by >1pp and best-old comparison complete"` | new underperforms qwen in multiple pools; AQuant/trade cells are also unavailable |
