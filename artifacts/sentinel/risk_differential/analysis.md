# Risk Differential Closure — 2026-08-21

> **历史证据快照：** 本文解释本目录中的冻结差分与 counterfactual 结果，不授予当前生产
> 权限。当前权限合同见 [`docs/RISK_SENTINEL.md`](../../../docs/RISK_SENTINEL.md)。

## 结论

报告闭集判定为 `NO_PROMOTABLE_INCREMENTAL_RISK`；面向项目的结论代码为
`NO_INCREMENTAL_PROMOTABLE_RISK_CAPABILITY`。`trade` 仍有两类结构差异：
`sleeve agreement` 观察和 `graded trim` 执行政策；前者不可转移为生产动作，后者会复制第二套
风险执行权。现有因果和经济证据均未证明它们具有可晋级的独立增量价值。因此本阶段不增加
Risk Sentinel 权限、不修改阈值、不恢复 Phase 5 gross cap 或 Phase 7 exclusive Freeze。

在当前证据条件下，Risk Sentinel 已接近合理终态：继续扩展的预期边际收益不足以补偿复杂度、
执行冲突和过拟合风险。

## 固定边界与 Provenance

- uquant 起始 main：`ba314003044a229969270bee6854240dfb7f211e`
- trade 只读 challenger：`2066fbf0f99be94142c5d0cb0b6c99d276c2472d`
- trade 完整 Python source SHA-256：`48280acee356ee4bd28fa83b260426f3025e6b3bd93c1cee2f92188486761b90`
- trade risk source SHA-256：`f954da461936eac61ef7b31c569c30dfeaf44c860554a1cc4b515475cf222cfb`
- trade lock-files SHA-256：`182d6bbfc2dba29d568f521ee765de335227e721e783a8a9a9cdfef436db7ba2`
- 覆盖：30 个 official cells、132 个 READY generalization cells、102 个显式非经济/失败 rows
- 逐日共同 sessions：40,049
- challenger checkout 必须是实际 Git worktree；框架同时验证 `git rev-parse HEAD`、完整 Python
  source hash、risk source hash 和全部 lock-file hash。仅有自报 SHA marker 的导出会被拒绝。
- challenger trace 已 source-bound、canonical sealed；运行固定 `PYTHONHASHSEED=0`
- 所有 outcome 定义先冻结事件身份，再离线填充 1/3/5/10/20 日结果

## Capability Inventory

共 34 项能力（含显式拆分的 early sector risk）。机器清单不允许 `UNKNOWN`，且全部
`production_promotion_allowed_this_phase=false`。

| Mapping | 数量 | 含义 |
|---|---:|---|
| `ABSORBED_BASE` | 10 | Base Risk 已承担 |
| `ABSORBED_SENTINEL` | 8 | Sentinel 已观察 |
| `ABSORBED_ARCHITECTURALLY` | 4 | uquant 架构已有等价所有权/语义 |
| `PARTIAL_EQUIVALENT` | 8 | 部分等价，只能固定 shadow 研究 |
| `INCREMENTAL_OBSERVATIONAL` | 1 | sleeve agreement，只作观察 |
| `INCREMENTAL_EXECUTION_POLICY` | 1 | graded trim，不可复制第二执行权 |
| `REJECTED_PREVIOUSLY` | 2 | 历史 negative controls |

动作转移分类为：17 `DIRECTLY_REPLAYABLE`、7 `TRANSLATABLE`、3
`HYBRID_DIAGNOSTIC`、7 `NON_TRANSFERABLE`。

## 三方因果 Differential

风险等级与 gross-cap 均按原始标量比较（不再压缩成布尔值）。风险等级的逐日集合为：

| 集合 | sessions |
|---|---:|
| `ALL_AGREE` | 20,982 |
| `ALL_SILENT` | 20,897 |
| `BASE_ONLY` | 10,984 |
| `SENTINEL_ONLY` | 4,826 |
| `TRADE_ONLY` | 649 |
| `TRADE_AND_SENTINEL_ONLY` | 35 |

649 个 trade-only warning days 合并为 409 个非重叠 episodes，其中只有 6 个 episode 在当日存在
任何 BUY/pyramid 可行动意图。按具体动作轴，entry freeze 有 0 个 actionable episode；pyramid
freeze 只有 1 个 actionable episode、且只覆盖 1 个 window/1 个 family；24 个 trade-only gross-cap
episodes 没有可行动意图。因此多数表面 differential 是重复风险描述或对当前组合无动作增量。

固定事件身份共有 1,218 个逐日事件、809 个非重叠 episodes。1,144 个事件具有完整 20-session
outcome；末端 74 个严格标记为 right-censored，没有用缩短 horizon 冒充完整结果。

`trade` 的 warning median lead 为 13 sessions，Base 为 2；但 trade precision 17.99%，低于
Base 22.38%，recall 41.94%，显著低于 Base 66.94%。提前量没有在保持 precision/recall 的
条件下形成检测增量，detection gate 未通过。

## Portfolio Counterfactual

8 个策略在全部 30 个 official cells 上各运行一次，共 240 次生产引擎回放；另外以固定三策略
在 132 个 generalization cells 上运行 396 次，总计 636 次；没有复制 baseline 行来伪造策略结果。
Shadow 只在深拷贝账户运行，复用真实 close-decision/next-open、T+1、100 股手数、费用、滑点、
涨跌停/停牌、订单生命周期与账户 netting。没有使用 `equity × ratio` 数学缩放。

| Policy | 触发 | Wealth retention | MDD 改善 | Acute-loss 改善 | Orders | Gross turnover | 判定 |
|---|---:|---:|---:|---:|---:|---:|---|
| Entry freeze | 37 / 19 cells | 100% | 全部 0 | 全部 0 | 0 | 0 | `INSUFFICIENT_SAMPLE` |
| Pyramid freeze | 421 / 30 cells | 100% | 全部 0 | 全部 0 | 0 | 0 | `INSUFFICIENT_SAMPLE` |
| Limited gross cap | 243 / 30 cells | median 93.88%; worst 75.29% | median 0；局部最大 +4.103pp；最差 -0.072pp | 无改善；最差 -6.673pp | 总计 +52 | 总计 -16.150 | reject |
| Layered protection | 21 / 16 cells | median 98.89%; worst 17.26% | median 0；局部最大 +5.906pp；最差 -1.078pp | 无改善；最差 -1.903pp | 总计 -16 | 总计 -128.764 | reject |
| Weakest-cluster trim | 0 | 100% | 0 | 0 | 0 | 0 | `HYBRID_DIAGNOSTIC_ONLY` |

Layered protection 的单单元 MDD 改善最大（+5.906pp），gross cap 次之（+4.103pp），但两者的
中位数 MDD 改善均为 0，也没有 acute-loss 改善；最差 wealth retention 分别只有 17.26% 与
75.29%。Turnover 下降来自风险退出/少持仓，却没有保持收益，不能当作成本效率提升。两者均
不得因局部保护改善而晋级。

## Promotion 与 Negative Controls

没有候选同时通过 sample、detection、economic、generalization、bull-silence 和 causal-validity
门禁。Phase 5 `LIMITED_GROSS_CAP` 在 `9a82143a...` detached rerun 中精确复现归档 SHA，wealth
retention 91.85%、MDD 无改善、订单 +4、turnover +1.209，保持 `REJECTED`。报告给出的 Phase 7
SHA `c559c009...` 在本地与远端均不可达；框架显式记录该事实，并在可达的 reviewed terminal
`1441b8f4...` 上复跑、绑定 archive `239d7957...`：1 个 exclusive event、0 个 blocked BUY，
经济行为 exact 相等，仍为 `REJECTED`。框架没有错误恢复历史拒绝路线。

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
- `negative_controls_rerun.json`：锁定提交上的历史负控复跑
- `promotion_analysis.json` / `closure.json`：门禁与 terminal decision
- `trade_challenger_trace.json.gz`：固定 challenger trace
- `production_economic_equivalence.json`：45-cell exact production 等价证明
