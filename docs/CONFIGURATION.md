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
| `restoration_min_trade_weight` | 0.01 | 恢复意图的专用最小权重差；避免 5% 普通死区，也抑制亚 1% 补单 |
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

生产 `REFERENCE_UNIVERSE` 恒等于评审后的 `STABLE_REFERENCE_UNIVERSE`，用于截面排名、行业篮子、风险锚和数据覆盖检查。`EXPANDING_RESEARCH_REFERENCE` 只与稳定篮子组成离线 `RESEARCH_REFERENCE_UNIVERSE`；生产引擎不读取该研究并集。把研究证券晋级到生产必须显式修改稳定篮子、补齐数据并重新通过评审，不能靠填充研究 tuple 改写实时百分位或缓存键。

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

行业参数是生产默认路径，不要求每日人工选择行业。覆盖不足会降低置信度。行业证据参与领涨评分与 admission utility、普通跨行业 hand-off、恢复副锚替换确认和 challenger scout；它不降低替换边际、不替换恢复主锚，也不增加轮动次数。只有唯一 `PortfolioAllocator` 在其他结构、风险、空槽、现金和确认期约束同时满足后才能产生一个 `SATELLITE` scout 目标，行业模块本身不创建第二套组合或拥有仓位。

## 组合与轮动

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `trend_entry_gross` | 0.80 | 趋势初始目标仓位 |
| `trend_target_gross` | 0.95 | 常规趋势目标仓位 |
| `strong_trend_gross` | 1.00 | 强趋势目标仓位 |
| `choppy_target_gross` | 0.60 | 震荡目标仓位 |
| `weak_gross` | 0.25 | 弱势目标仓位 |
| `single_core_entry_cap` | 0.50 | 单核心初始上限 |
| `max_satellites` | 2 | 最大卫星仓数量 |
| `industry_weight_cap` | 0.75 | 单行业权重上限 |
| `replacement_edge` | 0.35 | 替换所需优势 |
| `replacement_confirm_days` | 3 | 替换连续确认天数 |
| `min_hold_days` | 10 | 最短持有天数 |
| `max_rotations_20d` | 2 | 20 日最大轮动次数 |

### 动态战略 cohort 与 epoch

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `strategic_cohort_symbols` | `()` | 已退役兼容字段；非空配置会被拒绝 |
| `strategic_dynamic_enabled` | `True` | 从用户全集按证据发现战略成员 |
| `strategic_cohort_size` | 3 | 每个 epoch 最多战略成员数 |
| `strategic_cohort_min_size` | 3 | 激活常规长周期 cohort 的最少合格成员数 |
| `strategic_two_name_gross` | 0.85 | 证据充分的双成员 cohort 总仓上限 |
| `strategic_one_name_gross` | 0.50 | exceptional 单成员 cohort 总仓上限 |
| `strategic_two_name_confirm_days` | 3 | 双成员候选连续确认日数 |
| `strategic_one_name_confirm_days` | 4 | 单成员候选连续确认日数 |
| `strategic_partial_universe_max_size` | 8 | 允许 2/1 成员后备队列的最大固定用户全集；同步反转除外 |
| `adaptive_broad_universe_min_size` | 10 | 自动启用广池兼容分析口径的固定用户全集下限 |
| `adaptive_broad_universe_compatibility_enabled` | `True` | 9 只过渡全集只切换风险计票；广池关闭经消融证实负向的同日倾斜、分组收缩和家族计票决策；诊断证据仍保留 |
| `strategic_secular_min_score` | 0.58 | 长周期综合分下限 |
| `strategic_secular_min_confidence` | 0.65 | 长周期证据置信度下限 |
| `strategic_established_min_median_ret240` | 1.00 | 10 只以上成熟路线的组内 240 日持久收益中位数下限 |
| `strategic_expansive_universe_min_size` | 20 | 启用宽全集成熟证据与首次恢复仓位保护的全集下限 |
| `strategic_long_cycle_min_ret20` | -0.05 | 常规长期路线允许轻微整理，但拒绝 20 日收益低于 -5% 的旧赢家 |
| `strategic_long_cycle_min_ret60` | 0.00 | Established 路线至少保持非负 60 日结构 |
| `strategic_long_cycle_min_ret120` | 0.00 | 新长期 cohort 至少保持非负 120 日结构，避免用行业配额填入衰退弱腿 |
| `strategic_current_factor_floor` | 0.50 | Established 路线的动量、相对强度等当前健康分位下限 |
| `strategic_transition_min_score` | 0.70 | Emerging 路线的多周期交接综合分下限，不读取 240 日绝对涨幅 |
| `strategic_transition_min_component` | 0.70 | Emerging 路线突破与相对强度的独立分位下限 |
| `strategic_transition_impulse_min_score` | 0.48 | 同行业同步交接的多因子最低分；仍受正常风险、趋势环境及统一生命周期约束 |
| `strategic_transition_impulse_min_ret20` | 0.05 | 同步交接成员连续确认所需的 20 日最小涨幅 |
| `strategic_transition_impulse_min_ret60` | -0.12 | 同步交接允许的 60 日整理下限，拒绝深度中期破坏 |
| `strategic_transition_impulse_min_ret120` | -0.20 | 同步交接允许的 120 日结构下限，防止深度旧周期破坏伪装成新领导 |
| `strategic_transition_impulse_max_ret120` | 0.10 | 同步交接的 120 日成熟上限，避免把已延伸的旧周期成员当作新交接 |
| `strategic_transition_impulse_min_market_ret20` | 0.00 | 同步交接要求宽基与科技指数当前 20 日结构均未转负，拒绝熊市反弹旁路 |
| `strategic_long_cycle_max_tech_ret120` | 0.20 | 常规长期路线的科技指数 120 日追涨上限 |
| `strategic_cohort_confirm_days` | 2 | 常规长期候选签名连续确认日数 |
| `strategic_epoch_cooldown_sessions` | 30 | 完整退出后下一 epoch 的最短可见交易日冷却 |
| `strategic_epoch_min_symbol_change` | 1 | 新 epoch 相对上一 cohort 的最少新增成员数 |
| `strategic_cohort_profit_arm` | 0.10 | 战略赢家启用结构破坏 + ATR 分段保护所需峰值 MFE |
| `strategic_cohort_trail_atr` | 3.55 | 五条相邻保护带的中心 ATR 距离 |
| `strategic_cohort_trail_spacing` | 0.05 | 相邻 ATR 保护带间距 |
| `strategic_cohort_trail_bands` | 5 | 分段保护带数量；仍只产生每只证券一个最终目标 |
| `strategic_cohort_exit_step` | 0.01 | 每次已触发保护带的目标减量 |
| `strategic_cohort_disaster_stop` | -0.20 | 未处于独立风险保护时的战略灾难退出线 |

成员只从本次用户全集产生。每只候选至少需要 121 个可见收盘记录；成熟路线保留经消融验证的 240 日持久收益证据，小型集中机会集还可使用正 120 日结构、`secular_score` 与 2/3 趋势持久性共同确认的因果替代证据。完整三成员组优先；固定全集不超过 8 时，允许 2 个高质量成员以 85% 总仓、exceptional 单成员以 50% 总仓后备进入，并分别确认 3/4 日。9 只过渡全集拒绝不完整队列并自动采用风险动作兼容计票；10 只及以上广池采用完整兼容分析口径，避免早期未上市子集把广池误判为小池。候选签名用 `SECULAR`/`EMERGING_SECULAR` 表达状态，并在 `evidence=` 中保留来源。完整退出后，还需经过 30 个可见交易日并重新确认；epoch 状态均随 schema v3 账户持久化。

风险模块临时压缩战略持仓时会逐票保存压缩前目标，但不会因风险上限随后放宽就立即买回。只有风险恢复到 `NORMAL`，或处于票数不超过 2 且 `transition_damage` 已回到修复线以下的 `CAUTION`，才按保存比例恢复；唯一恢复上限是风险模块当日给出的 `target_gross_cap`，组合层不再叠加隐性残余仓位 cap。restore 只有在风险 cap 足以覆盖未缩放的完整保存目标、每个成员都达到至少 95% 原目标且相关 BUY 均已结束后才清除；组合总仓或其他成员超配不能掩盖容量受限的缺票。由战略 ATR 保护带或灾难线产生的最终策略退出会逐票退休目标、保护带以及战略和风险两类恢复权，不进入买回路线。

## 加仓与恢复

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `add1_min_mfe` | 0.04 | 第一次加仓最低浮盈 |
| `add2_min_mfe` | 0.10 | 第二次加仓最低浮盈 |
| `add_tranche_cooldown_sessions` | 5 | 组合级加仓冷却天数 |
| `recovery_target_gross` | 0.92 | 完成修复后的仓位上限 |
| `recovery_expansive_universe_gross` | 0.70 | 20 只以上宽全集首次三成员恢复部署的总仓上限；后续仍可按确认自动恢复 |
| `recovery_conviction_weighting_enabled` | `True` | 完整三成员恢复队列默认保留因果领涨股权重 |
| `recovery_conviction_retention_bonus` | 0.30 | 同生命周期危机裁剪中因果领涨所有权的保留效用加分 |
| `recovery_member_confirm_days` | 3 | 欠分散的临时恢复成员连续保持同一候选签名后才投入，抑制次日换锚 |
| `recovery_winner_mfe_arm` | 0.20 | 恢复锚赢家 trail 的峰值 MFE 启用线 |
| `recovery_winner_trail` | 0.10 | 已启用恢复赢家相对峰值的退出回撤 |
| `recovery_cohort_tail_guard_days` | 90 | 恢复 cohort 进入同步结构尾部保护前的最短交易日 |
| `recovery_cohort_tail_line` | 0.12 | 成熟恢复 cohort 同步破坏的运行回撤确认线 |
| `tactical_rebound_weight` | 0.60 | 战术反弹/恢复主锚的最大目标权重 |
| `tactical_probe_weight` | 0.60 | 空仓超跌战术探针目标权重，仍受风险上限和容量约束 |
| `tactical_rebound_take_profit` | 0.065 | 战术反弹止盈线 |
| `recovery_cohort_weak_market_ret120` | -0.10 | 空仓战术路线要求宽基和科技指数 120 日收益都不高于该熊市阈值 |
| `recovery_transition_weak_leg_ret120` | -0.08 | 过渡修复路线中较弱指数的 120 日收益上限 |
| `recovery_transition_strong_leg_max_ret120` | 0.08 | 过渡修复路线中较强指数的 120 日收益上限 |
| `recovery_transition_min_divergence` | 0.10 | 过渡修复路线要求的双指数最小分化 |
| `recovery_confirm_days` | 2 | 恢复确认天数 |
| `fast_v_recovery_confirm_days` | 2 | 快速 V 型修复确认 |
| `persistent_v_recovery_wait_days` | 15 | 长期单仓修复最短等待 |
| `recovery_substitution_edge` | 0.35 | 破坏恢复副锚切换到独立行业储备所需的连续分数优势 |
| `recovery_substitution_max_ret20` | 0.30 | 恢复副锚挑战者允许的 20 日涨幅上限，避免追逐已扩展反弹 |

`recovery_winner_*` 只保护已经获得至少 20% 峰值 MFE 的恢复锚赢家，并且只在风险非正常、冻结新增风险或机会处于 `CHOPPY`/`WEAK`/`RECOVERY` 时检查 10% 峰值回撤；它不是所有 `CORE` 的通用移动止损。60% 战术探针只面向空仓、无旧恢复锚的崩跌修复：单票已发生至少 35% 因果崩跌并出现新的极端洗盘时可独立进入；较浅的反弹路线仍要求双指数弱势或满足 -8%/+8% 边界与至少 10 个百分点分化。所有路线都要通过流动性、容量、冷却和风险约束；欠分散的临时新增成员需保持同一签名 3 日，若一次已确认完整三票则不重复等待。恢复换锚只在副锚结构已破坏、跨行业交接曾被观察、独立储备成熟、20 日涨幅不超过 30% 且优势连续确认时工作；`CAUTION` 下只能等额卖出融资，不增加组合总风险。

## 置信度仓位与 challenger scout

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `regime_factor_blend_enabled` | `True` | 按市场状态混合领涨因子 |
| `confidence_sizing_enabled` | `True` | 高置信强趋势允许提高初始核心总仓 |
| `high_confidence_entry_gross` | 0.90 | 高置信初始总仓上限 |
| `exceptional_entry_gross` | 0.95 | 极高置信初始总仓上限 |
| `high_confidence_entry_score` | 0.84 | 高置信领涨分数下限 |
| `high_confidence_entry_breadth` | 0.60 | 高置信行业广度下限 |
| `high_confidence_entry_vol20` | 0.045 | 高置信入场 20 日波动上限 |
| `conviction_weighting_enabled` | `True` | 核心之间按非负 conviction 分配 |
| `challenger_scout_enabled` | `True` | 启用只使用闲置现金的跨行业 scout |
| `challenger_scout_weight` | 0.06 | scout 最大目标权重 |
| `challenger_scout_confirm_days` | 7 | challenger 证据连续确认日数 |
| `challenger_scout_score_edge` | 0.08 | challenger 相对最弱 incumbent 的最低分数优势 |
| `challenger_scout_incumbent_hysteresis` | 0.08 | scout 不得融资卖出 incumbent 的目标权重容差 |

90%/95% 的置信度总仓只在 `STRONG_TREND`、风险正常、未冻结新增风险且不存在短期追高时生效；所有数值仍受风险、总仓、单票和行业上限约束。核心之间进一步采用不等权 conviction 还要通过独立联合门：至少两只新核心、每只的韧性和相对强度不低于 0.60、流动性分不低于 0.70，并且可计算的所有票间绝对相关性不超过 `risk_correlation` 0.75。相关性证据缺失或任一分项不足时退回等权，不把分数离散本身当作集中依据。scout 还要求跨行业、空槽、incumbent 衰减和至少 6% 闲置现金，不会卖出 incumbent 给探针融资。

`CHOPPY` 与 `WEAK` 的 60%/25% 是普通领涨路径的新增机会预算。scout 处理完成后，分配器只在拟议增量上执行确定性的稀疏收缩；已持有的健康 Core 不会因一个离散机会标签被机械卖出，强制减仓仍由确认风险、Chronic Deterioration 或自身生命周期负责。战略 cohort 和恢复路线继续使用各自的显式生命周期及风险契约。

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
| `risk_off_gross` | 0.75 | 持仓损伤、连续退化和票数共同确认时的降险上限，其他覆盖层可进一步压低 |
| `concentrated_crisis_gross` | 0.255 | 集中破坏仓位上限；保留健康核心但不提供标签豁免 |

### 动态风险锚、连续损伤与资本预算阶梯

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `dynamic_risk_anchors_enabled` | `True` | 从固定参考篮子动态确认长期风险锚 |
| `risk_anchor_count` | 3 | 风险锚数量上限 |
| `risk_anchor_min_groups` | 2 | 锚篮子最少行业组数 |
| `risk_anchor_confirm_days` | 5 | 新锚签名连续确认日数 |
| `risk_anchor_min_secular_score` | 0.55 | 锚候选长期分下限 |
| `same_day_leader_pipeline_enabled` | `True` | 结构评分后按本日机会施加 alpha，并且 tenure 只更新一次；广池兼容时自动停用 |
| `group_balanced_reference_enabled` | `True` | 参考广度按证券和行业组共同汇总；广池兼容时自动停用 |
| `hierarchical_industry_shrinkage_enabled` | `True` | 子行业分数按样本量向全局父级收缩；广池兼容时自动停用 |
| `evidence_family_voting_enabled` | `True` | 六个独立证据家族各最多一票；9 只过渡全集和广池自动使用兼容动作票数，但继续输出家族诊断 |
| `risk_breadth_name_weight` | 0.50 | 证券等权广度在混合广度中的权重 |
| `stable_reference_global_weight` | 0.70 | 稳定参考证据中全局篮子权重 |
| `unknown_industry_confidence` | 0.55 | 未知证券行业推断的最低可信度 |
| `unknown_industry_weight_cap` | 0.18 | 低置信未知行业证券权重上限 |
| `transition_overlay_enabled` | `True` | 启用连续转坏覆盖层 |
| `transition_damage_freeze` | 0.58 | 连续损伤触发冻结新增风险的门槛 |
| `transition_damage_repair` | 0.38 | 允许累计修复日的低损伤门槛 |
| `transition_confirm_days` | 3 | 冻结新增风险的连续确认日数 |
| `transition_repair_days` | 4 | 转坏状态修复确认日数 |
| `chronic_overlay_enabled` | `True` | 启用震荡/弱势慢性退化状态 |
| `chronic_confirm_days` | 4 | 慢性退化连续确认日数 |
| `chronic_repair_days` | 5 | 慢性状态清除所需低损伤日数 |
| `chronic_moderate_cap` | 0.45 | 中度慢性退化总仓上限 |
| `chronic_severe_cap` | 0.30 | 重度慢性退化总仓上限 |
| `capital_budget_ladder_enabled` | `True` | 启用有确认和修复迟滞的资本预算阶梯 |
| `capital_budget_level2_dd` | 0.12 | level 2 资本回撤线 |
| `capital_budget_level2_cap` | 0.82 | level 2 总仓上限 |
| `capital_budget_level3_dd` | 0.16 | level 3 资本回撤线 |
| `capital_budget_level3_cap` | 0.50 | level 3 总仓上限 |
| `capital_budget_repair_days` | 5 | 预算下调前所需连续修复日数 |

连续损伤会持久化 MA20/MA60 广度、领涨失效、相关性、波动、`transition_damage` 和 `trend_health`。风险 `reduction_level` 为 1 时只冻结新增风险，2 时加总仓裁剪，3 时进入危机级减仓；它不是可以绕过 `Risk` 状态机的第二个风险所有者。

## 已部署持仓冲击保护

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `sector_guard_enabled` | `True` | 启用持仓同步冲击状态机 |
| `sector_guard_min_symbols` | 2 | 形成持仓广度所需的最少证券数 |
| `sector_shock_return` | -0.045 | 等权单日收益冲击阈值 |
| `sector_shock_breadth` | 0.20 | 冲击日正收益持仓比例上限 |
| `sector_weighted_shock_return` | -0.024 | 权重加权单日收益冲击阈值 |
| `sector_weighted_negative_exposure` | 0.70 | 权重口径负收益敞口下限 |
| `sector_shock_window` | 4 | 统计重复冲击的共同交易日窗口 |
| `sector_shock_confirmations` | 2 | 激活保护所需的冲击次数 |
| `sector_guard_divergence` | 0.50 | 科技相对宽基长期偏离确认阈值 |
| `sector_guard_gross` | 0.40 | 重复同步持仓冲击的 Level-2 总仓上限；14% 是资本回撤线，不是剩余仓位 |
| `sector_guard_min_sessions` | 8 | 允许退出保护前的最少交易日 |
| `sector_recovery_ma` | 10 | 持仓结构修复均线窗口 |
| `sector_recovery_return` | 0.00 | 恢复日等权收益下限 |
| `sector_recovery_breadth` | 0.67 | 恢复日站上均线的持仓比例下限 |
| `sector_recovery_confirmations` | 3 | 退出保护所需的连续修复日 |

该保护只观察真实持仓或受保护权重，不能被未持有证券的上涨稀释。信息不足时保持当前状态，不会猜测恢复。由该保护触发的减仓携带独立的 `sector_guard` reason/exit kind，归因不会与普通 `risk_off`、危机或资本预算减仓混成同一原因桶。

## 安全开关

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `risk_overlay_enabled` | `True` | 开启独立风险覆盖层 |
| `industry_rotation_enabled` | `True` | 开启行业广度与跨行业确认 |
| `sector_guard_enabled` | `True` | 开启持仓同步冲击保护 |
| `dynamic_risk_anchors_enabled` | `True` | 开启动态风险锚确认 |
| `transition_overlay_enabled` | `True` | 开启连续转坏冻结层 |
| `chronic_overlay_enabled` | `True` | 开启慢性退化保护 |
| `capital_budget_ladder_enabled` | `True` | 开启资本预算阶梯 |
| `confidence_sizing_enabled` | `True` | 开启高置信仓位 |
| `challenger_scout_enabled` | `True` | 开启闲置现金 challenger scout |
| `fail_closed` | `True` | 数据、代码和账户身份异常时拒绝运行 |

## 修改原则

1. 硬约束不得通过参数放宽到杠杆、做空、单票超过 60% 或持仓超过 6 只。
2. 风险参数的顺序关系由 `SystemConfig.__post_init__()` 校验。
3. 修改参数后必须运行完整测试和至少一个包含上涨、下跌、恢复阶段的连续回放。
4. 不要只依据单一股票池或单一窗口调整默认值。
5. 生产账户会绑定 schema v3 和代码指纹；发布新代码后应按运行手册显式迁移并核对 tranche 入场证据、订单减仓策略、epoch、风险锚、预算和 scout 状态。
