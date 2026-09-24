# 工程验收收尾：57 项失败逐项结案

基线 `17e9e12881e861825a4a1a9145f3a54d812c7523` 的原始 57 项失败已在本轮独立复现；同一 nodeid 清单在锁定环境中的最终运行 `closure-final-57-junit-readback` 为 **57/57 通过**。JUnit 逐一核对了原 nodeid，失败、错误、跳过均为零。下表每行保留原 nodeid、根因和最终运行回执；没有删除、跳过或改名。

## 根因、职责与修复

| 代号 | 数量 | 类别、观察及保留的职责 | 修复位置 |
| --- | ---: | --- | --- |
| F1 | 53 | 测试数据仍按成交当日成交量制造部分／零成交，生产链已按前一完成交易日成交量与成交额推定下一开盘容量；部分预设未发生。停牌、涨幅夹具也使用了开盘时无效的条件。保留同一订单的余量、资金和份额、成交与持仓归因、撤单／恢复、风险 SELL 优先级及重启连续性。 | `tests/_causal_execution_fixtures.py`、相关测试、`research/execution_stress.py`；调整前一日量价金额并继续检查原生账本 |
| F2 | 1 | 手工构造的风险 SELL 待单缺少原生归因身份，规划器正确拒绝非法来源；补齐合法身份后仍检查部分卖出保留同一订单、买单被取消。 | `tests/_lifecycle_freeze_execution_cases.py` |
| M1 | 1 | 旧断言固定要求冠军短回放某一天成交；实际同一订单跨日逐笔成交。保留成交跨日及原订单身份。另在修正 F1 的早期 cohort 夹具后，按真实手续费重定后的请求量精确核对成交量与未成交量。 | `tests/test_cross_ai_strategy.py`、`tests/test_early_cohort_capital.py` |
| A1 | 1 | 因果开盘容量及风险未满足状态使 `_size_open_order` 超出现有复杂度预算。 | `uquant/execution/open_execution.py` 提取前一日容量与风险缺口两个现有计算，不改变预算 |
| A2 | 1 | 执行迁移后 `_blocked` 的旧冻结 AST 身份不再是已审查实现。 | `tests/architecture/_execution_application_transport.py` 对 PR #85 指定源码及单一定义 AST 摘要作机械绑定 |

上述 57 项中未发现须改变经济行为的真实生产执行缺陷。另有两个不同于 pytest 57 项的根因：R1 为历史回放脚本中三处 Ruff 导入／单行语句格式问题，已在该脚本修复；C1 为 Ownership 冠军复用旧 `23.28417871275582×` 底线，已改为与 Absolute 共用现行 `principal_wealth_floor` 的 `15×`。Ownership 判定先复制来源违规列表，再追加本地违规，避免共享列表原位迭代。冻结合同与历史原件均未改写。合计 **7 类独立基线根因**（F1、F2、M1、A1、A2、R1、C1）；无本轮新增策略候选。PR 初次运行的严格类型检查另发现拆分后多余的 `pd.Series` 转换；已删除并在最终源码上通过 331 个源文件的 mypy 检查。

C1 是真实验收判定缺陷，不是第二条账户产生了不同收益；本轮没有发现需要变更经济策略的执行缺陷。冠军边界夹具分别验证低于 15× 拒绝、恰好 15× 通过及高于 15× 通过；错误来源、账本和其他违规仍由原验证器拒绝。

## 初始失败逐项对账

| # | 原始 pytest nodeid | 根因 | 结案回执 |
| ---: | --- | --- | --- |
| 1 | `tests/architecture/test_complexity_budgets.py::test_module_and_function_debt_is_exact_non_growing_and_monotonic` | A1 | `closure-final-57-junit-readback`: PASS |
| 2 | `tests/architecture/test_execution_application_boundaries.py::test_execution_moved_definitions_are_mechanically_bound_to_immutable_source` | A2 | `closure-final-57-junit-readback`: PASS |
| 3 | `tests/test_native_execution_stress.py::test_execution_stresses_use_native_next_open_and_preserve_blocked_orders` | F1 | `closure-final-57-junit-readback`: PASS |
| 4 | `tests/test_ordinary_cash_rearm.py::test_partial_ordinary_rearm_keeps_exact_order_and_unfilled_quantity_after_restart` | F1 | `closure-final-57-junit-readback`: PASS |
| 5 | `tests/test_ordinary_cash_rearm.py::test_partial_attempt_losing_current_quality_is_cancelled_without_reopening_capital` | F1 | `closure-final-57-junit-readback`: PASS |
| 6 | `tests/test_ordinary_pullback_execution.py::test_partial_loses_current_proof_cancels_and_cannot_revive_after_restart` | F1 | `closure-final-57-junit-readback`: PASS |
| 7 | `tests/test_ordinary_pullback_execution.py::test_current_partial_permission_keeps_the_original_order_then_really_finishes` | F1 | `closure-final-57-junit-readback`: PASS |
| 8 | `tests/test_ordinary_pullback_execution.py::test_smaller_cap_cancels_remainder_instead_of_replacing_it_with_an_unproven_buy` | F1 | `closure-final-57-junit-readback`: PASS |
| 9 | `tests/test_ordinary_pullback_lifecycle.py::test_fifo_removing_first_fill_keeps_continuous_original_proof_after_restart` | F1 | `closure-final-57-junit-readback`: PASS |
| 10 | `tests/test_ordinary_self_credible.py::test_partial_current_credible_continues_then_score_loss_cancels_without_restart_revival` | F1 | `closure-final-57-junit-readback`: PASS |
| 11 | `tests/test_ordinary_trend_budget.py::test_ordinary_partial_loses_common_permission_and_cannot_revive_after_strict_restart` | F1 | `closure-final-57-junit-readback`: PASS |
| 12 | `tests/test_ordinary_trend_budget.py::test_real_consumed_repair_partial_cannot_use_mature_common_permission` | F1 | `closure-final-57-junit-readback`: PASS |
| 13 | `tests/test_ordinary_trend_capital.py::test_existing_ordinary_capital_reserves_weak_market_allowance[partial]` | F1 | `closure-final-57-junit-readback`: PASS |
| 14 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[FIFO-0.29-False]` | F1 | `closure-final-57-junit-readback`: PASS |
| 15 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[FIFO-0.31-False]` | F1 | `closure-final-57-junit-readback`: PASS |
| 16 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[FIFO-0.0-True]` | F1 | `closure-final-57-junit-readback`: PASS |
| 17 | `tests/test_pending_attribution_continuation_regression.py::test_changed_partial_sell_gets_a_new_canonical_event[RISK_PRIORITY-0.31-False]` | F1 | `closure-final-57-junit-readback`: PASS |
| 18 | `tests/test_pending_attribution_continuation_regression.py::test_supported_partial_sell_continuation_retains_the_exact_native_intent[FIFO-0.3]` | F1 | `closure-final-57-junit-readback`: PASS |
| 19 | `tests/test_pending_attribution_continuation_regression.py::test_supported_partial_sell_continuation_retains_the_exact_native_intent[RISK_PRIORITY-0.29]` | F1 | `closure-final-57-junit-readback`: PASS |
| 20 | `tests/test_pending_attribution_continuation_regression.py::test_qualified_partial_buy_neighbour_keeps_native_order_and_fill_identity` | F1 | `closure-final-57-junit-readback`: PASS |
| 21 | `tests/test_persistent_formation.py::test_partial_formation_keeps_fills_and_revokes_invalid_owner_remainder` | F1 | `closure-final-57-junit-readback`: PASS |
| 22 | `tests/test_realized_strategic_ownership.py::test_first_real_owner_buy_activates_ownership_without_completing_grant[partial-first-order]` | F1 | `closure-final-57-junit-readback`: PASS |
| 23 | `tests/test_realized_strategic_ownership.py::test_native_submitted_zero_fill_does_not_activate_ownership` | F1 | `closure-final-57-junit-readback`: PASS |
| 24 | `tests/test_realized_strategic_ownership.py::test_same_session_partial_receipts_do_not_complete_deployment` | F1 | `closure-final-57-junit-readback`: PASS |
| 25 | `tests/test_repair_capital_custody.py::test_current_full_certificate_can_use_free_capital_beside_repair_holding[True]` | F1 | `closure-final-57-junit-readback`: PASS |
| 26 | `tests/test_shared_core_qualification.py::test_partial_common_core_continuation_uses_the_same_current_certificate[full-still-ready]` | F1 | `closure-final-57-junit-readback`: PASS |
| 27 | `tests/test_shared_core_qualification.py::test_partial_common_core_continuation_uses_the_same_current_certificate[all-routes-lost]` | F1 | `closure-final-57-junit-readback`: PASS |
| 28 | `tests/test_single_immature_core.py::test_partial_price_drift_and_restart_keep_early_slot_occupied` | F1 | `closure-final-57-junit-readback`: PASS |
| 29 | `tests/test_strategic_core_profit_lock.py::test_incomplete_core_entry_cannot_arm_profit_lock[partial]` | F1 | `closure-final-57-junit-readback`: PASS |
| 30 | `tests/test_strategic_core_structural_exit.py::test_confirmed_core_structure_exit_keeps_native_identity_until_final_fill[-0.09]` | F1 | `closure-final-57-junit-readback`: PASS |
| 31 | `tests/test_strategic_core_structural_exit.py::test_confirmed_core_structure_exit_keeps_native_identity_until_final_fill[0.03]` | F1 | `closure-final-57-junit-readback`: PASS |
| 32 | `tests/test_strategic_core_structural_exit.py::test_core_exit_preserves_existing_conjunction_and_entry_completion[partial-entry]` | F1 | `closure-final-57-junit-readback`: PASS |
| 33 | `tests/test_strategic_epoch_settlement_session.py::test_real_core_liquidation_uses_current_settlement_session_across_restart[actual-partial]` | F1 | `closure-final-57-junit-readback`: PASS |
| 34 | `tests/test_strategic_exit_band_settlement.py::test_settled_soft_plan_does_not_block_exit_escalation[post_guard]` | F1 | `closure-final-57-junit-readback`: PASS |
| 35 | `tests/test_strategic_exit_band_settlement.py::test_native_broker_buy_origin_settles_fifo_sale[broker-lot]` | F1 | `closure-final-57-junit-readback`: PASS |
| 36 | `tests/test_strategic_exit_band_settlement.py::test_native_broker_buy_origin_settles_fifo_sale[broker-split-lot]` | F1 | `closure-final-57-junit-readback`: PASS |
| 37 | `tests/test_strategic_flat_retirement.py::test_oldest_initial_lot_can_disappear_without_retiring_survivor` | F1 | `closure-final-57-junit-readback`: PASS |
| 38 | `tests/test_strategic_flat_retirement.py::test_retired_restoration_remainder_never_revives_but_real_liability_survives[in-flight]` | F1 | `closure-final-57-junit-readback`: PASS |
| 39 | `tests/test_strategic_flat_retirement.py::test_retired_restoration_remainder_never_revives_but_real_liability_survives[legacy-late]` | F1 | `closure-final-57-junit-readback`: PASS |
| 40 | `tests/test_strategic_flat_retirement.py::test_native_fifo_consumes_oldest_lot_without_retiring_continuous_member` | F1 | `closure-final-57-junit-readback`: PASS |
| 41 | `tests/test_strategic_probe_holding.py::test_entry_quality_loss_retains_actual_probe_holding[partial]` | F1 | `closure-final-57-junit-readback`: PASS |
| 42 | `tests/test_strategic_probe_holding.py::test_cancelled_partial_never_rearms_after_restart_and_risk_recovery` | F1 | `closure-final-57-junit-readback`: PASS |
| 43 | `tests/test_strategic_probe_holding.py::test_late_fill_accounts_for_shares_without_reviving_expired_grant[in-flight-cancel]` | F1 | `closure-final-57-junit-readback`: PASS |
| 44 | `tests/test_strategic_probe_holding.py::test_late_fill_accounts_for_shares_without_reviving_expired_grant[cancel-completed]` | F1 | `closure-final-57-junit-readback`: PASS |
| 45 | `tests/test_strategic_probe_holding.py::test_partial_qualification_expiry_preserves_prior_lower_holding_target` | F1 | `closure-final-57-junit-readback`: PASS |
| 46 | `tests/test_unified_core_book.py::test_partial_core_retry_keeps_one_order_and_event_after_restart` | F1 | `closure-final-57-junit-readback`: PASS |
| 47 | `tests/test_unified_core_book.py::test_partial_ordinary_restore_survives_restart_without_new_entry_qualification` | F1 | `closure-final-57-junit-readback`: PASS |
| 48 | `tests/test_attribution_identity.py::test_native_partial_multilot_chain_round_trips` | F1 | `closure-final-57-junit-readback`: PASS |
| 49 | `tests/test_attribution_identity.py::test_native_live_tranche_event_must_chain_to_originating_buy` | F1 | `closure-final-57-junit-readback`: PASS |
| 50 | `tests/test_confirmed_recovery_pipeline.py::test_real_partial_recovery_keeps_fills_and_cancels_lost_current_proof` | F1 | `closure-final-57-junit-readback`: PASS |
| 51 | `tests/test_core_transfer_attribution.py::test_unsettled_rotation_cannot_label_a_buy_funded_by_another_sell[open_unfilled]` | F1 | `closure-final-57-junit-readback`: PASS |
| 52 | `tests/test_cross_ai_strategy.py::test_small_champion_replay_uses_real_next_open_and_sealed_evidence` | M1 | `closure-final-57-junit-readback`: PASS |
| 53 | `tests/test_early_cohort_capital.py::test_partial_founding_buy_keeps_its_cap_and_identity_after_restart` | F1 | `closure-final-57-junit-readback`: PASS |
| 54 | `tests/test_execution.py::test_limit_and_suspension_keep_pending` | F1 | `closure-final-57-junit-readback`: PASS |
| 55 | `tests/test_execution.py::test_large_opening_gap_reprices_target_and_preserves_weight_cap` | F1 | `closure-final-57-junit-readback`: PASS |
| 56 | `tests/test_independent_market_confirmation.py::test_market_confirmation_loss_cancels_native_partial_buy_but_keeps_real_holding` | F1 | `closure-final-57-junit-readback`: PASS |
| 57 | `tests/test_lifecycle_and_risk.py::test_frozen_strategic_member_preserves_partial_sell_identity_and_cancels_buy` | F2 | `closure-final-57-junit-readback`: PASS |

## 校验与证据范围

- 初始失败原始 stdout SHA-256：`e18aad4f3e1a93c2468300aaddeb40f9a8ecc446a8f33a197db428441e497e1b`；原 nodeid 清单 SHA-256：`7292db24c69cdfc29675d65492dbb690ddf233f70449dbd7f9c3f037caca08aa`。原日志保存在私有证据存储，不提交公开源码仓库。
- 相同 11 项原生执行压力场景，基线与候选结果的字节 SHA-256 均为 `2867aff5d5e1bc737ab386b6ca29f551fed484c718f9feafd8f20362166ffc41`；该对照仅覆盖执行重构，不能冒充完整 Absolute 回放。
- 最终 57 项 JUnit SHA-256：`60d60d2978601a86f0b62805bc09336ae215b536465c0ea517366ceb48a467d3`；它逐一包含上述原 nodeid，57 测试、0 失败、0 错误、0 跳过。最终 stdout SHA-256：`5603b3bdbd979d86e8c62be43291039331c1f18d392c470cba56f4eba489b84d`。
- Ruff 仓库全量检查通过；mypy 检查 331 个源文件无错误；前述两项架构失败及风险 SELL/T+1/Ownership 边界等聚焦检查 43 项通过。全架构本地大集合在 10 分钟预算时仍未完成，不能把超时当作通过；PR #90 的 architecture-portfolio 作业已通过，其余 CI 状态以实际运行回执为准。
- 正式候选 C 八片和 final 的原始生产者是 `115a151761f458da056b6118fa41a2339ec49914`，final SHA-256 `3a7c66fc3429aa9e7f9de18ee70cec749c449a49d2bc46a8f214320a2deb9ef3`；八片合计 5,259,150,004 字节，已保存原件。执行重构只在上述压力账户有逐字节配对；若当前身份合同要求完全同源，不把旧分片改贴本次提交身份。

## 最终身份的原生 Ownership 与经济对照

在 Python `3.12.13`、uv `0.11.33`、NumPy `2.5.1`、pandas `3.0.5` 及冻结锁文件下，
五个原生分片均以最终生产源码指纹
`a8b10880b15f8d0305f17e2acecc5939af3fc3e95a9ddb2803f753536652d7ee`
运行；`champion` 2/2、`critical` 2/2、`ghost-a` 3/3、`ghost-b` 2/2、
`continuity` 4/4，合计 **13/13 个互不重复的场景全部 PASS**。
完整账户、分片回执、原始运行记录及逐文件哈希见私有证据包；独立汇总校验
`closure-final-reconciliation.json` 的 SHA-256 为
`806d6e0e543ef047f2127750bbd137a04bc4c119a914eb8d7d1deba00760b41d`。

冠军财富 `21.868143196721125×`，最大回撤 `26.628444457956535%`，
满足同一现行 15× 财富底线和原有 30% 回撤限制；与候选 C 已封存的经济数字一致。
删除多余类型转换前后，13 个场景的财富与回撤逐项相同；冠军 869 日权益曲线、
现金、持仓、27 笔成交、28 个物理订单号及去除来源派生 ID 后的成交和订单账本
逐项一致。`grant_id`、`epoch_id` 因生产源码指纹改变而重新派生，两个版本均由各自
原生账本及归因校验通过，不能把旧 ID 或旧证据改贴最终源码身份。

初始日志和最终原生证据分别保存为私有原件，后者包含 30 个原始文件，上传前后
归档 SHA-256 均为 `a880295a3800d5c179a9568f9395cedf33bb1b32c2495fd9da541b3f6f77fb3b`。
候选 C 的八片完整 Absolute 原件保留原生产者与原始成功声明；本次没有重跑
5.26 GB 的 Absolute 全矩阵，也不把旧结果称为最终 HEAD 的正式新身份验收。
