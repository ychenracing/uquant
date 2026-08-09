# Phase 0–9 实施状态

本文件只记录融合实施进度和相对三个旧项目的能力差距。旧 `73/74`、promotion holdback 与“是否通过验收”不再作为目标。

| Phase | 实施状态 | 当前能力判断 | 仍需改进 |
|---|---|---|---|
| 0 统一 Benchmark | 已实施 | 三旧项目有冻结代码/数据指纹和 common-contract 实跑基线，可按同一 next-open、成本、窗口和指标比较。 | 旧基线继续只读；新增比较必须标明当前代码指纹和窗口。 |
| 1 最小统一骨架 | 已实施 | 单一 DataStore、AccountState、ProductionEngine、Allocator、ExecutionPlanner、target、日报；daily/backtest 同内核。新增 broker snapshot/fill 幂等同步与 append-safe 数据前缀 provenance。 | 在线 raw-index 刷新、第二数据源和自动券商 connector 未实现。 |
| 2 qwenquant Trend + Recovery | 大部分实施 | Trend、Recovery、no-trade、Core/Add1、Shock、cooldown、winner protection 已生产化；强趋势六池收益均超过旧项目最佳。 | 通用 CHOPPY mean-reversion 仍只有受控 rebound probe；恢复逻辑还需降低路径依赖。 |
| 3 AQuant Leader Intelligence | 大部分实施 | fixed reference、leader score、mature/emerging、confidence、tenure、core/satellite、replacement edge 已生产化。 | Candidate A/B/C 未显式建模；2021 大轮动对新爆发 leader 的捕获仍弱。 |
| 4 trade Risk Radar | 已实施，结果未达标 | 独立 risk basket、行业 breadth、MA20、correlation、volatility、leader failure、tail/account risk 均在单一风险状态机。 | 连续六池 DD 为 28%–40%，仍高于 `trade` 约 19%–21% 的最佳档。 |
| 5 Opportunity × Risk | 已实施 | 五类 Opportunity 与四类 Risk 独立计算，只有 allocator 可合成 target，Risk 无执行权。 | 继续清理少数路径依赖，确保恢复/战略 cohort 不因输入池扩张产生意外分叉。 |
| 6 统一 Portfolio Allocator | 已实施，轮动未达标 | dynamic K、effective N、行业/相关性、conviction gap、集中度、target hysteresis、add/drop、低换手均已统一。 | 轮动 D/F22/E 收益仍低于旧项目最佳；不能用增加无效订单解决。 |
| 7 Capital DD / Tail Guard | 已实施，DD 未达标 | operating/capital peak 分离、集中破坏、severe shock、恢复失败 relapse、capital cooldown 与后备 V 修复进入生产。 | 在不压制强趋势与长期复利的前提下，把连续 DD 降到旧最佳档。 |
| 8 Validation / Research | 设施已实施 | stress、universe perturbation、成本/容量、nested walk-forward、PBO/DSR、三方 adapter 均可用。 | 用户已放弃旧验收目标；当前代码没有重跑整套 900 random，旧产物不能证明当前尾部能力。只做与具体改动相关的反事实。 |
| 9 移除旧依赖 | 已实施 | 正式包不 import 或调用 qwenquant/AQuant/trade；旧项目只作为 benchmark。 | 无生产依赖缺口。 |

## 当前经济结果

### 连续周期（2018-01-02 至 2026-07-20）

- 收益：6 个池中 5 个超过三旧最佳；a 为 36.846x，略低于 qwenquant 38.063x。
- 回撤：6 个池均未达到三旧最低 DD；这是当前最普遍的能力缺口。
- 订单：6 个池均少于三旧最少订单数。
- b 的 extended single-name V 修复把连续财富从 6.551x 恢复到 23.930x；该后备只在普通 fast-V 当日没有推进时生效，其余五池未新增该事件。

### 强趋势（2025-04-01 至 2026-06-30）

- 六池财富 13.095x–13.202x，全部超过三旧最佳。
- 最大回撤约 15.96%–15.98%，六池中四池优于三旧最低 DD。
- 订单 10–12，六池中四池少于三旧最少订单。

### 轮动（2021）

- d/f22/e 财富为 1.182x/0.881x/1.153x，均低于三旧最佳 1.811x/1.578x/1.616x。
- f22/e 的回撤和三池订单数已有优势；收益接棒是下一阶段第一 Alpha 问题。

## 后续优先级

1. 提高轮动 leader 接棒收益，同时保留 10–13 笔级别的低换手。
2. 把连续周期 DD 从 28%–40% 降向 19%–21%，但不得破坏当前强趋势收益。
3. 补齐 a 连续收益的小幅差距，并重建当前代码的 unknown/random 尾部证据。
4. 再补 Candidate A/B/C、通用 mean-reversion、数据双源和实盘归因/券商自动化。
