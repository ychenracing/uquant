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
| `validation/` | 冻结数据、Phase 1 绩效、Phase 2 泛化和证据完整性门禁 |
| `research/` | 调用方驱动的离线分析，不参与生产导入 |

`engine.py` 是 application 编排的稳定 facade；`portfolio_leaders.py`、
`portfolio_strategic.py` 和 `portfolio_recovery.py` 保留旧导入与 pickle 身份。其他顶层模块若在
上表中被明确列为所有者，仍承担真实职责。新增实现必须进入对应所有者，不能在兼容 facade
中建立第二套状态机。

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

`RiskAssessment.target_gross_cap` 是总仓位上限的唯一来源。持仓同步冲击、慢性退化和资本预算只向该评估提供证据或更严格上限，不能各自建立独立组合。

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

## 执行与账户

订单先卖后买，并统一处理 T+1 可卖数量、停牌、涨跌停、手数、容量、现金、费用和滑点。未成交或部分成交的意图保留稳定 `order_id`；同一经济意图不会因为每日重算而重复计数。

生产经济状态只沿 `Decision → Order → Fill → AccountState` 单向推进。Base Risk 汇总
风险证据并拥有 `target_gross_cap`，`PortfolioAllocator` 在该上限内拥有唯一目标权重，
执行层只能把既有目标转成订单和成交，账户层只能持久化已验证结果。Risk Sentinel 的
`FREEZE_ONLY` 结论至多阻止新增风险，不能建立第二个仓位、卖出或账户权限。

账户文件使用临时文件、刷盘和原子替换保存。当前数据契约记录现金、持仓 tranche、挂单、成交、机会/风险状态、组合生命周期、资本高水位、数据摘要和代码指纹。加载时会校验：

- 现金、股数、价格和序号范围；
- 订单、成交和持仓引用；
- 唯一标识及终态一致性；
- 当前 schema 和生产代码指纹；
- 已参与决策的数据前缀。

券商快照只覆盖现实世界字段；策略状态不能由券商持仓反向推导。

## 验证与失败处理

验证层锁定数据清单、执行口径、六个官方 AI-era 窗口和证据摘要。34 只证券的
canonical AI universe manifest 同时拥有点时成员与行业身份；Generalization 对每个
窗口构造同一固定场景契约，不允许研究模块另建证券全集或修改参考上下文。

`Engineering`、`Phase 1 Performance` 和 `Phase 2 Generalization` 是三个独立阻断结论。
Phase 1 始终运行 `promotion --profile full`；Phase 2 的六个分片全部结束后，aggregator
检查精确 HEAD、生产源码、配置、冻结数据、运行时、锁文件、universe、行业、窗口、
场景与前窗证据身份，并用冻结 policy 重算完整 cell 证据。路径过滤、矩阵 fail-fast
和并发取消都不能让最终结论跳过。缺文件、重复 JSON 键、摘要漂移、未提交生产源码
或运行中修改证据都会失败关闭。

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
当前 `production_wheel_v1` source epoch 已登记生产 wheel 的成员与摘要，只对新账户和新
观察向前生效。任何后续边界变化都必须创建新 epoch，不能回填旧 epoch、修改冻结 oracle
或重写既有 Holdout Lane 来伪造连续性。
