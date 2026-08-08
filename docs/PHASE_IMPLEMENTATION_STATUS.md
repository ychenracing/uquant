# Phase 0–9 Implementation Status

结论：单一生产内核、正确性契约和可用数据上的正式验证已经贯通；严格替代状态仍为 **NOT FULLY ACCEPTED / CANDIDATE**。完整逐项映射见 `docs/IMPLEMENTATION_AUDIT.md`，机器判定见根目录 `acceptance_results.json`。

| Phase | 实现状态 | 验证状态 | 当前证据与硬缺口 |
|---|---|---|---|
| 0 Unified Benchmark | PARTIAL | FAIL | 三旧仓库及基线已冻结；pool b 完成同数据/费用/账户/next-open 实跑。qwenquant 原适配器误含盘中退出，已保留原值并以更强的纯 next-open 结果更正。其余四池缺少三旧系统共同适配器矩阵；数据始于 2022-01-04，不能执行 2018/2020/2021。 |
| 1 Minimal Unified Skeleton | IMPLEMENTED | PASS | `DataStore`、单一 `AccountState`、唯一 next-open 执行、费用/T+1、唯一 target、日报及逐日确定性回放完成；A/B/M 类命名测试通过。 |
| 2 Trend + Recovery | PARTIAL | PARTIAL | 趋势、恢复、no-trade、Shock、两日恢复确认、战术 probe 与严重恢复仓位上限已进入生产路径；Add1/Add2 仅保留类型/配置，尚未形成独立生产加仓状态机。 |
| 3 Leader Intelligence | PARTIAL | PARTIAL | 固定 Reference、point-in-time score、mature/emerging/unknown、tenure 和置信度契约通过；replacement edge 的正式轮换路径及 20d/40d replacement spread 未完成。 |
| 4 Independent Risk Radar | IMPLEMENTED | PARTIAL | 独立 reference/行业篮子、breadth、相关性、波动、leader failure、账户双回撤、L1/L2/L3 等价风险状态及 tail guard 已统一；三旧系统 lead-time、误报标签与 RiskUtility 对照不完整。 |
| 5 Opportunity × Risk | IMPLEMENTED | PASS for architecture | Opportunity 和 Risk 独立计算，Risk 只输出唯一 gross cap，两个轴只在 allocator 汇合；无多风险层直接成交。 |
| 6 Unified Allocator | PARTIAL | PARTIAL | 单一 allocator、集中度、流动性、恢复 cohort、输入池不变性、迟滞和唯一 target 已实现；`effective_n` 已实现但未驱动生产动态 K，通用 rotation/replacement 状态机未完成。 |
| 7 Capital DD / Tail Guard | IMPLEMENTED | PASS for available stress | operating/capital peak 独立持久化、集中结构破坏、severe shock、恢复确认、90 日 rearm 与保护权重完成。900 random p90/最差 DD 为 17.20%/21.14%，优于 trade；五池 July 均低于 17%。 |
| 8 Validation / Promotion | IMPLEMENTED | FAIL promotion | 954 场景、±5%/±10%/双参数、成本/容量、3 折 nested walk-forward、PBO/DSR 和 Pareto 均已实跑并签名；remove-one -76.36%、PBO 0.667，且无法追溯声明 untouched holdback。 |
| 9 Remove Legacy Dependencies | IMPLEMENTED | PASS | 正式包不 import 或 shell 调用旧项目；旧项目仅以冻结 JSON 基准存在。CLI、报告、24 个测试和签名验收产物已齐备。 |

## 关键实跑结果

共同 pool b：`sz300308, sz300502, sz300394, sh688008, sh603986`；bull 窗口：`2025-04-01` 至 `2026-06-30`；初始资金 200 万元。

| 系统 | 期末财富 | 最大回撤 | 账户订单 | 比较口径 |
|---|---:|---:|---:|---|
| qwenquant | 12.7595x | 20.59% | 9 | 盘中退出已禁用的 common next-open 更正值 |
| aquant | 7.9553x | 17.46% | 30 | common adapter |
| trade | 4.7861x | 15.52% | 229 | common adapter |
| unified-ai-quant | 12.6454x | 15.50% | 11 | 当前生产引擎 |

本单元 `wealth_new >= 99% * best_old` 且 `DD <= best_old +0.5pp`，严格支配单元数为 0；但 C3 的 11 对 9 订单未获 5% 财富提升，因此交易经济性仍 FAIL。

## 已通过与剩余阻断

已通过：A1–A10、B1–B5、D1、D2、E1–E3、G1–G3、H2–H3、I1/I3/I4、J1–J4、K1/K3/K4、L1–L3、M1–M11、N1–N3、`dominated_cells=0`。

仍阻断 Production：C1/C2 三方矩阵不完整、C3/C4、D3/D4 的三方证据、2024 震荡期、F1–F4、G4、H1、I2、K2、O 类专项与 `MATRIX_COMPLETENESS`。缺失证据按 FAIL 处理，不能由局部优秀结果替代。
