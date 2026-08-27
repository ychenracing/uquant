# 架构说明

本文描述的生产系统只服务于 2023 年以来的 A 股 AI 产业链：现金多头、日频、盘后决策、次日执行，并由人工核对和辅助下单。研究模块不得扩大这一生产边界。

## 总体设计

uquant 把数据、信号、风险、组合、执行和账户放在一条可审计链路中：

```text
点时行情
  → 因果特征与参考上下文
  → 领涨和行业证据
  → 机会状态 + 风险状态
  → 唯一目标组合
  → 次日开盘订单意图
  → 模拟成交或券商成交
  → 账户状态与日报
```

`ProductionEngine` 是生产编排入口。`daily` 与 `backtest` 都调用 `decide()`；报告只渲染决策结果，研究模块也不能直接写入生产仓位。

## 模块边界

| 模块 | 单一职责 |
|---|---|
| `application/` | `decide()`、回放、指标、归因和生产用例编排 |
| `market/` | 点时市场工作区、交易日对齐与确定性 replay 输入 |
| `data.py`、`features.py`、`reference*.py` | OHLCV、因果特征、点时成员与共享截面上下文 |
| `industry.py`、`leader.py`、`opportunity.py` | 行业、领涨和机会状态证据 |
| `market_risk.py`、`risk_sector.py`、`risk/` | Base Risk 证据、状态转换、资本损伤和唯一仓位上限 |
| `portfolio_core.py`、`portfolio/` | 唯一目标组合、硬约束、风险缩减及各持仓生命周期 |
| `execution/` | 订单规划、市场约束、费用、部分成交、挂单和 tranche |
| `account/` | 账户编码、校验、经济身份、迁移和原子持久化 |
| `contracts/` | 严格 JSON、universe、运行时和 source-surface 合同 |
| `risk_sentinel/` | 独立风险证据、Coverage、离线 calibration 与窄映射 |
| `broker.py`、`report.py` | 券商快照/成交对账与只读日报渲染 |
| `validation/` | 冻结数据、AI-era 性能、泛化和证据完整性门禁 |
| `research/` | 调用方驱动的离线分析，不参与生产导入 |

`engine.py` 是 application 编排的稳定 facade；`portfolio_leaders.py`、
`portfolio_strategic.py` 和 `portfolio_recovery.py` 保留旧导入与 pickle 身份。其他顶层模块若在
上表中被明确列为所有者，仍承担真实职责。新增实现必须进入对应所有者，不能在兼容 facade
中建立第二套状态机。

### 权限与接口矩阵

| 能力 | 唯一所有者 | 可以做什么 | 明确禁止 |
|---|---|---|---|
| 日常决策 | `ProductionEngine.decide()` | 生成同一份 `Decision` | 报告、研究或脚本建立第二决策路径 |
| 风险派生上限 | Base Risk | 生成 `RiskAssessment.target_gross_cap` | Opportunity、Sentinel 或执行层扩大上限 |
| 目标权重 | `PortfolioAllocator` | 在风险与硬约束下生成 `Target` | 风险、报告或券商快照直接生成组合 |
| 订单与成交 | execution / broker reconciliation | 把既有目标转为订单并吸收真实成交 | 推断、伪造或覆盖券商事实 |
| 生产持久化 | `AccountState` | 保存已校验的经济状态 | Journal、Holdout 或 Sentinel 写第二账户 |
| Future Holdout | `python -m scripts.*` | 追加观察、回放、Journal 和证据 | 回填、调参或获得生产权限 |
| Sentinel CLI | `uquant-sentinel` | 离线、只读 Shadow 诊断 | 当作生产 `FREEZE_ONLY` 入口 |

### 术语

| 术语 | 精确定义 |
|---|---|
| Opportunity | 市场是否值得承担风险的机会轴，不拥有强制风险上限 |
| Risk / `target_gross_cap` | Base Risk 状态及其风险派生总仓上限 |
| Sentinel Level | 独立观察意见；不是第二个 `Risk`，生产最多映射到 `freeze_new_risk` |
| Target Gross | 目标权重之和；受机会预算、风险 cap、硬约束及已接受窄例外共同决定 |
| Actual Gross | 按真实现金、持仓和价格计算的当前敞口，可能因未成交暂时偏离 Target Gross |
| `Target` | 组合层期望权重，不代表订单已提交或成交 |
| `PendingOrder` / `AccountOrder` | 待执行意图 / 已提交且有稳定生命周期的账户订单 |
| `Fill` | 券商或回放确认的成交事实，只有它才能推进持仓经济状态 |
| `StrategicGrantIntent` | 已授权但可能尚未完整成交的单一战略授冠经济意图；跨重试和重启保持同一 `grant_id` |
| Lifecycle | `CORE/ADD1/ADD2/SATELLITE/RECOVERY` 的持仓来源和风险减仓优先级 |

## 决策时点

`DataStore` 按 `as_of` 截断每个证券的数据。所有特征、参考成员、行业统计和候选排序只读取该日期及以前的行。目标组合在收盘后形成，`ExecutionPlanner` 最早在下一可交易日开盘执行，因此同日收盘信号不会获得同日成交价。

账户中的数据摘要覆盖已经参与决策的历史前缀。新增交易日允许追加；修改已经使用的历史行会导致摘要不一致并停止运行。

2023 年以前的行情前缀只用于形成均线、ATR 等因果特征 warm-up。经济账本在 AI-era 起点重新定基；这些行不能产生发布验收使用的权益、订单、成交、换手、回撤、Sharpe 或 Calmar。生产验收只覆盖 `h1_2023`、`h2_2023`、`h1_2024`、`h2_2024`、`bull_crash_2025_2026` 和 `continuous_ai_era`。

## 共享参考上下文

`ReferenceContext` 每个决策日只计算一次，提供：

- 证券等权与行业均衡广度；
- 压力、相关性和截面离散度；
- 行业覆盖率与缺失诊断；
- 风险锚和领涨评分需要的共同输入。

参考成员按生效日期解析，避免回放读取当时尚不可见的成员。参考证据只提供市场结构信息，不直接拥有仓位。

## 双轴状态与唯一风险权限

机会轴回答“是否值得承担风险”，风险轴回答“最多允许承担多少风险”。风险模块把速度、广度、协方差、领涨损伤、持仓损伤和资本损伤按证据家族归并，同一家族最多贡献一票。

`RiskAssessment.target_gross_cap` 是风险派生总仓上限的唯一来源。持仓同步冲击、慢性退化
和资本预算只向该评估提供证据或更严格上限，不能各自建立独立组合。

组合层保留一个冻结经济行为中的有限解释：当账户只有单一战略主导者，风险仅为
`NORMAL/CAUTION` 一级预警、没有 sector/strategic/acute guard，且策略本身不要求减仓时，
`PortfolioAllocator` 可以把风险 cap 解释为“冻结新增风险”，将该既有持仓保留至
`strategic_dominant_max_weight`。这不是第二个风险 owner：它不能买入补足权重、不能作用于
多持仓组合，且 `CRISIS` 或明确风险减仓始终覆盖该例外。
完整谓词与否定边界由
[ADR 0001](decisions/0001-economic-authority-and-causal-execution.md)统一定义。

## 唯一组合所有者

`PortfolioAllocator` 负责所有目标权重，并按以下顺序处理：

1. 根据机会状态给出候选风险预算；
2. 应用风险上限和冻结新增风险标记；
3. 选择战略、普通领涨或修复路径；
4. 管理 `CORE`、`ADD1`、`ADD2`、`SATELLITE`、`RECOVERY` 生命周期；
5. 应用总仓、单票、持仓数、行业、相关性和流动性约束；
6. 使用迟滞与最小交易门槛过滤无经济意义的变化；
7. 输出确定性排序的目标。

其他模块只能提供证据、状态或成交结果。

### 战略资格与部署

战略处理先执行只读的 qualification observation，再执行 deployment authorization。观察步骤
读取当日可见数据，更新候选、路线、资格证据摘要、连续确认、阻塞原因和失效原因；它不生成
目标或订单，也不改变总仓上限。`freeze_new_risk`、风险状态和资本预算只阻塞部署，不机械清空
已经观察到的候选证据。

部署授权只在风险、机会、资本预算、执行和账户状态均允许时创建
`StrategicGrantIntent`。同一账户最多一个未终结授冠；`grant_id` 由账户、候选、路线、资格
证据和生产源码身份确定性生成。证券不可通过修改旧授冠换人，所有 Target、Order、Fill、
Tranche 和 Position 必须携带一致的证券、事件和授冠身份。

## 执行与账户

订单先卖后买，并统一处理 T+1 可卖数量、停牌、涨跌停、手数、容量、现金、费用和滑点。
普通未成交意图继续复用既有订单生命周期；战略授冠部分成交后只按剩余数量生成新的物理订单，
但保持同一 `grant_id` 和事件身份。迟到成交计入原授冠并取消重叠重试，避免重复经济订单或第二个
仓位 owner。每次战略重试都重新确认资格，并重新经过 Risk 与 `PortfolioAllocator`。

生产经济状态只沿 `Decision → Order → Fill → AccountState` 单向推进。Base Risk 汇总
风险证据并拥有风险派生 `target_gross_cap`，`PortfolioAllocator` 在该上限和上述单一战略
主导者一级预警保留例外内拥有唯一目标权重，
执行层只能把既有目标转成订单和成交，账户层只能持久化已验证结果。Risk Sentinel 的
`FREEZE_ONLY` 结论至多阻止新增风险，不能建立第二个仓位、卖出或账户权限。

账户文件使用临时文件、刷盘和原子替换保存。当前数据契约记录现金、持仓 tranche、挂单、成交、
战略资格观察、授冠意图、机会/风险状态、组合生命周期、资本高水位、数据摘要和代码指纹。
旧账户缺少授冠字段时读取为空授冠状态，不改变现金、持仓、订单或成交。加载时会校验：

- 现金、股数、价格和序号范围；
- 订单、成交和持仓引用；
- 唯一标识及终态一致性；
- 当前 schema 和生产代码指纹；
- 已参与决策的数据前缀。

券商快照只覆盖现实世界字段；策略状态不能由券商持仓反向推导。

## 信任边界

CSV、券商 JSON、账户文件、Journal、命令行路径和研究输入都按不可信输入处理。入口必须
拒绝路径别名、符号链接/硬链接越界、重复 JSON 键、非有限数值、乱序成交和输出覆盖输入；
账户及证据使用锁、临时文件、刷盘和原子替换。private-import scanner 只约束当前仓库的
开发期模块边界，不是任意 Python 代码或对象图的安全沙箱。

券商快照只拥有现金、持仓、可卖数量、订单确认和真实成交；公司行动、证券代码变化、
复权切换和交易所日历异常必须由 operator 核对，不能从策略状态猜测。

## 验证与失败处理

验证层锁定数据清单、执行口径、六个官方 AI-era 窗口和证据摘要。34 只证券的
canonical AI universe manifest 同时拥有点时成员与行业身份；Generalization 对每个
窗口构造同一固定场景契约，不允许研究模块另建证券全集或修改参考上下文。

`Engineering` 与 `Strategic Grant Acceptance` 是 PR 和 `main` push 的独立阻断结论；
完整性能和泛化矩阵保留为手动触发的 `Extended Performance Matrix` 与
`Extended Economic Matrix`。精确窗口、矩阵、指标与复现命令由
[性能与证据](PERFORMANCE.md)唯一维护。缺文件、重复
JSON 键、摘要漂移、未提交生产源码或运行中修改证据都会失败关闭。

经济归因的稳定身份从 Target 传播到 Order、Tranche 和 Fill；人工可读 reason text
只用于展示。已实现与未平仓 lot PnL 必须和账户权益变化对账，cash drag 与配对的
risk avoidance 只能作为诊断。每日运行、核对和下单仍由人工负责；外部 journal 与
holdout 观察不进入 `ProductionEngine.decide()` 或账户状态。

关键错误不会被自动修补：

- 数据历史被改写：恢复可信数据后重新校验；
- 账户与券商不一致：以完整券商快照对账；
- 代码指纹不一致：先备份账户并执行兼容转换；
- 订单引用或成交顺序矛盾：修正快照来源，不猜测成交；
- 验证证据缺失：补齐经过评审的真实证据，不生成占位值。

这种边界使系统在无法证明状态一致时停止，而不是继续给出看似完整的交易建议。

## 源码身份与发布边界

生产 wheel 只发现 `uquant*`；仓库内的 `research/`、`scripts/`、`tests/`、`artifacts/`、
`benchmarks/`、`data/` 和 `docs/` 不进入安装包。仓库证据仍以
`artifacts/architecture_refactor/baseline_inventory.json`、
`benchmarks/source_surface_registry.json` 和 `data/frozen/DATA_MANIFEST.json` 为高风险
锚点。`full_package_v1` 与 `requirements.txt` 继续是 `KEEP_AUTHORITATIVE` 的历史身份面；
`production_wheel_v1/v2/v3` 作为历史 epoch 保留；当前 `production_wheel_v4` 登记文档、
构建治理与生产叙事一致性后的确定性 wheel、逐成员 manifest 和 source-surface 摘要，只对新账户和新观察向前
生效。v2 的远程恢复保证 payload 精确，并明确保留历史 ZIP 权限元数据差异。任何后续身份变化都必须创建新 epoch，不能回填旧 epoch、修改冻结 oracle
或重写既有 Holdout Lane 来伪造连续性。
