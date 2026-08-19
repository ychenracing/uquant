# 参数参考

## 配置原则

`uquant.config.SystemConfig` 是参数的唯一来源，`DEFAULT_CONFIG` 是生产默认值。运行时可以用 `DEFAULT_CONFIG.override(...)` 创建不可变副本；不要修改模块级对象，也不要在日报、脚本或研究模块中复制第二份默认值。

查看全部参数：

```bash
uv run python - <<'PY'
import json
from uquant.config import DEFAULT_CONFIG
print(json.dumps(DEFAULT_CONFIG.to_dict(), indent=2, sort_keys=True))
PY
```

本页列出最影响收益、回撤和交易次数的参数。未列出的诊断参数仍以 `to_dict()` 输出为准。

## 生产与验收边界

配置只面向 2023 年以来的 A 股 AI 产业链现金多头组合，运行频率为日频，收盘后决策、下一交易日执行，并保留人工核对环节。经济性验收不得早于 `2023-01-01`；更早数据只允许作为特征 warm-up，不得用于收益、回撤、订单或换手门槛。唯一支持的运行时是 Python 3.12。

以下生产路径开关由 `DEFAULT_CONFIG` 直接定义；`_decision_config_for_universe()` 对所有股票池大小都返回同一个传入配置：

| 参数 | 生产默认值 |
|---|---:|
| `same_day_leader_pipeline_enabled` | false |
| `group_balanced_reference_enabled` | false |
| `hierarchical_industry_shrinkage_enabled` | false |
| `evidence_family_voting_enabled` | false |

## 参数治理

`benchmarks/config_parameter_governance.json` 要求每个 `SystemConfig` 字段恰好属于
`MARKET_RULE`、`SAFETY`、`ECONOMIC`、`DERIVED` 或 `COMPATIBILITY` 一类，并有唯一
owner。市场费用、T+1、涨跌停、停牌、手数、现金和组合硬上限不是搜索自由；derived
字段不能独立覆盖，compatibility 字段只用于确定性等价，只有 `ECONOMIC` 字段可以
进入候选选择。任何被接受的默认值变化都必须重新通过 Phase 1 和完整的 Phase 2
Generalization 六窗口门禁，不能由人工日常运行或研究脚本临时注入场景专用参数。

官方 Generalization 的六个窗口、基准种子 `20260810`、索引 `0..4`、池大小
`5 / 9 / 15 / 20` 是验证输入，不是 `SystemConfig` 调参项。future holdout 从
`2026-08-06` 起只做观察，不能据其表现改参数；2023 年以前的数据仍只作 warm-up。

## 资金与执行

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `initial_cash` | 2,000,000 | 回放和新账户初始资金 |
| `max_gross` | 1.00 | 最大总仓 |
| `max_symbol_weight` | 0.60 | 单票最大权重 |
| `max_positions` | 6 | 最大持仓数 |
| `industry_weight_cap` | 0.75 | 单行业最大权重 |
| `min_trade_weight` | 0.05 | 常规最小权重变化 |
| `restoration_min_trade_weight` | 0.05 | 恢复补仓最小权重变化 |
| `protected_restore_min_trade_weight` | 0.04 | 受保护核心的恢复门槛 |
| `min_trade_value` | 20,000 | 最小交易金额 |
| `max_volume_participation` | 0.005 | 单日成交量参与率 |
| `slippage` | 0.001 | 单边滑点 |
| `commission_rate` | 0.00025 | 佣金率 |
| `min_commission` | 5 | 最低佣金 |
| `stamp_duty` | 0.0005 | 卖出印花税 |
| `transfer_fee` | 0.00001 | 过户费 |

## 特征窗口

| 参数 | 默认值 |
|---|---:|
| `trend_fast / medium / slow` | 20 / 60 / 120 |
| `min_history` | 120 |
| `atr_window` | 14 |
| `breakout_window` | 40 |
| `correlation_window` | 40 |
| `minimum_median_amount` | 20,000,000 |

缩短窗口会提高反应速度和换手；延长窗口会降低噪声，但可能推迟风险与修复确认。

## 机会仓位

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `trend_entry_gross` | 0.80 | 常规趋势首次入场 |
| `trend_target_gross` | 0.95 | 趋势目标仓位 |
| `strong_trend_gross` | 1.00 | 强趋势目标仓位 |
| `high_confidence_entry_gross` | 0.90 | 高置信首次入场 |
| `exceptional_entry_gross` | 0.95 | 极高置信首次入场 |
| `choppy_target_gross` | 0.60 | 震荡环境新增机会预算 |
| `weak_gross` | 0.25 | 弱势环境新增机会预算 |
| `recovery_target_gross` | 0.92 | 已确认修复目标 |
| `fast_v_recovery_gross` | 0.60 | 快速修复探针 |
| `market_crisis_gross` | 0.50 | 常规危机上限 |
| `severe_crisis_gross` | 0.20 | 严重冲击上限 |

所有机会仓位最终都取自身目标与 `target_gross_cap` 的较小值。

## Risk Sentinel 模式

| 参数 | 默认值 | 边界 |
|---|---:|---|
| `risk_sentinel_mode` | `FREEZE_ONLY` | `LIMITED_GROSS_CAP` 仅保留为被拒绝的研究模式 |
| `risk_sentinel_defensive_gross_cap` | 0.70 | 锁定，不可按回放结果调节 |
| `risk_sentinel_critical_gross_cap` | 0.50 | 锁定，不可按回放结果调节 |
| `risk_sentinel_confirm_days` | 2 | 历史证据因果重算 |
| `risk_sentinel_repair_days` | 3 | 连续低风险历史证据因果重算 |

Sentinel cap 只能由 `uquant.assess_risk()` 与基础 cap 取最小值；配置覆盖不能让 Sentinel
放宽基础风险。该模式不改变 `max_symbol_weight` 或任何单票集中度政策。

## 领涨、持有与替换

| 参数 | 默认值 |
|---|---:|
| `leader_mature_score` | 0.72 |
| `leader_emerging_score` | 0.76 |
| `leader_min_confidence` | 0.70 |
| `leader_tenure_days` | 5 |
| `emerging_tenure_days` | 3 |
| `min_hold_days` | 10 |
| `add1_min_mfe / add1_weight` | 0.04 / 0.05 |
| `add2_min_mfe / add2_weight` | 0.10 / 0.05 |
| `add_tranche_cooldown_sessions` | 5 |
| `replacement_edge` | 0.35 |
| `replacement_confirm_days` | 3 |
| `max_rotations_20d` | 2 |
| `dynamic_k_confirm_days` | 3 |
| `dynamic_k_change_interval` | 20 |

降低确认期或替换优势通常会增加换手；提高领涨门槛会减少持仓机会并增加现金时间。

## 战略组合

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `strategic_dynamic_enabled` | true | 自动发现长周期候选 |
| `strategic_cohort_size` | 3 | 完整组合成员数 |
| `strategic_cohort_confirm_days` | 2 | 当前战略路由确认期；完整三成员及同步反转单/双成员均使用 |
| `strategic_secular_min_score` | 0.58 | 长周期最低分数 |
| `strategic_secular_min_confidence` | 0.65 | 最低置信度 |
| `strategic_cohort_min_ret240` | 1.70 | 240 日持续收益门槛 |
| `strategic_dominant_max_weight` | 0.95 | 独立证据确认的战略主导者特例上限 |
| `strategic_damage_guard_gross` | 0.89 | 战略组合受损时的仓位上限 |
| `strategic_two_name_gross` | 0.85 | 双成员总仓 |
| `strategic_two_name_confirm_days` | 3 | 为配置兼容和治理清单保留；当前路由不选择 |
| `strategic_one_name_gross` | 0.50 | 单成员总仓 |
| `strategic_one_name_confirm_days` | 4 | 为配置兼容和治理清单保留；当前路由不选择 |
| `strategic_epoch_cooldown_sessions` | 30 | 完整退出后的冷却 |
| `strategic_cohort_profit_arm` | 0.10 | ATR 保护启动 MFE |
| `strategic_cohort_trail_atr` | 3.55 | ATR 保护距离 |
| `strategic_cohort_disaster_stop` | -0.20 | 灾难退出线 |

`strategic_cohort_symbols` 是账户状态字段，不是 `SystemConfig` 参数；新账户初始为空，
成员只能从调用方给出的固定全集中按因果证据动态产生。单/双成员只在同步反转证据下
可入场；该条件会先选择 `strategic_cohort_confirm_days=2`，因此两者当前都要求连续两日确认。
`strategic_two_name_confirm_days=3` 和 `strategic_one_name_confirm_days=4` 仍保留在
`SystemConfig` 和参数治理清单中，以维持配置兼容，但当前路由不会选中它们；
成员路由也不按证券全集大小切换。

## 风险与资本预算

| 参数 | 默认值 |
|---|---:|
| `risk_fast_return` | -0.045 |
| `risk_breadth` | 0.65 |
| `risk_below_ma20` | 0.65 |
| `risk_correlation` | 0.75 |
| `risk_volatility_ratio` | 1.80 |
| `caution_confirm_days` | 2 |
| `risk_off_confirm_days` | 2 |
| `crisis_confirm_days` | 1 |
| `risk_off_gross` | 0.66 |
| `narrow_anchor_guard_gross` | 0.84 |
| `operating_dd_caution` | 0.08 |
| `capital_dd_risk_off` | 0.14 |
| `capital_dd_crisis` | 0.20 |
| `capital_budget_level2_dd / cap` | 0.12 / 0.82 |
| `capital_budget_level3_dd / cap` | 0.16 / 0.50 |
| `capital_budget_repair_days` | 5 |
| `chronic_moderate_cap` | 0.45 |
| `chronic_severe_cap` | 0.30 |

资本预算阶梯默认开启；`evidence_family_voting_enabled` 明确默认为 `false`。风险状态仍消费同一份因果证据，但生产不会在引擎内部按股票池大小偷偷打开证据家族投票或另一个策略配置。改变这些开关会显著影响回撤和恢复速度，必须重新运行完整 AI-era 门禁。

## 持仓同步冲击

| 参数 | 默认值 |
|---|---:|
| `sector_guard_enabled` | true |
| `sector_shock_window` | 4 |
| `sector_shock_confirmations` | 2 |
| `sector_shock_return` | -0.045 |
| `sector_weighted_shock_return` | -0.024 |
| `sector_shock_breadth` | 0.20 |
| `sector_weighted_negative_exposure` | 0.70 |
| `sector_guard_divergence` | 0.50 |
| `sector_guard_gross` | 0.40 |
| `sector_guard_min_sessions` | 8 |
| `sector_recovery_ma` | 10 |
| `sector_recovery_breadth` | 0.67 |
| `sector_recovery_confirmations` | 3 |

## 修复与侦察仓

| 参数 | 默认值 |
|---|---:|
| `recovery_confirm_days` | 2 |
| `recovery_stabilize_days` | 8 |
| `recovery_crash_drawdown` | 0.15 |
| `recovery_member_confirm_days` | 3 |
| `recovery_substitution_edge` | 0.35 |
| `recovery_substitution_max_ret20` | 0.30 |
| `recovery_winner_mfe_arm` | 0.20 |
| `recovery_winner_trail` | 0.10 |
| `challenger_scout_enabled` | true |
| `challenger_scout_confirm_days` | 7 |
| `challenger_scout_score_edge` | 0.08 |
| `challenger_scout_weight` | 0.06 |

## 关键联动约束

`SystemConfig.__post_init__()` 会拒绝不一致组合，重要关系包括：

- `0 < protected_restore_min_trade_weight <= restoration_min_trade_weight <= min_trade_weight`；
- 仓位和权重必须在 `[0, 1]`，持仓数、确认期和窗口必须为正；
- 风险恢复线必须低于风险触发线；
- 高置信仓位不能越过最大总仓；
- 单成员、双成员和完整组合预算必须与成员数约束兼容；
- 行业、相关性和未知行业上限不能绕过单票上限；
- `fail_closed` 默认开启。

## 调参建议

1. 一次只改变一个参数族，并记录精确配置摘要；
2. 先运行相关单元测试，再运行统一的 `promotion --profile full` AI-era 绩效门；
3. 同时观察收益、最大回撤、订单数、换手和急跌期表现；
4. 使用预先划分的 2023+ in-sample 研究证据，不以单一高收益区间或官方随机 cell 选择参数；
5. 不通过降低费用、放宽数据校验或修改统计口径制造改善；
6. 任何默认值变化都应单独提交，并附可复现证据；固定官方窗口、种子和证券池不得作为调节旋钮。
