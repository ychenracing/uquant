# uquant 参数参考

全部运行参数定义在 `uquant/config.py` 的不可变 `SystemConfig` 中。`DEFAULT_CONFIG` 同时用于每日决策和历史回放。研究时使用 `DEFAULT_CONFIG.override(...)` 创建新实例，不要在运行过程中修改共享默认对象。

## 账户与硬约束

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `initial_cash` | 2,000,000 | 回放与账户初始化默认现金 |
| `max_gross` | 1.00 | 最大总仓位，不允许杠杆 |
| `max_symbol_weight` | 0.60 | 单票最大权重 |
| `max_positions` | 6 | 最大持仓数 |
| `min_trade_weight` | 0.05 | 最小权重变化门槛 |
| `target_hysteresis` | 0.08 | 目标权重迟滞区间 |
| `min_trade_value` | 20,000 | 最小交易金额 |

## 成本、容量与市场规则

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `commission_rate` | 0.00025 | 双边佣金率 |
| `min_commission` | 5 | 单笔最低佣金 |
| `stamp_duty` | 0.0005 | 卖出印花税 |
| `transfer_fee` | 0.00001 | 过户费率 |
| `slippage` | 0.001 | 开盘价单边滑点 |
| `max_volume_participation` | 0.005 | 单日最大成交量参与率 |
| `minimum_median_amount` | 20,000,000 | 流动性门槛 |

执行层还会强制 T+1、涨跌停、停牌、整手和现金约束，这些不是可关闭的策略选项。

## 特征与领涨确认

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `min_history` | 120 | 成熟评分最少历史天数 |
| `emerging_min_history` | 60 | 新兴评分最少历史天数 |
| `trend_fast` | 20 | 快速趋势窗口 |
| `trend_medium` | 60 | 中期趋势窗口 |
| `trend_slow` | 120 | 慢速趋势窗口 |
| `breakout_window` | 40 | 突破窗口，不含当日高点 |
| `atr_window` | 14 | ATR 窗口 |
| `leader_mature_score` | 0.72 | 成熟领涨分数门槛 |
| `leader_emerging_score` | 0.76 | 新兴领涨分数门槛 |
| `leader_min_confidence` | 0.70 | 最低置信度 |
| `leader_tenure_days` | 5 | 成熟领涨连续确认天数 |
| `emerging_tenure_days` | 3 | 新兴领涨连续确认天数 |

## 行业证据与轮动确认

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `industry_rotation_enabled` | `True` | 启用行业证据和跨行业确认 |
| `industry_signal_min_members` | 2 | 行业信号达到满置信度的最少参考成员 |
| `industry_rotation_min_score` | 0.62 | 行业领先确认的最低综合分 |
| `industry_rotation_min_confidence` | 0.50 | 行业覆盖置信度下限 |
| `industry_rotation_edge` | 0.18 | 新旧行业强度差下限 |
| `industry_rotation_deterioration` | 0.48 | 原行业弱化阈值 |
| `industry_rotation_breadth` | 0.50 | 原行业短期广度弱化阈值 |

行业参数是生产默认路径，不要求每日人工选择行业。覆盖不足会降低置信度；行业证据只在既有恢复替换候选中提供排序确认，不降低替换边际、不替换主锚、不增加替换次数，也不直接创建第二套组合。

## 组合与轮动

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `trend_entry_gross` | 0.80 | 趋势初始目标仓位 |
| `trend_target_gross` | 0.95 | 常规趋势目标仓位 |
| `strong_trend_gross` | 1.00 | 强趋势目标仓位 |
| `choppy_target_gross` | 0.60 | 震荡目标仓位 |
| `weak_gross` | 0.25 | 弱势目标仓位 |
| `single_core_entry_cap` | 0.50 | 单核心初始上限 |
| `satellite_weight` | 0.08 | 卫星仓默认权重 |
| `max_satellites` | 2 | 最大卫星仓数量 |
| `industry_weight_cap` | 0.75 | 单行业权重上限 |
| `replacement_edge` | 0.35 | 替换所需优势 |
| `replacement_confirm_days` | 3 | 替换连续确认天数 |
| `min_hold_days` | 10 | 最短持有天数 |
| `max_rotations_20d` | 2 | 20 日最大轮动次数 |

## 加仓与恢复

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `add1_min_mfe` | 0.04 | 第一次加仓最低浮盈 |
| `add2_min_mfe` | 0.10 | 第二次加仓最低浮盈 |
| `add_tranche_cooldown_sessions` | 5 | 组合级加仓冷却天数 |
| `recovery_probe_gross` | 0.30 | 恢复探针总仓位 |
| `recovery_target_gross` | 0.92 | 完成修复后的仓位上限 |
| `recovery_confirm_days` | 2 | 恢复确认天数 |
| `recovery_cooldown_days` | 10 | 常规恢复冷却 |
| `fast_v_recovery_confirm_days` | 2 | 快速 V 型修复确认 |
| `persistent_v_recovery_wait_days` | 15 | 长期单仓修复最短等待 |

## 风险预算

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `risk_fast_return` | -0.045 | 篮子短期收益风险阈值 |
| `risk_breadth` | 0.65 | 下跌广度阈值 |
| `risk_below_ma20` | 0.65 | 跌破 MA20 比例阈值 |
| `risk_correlation` | 0.75 | 相关性风险阈值 |
| `risk_volatility_ratio` | 1.80 | 波动放大阈值 |
| `operating_dd_caution` | 0.08 | 运行回撤警戒线 |
| `capital_dd_risk_off` | 0.14 | 资本回撤降险线 |
| `capital_dd_crisis` | 0.20 | 资本回撤危机线 |
| `caution_gross` | 0.60 | 警戒状态仓位上限 |
| `risk_off_gross` | 0.75 | 降险状态基础上限，其他证据可进一步压低 |
| `crisis_gross` | 0.50 | 常规危机仓位上限 |
| `concentrated_crisis_gross` | 0.30 | 集中破坏仓位上限 |

## 已部署持仓冲击保护

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `sector_guard_enabled` | `True` | 启用持仓同步冲击状态机 |
| `sector_guard_min_symbols` | 2 | 形成持仓广度所需的最少证券数 |
| `sector_shock_return` | -0.045 | 等权单日收益冲击阈值 |
| `sector_shock_breadth` | 0.20 | 冲击日正收益持仓比例上限 |
| `sector_shock_window` | 4 | 统计重复冲击的共同交易日窗口 |
| `sector_shock_confirmations` | 2 | 激活保护所需的冲击次数 |
| `sector_guard_divergence` | 0.50 | 科技相对宽基长期偏离确认阈值 |
| `sector_guard_gross` | 0.40 | 保护激活后的总仓位上限 |
| `sector_guard_min_sessions` | 8 | 允许退出保护前的最少交易日 |
| `sector_recovery_ma` | 10 | 持仓结构修复均线窗口 |
| `sector_recovery_return` | 0.00 | 恢复日等权收益下限 |
| `sector_recovery_breadth` | 0.67 | 恢复日站上均线的持仓比例下限 |
| `sector_recovery_confirmations` | 3 | 退出保护所需的连续修复日 |

该保护只观察真实持仓或受保护权重，不能被未持有证券的上涨稀释。信息不足时保持当前状态，不会猜测恢复。

## 安全开关

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `risk_overlay_enabled` | `True` | 开启独立风险覆盖层 |
| `industry_rotation_enabled` | `True` | 开启行业广度与跨行业确认 |
| `sector_guard_enabled` | `True` | 开启持仓同步冲击保护 |
| `fail_closed` | `True` | 数据、代码和账户身份异常时拒绝运行 |
| `production_stage` | `PRODUCTION` | 运行阶段标记 |
| `deterministic_seed` | 20260808 | 需要确定性抽样时使用的固定种子 |

## 修改原则

1. 硬约束不得通过参数放宽到杠杆、做空、单票超过 60% 或持仓超过 6 只。
2. 风险参数的顺序关系由 `SystemConfig.__post_init__()` 校验。
3. 修改参数后必须运行完整测试和至少一个包含上涨、下跌、恢复阶段的连续回放。
4. 不要只依据单一股票池或单一窗口调整默认值。
5. 生产账户会绑定代码指纹；发布新代码后应按运行手册显式迁移并核对账户状态。
