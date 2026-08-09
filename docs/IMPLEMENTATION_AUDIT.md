# 融合实施报告逐项复核

本复核只回答两个问题：原《三项目融合实施报告》的每项机制是否进入唯一生产路径，以及当前能力是否达到或超过 `qwenquant`、`AQuant`、`trade`。旧 `73/74` 验收结论、promotion holdback 和旧验收标准不再作为目标或完成依据。

状态定义：

- **已实施**：机制进入 `ProductionEngine.decide()` 所在的唯一生产路径，并有针对性契约检查。
- **部分实施**：主体可用，但报告要求的子能力缺失或只存在于离线分析。
- **未实施**：生产路径没有该能力。
- **能力未达标**：代码存在，但实跑结果仍弱于三个项目中的最佳水平。

## 1. 最终定位与核心原则

| 报告项 | 实施状态 | 当前结论 |
|---|---|---|
| 统一数据 → Opportunity/Risk → Allocator → Account → Execution → Daily Report | 已实施 | `engine.py` 是日常与回放的共同内核，没有第二条生产决策链。 |
| 一个真实账户 | 已实施 | 只有一个 `AccountState`；Alpha、Leader、Risk 都不拥有独立资金账户。 |
| 每票每天一个最终目标权重 | 已实施 | `PortfolioAllocator._targets()` 汇总为唯一 target，订单只由目标与实际持仓差额产生。 |
| Opportunity 与 Risk 分离 | 已实施 | `opportunity.py` 和 `risk.py` 独立计算，只在 allocator 汇合；Risk 不直接生成券商订单。 |
| 用户每天只看一份结果 | 已实施 | `report.py` 输出 Opportunity、Risk、总仓、K、动作、原因、风险摘要和次日订单。 |
| 不再运行三个旧项目 | 已实施 | 正式包不 import、shell 调用或依赖三个旧项目；它们只保留为冻结基线。 |

## 2. 三个项目的能力融合

### 2.1 qwenquant

| 必须吸收项 | 状态 | 落实与差距 |
|---|---|---|
| 单一 Router / 账户 / 次日人工执行 | 已实施 | `ProductionEngine`、`AccountState`、next-tradable-open 执行器及 `account-sync`。 |
| Trend / Recovery / Defensive 状态化 | 已实施 | 五类 Opportunity、Shock/Recovery、弱市现金与 graded risk cap 均在生产路径。 |
| MeanReversion | 部分实施 | 有受控 oversold rebound probe；尚无独立、通用的 CHOPPY mean-reversion 候选层。 |
| Shock Event、慢性恶化、FAILED_REPAIR、恢复迟滞 | 已实施 | 风险状态、protected weights、确认 streak、rearm、capital relapse/cooldown 均持久化。 |
| mature winner 保护 | 已实施 | retention bonus、自身结构破坏确认、战略 cohort 分段退出，避免按日排名机械卖出。 |
| no-trade、经济批次、T+1 | 已实施 | 权重变化带、最小经济交易、手数、tranche 与 sellable date 统一处理。 |
| 真实账户状态与低频轮动 | 已实施 | 订单账、成交账、pending 状态、rotation 限频和 broker snapshot 对账。 |
| 生产门禁与消融 | 部分实施 | fail-closed、反事实和验证设施存在；旧 promotion 报告已废弃，当前优化不再以其 PASS/FAIL 代替能力比较。 |

### 2.2 AQuant

| 必须吸收项 | 状态 | 落实与差距 |
|---|---|---|
| 固定 AI Reference Universe | 已实施 | 34 只固定 reference，与用户输入池分离。 |
| Leader score 与 RS/industry/breakout/resilience/confidence | 已实施 | `leader.py` 使用固定横截面、历史置信度和因果 tenure。 |
| Mature / Emerging 双通道 | 已实施 | Mature 可成为 core；Emerging 只能以受限 satellite 探索并晋升或退出。 |
| Candidate A/B/C | 部分实施 | 候选确认、mature/emerging/recovery reserve 均有，但未把候选显式建模为 A/B/C 三类。 |
| Core + Satellite、leader tenure、challenger replacement | 已实施 | 生命周期、确认日、替代优势、winner penalty、行业/不确定性成本均已进入 allocator。 |
| Rotation edge | 已实施，能力未达标 | 机制完整且低换手，但 2021 大轮动 D/F22/E 的收益仍低于旧项目最佳。 |
| Trade attribution | 部分实施 | 实盘可按 lifecycle/reason 归集 fills、费用和 realized PnL；20/40 日 replacement spread 与 false-exit regret 仍由离线比较器补充，实盘字段为空。 |
| PBO / DSR、参数邻域、nested walk-forward | 已实施为研究设施 | `validation/` 中可用；没有用旧验收结论宣称当前生产能力已全面达标。 |
| Operating DD / Capital DD 双口径 | 已实施 | 两个峰值分别持久化，恢复失败不会清除历史 capital risk。 |
| manual close → next open | 已实施 | 是唯一生产执行模型。 |

### 2.3 trade

| 必须吸收项 | 状态 | 落实与差距 |
|---|---|---|
| 独立 AI 风险篮子与子行业篮子 | 已实施 | 固定 reference、行业映射、breadth 与 sector stress 不依赖用户池构成。 |
| 同步下跌、MA20、correlation、volatility、leader failure | 已实施 | 均进入 `assess_risk()` 的独立证据与 risk votes。 |
| low-frequency regime、L1/L2/L3、tail guard | 已实施（语义等价） | 用 NORMAL/CAUTION/RISK_OFF/CRISIS 和 gross cap 表达，没有另建一套可成交的 L1/L2/L3 overlay。 |
| candidate confirmation 与 account-level risk | 已实施 | 所有风险切换、修复和集中破坏都需要账户持久化 streak 或明确急性条件。 |
| broker/account order 与内部信号分离 | 已实施 | target intent、pending order、broker-visible order ledger、fill ledger 分层。 |
| ProductionReplayEngine 思想 | 已实施 | backtest 逐日调用与 daily 相同的 `decide()`、账户状态和 next-open 执行。 |
| prefix/leave-one-out/add-one/random/permutation stress | 已实施为研究设施 | 设施与历史产物存在；当前代码没有重跑整套 900 random，因此不能用旧产物宣称当前尾部已达到 trade。 |
| fail-closed 与数据/代码 hash | 已实施 | 账户绑定代码 hash 和 append-safe、as-of bounded 数据前缀 hash；历史前缀改写会拒绝运行，正常追加可推进。 |
| optimizer promotion 绑定生产回放 | 已实施为设施 | runner 能绑定签名；旧 promotion 结论不再是当前目标。 |

## 3. 数据层与因果边界

| 报告项 | 状态 | 落实与差距 |
|---|---|---|
| 本地 CSV | 已实施 | 冻结数据与日常数据都经 `DataStore`。 |
| 至少一个联网 A 股源 | 已实施 | 可选 AkShare 股票 QFQ 刷新。 |
| 第二源交叉校验 | 未实施 | 没有第二在线源或自动 cross-check。 |
| 股票 QFQ、指数原始口径 | 部分实施 | manifest 明确口径并拒绝用 QFQ 股票接口刷新指数；在线 raw-index adapter 尚未实现。 |
| 日期、重复、OHLC、volume 合法性 | 已实施 | 读取时 fail closed。 |
| 最新日期一致、停牌可解释 | 部分实施 | common-session 回放和缺失 fail closed 已有；未建立显式交易日历/停牌原因审计。 |
| 数据 hash | 已实施 | canonical prefix hash chain 支持“未来行追加不改历史 hash、历史行改写必失败”。 |
| point-in-time 与上市前不可见 | 已实施 | 所有滚动特征按 as-of 计算，未上市证券不会出现在当日候选。 |
| 固定 30–50 只 AI reference | 已实施 | 当前为 34 只，覆盖光模块、算力、设备、材料、封测、PCB、数据中心等。 |

## 4. 特征、Opportunity、Risk 与 Leader

| 报告项 | 状态 | 落实与差距 |
|---|---|---|
| 20/60/120 收益、MA、Donchian、ATR、ADX、斜率、动量差 | 已实施 | `features.py` 全部因果计算。 |
| Leader 横截面、RS、行业、breakout、resilience、trend、volume、confidence | 已实施 | 固定 reference percentile 与历史覆盖置信度。 |
| Risk 指数速度、breadth、MA20、行业、相关性、波动、leader failure、双 DD、成本破坏、effective N | 已实施 | 组合与市场证据共同进入风险与分配。 |
| STRONG_TREND | 已实施 | 允许证据集中、Add1/Add2、低频 replacement。 |
| TREND | 已实施 | 动态 2–5 核心、严格 Add2、受限 satellite。 |
| RECOVERY | 已实施 | probe → 连续确认 → cohort；失败 cooldown；新增 extended single-name V 修复只在普通 fast-V 当日未推进时作为后备。 |
| CHOPPY | 部分实施 | mature 保留、宽 no-trade 和低频轮动已实现；通用 mean-reversion 仍缺。 |
| WEAK | 已实施 | 无独立确认 leader 时回到现金，战术 probe 有总仓与冷却限制。 |
| NORMAL / CAUTION / RISK_OFF / CRISIS | 已实施 | 独立状态机和唯一 gross cap；CAUTION 不机械清掉 mature winner。 |
| Shock / persistent stress / recovery / failed restoration | 已实施 | acute shock、protected restoration、capital relapse 和 60 日 capital guard cooldown。 |
| Risk lead-time 指标 | 部分实施 | 回放输出 first caution/risk-off/reduce、到 10%/15% DD 提前量；false-positive、opportunity/recovery cost 在离线比较器，不在 daily report。 |
| Mature / Emerging 特权和降级 | 已实施 | tenure、confidence、自身结构破坏、卫星期限和晋升路径均存在。 |

## 5. Allocator、生命周期、Rotation 与执行

| 报告项 | 状态 | 落实与差距 |
|---|---|---|
| 最大 6 持仓、单票 60%、总仓 100% | 已实施 | config、allocator 和 execution 多层约束。 |
| conviction gap、行业、correlation、effective N、动态 K | 已实施 | K 有确认和变更间隔；强证据可集中，普通同簇会被惩罚。 |
| CORE / ADD1 / ADD2 / SATELLITE / RECOVERY | 已实施 | tranche、MFE、risk gate、cooldown 和归因贯通。 |
| 不做 Add3/Add4+ | 已实施 | 生产生命周期只有报告规定的五类。 |
| replacement edge 公式 | 已实施 | 包含 incumbent winner、same-cluster、uncertainty、结构破坏和交易边际。 |
| 连续确认、最小持有、20 日 rotation 限制 | 已实施 | 全部持久化。 |
| AGGRESSIVE/SELECTIVE/FROZEN 显式模式 | 部分实施 | 行为由 Opportunity/Risk 决定，但未公开三个命名模式。 |
| 三层 no-trade / hysteresis | 已实施 | 最小经济额、目标差异带、replacement edge。 |
| close → next tradable open | 已实施 | daily 与 replay 共用。 |
| T+1、涨跌停、停牌、手数、科创板首次 200 股 | 已实施 | 执行层统一处理。 |
| 佣金、最低佣金、税费、滑点、容量、open gap、部分成交 | 已实施 | 订单 ledger 记录尝试、剩余数量和状态。 |
| 真实券商账户同步 | 已实施为人工快照 | `account-sync` / `daily --broker-snapshot` 可幂等导入现金、持仓、可卖数量和 fill；没有券商自动 API connector。 |

## 6. 账户、日报、工程与禁止项

| 报告项 | 状态 | 落实与差距 |
|---|---|---|
| cash/positions/cost/entry/high/tranche/T+1 | 已实施 | 原子 JSON 持久化。 |
| operating/capital peak、Opportunity/Risk、cooldown/tenure/shock/pending | 已实施 | 全部属于 `AccountState`。 |
| last run、data/code hash | 已实施 | 账户初始化和每次成功决策都更新 bounded provenance。 |
| 一页 Daily Report | 已实施 | 仅展示最终目标、风险与次日动作，不暴露第二套建议。 |
| 小型单包、无 v2/v3 目录 | 已实施 | 正式代码集中在 `unified_ai_quant/`。 |
| 三项目投票、三账户、多执行模型 | 禁止项已满足 | 正式路径不存在。 |
| 按股票数量复制大量参数 | 禁止项已满足 | 主要由 effective N、risk、opportunity、coverage 和 conviction 决定；固定池只存在于 benchmark。 |
| 全局紧止损、无限 pyramiding、每日重选 leader | 禁止项已满足 | 风险状态、Add1/Add2 上限、tenure/edge 保护均已实现。 |
| 未来样本、忽略 universe 扰动、修改旧基线掩盖回退 | 禁止项已满足 | 因果数据边界和只读旧基线保留；本轮只做针对性反事实，没有改写旧项目结果。 |

## 7. 当前同合同能力比较

下表是当前代码在冻结数据上的针对性重放，不是旧验收报告。优先级按用户指定：收益 > 回撤 > 交易次数。

### 2018-01-02 至 2026-07-20 连续周期

| Pool | Unified 财富 | 三旧最佳财富 | Unified DD | 三旧最佳 DD | Unified orders | 三旧最少 orders |
|---|---:|---:|---:|---:|---:|---:|
| a | 36.846x | 38.063x | 28.89% | 21.11% | 53 | 147 |
| b | 23.930x | 22.371x | 39.93% | 18.97% | 84 | 176 |
| c | 36.682x | 30.530x | 30.87% | 20.21% | 68 | 200 |
| d | 63.785x | 43.592x | 28.40% | 20.41% | 86 | 213 |
| f22 | 52.968x | 12.789x | 28.07% | 20.89% | 89 | 113 |
| e | 52.252x | 14.384x | 33.36% | 19.84% | 132 | 166 |

结论：收益 5/6 超过旧项目最佳，订单 6/6 更少；最大回撤 0/6 达到 `trade` 的最佳档。Pool-a 收益仍低约 3.2%。

### 2025-04-01 至 2026-06-30 强趋势

| Pool | Unified 财富 / DD / orders | 三旧最佳财富 / 最低 DD / 最少 orders |
|---|---:|---:|
| a | 13.095x / 15.96% / 10 | 9.262x / 17.54% / 12 |
| b | 13.095x / 15.96% / 10 | 12.759x / 15.52% / 9 |
| c | 13.095x / 15.96% / 10 | 10.149x / 15.79% / 13 |
| d | 13.202x / 15.98% / 12 | 11.273x / 18.62% / 27 |
| f22 | 13.202x / 15.98% / 12 | 10.779x / 16.53% / 10 |
| e | 13.202x / 15.98% / 12 | 11.695x / 17.27% / 26 |

结论：强趋势收益 6/6 超过旧项目最佳，DD 4/6 更低，订单 4/6 更少；已达到或超过三个项目的强项水平。

### 2021 大轮动已知缺口

| Pool | Unified 财富 / DD / orders | 三旧最佳财富 / 最低 DD / 最少 orders |
|---|---:|---:|
| d | 1.182x / 26.48% / 13 | 1.811x / 13.65% / 34 |
| f22 | 0.881x / 18.44% / 13 | 1.578x / 18.84% / 30 |
| e | 1.153x / 12.23% / 13 | 1.616x / 18.29% / 22 |

结论：F22/E 的 DD 与订单已达到或超过旧项目，但三池收益都未达到；D 的 DD 也未达到。这是当前最大的 Alpha 缺口。

## 8. 最终判断与下一步

原报告的大多数生产机制已经落地，但“任何/绝大多数方面达到或超过三个项目”的唯一目标尚未完全实现。当前已经领先的部分是强趋势收益、五个连续池收益、全部连续池订单数量、单一生产路径、因果数据 provenance 和真实成交对账。未达标部分按优先级为：

1. 轮动行情收益，尤其 D/F22/E 对新爆发 leader 的接棒；
2. 连续周期最大回撤，六池都未到 `trade` 约 19%–21% 的档位；
3. Pool-a 连续收益约 3.2% 的缺口；
4. 当前代码的完整 unknown/random 尾部能力尚未重新建立，不能引用旧压力产物替代；
5. Candidate A/B/C、通用 CHOPPY mean-reversion、在线 raw-index/第二数据源、实盘 regret/spread 和自动券商 connector 仍是明确工程缺口。

后续改动必须同时报告对强趋势、连续复利、回撤和订单数的影响；任何只改善单一轮动窗口却破坏长期复利的方案都不保留。
