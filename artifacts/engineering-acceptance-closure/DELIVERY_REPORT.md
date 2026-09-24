# 工程验收收尾：57 项失败逐项结案

基线 `17e9e12881e861825a4a1a9145f3a54d812c7523` 的原始 57 项失败已在本轮独立复现；同一 nodeid 清单的最终运行 `closure-final-initial-57` 为 **57/57 通过**。下表每行保留原 nodeid、根因和同一最终运行回执；没有删除、跳过或改名。

## 根因、职责与修复

| 代号 | 数量 | 类别、观察及保留的职责 | 修复位置 |
| --- | ---: | --- | --- |
| F1 | 53 | 测试数据仍按成交当日成交量制造部分／零成交，生产链已按前一完成交易日成交量与成交额推定下一开盘容量；部分预设未发生。停牌、涨幅夹具也使用了开盘时无效的条件。保留同一订单的余量、资金和份额、成交与持仓归因、撤单／恢复、风险 SELL 优先级及重启连续性。 | `tests/_causal_execution_fixtures.py`、相关测试、`research/execution_stress.py`；调整前一日量价金额并继续检查原生账本 |
| M1 | 2 | 旧断言固定要求某一天成交或要求最初请求股数永不因手续费重定目标；实际多日逐笔成交及同一订单的修订请求量都自洽。保留成交跨日、原订单身份、成交量与未成交量精确对账。 | `tests/test_cross_ai_strategy.py`、`tests/test_early_cohort_capital.py` |
| A1 | 1 | 因果开盘容量及风险未满足状态使 `_size_open_order` 超出现有复杂度预算。 | `uquant/execution/open_execution.py` 提取前一日容量与风险缺口两个现有计算，不改变预算 |
| A2 | 1 | 执行迁移后 `_blocked` 的旧冻结 AST 身份不再是已审查实现。 | `tests/architecture/_execution_application_transport.py` 对 PR #85 指定源码及单一定义 AST 摘要作机械绑定 |

上述 57 项中未发现须改变经济行为的真实生产执行缺陷。另有两个不同于 pytest 57 项的根因：R1 为历史回放脚本中三处 Ruff 导入／单行语句格式问题，已在该脚本修复；C1 为 Ownership 冠军复用旧 `23.28417871275582×` 底线，已改为与 Absolute 共用现行 `principal_wealth_floor` 的 `15×`。Ownership 判定先复制来源违规列表，再追加本地违规，避免共享列表原位迭代。冻结合同与历史原件均未改写。合计 **6 类独立基线根因**（F1、M1、A1、A2、R1、C1）；无本轮新增策略候选。

## 初始失败逐项对账

| # | 原始 pytest nodeid | 根因 | 结案回执 |
| ---: | --- | --- | --- |
| 1 | `tests/architecture/test_complexity_budgets.py::test_module_and_function_debt_is_exact_non_growing_and_monotonic` | A1 | `closure-final-initial-57`: PASS |
| 2 | `tests/architecture/test_execution_application_boundaries.py::test_execution_moved_definitions_are_mechanically_bound_to_immutable_source` | A2 | `closure-final-initial-57`: PASS |
| 3 | `tests/test_native_execution_stress.py::test_execution_stresses_use_native_next_open_and_preserve_blocked_orders` | F1 | `closure-final-initial-57`: PASS |
| 4 | `tests/test_ordinary_cash_rearm.py::test_partial_ordinary_rearm_keeps_exact_order_and_unfilled_quantity_after_restart` | F1 | `closure-final-initial-57`: PASS |
| 5 | `tests/test_ordinary_cash_rearm.py::test_partial_attempt_losing_current_quality_is_cancelled_without_reopening_capital` | F1 | `closure-final-initial-57`: PASS |
| 6 | `tests/test_ordinary_pullback_execution.py::test_partial_loses_current_proof_cancels_and_cannot_revive_after_restart` | F1 | `closure-final-initial-57`: PASS |
| 7 | `tests/test_ordinary_pullback_execution.py::test_current_partial_permission_keeps_the_original_order_then_really_finishes` | F1 | `closure-final-initial-57`: PASS |
| 8 | `tests/test_ordinary_pullback_execution.py::test_smaller_cap_cancels_remainder_instead_of_replacing_it_with_an_unproven_buy` | F1 | `closure-final-initial-57`: PASS |
| 9 | `tests/test_ordinary_pullback_lifecycle.py::test_fifo_removing_first_fill_keeps_continuous_original_proof_after_restart` | F1 | `closure-final-initial-57`: PASS |
| 10 | `tests/test_ordinary_self_credible.py::test_partial_current_credible_continues_then_score_loss_cancels_without_restart_revival` | F1 | `closure-final-initial-57`: PASS |
| 11 | `tests/test_ordinary_trend_budget.py::test_ordinary_partial_loses_common_permission_and_cannot_revive_after_strict_restart` | F1 | `closure-final-initial-57`: PASS |
| 12 | `tests/test_ordinary_trend_budget.py::test_real_consumed_repair_partial_cannot_use_mature_common_permission` | F1 | `closure-final-initial-57`: PASS |
| 13 | `tests/test_ordinary_trend_capital.py::test_existing_ordinary_capital_reserves_weak_market_allowance[partial]` | F1 | `closure-final-initial-57`: PASS |
| 14 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[FIFO-0.29-False]` | F1 | `closure-final-initial-57`: PASS |
| 15 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[FIFO-0.31-False]` | F1 | `closure-final-initial-57`: PASS |
| 16 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[FIFO-0.0-True]` | F1 | `closure-final-initial-57`: PASS |
| 17 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[RISK_PRIORITY-0.31-False]` | F1 | `closure-final-initial-57`: PASS |
| 18 | `tests/test_pending_attribution_continuation_regression.py::test_supported_partial_sell_continuation_retains_the_exact_native_intent[FIFO-0.3]` | F1 | `closure-final-initial-57`: PASS |
| 19 | `tests/test_pending_attribution_continuation_regression.py::test_supported_partial_sell_continuation_retains_the_exact_native_intent[RISK_PRIORITY-0.29]` | F1 | `closure-final-initial-57`: PASS |
| 20 | `tests/test_pending_attribution_continuation_regression.py::test_qualified_partial_buy_neighbour_keeps_native_order_and_fill_identity` | F1 | `closure-final-initial-57`: PASS |
| 21 | `tests/test_persistent_formation.py::test_partial_formation_keeps_fills_and_revokes_invalid_owner_remainder` | F1 | `closure-final-initial-57`: PASS |
| 22 | `tests/test_realized_strategic_ownership.py::test_first_real_owner_buy_activates_ownership_without_completing_grant[partial-first-order]` | F1 | `closure-final-initial-57`: PASS |
| 23 | `tests/test_realized_strategic_ownership.py::test_native_submitted_zero_fill_does_not_activate_ownership` | F1 | `closure-final-initial-57`: PASS |
| 24 | `tests/test_realized_strategic_ownership.py::test_same_session_partial_receipts_do_not_complete_deployment` | F1 | `closure-final-initial-57`: PASS |
| 25 | `tests/test_repair_capital_custody.py::test_current_full_certificate_can_use_free_capital_beside_repair_holding[True]` | F1 | `closure-final-initial-57`: PASS |
| 26 | `tests/test_shared_core_qualification.py::test_partial_common_core_continuation_uses_the_same_current_certificate[full-still-ready]` | F1 | `closure-final-initial-57`: PASS |
| 27 | `tests/test_shared_core_qualification.py::test_partial_common_core_continuation_uses_the_same_current_certificate[all-routes-lost]` | F1 | `closure-final-initial-57`: PASS |
| 28 | `tests/test_single_immature_core.py::test_partial_price_drift_and_restart_keep_early_slot_occupied` | F1 | `closure-final-initial-57`: PASS |
| 29 | `tests/test_strategic_core_profit_lock.py::test_incomplete_core_entry_cannot_arm_profit_lock[partial]` | F1 | `closure-final-initial-57`: PASS |
| 30 | `tests/test_strategic_core_structural_exit.py::test_confirmed_core_structure_exit_keeps_native_identity_until_final_fill[-0.09]` | F1 | `closure-final-initial-57`: PASS |
| 31 | `tests/test_strategic_core_structural_exit.py::test_confirmed_core_structure_exit_keeps_native_identity_until_final_fill[0.03]` | F1 | `closure-final-initial-57`: PASS |
| 32 | `tests/test_strategic_core_structural_exit.py::test_core_exit_preserves_existing_conjunction_and_entry_completion[partial-entry]` | F1 | `closure-final-initial-57`: PASS |
| 33 | `tests/test_strategic_epoch_settlement_session.py::test_real_core_liquidation_uses_current_settlement_session_across_restart[actual-partial]` | F1 | `closure-final-initial-57`: PASS |
| 34 | `tests/test_strategic_exit_band_settlement.py::test_settled_soft_plan_does_not_block_exit_escalation[post_guard]` | F1 | `closure-final-initial-57`: PASS |
| 35 | `tests/test_strategic_exit_band_settlement.py::test_native_broker_buy_origin_settles_fifo_sale[broker-lot]` | F1 | `closure-final-initial-57`: PASS |
| 36 | `tests/test_strategic_exit_band_settlement.py::test_native_broker_buy_origin_settles_fifo_sale[broker-split-lot]` | F1 | `closure-final-initial-57`: PASS |
| 37 | `tests/test_strategic_flat_retirement.py::test_oldest_initial_lot_can_disappear_without_retiring_survivor` | F1 | `closure-final-initial-57`: PASS |
| 38 | `tests/test_strategic_flat_retirement.py::test_retired_restoration_remainder_never_revives_but_real_liability_survives[in-flight]` | F1 | `closure-final-initial-57`: PASS |
| 39 | `tests/test_strategic_flat_retirement.py::test_retired_restoration_remainder_never_revives_but_real_liability_survives[legacy-late]` | F1 | `closure-final-initial-57`: PASS |
| 40 | `tests/test_strategic_flat_retirement.py::test_native_fifo_consumes_oldest_lot_without_retiring_continuous_member` | F1 | `closure-final-initial-57`: PASS |
| 41 | `tests/test_strategic_probe_holding.py::test_entry_quality_loss_retains_actual_probe_holding[partial]` | F1 | `closure-final-initial-57`: PASS |
| 42 | `tests/test_strategic_probe_holding.py::test_cancelled_partial_never_rearms_after_restart_and_risk_recovery` | F1 | `closure-final-initial-57`: PASS |
| 43 | `tests/test_strategic_probe_holding.py::test_late_fill_accounts_for_shares_without_reviving_expired_grant[in-flight-cancel]` | F1 | `closure-final-initial-57`: PASS |
| 44 | `tests/test_strategic_probe_holding.py::test_late_fill_accounts_for_shares_without_reviving_expired_grant[cancel-completed]` | F1 | `closure-final-initial-57`: PASS |
| 45 | `tests/test_strategic_probe_holding.py::test_partial_qualification_expiry_preserves_prior_lower_holding_target` | F1 | `closure-final-initial-57`: PASS |
| 46 | `tests/test_unified_core_book.py::test_partial_core_retry_keeps_one_order_and_event_after_restart` | F1 | `closure-final-initial-57`: PASS |
| 47 | `tests/test_unified_core_book.py::test_partial_ordinary_restore_survives_restart_without_new_entry_qualification` | F1 | `closure-final-initial-57`: PASS |
| 48 | `tests/test_attribution_identity.py::test_native_partial_multilot_chain_round_trips` | F1 | `closure-final-initial-57`: PASS |
| 49 | `tests/test_attribution_identity.py::test_native_live_tranche_event_must_chain_to_originating_buy` | F1 | `closure-final-initial-57`: PASS |
| 50 | `tests/test_confirmed_recovery_pipeline.py::test_real_partial_recovery_keeps_fills_and_cancels_lost_current_proof` | F1 | `closure-final-initial-57`: PASS |
| 51 | `tests/test_core_transfer_attribution.py::test_unsettled_rotation_cannot_label_a_buy_funded_by_another_sell[open_unfilled]` | F1 | `closure-final-initial-57`: PASS |
| 52 | `tests/test_cross_ai_strategy.py::test_small_champion_replay_uses_real_next_open_and_sealed_evidence` | M1 | `closure-final-initial-57`: PASS |
| 53 | `tests/test_early_cohort_capital.py::test_partial_founding_buy_keeps_its_cap_and_identity_after_restart` | M1 | `closure-final-initial-57`: PASS |
| 54 | `tests/test_execution.py::test_limit_and_suspension_keep_pending` | F1 | `closure-final-initial-57`: PASS |
| 55 | `tests/test_execution.py::test_large_opening_gap_reprices_target_and_preserves_weight_cap` | F1 | `closure-final-initial-57`: PASS |
| 56 | `tests/test_independent_market_confirmation.py::test_market_confirmation_loss_cancels_native_partial_buy_but_keeps_real_holding` | F1 | `closure-final-initial-57`: PASS |
| 57 | `tests/test_lifecycle_and_risk.py::test_frozen_strategic_member_preserves_partial_sell_identity_and_cancels_buy` | F1 | `closure-final-initial-57`: PASS |

## 校验与证据范围

- 初始失败原始 stdout SHA-256：`e18aad4f3e1a93c2468300aaddeb40f9a8ecc446a8f33a197db428441e497e1b`；原 nodeid 清单 SHA-256：`7292db24c69cdfc29675d65492dbb690ddf233f70449dbd7f9c3f037caca08aa`。最终 57 项 stdout SHA-256：`5603b3bdbd979d86e8c62be43291039331c1f18d392c470cba56f4eba489b84d`。原日志保存在私有证据存储，不提交公开源码仓库。
- 相同 11 项原生执行压力场景，基线与候选结果的字节 SHA-256 均为 `2867aff5d5e1bc737ab386b6ca29f551fed484c718f9feafd8f20362166ffc41`；该对照仅覆盖执行重构，不能冒充完整 Absolute 回放。
- Ruff 仓库全量检查：通过；当前架构、Ownership 模型与冠军原生分片结果见本轮最终回执。
- 正式候选 C 八片和 final 的原始生产者是 `115a151761f458da056b6118fa41a2339ec49914`，final SHA-256 `3a7c66fc3429aa9e7f9de18ee70cec749c449a49d2bc46a8f214320a2deb9ef3`；八片合计 5,259,150,004 字节，已保存原件。执行重构只在上述压力账户有逐字节配对；若当前身份合同要求完全同源，不把旧分片改贴本次提交身份。
