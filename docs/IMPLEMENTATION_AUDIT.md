# Implementation Report Item-by-Item Audit

本表逐项对应 `docs/IMPLEMENTATION_SPEC.md`。`DONE` 表示机制进入唯一生产路径且有可执行证据；最终验收仍以 `acceptance_results.json` 为准。当前 74 项中 73 项 PASS，唯一例外是不可追溯修正的 K2 单次 holdback。

## 1. 定位、融合来源与核心原则

| 报告项 | 状态 | 落实位置 / 证据 |
|---|---|---|
| 单一日频现金多头系统 | DONE | `engine.py`, `account.py`, `cli.py`；无杠杆、无做空。 |
| qwenquant 生产骨架、低换手、Trend/Recovery | DONE | `engine.py`, `opportunity.py`, `portfolio.py`, `execution.py`。 |
| AQuant fixed-reference Leader Intelligence | DONE | `leader.py`；G1–G4 PASS。 |
| trade Risk Radar 与 Formal Stress | DONE | `risk.py`, `validation/stress.py`；D/F/I/O 类证据通过。 |
| 一个账户、一个 allocator、一个执行模型 | DONE | 唯一 `AccountState`、`PortfolioAllocator`、next-open `ExecutionPlanner`。 |
| 每票每天一个最终 target | DONE | `_targets()` 和 B5/M 类契约；Alpha/Risk 不直接下单。 |
| Opportunity 与 Risk 分离 | DONE | 独立模块计算，在 allocator 中唯一汇合。 |

## 2. 数据、特征与因果边界

| 报告项 | 状态 | 落实位置 / 证据 |
|---|---|---|
| 股票 QFQ、指数原始数据、OHLCV 契约 | DONE | `data/frozen`, `DataStore`; A/B/M tests。 |
| SHA-256 manifest / fail closed | DONE | `DATA_MANIFEST.json`, `SHA256SUMS`, account data/code hash；stress/robustness 长跑要求起止生产、验证、配置和有界数据签名完全一致，否则拒绝混合版本证据。 |
| 上市前不可见、当日只见当日及以前 | DONE | point-in-time `visible_users`、bounded fingerprint 和泄漏回归测试。 |
| 固定 AI Reference Universe | DONE | `REFERENCE_UNIVERSE`；与用户输入池分离。 |
| 趋势、波动、流动性、Leader 特征 | DONE | `features.py`, `leader.py`；所有 rolling 计算因果。 |
| 冻结 common-contract 基线 | DONE | `BENCHMARK_LOCK.json`, `legacy_common_adapter.json`；135 个单元。 |

## 3. Opportunity、Risk 与 Leader

| 报告项 | 状态 | 落实位置 / 证据 |
|---|---|---|
| STRONG_TREND / TREND / RECOVERY / CHOPPY / WEAK | DONE | `classify_opportunity`、迟滞与恢复确认。 |
| NORMAL / CAUTION / RISK_OFF / CRISIS | DONE | `assess_risk` 唯一状态机。 |
| Operating DD 与 Capital DD 分离 | DONE | `_portfolio_drawdowns` 和账户峰值持久化。 |
| Shock / failed repair / recovery / rearm | DONE | protected weights、severity、repair streak、90 日 rearm。 |
| CAUTION 多票降仓 | DONE | 四个独立风险票时 gross cap 60%；专项测试覆盖。 |
| 战略 cohort 新仓门禁 | DONE | RISK_OFF/CRISIS 或 CAUTION≥2 票禁止新建；一票 benign transition 保留。 |
| Risk lead-time、误报、RiskUtility 反事实 | DONE | F1–F4、D4 PASS；三旧系统 common adapter 对照。 |
| Mature/Emerging/Unknown、tenure | DONE | point-in-time history/confidence gates；G1–G3 PASS。 |
| Replacement spread 20d/40d | DONE | replacement ledger 与 G4 PASS。 |

## 4. Allocator、生命周期、执行、状态与日报

| 报告项 | 状态 | 落实位置 / 证据 |
|---|---|---|
| 最大 6 持仓、单票 60%、总仓 100% | DONE | config、allocator 和 execution 三层硬检查。 |
| 有证据集中、行业/相关性约束 | DONE | leader ranking、industry cap、correlation admission 与 risk structure。 |
| Effective N / 动态 K | DONE | 确认、扩张与变更间隔均持久化，边界/扰动测试通过。 |
| CORE / ADD1 / ADD2 / SATELLITE / RECOVERY | DONE | 唯一账户生命周期、冷却、MFE 门和归因。 |
| Rotation / replacement edge | DONE | tenure、edge、确认日、转移上限、ledger 与 spread 证据。 |
| No-trade / hysteresis | DONE | sticky holding、最小交易权重、冷却、pending-order merge。 |
| close→next-open、T+1、停牌/涨跌停 | DONE | `execution.py`; A2–A5、M1–M3 PASS。 |
| 手数、科创板首次 200 股、费用/滑点/容量/部分成交 | DONE | A6/A9、L1–L3、M4/M9–M11 PASS。 |
| 原子账户持久化与恢复 | DONE | `account.py`; 峰值、tenure、shock、cooldown、pending orders 均入账。 |
| 一页 Daily Report | DONE | `report.py`; N1/N2 PASS。 |

## 5. 工程结构、Phase 0–9 与禁止项

| 工程项 | 状态 | 证据 |
|---|---|---|
| 单一小型生产包、无版本目录 | DONE | `unified_ai_quant/`。 |
| Phase 0 common benchmark | DONE | 3×5×9 common adapter 矩阵完整。 |
| Phase 1–7 生产机制 | DONE | A–J、L–N 全部对应检查通过。 |
| Phase 8 验证设施 | DONE | 963 stress、180 robustness、54 nested WF、PBO/DSR/Pareto。 |
| Phase 8 最终 promotion | PENDING | 历史 K2 为不可改写的 `CONSUMED_FAIL`；修复候选 73/73 非保留窗通过，新未来 K2 已预注册但数据尚未到齐。 |
| Phase 9 移除旧依赖 | DONE | N3 source scan；正式包无旧项目运行时依赖。 |
| 三项目投票 / 三账户 / 多执行模型 | COMPLIANT | 仅一个账户、allocator 和执行器。 |
| 未来样本参与当日决策 | COMPLIANT | A1/G2、bounded signatures 与 point-in-time tests。 |
| 忽略 universe 扰动 | COMPLIANT | 963 场景；I1–I4 PASS。 |
| 修改黄金基线掩盖回退 | COMPLIANT | 原 common adapter 值、修订原因和签名均保留。 |
| 在最终 holdback 失败后继续把同窗当 promotion | COMPLIANT | 历史 FAIL 原样保留；修复同窗只标记 diagnostic。 |

## 最终判断

实现层面没有遗留的旧项目依赖或并行生产路径；实施报告的生产与验证机制均已落实，非保留窗证据链为 73/73。最终 promotion 结果尚未落实为 PASS：候选已按 production `f8fdd2ab989f`、validation `e35831e29770`、config `5821f769d963` 冻结，并在 `benchmarks/PROMOTION_HOLDBACK_NEXT.json` 预注册 2026-08-06 至 2026-08-21 的 12-session 未来窗口；本地数据截至 2026-08-05，只能保持 `PENDING_FUTURE_DATA / CANDIDATE`。该窗口完整封存并由同一候选单次达到 74/74 后，才能 Production。
