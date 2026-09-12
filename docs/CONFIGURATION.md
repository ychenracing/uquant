# 参数参考

## 公开设置与固定规则

`SystemConfig` 只接受下面 13 个公开设置；`DEFAULT_CONFIG.override(...)` 也只接受这些键。
金额单位为人民币元，费率、滑点、参与率和仓位使用小数比例。未知键、固定规则键、
非数值、NaN、无穷及越界输入会被拒绝；当前对象保持不可变。

| 设置 | 默认值 | 用途 |
|---|---:|---|
| `initial_cash` | 2,000,000 | 新账户或回放初始现金 |
| `max_gross` | 1.0 | 账户总仓限制，有限数值 `(0, 1]`；1 表示100% |
| `max_symbol_weight` | 0.60 | 普通单票上限，有限数值 `(0, 0.60]`；已确认战略主导者窄例外仍受总仓限制 |
| `max_positions` | 6 | 可部署持仓数量上限，严格整数 `1..6`（不接受布尔或小数） |
| `commission_rate` | 0.00025 | 佣金率 |
| `min_commission` | 5 | 最低佣金 |
| `stamp_duty` | 0.0005 | 卖出印花税 |
| `transfer_fee` | 0.00001 | 过户费 |
| `slippage` | 0.001 | 单边滑点 |
| `max_volume_participation` | 0.005 | 成交量参与率 |
| `minimum_median_amount` | 20,000,000 | 中位成交额门槛 |
| `min_trade_value` | 20,000 | 最小交易金额 |
| `risk_sentinel_mode` | `FREEZE_ONLY` | 生产冻结；`SHADOW` 用于离线只读诊断 |

### 日常加载

`account-init`、`daily`、`backtest` 共用可选 `--config settings.json`。
省略时使用默认设置。JSON 只接受上表公开键，省略键使用默认值；拒绝重复键、
固定规则覆盖、非有限数值、未知键、布尔冒充数值及生产 `SHADOW` 模式。

```json
{"max_gross": 0.5, "max_symbol_weight": 0.3, "max_positions": 1}
```

这表示总仓最多50%、普通单票最多30%、最多部署1只，不要求用满额度。
较低设置可能得到有候选但预算不足的有效结果；最小参与金额、整手与资格确认不会降低。
少持仓不一定更安全，也不保证相同收益或更高收益。观察所需的三成员战略资格与实际
持仓名额分开；容量不足的完整部署被拒绝，原有单／双成员路线仍按各自证据要求评估。

```bash
uv run uquant account-init --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh603688 sh688012 \
  --date 2026-08-05 --config settings.json --output account_state.json
uv run uquant daily --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh603688 sh688012 \
  --date 2026-08-05 --config settings.json --account account_state.json \
  --output daily_report.md --html-output daily_report.html
uv run uquant backtest --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh603688 sh688012 \
  --start 2026-08-04 --end 2026-08-05 --config settings.json --output backtest.json
```

上述固定历史日期用于运行入口示例，不是当天交易建议。真实日扫使用已取得完整数据的
盘后日期和最新券商快照，同一账户不能重复决策同一日。省略 `--cash` 时，新账户采用
选定配置的初始资金；显式 `--cash` 与配置中显式 `initial_cash` 不同时报错。
已有账户的实际现金只来自账户／券商同步，不从配置重置。

初始化将选定配置指纹记录在账户的现有身份记录中。日扫必须使用同一配置；
没有配置绑定记录的现存账户只允许默认配置，并继续核验代码、数据和战略身份。
本入口不提供更换已有账户配置的自动迁移，不通过重新初始化绕过持仓和在途责任。
`account-code-migrate` 仅处理代码身份，不授权改变配置。价格漂移和未完成订单可能令
实际持仓暂时不同于目标和上限；报告不会将目标当作已经成交。

查看公开设置和完整有效规则：

```bash
uv run python - <<'PY'
import json
from dataclasses import asdict
from uquant.config import DEFAULT_CONFIG
print(json.dumps(asdict(DEFAULT_CONFIG), indent=2, sort_keys=True))
print(json.dumps(DEFAULT_CONFIG.to_dict(), indent=2, sort_keys=True))
PY
```

`config/policies.py` 按组合、特征、龙头选择、恢复、战略、机会、风险和行业风险所有者
保存 253 条固定规则。它们没有构造参数或覆盖入口。`to_dict()` 是完整的只读有效策略
摘要，包含公开设置和固定规则，不是构造配置的输入文件。下文的阈值表说明规则含义，
并不增加可调入口。固定规则内化没有消除其经济自由度或过拟合风险。

## 生产与验收边界

配置只面向 2023 年以来的 A 股 AI 产业链现金多头组合，运行频率为日频，收盘后决策、下一交易日执行，并保留人工核对环节。经济性验收不得早于 `2023-01-01`；更早数据只允许作为特征 warm-up，不得用于收益、回撤、订单或换手门槛。唯一支持的运行时是 Python 3.12。

`_decision_config_for_universe()` 对所有股票池大小都返回同一个传入配置。生产决策使用持久化 leader tenure，行业稀疏度按 `industry_signal_min_members` 收缩到中性值，group-balanced reference 只写诊断证据，风险票数按基础因果指标累计。

## 规则身份与验证情境

当前治理由打包资源 `uquant/contracts/resources/config_policy_governance.json` 描述，
每个有效名称有唯一分类和所有者。`config_fingerprint()` 覆盖完整有效载荷；源码指纹
同时覆盖规则定义、治理资源和具名验证实现。历史治理和结果保留原身份，仅在历史证据
校验中读取，不作为当前配置迁移步骤。

生产 `ProductionEngine(...)` 只接受精确的 `SystemConfig`，拒绝自定义配置子类和
替代对象。`uquant.validation.parameter_policy.validation_engine(data_dir, profile, cfg)` 是同一实现的离线
验证入口，只接受具名情境和精确的公开基础配置：冻结合同的 `p2_lower/upper`、
`p7_lower/upper`、`p8_lower/upper`、`confirmation_lower/upper`，以及既有回放测试的
`recovery_breadth_lower/upper`。这 10 个情境扰动 5 条固定规则；不接受任意规则名和值。
费用压力继续使用公开费用设置。验证结果记录实际完整配置及源码身份，不作为生产配置。

官方 Generalization 的六个窗口、基准种子 `20260810`、索引 `0..4`、池大小
`5 / 9 / 15 / 20` 是验证输入，不是 `SystemConfig` 调参项。future holdout 从
`2026-08-06` 起只做观察，不能据其表现改参数；2023 年以前的数据仍只作 warm-up。

## 组合固定规则

| 参数 | 默认值 | 作用 |
|---|---:|---|
| `industry_weight_cap` | 0.75 | 单行业最大权重 |
| `min_trade_weight` | 0.05 | 常规最小权重变化 |
| `restoration_min_trade_weight` | 0.05 | 恢复补仓最小权重变化 |
| `protected_restore_min_trade_weight` | 0.04 | 受保护核心的恢复门槛 |


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
| `core_admission_weight` | 0.20 | 独立确认的新核心初始权重上限 |
| `max_gross` | 1.00 | 账户总仓硬上限，仍取当日 Risk cap |
| `industry_weight_cap` | 0.75 | 一般行业与相关风险簇的共同上限 |
| `market_crisis_gross` | 0.50 | 常规危机上限 |
| `severe_crisis_gross` | 0.20 | 严重冲击上限 |

所有新风险和常规目标最终都取自身目标与 `target_gross_cap` 的较小值。唯一冻结行为例外是
单一战略主导者在一级预警下保留已有权重；该例外不新增风险，并受下文边界约束。

## 领涨、持有与替换

| 参数 | 默认值 |
|---|---:|
| `leader_mature_score` | 0.72 |
| `leader_emerging_score` | 0.76 |
| `leader_min_confidence` | 0.70 |
| `leader_tenure_days` | 5 |
| `min_hold_days` | 10 |
| `replacement_edge` | 0.35 |
| `replacement_confirm_days` | 3 |
| `replacement_transfer_cap` | 0.30 |
| `max_rotations_20d` | 2 |

降低确认期或替换优势通常会增加换手；提高领涨门槛会减少持仓机会并增加现金时间。
确认按证券和证据类型累积；新核心使用共享预算，不按旧路径的整本目标仓位重新分配。
原账户范围的退出冷却和最少成员变化控制已删除。单票真实失效会重置自身资格，账户风险
修复进度与资本高水位保持独立。空缺配置键或无效参数仍由正常配置校验报错。

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
| `strategic_two_name_confirm_days` | 3 | `STRONG_PAIR` 资格连续确认期 |
| `strategic_one_name_gross` | 0.50 | 单成员总仓 |
| `strategic_one_name_confirm_days` | 4 | `ABSOLUTE_SINGLE` 资格连续确认期 |
| `strategic_cohort_profit_arm` | 0.10 | ATR 保护启动 MFE |
| `strategic_cohort_trail_atr` | 3.55 | ATR 保护距离 |
| `strategic_cohort_disaster_stop` | -0.20 | 灾难退出线 |

`strategic_cohort_symbols` 是账户状态字段，不是 `SystemConfig` 参数；新账户初始为空，
成员只能从调用方给出的固定全集中按因果证据动态产生。至少三名候选的 `FULL_COHORT`
使用 `strategic_cohort_confirm_days=2`；两名候选的 `STRONG_PAIR` 使用 3 日；单名候选的
`ABSOLUTE_SINGLE` 使用 4 日并满足更高分数门槛。成员路由由当日合格候选数和证据决定，
不按调用方证券全集大小切换。

`strategic_dominant_max_weight=0.95` 只约束单一战略主导者特例。账户必须只有该主导者，
Risk 必须为 `NORMAL/CAUTION`、`reduction_level <= 1`，且不存在 sector guard、strategic
damage guard 或 acute evacuation；策略目标还必须不低于当前总仓。满足条件时系统只保留
既有权重，不会买入补足至 95%。`CRISIS`、更高减仓等级和显式 guard 始终严格执行风险 cap。
机器可核对的完整契约见
[ADR 0001](decisions/0001-economic-authority-and-causal-execution.md)。

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

资本预算阶梯默认开启；风险状态消费同一份因果证据，并按基础指标累计票数。改变风险阈值会显著影响回撤和恢复速度，必须重新运行完整 AI-era 门禁。

### Risk Sentinel 生产边界

| 参数 | 生产默认值 | 边界 |
|---|---:|---|
| `risk_sentinel_mode` | `FREEZE_ONLY` | 仅允许 `SHADOW` / `FREEZE_ONLY` |
| `risk_sentinel_min_confidence` | 0.80 | 冻结生产阈值；离线分析不得改写 |
| `risk_sentinel_confirm_days` | 2 | 完整市场历史诊断 |
| `risk_sentinel_repair_days` | 3 | 完整市场历史诊断 |
| `risk_sentinel_causal_confirmation_enabled` | false | 无独立经济增量证据，不授予新增生产权限 |

`LIMITED_GROSS_CAP` 会抛出明确拒绝错误；`SENTINEL_EXCLUSIVE_FREEZE` 和其他未知值
同样无效。Sentinel 不修改基础 Risk state、总仓上限、减仓等级、冲击状态或资本预算，
也不产生 SELL、目标、订单、Fill 或账户字段。

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

## 变更规则

日常运行只需维护证券范围、交易日、账户事实、数据来源、输出位置，以及实际适用的
费用和执行约束。固定规则修改属于源码和策略变更，须另有明确授权，并遵循适用的
收益、回撤、成本、交易次数和证据合同；不能以单个高收益窗口选择规则。

任何被接受的默认值变化都必须重新运行性能验收和完整的六窗口泛化验收。
治理类别 `MARKET_RULE`、`SAFETY`、`ECONOMIC`、`DERIVED` 描述规则责任；
它们不授予额外的生产配置入口。当前固定规则仍纳入完整有效配置指纹。
