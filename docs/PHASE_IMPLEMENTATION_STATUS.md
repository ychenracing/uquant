# Phase 0–9 Implementation Status

结论：Phase 0–9 的统一实现、共同适配器和非保留窗验证已贯通。当前严格状态为 **NOT FULLY ACCEPTED / CANDIDATE（73/74）**，唯一失败是已经单次消费且不可重用的 K2 promotion holdback。机器判定以根目录 `acceptance_results.json` 为准；K2 的根因和修复边界见 `docs/PROMOTION_POSTMORTEM.md`。

| Phase | 实现状态 | 验证状态 | 主要证据 |
|---|---|---|---|
| 0 Unified Benchmark | IMPLEMENTED | PASS | 三旧仓库冻结锁；`legacy_common_adapter.json` 包含 3 系统 × 5 池 × 9 窗口 = 135 个 common-contract 实跑单元；矩阵完整性 PASS。 |
| 1 Minimal Unified Skeleton | IMPLEMENTED | PASS | 单一 `DataStore`、`AccountState`、`ProductionEngine`、next-open 执行器、唯一 target 与日报；确定性和 fail-closed 契约通过。 |
| 2 Trend + Recovery | IMPLEMENTED | PASS | Trend/Recovery/No-trade、Shock、probe、Add1/Add2、冷却和恢复确认均在同一生产路径；对应生命周期和成本归因测试通过。 |
| 3 Leader Intelligence | IMPLEMENTED | PASS | 固定 Reference、point-in-time mature/emerging/unknown、tenure、置信度、replacement edge 与 20d/40d spread 证据通过。 |
| 4 Independent Risk Radar | IMPLEMENTED | PASS | breadth、行业、相关性、波动、leader failure、operating/capital DD、风险提前量、误报和反事实 RiskUtility 均有三方矩阵证据。 |
| 5 Opportunity × Risk | IMPLEMENTED | PASS | Opportunity 与 Risk 独立计算，只在唯一 allocator 汇合；Risk 只给约束，不直接成交。 |
| 6 Unified Allocator | IMPLEMENTED | PASS | 集中度、行业/相关性、动态 K、迟滞、rotation/replacement、add/drop/permutation 和每票唯一 target 均通过。 |
| 7 Capital DD / Tail Guard | IMPLEMENTED | PASS | 双峰值持久化、集中结构破坏、severe shock、恢复/rearm、CAUTION gross cap 和战略新仓风险门禁已统一；急性风险与随机压力门通过。 |
| 8 Validation / Promotion | IMPLEMENTED | 73/74 | 963 场景、180 个参数/成本/容量实验、54 个 nested walk-forward 单元、PBO/DSR/Pareto 均通过；长跑起止签名一致，混合版本证据会 fail closed；K2 单次保留窗真实失败，修复后必须等待新未来窗口。 |
| 9 Remove Legacy Dependencies | IMPLEMENTED | PASS | 正式包不 import、shell 调用或运行时依赖 `qwenquant`、`aquant`、`trade`；旧项目仅作为冻结基准和 common adapter 输入。 |

## 关键实跑结果

共同 bull 窗口为 2025-04-01 至 2026-06-30，初始资金 200 万元。五个 pool 的 Unified 财富均满足 `>= 99% × best_old`，回撤和订单门均通过，且没有被旧系统严格支配的单元。

| 指标 | 当前结果 |
|---|---:|
| pool-b Unified 财富 / 最大回撤 / 账户订单 | 13.1098x / 15.96% / 10 |
| pool-b 三旧系统最佳财富 | 12.7595x（qwenquant） |
| 45 个主要单元 return / DD / orders 通过率 | 66.67% / 60.00% / 82.22% |
| 被任一旧系统严格支配的单元 | 0 |
| 随机压力样本 | 900 |
| 随机 return 中位数 / p10 / 最差 | 164.48% / 1.52% / -14.73% |
| 随机 DD p90 / 最差 | 17.23% / 20.87% |
| 随机订单 p90 / 最差 | 16 / 26 |
| 参数/成本/容量实验 | 180 |
| Nested walk-forward 单元 | 54 |
| PBO / DSR | 0.667 / 0.9975 |

## Promotion 边界

- A–N 与矩阵完整性共 73 个非 K2 检查通过。
- K2 在 2026-07-21 至 2026-08-05 的 12 个 session 上被正确消费一次，结果为 `CONSUMED_FAIL`。
- 修复后对已消费日期的诊断重放五池都低于原 17% loss/DD 限制，但这不是新的 promotion 证据。
- 修复后的候选已冻结为 production `f8fdd2ab989f`、validation `e35831e29770`、config `5821f769d963`；正式 stress/robustness 产物均与这些签名及截至 2026-07-20 的数据指纹匹配。
- 新的单次窗口已在 `benchmarks/PROMOTION_HOLDBACK_NEXT.json` 预注册为 2026-08-06 至 2026-08-21、12 个预期交易日，门槛保持每池 loss/DD 严格小于 17%。本地冻结数据目前截至 2026-08-05，因此状态为 `PENDING_FUTURE_DATA`，没有 canonical data hash，也没有运行任何新 K2 指标。
- 只有新窗口完整到齐、先封存数据 hash、再用同一签名候选单次达到 74/74，才能标记 Production。
