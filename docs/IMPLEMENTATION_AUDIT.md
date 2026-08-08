# Implementation Report Item-by-Item Audit

本表逐项对应 `docs/IMPLEMENTATION_SPEC.md`。状态含义：`DONE` 为生产路径已实现且有证据；`PARTIAL` 为部分实现或缺正式对照；`BLOCKED` 为当前冻结数据/实验历史无法补做；`NOT DONE` 为代码尚未落实。验收结果以 `acceptance_results.json` 为准。

## 1–3. 定位、融合来源与核心原则

| 报告项 | 状态 | 落实位置 / 证据 | 剩余问题 |
|---|---|---|---|
| 单一日频现金多头系统 | DONE | `engine.py`, `account.py`, `cli.py` | 无 |
| qwenquant 生产骨架、低换手、Trend/Recovery | PARTIAL | `engine.py`, `execution.py`, `opportunity.py`, `portfolio.py` | Add1/Add2 独立状态机未进入生产 |
| AQuant fixed-reference Leader Intelligence | PARTIAL | `leader.py`; G1–G3 PASS | G4 replacement spread 未完成 |
| trade Risk Radar 与 Formal Stress | DONE | `risk.py`, `validation/stress.py`; D2/E2 PASS | O-trade 仍被 I2 remove-one 控制为 FAIL |
| 一个账户 | DONE | 唯一 `AccountState`; B3 PASS | 无 |
| 每票每天一个最终 target | DONE | `PortfolioAllocator._targets`; B5 PASS | 无 |
| Opportunity 与 Risk 分离 | DONE | `opportunity.py` 与 `risk.py` 独立，统一在 allocator 汇合 | 无 |

## 4–5. 数据与特征

| 报告项 | 状态 | 落实位置 / 证据 | 剩余问题 |
|---|---|---|---|
| 正式 QFQ 股票/原始指数数据 | DONE | `data/frozen`, `DataStore`, Phase-0 common contract | 公司行动的在线人工复核仍属实盘责任 |
| OHLCV/日期/重复/缺失契约 | DONE | `DataStore._validate`; A/B/M tests | 无 |
| SHA-256 manifest / fail closed | DONE | `DataStore.manifest`, account code/data hash | 无 |
| 上市前历史可见性 | DONE | `visible_users` point-in-time 过滤；专门回归测试 | 无 |
| 固定 AI Reference Universe | DONE | `REFERENCE_UNIVERSE`; G1 PASS | 历史成分变更数据未提供 |
| 趋势特征 | DONE | `features.py` point-in-time rolling features | 无 |
| Leader 特征与置信度 | DONE | `leader.py`; mature/emerging/unknown tests | replacement attribution 缺失 |
| Risk breadth/行业/相关性/波动/tail | DONE | `risk.py` | lead-time/false-positive 正式标签缺失 |

## 6–9. Opportunity、Risk、Lead-Time、Leader

| 报告项 | 状态 | 落实位置 / 证据 | 剩余问题 |
|---|---|---|---|
| STRONG_TREND / TREND / RECOVERY / CHOPPY / WEAK | DONE | `Opportunity` enum 与 `classify_opportunity` | 2024 choppy 经济表现 FAIL |
| Opportunity 迟滞与恢复确认 | DONE | evidence run + 两日 `recovery_stable`；±10% breadth 路径回归 | 无 |
| NORMAL / CAUTION / RISK_OFF / CRISIS | DONE | `assess_risk` 唯一状态机 | F3 形式误报率 2.40 次/年且标签未完成 |
| Operating DD 与 Capital DD 分离 | DONE | `_portfolio_drawdowns`, persisted peaks | 无 |
| Shock / failed repair / recovery | DONE | protected weights、severity、repair streak、rearm | H2/H3 PASS；H1 老系统对照缺失 |
| Risk lead-time 正式目标 | PARTIAL | risk event ledger、2026-07-02 CRISIS | F1/F2/F4 缺三方事件/反事实 |
| Mature winner hold | DONE | tenure、sticky anchors | C4 老系统 false-exit regret 不可比 |
| Emerging/Unknown | DONE | history/confidence gates | 无 |

## 10–16. Allocator、生命周期、轮换、执行、状态与日报

| 报告项 | 状态 | 落实位置 / 证据 | 剩余问题 |
|---|---|---|---|
| 最大 6 持仓、单票 60%、总仓 100% | DONE | config + allocator/execution hard checks；A7/A8 PASS | 无 |
| 有证据集中、行业/相关性意识 | PARTIAL | leader/recovery cohort、risk industry/correlation | allocator 未直接使用行业权重投影 |
| Effective N | PARTIAL | `effective_n()` 已实现 | 未驱动生产动态 K |
| CORE | DONE | sticky anchor lifecycle | 无 |
| ADD1 / ADD2 | NOT DONE | 类型和阈值存在 | 未形成可验证的生产加仓状态机 |
| SATELLITE | NOT DONE | 类型存在 | 未形成生产 sleeve；当前选择少而稳的单 allocator |
| RECOVERY | DONE | tactical probe、cohort、protected restoration | H2/H3 PASS |
| Rotation / replacement edge | PARTIAL | tenure/config/state字段存在 | 通用替换路径及 G4 spread 未完成 |
| No-trade / hysteresis | DONE | sticky holdings、target thresholds、pending-order merge | C3 仍多 2 单 |
| close→next-open、T+1、涨跌停/停牌 | DONE | `execution.py`; A2–A5/M1–M3 PASS | 无 |
| 手数、科创板 200 股、费用/滑点/容量/部分成交 | DONE | `execution.py`; A6/A9/M4/M9–M11, L1–L3 PASS | 无 |
| 账户峰值/tenure/shock/cooldown 持久化 | DONE | `AccountState`, `account.py`; B3 PASS | 部分预留 rotation 字段尚未用于生产 |
| 一页 Daily Report | DONE | `report.py`, N1/N2 PASS | 无 |

## 17–18. 工程结构与 Phase 0–9

| Phase / 工程项 | 状态 | 证据 | 剩余问题 |
|---|---|---|---|
| 单一小型生产包、无版本目录 | DONE | `unified_ai_quant/` | 无 |
| Phase 0 common benchmark | PARTIAL | `BENCHMARK_LOCK.json`, `phase0_baseline.json` | 仅 pool b 三方可比；2018/2020/2021 BLOCKED |
| Phase 1 skeleton | DONE | A/B/M contracts | 无 |
| Phase 2 Trend + Recovery | PARTIAL | 生产回放、H2/H3 | Add1/Add2 NOT DONE |
| Phase 3 Leader | PARTIAL | G1–G3 | G4 NOT DONE |
| Phase 4 Risk Radar | DONE | D2/E2 与 stress artifact | lead-time comparison PARTIAL |
| Phase 5 双轴 | DONE | 唯一汇合路径 | 无 |
| Phase 6 allocator | PARTIAL | I1/I3/I4 PASS | I2、dynamic K、rotation |
| Phase 7 tail guard | DONE | D1/D2、July mechanism limits | D4 三方 RiskUtility 缺失 |
| Phase 8 validation/promotion | PARTIAL | stress/robustness/WF/PBO/DSR 已完成 | K2 holdback BLOCKED；最终 promotion FAIL |
| Phase 9 移除旧依赖 | DONE | N3 source scan | 无 |

## 19. 禁止项核查

| 禁止项 | 状态 | 说明 |
|---|---|---|
| 三项目投票 / 三账户 / 多执行模型 | COMPLIANT | 只有一个账户、allocator 和 next-open execution |
| 复制所有模块 | COMPLIANT | 未复制旧运行时；仅保留有明确生产作用的机制 |
| 固定历史参数直接搬运 | COMPLIANT | 48 个实验、54 个走步单元及 PBO 全披露 |
| 按股票数硬切大量参数 | COMPLIANT | 场景统一配置；边界财富变化为 0 |
| 全局紧止损 / 无限 pyramiding | COMPLIANT | 风险以组合证据触发；当前无 pyramiding |
| 每日重选 leader | COMPLIANT | tenure + sticky anchor |
| 未来样本参与当日决策 | COMPLIANT | A1、G2 与 point-in-time 可见性测试 PASS |
| 忽略 universe 扰动 | COMPLIANT WITH FAIL DISCLOSED | 954 场景已跑；I2 -76.36% 明确 FAIL |
| 修改黄金基线掩盖回退 | COMPLIANT | qwen 适配更正提高门槛，并保留原值/原因 |
| 多风险层直接执行 | COMPLIANT | Risk 只给 constraint，execution 只接最终 target |
| 更多订单换小幅收益 | FAIL DISCLOSED | C3：11 对 9，财富未提高 5% |
| 在最终 holdback 继续调参 | BLOCKED / FAIL | 可用窗口已被查看，不能追溯制造 untouched holdback；K2 FAIL |

## 最终判断

生产工程和大量可用数据门已经完成，但实施报告并非“全部落实”：Add1/Add2、Satellite、dynamic K、正式 rotation/replacement attribution、三方 lead-time/RiskUtility、完整 Phase-0 矩阵和 untouched holdback 仍缺失或被数据阻断。项目只能保持 Candidate，直到这些项目和 `acceptance_results.json` 的全部最终门真实通过。
