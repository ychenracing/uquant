# Risk Differential Closure — 2026-08-21

## 结论

最终离线判定为 `NO_INCREMENTAL_PROMOTABLE_RISK_CAPABILITY`。`trade` 仍有两类结构差异：
`sleeve agreement` 观察和 `graded trim` 执行政策；前者不可转移为生产动作，后者会复制第二套
风险执行权。现有因果和经济证据均未证明它们具有可晋级的独立增量价值。因此本阶段不增加
Risk Sentinel 权限、不修改阈值、不恢复 Phase 5 gross cap 或 Phase 7 exclusive Freeze。

在当前证据条件下，Risk Sentinel 已接近合理终态：继续扩展的预期边际收益不足以补偿复杂度、
执行冲突和过拟合风险。

## 固定边界与 Provenance

- uquant 起始 main：`ba314003044a229969270bee6854240dfb7f211e`
- trade 只读 challenger：`2066fbf0f99be94142c5d0cb0b6c99d276c2472d`
- trade risk source SHA-256：`1e8a0a33d76b99fce9e741190b71d81bc35877ee9bb485441c7397fd6a46fda1`
- 覆盖：30 个 official cells、132 个 READY generalization cells、102 个显式非经济/失败 rows
- 逐日共同 sessions：40,049
- challenger trace 已 source-bound、canonical sealed；运行固定 `PYTHONHASHSEED=0`
- 所有 outcome 定义先冻结事件身份，再离线填充 1/3/5/10/20 日结果

## Capability Inventory

共 33 项能力。机器清单不允许 `UNKNOWN`，且全部
`production_promotion_allowed_this_phase=false`。

| Mapping | 数量 | 含义 |
|---|---:|---|
| `ABSORBED_BASE` | 10 | Base Risk 已承担 |
| `ABSORBED_SENTINEL` | 8 | Sentinel 已观察 |
| `ABSORBED_ARCHITECTURALLY` | 4 | uquant 架构已有等价所有权/语义 |
| `PARTIAL_EQUIVALENT` | 7 | 部分等价，只能固定 shadow 研究 |
| `INCREMENTAL_OBSERVATIONAL` | 1 | sleeve agreement，只作观察 |
| `INCREMENTAL_EXECUTION_POLICY` | 1 | graded trim，不可复制第二执行权 |
| `REJECTED_PREVIOUSLY` | 2 | 历史 negative controls |

动作转移分类为：17 `DIRECTLY_REPLAYABLE`、7 `TRANSLATABLE`、2
`HYBRID_DIAGNOSTIC`、7 `NON_TRANSFERABLE`。

## 三方因果 Differential

修正 Base 标准化后，风险等级的逐日集合为：

| 集合 | sessions |
|---|---:|
| `AGREE_ALL` | 21,192 |
| `BASE_ONLY` | 10,235 |
| `SENTINEL_ONLY` | 1,517 |
| `TRADE_ONLY` | 530 |
| `TRADE_AND_SENTINEL_NOT_BASE` | 90 |
| `TRADE_AND_BASE_NOT_SENTINEL` | 961 |
| `BASE_AND_SENTINEL_NOT_TRADE` | 5,524 |

530 个 trade-only warning days 合并为 398 个非重叠 episodes，其中只有 6 个 episode 在当日存在
任何 BUY/pyramid 可行动意图。按具体动作轴，entry freeze 有 0 个 actionable episode；pyramid
freeze 只有 1 个 actionable episode、且只覆盖 1 个 window/1 个 family；trade-only gross-cap
episode 为 0。因此多数表面 differential 是重复风险描述或对当前组合无动作增量。

`trade` 的 warning median lead 为 13 sessions，Base 为 2；但 trade precision 16.67%，低于
Base 21.07%，recall 41.94%，显著低于 Base 66.94%。提前量没有在保持 precision/recall 的
条件下形成检测增量，detection gate 未通过。

## Portfolio Counterfactual

Shadow 只在深拷贝账户运行，复用真实 close-decision/next-open、T+1、100 股手数、费用、滑点、
涨跌停/停牌、订单生命周期与账户 netting。没有使用 `equity × ratio` 数学缩放。

| Policy | 触发 | Wealth retention | MDD 改善 | Acute-loss 改善 | Orders | Turnover | 判定 |
|---|---:|---:|---:|---:|---:|---:|---|
| Entry freeze | 0 | 100% | 0 | 0 | 0 | 0 | `INSUFFICIENT_SAMPLE` |
| Pyramid freeze | 0 official | 100% | 0 | 0 | 0 | 0 | `INSUFFICIENT_SAMPLE` |
| Limited gross cap | 16 | median 83.59%; worst 75.29% | median +2.05pp | best +6.67pp | +6；单元最高 +150% | +1.582；最高 +47.90% | reject |
| Layered protection | 3 | median 97.95%; worst 94.67% | median 0；worst -0.055pp | best +1.90pp | +3；最高 +20% | +1.870；最高 +18.77% | reject |
| Weakest-cluster trim | 0 | 100% | 0 | 0 | 0 | 0 | `HYBRID_DIAGNOSTIC_ONLY` |

Limited gross cap 的急跌/MDD 保护最大，但收益留存和交易成本退化远超预注册门槛；layered
protection 也没有保持收益、订单和 turnover。两者均不得因局部保护改善而晋级。

## Promotion 与 Negative Controls

没有候选同时通过 sample、detection、economic、generalization、bull-silence 和 causal-validity
门禁。Phase 5 `LIMITED_GROSS_CAP` 保持 `REJECTED`；Phase 7 exclusive causal Freeze 仍为
`REJECTED`，框架没有错误恢复历史拒绝路线。

## Future Holdout

`risk_differential_shadow` lane 从 2026-08-24 起 observation-only、append-only、no-backfill。
source/config/data-contract/activation 均不可变，`parameter_changes_from_observation=false`。
当前 0 个真实 sessions，状态为 `OBSERVING / NON_REVIEWABLE`，formal scores 为 `null`。

## Production Economic Equivalence

本阶段未修改任何 `uquant/**/*.py`。45-cell committed baseline/candidate 矩阵通过：两侧 trace
SHA-256 均为 `8dab09e7019c1f0c8ea4e21f1f061260a2c4eb25a87abd8d75da73f86cab57f3`。
Decision Digest、RiskAssessment 控制字段、targets、orders、fills、经济 AccountState、final
wealth、MDD 和 trade/order count 全部 exact 相等。

## 机器证据

- `capability_inventory.json`：完整清单与统计
- `risk_differential_matrix.json` / `risk_differential_daily.json.gz`：三方逐日事实
- `exclusive_events.json` / `event_outcome_analysis.json`：冻结事件和结果
- `counterfactual_raw.json` / `counterfactual_summary.json`：真实执行语义模拟
- `promotion_analysis.json` / `closure.json`：门禁、negative controls 与 terminal decision
- `trade_challenger_trace.json.gz`：固定 challenger trace
- `production_economic_equivalence.json`：45-cell exact production 等价证明
