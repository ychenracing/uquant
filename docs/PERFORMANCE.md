# 性能与证据

生产绩效结论只针对 2023 年以来的 A 股 AI 产业链现金多头日频系统。系统盘后决策、下一交易日执行并由人工辅助使用；证据不能外推到自动实盘、做空、杠杆、盘中或非 AI 产业链策略。

## 回放契约

所有绩效结论建立在以下因果执行条件上：

- 信号只读取收盘日及以前的数据；
- 目标最早在下一可交易日开盘成交；
- 上市前证券不可见；
- 股票前复权，指数不复权；
- 模拟 T+1、停牌、涨跌停、手数、费用、滑点、容量和部分成交；
- 日报与回放共用 `ProductionEngine.decide()`；
- 订单数按实际发生成交的稳定账户订单统计；
- 数据、参数、源码和证据摘要在运行前后保持一致。
- 经济统计不得早于 `2023-01-01`；更早行情只用于特征 warm-up。

不同数据快照、复权方式、证券全集、起止日或执行口径的结果不能直接比较。

## 核心指标

| 指标 | 定义 | 解读 |
|---|---|---|
| 最终财富 | `期末权益 / 初始权益` | 1.0 表示资金不变 |
| 累计收益 | `最终财富 - 1` | 衡量区间总收益 |
| 最大回撤 | 权益从历史高点到后续低点的最大跌幅 | 越低越好 |
| 账户订单数 | 区间内至少有一笔成交的唯一订单数 | 比 fill 数更接近真实操作次数 |
| 总换手 | 成交名义金额相对权益的累计比例 | 衡量交易强度和成本敏感性 |
| 急跌期收益 | 指定压力区间的局部权益变化 | 观察冲击防守 |
| Sharpe | 日收益均值相对波动的年化比率 | 对非正态收益只能辅助解读 |
| Calmar | 年化收益相对最大回撤 | 同时观察收益和回撤 |

订单数低不等于策略更好；应与财富、回撤、换手和可执行性共同判断。

## 回放输出

`backtest` 输出包括：

- 最终财富、累计收益和最大回撤；
- 日收益与权益曲线；
- 账户订单、成交、费用和换手；
- 持有期、目标权重和风险事件；
- 机会状态、风险状态及关键证据；
- 数据、配置和代码摘要。

示例：

```bash
uv run uquant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2023-01-03 \
  --end 2026-07-20 \
  --output backtest_result.json
```

数据文件可以包含 pre-2023 行以形成均线和 ATR，但回放必须在 2023+ 重新定基；这些 warm-up 行不得进入权益、收益、回撤、订单、成交、换手、Sharpe 或 Calmar。

## 验证层次

### 数据完整性

```bash
uv run python -m uquant.validation data-manifest --data-dir data/frozen
```

该命令核对目录、清单和 SHA-256，并拒绝符号链接、不安全文件名、重复或缺失证券。

### 单元与性质测试

```bash
uv run pytest --cov=uquant --cov-report=term-missing
```

测试覆盖特征、状态转换、组合硬约束、T+1、费用、部分成交、账户恢复、数据失败路径和确定性。

### AI-era 阻断性能验收

```bash
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --profile full \
  --output benchmarks/ai_era_performance.json
```

最终文件在 checkout 后生成并由 CI 上传，不进入 Git。这样其中的
`production_commit` 才能严格等于被验证的 HEAD；仓库内可追踪的
`promotion_baseline.json` 只保存已经评审的上一任 champion，而不是伪装成当前
HEAD 的自引用运行结果。

full profile 是性能验收不可拆分的阻断经济性真相，窗口日期直接绑定
`uquant.contracts.runtime_identity.AI_ERA_WINDOWS`：

| 窗口 | 开始 | 结束 | 急跌观察开始 | 急跌观察结束 |
|---|---|---|---|---|
| `h1_2023` | `2023-01-03` | `2023-06-30` | `2023-04-20` | `2023-05-25` |
| `h2_2023` | `2023-07-03` | `2023-12-29` | `2023-07-26` | `2023-08-25` |
| `h1_2024` | `2024-01-02` | `2024-07-01` | `2024-01-03` | `2024-02-02` |
| `h2_2024` | `2024-07-01` | `2024-12-31` | `2024-08-01` | `2024-09-02` |
| `bull_crash_2025_2026` | `2025-01-02` | `2026-07-31` | `2026-06-30` | `2026-07-30` |
| `continuous_ai_era` | `2023-01-03` | `2026-08-05` | `2026-06-30` | `2026-07-30` |

`h1_2024` 与 `h2_2024` 在 `2024-07-01` 有意重叠一个 session；这是冻结运行合同，
不是应由文档“修正”的日期错误。急跌区间是窗口内诊断切片，不替代完整窗口门。

门禁同时约束财富、最大回撤、账户订单、换手和压力区间收益；缺失或失败的必需窗口必须失败关闭。证券子集、替代实现和其他研究性压力检查可以辅助诊断，但不能替代、分摊或放行这个统一门禁。失败不能通过删除场景、改写统计口径或放宽已评审阈值解决。

### Generalization 阻断验收

本地完整复现命令为：

```bash
uv run python -m uquant.validation generalization-matrix \
  --data-dir data/frozen \
  --output benchmarks/ai_era_generalization.json
```

CI 的六窗口 shard 与最终 aggregator 是发布权威；本地命令用于预检和诊断，不能用删减
场景的成功代替 CI 的完整身份与 234-record 聚合。

canonical manifest 固定 34 只 A 股 AI 产业链股票及点时行业身份；每个官方窗口保留
39 条 contract record，其中 32 条为经济 replay，样本不足记录也必须原样存在。
场景包括完整全集、逐一与全部移除三只核心、无 optical、行业均衡、有效子行业和
20 个固定随机池。随机池只使用基准种子 `20260810`、索引 `0..4`、大小
`5 / 9 / 15 / 20`，并按规范代码排序和固定摘要算法派生；失败 seed 不替换。

每个有效 cell 保存以下经济维度：

| 字段 | 含义 |
|---|---|
| `final_wealth` | 期末权益相对初始资金 |
| `max_drawdown` | 区间最大回撤 |
| `account_orders` | 至少有一笔成交的账户订单数 |
| `gross_turnover` / `annual_turnover` | 区间总换手与年化换手 |
| `top1_concentration` / `top3_concentration` | 绝对 symbol PnL 的 Top-1/Top-3 集中度 |
| `pnl_hhi` | 绝对 symbol PnL 份额 HHI |

聚合同时保留 median、worst、p10 wealth、p90 drawdown、p90 orders 与全部换手/集中度
尾部。v2 policy 逐 cell 对比冻结 champion，并检查 intrinsic floor 与固定随机组的正收益
比例、p10/p90 边界。报告始终显示原始 literal tail 结果；阻断结论同时使用已认证 champion
的 floor/ceiling 作为 non-regression 有效边界，因此 champion 相等或未变 cell 不需要先
Pareto 改进才能通过，而任何超出冻结边界与逐 cell 容差的恶化仍然失败。

`REPLAY_ERROR` 和 `INSUFFICIENT_SAMPLE` 是明确证据状态。与已认证 baseline 完全相同的
replay error 可以保留；新增或变化的 replay error 必须失败。若候选恢复了 baseline 的
replay error，tail non-regression 只在共同有效样本上比较，恢复 cell 还必须落在该组已认证
有效样本的最差包络及既有逐 cell 容差内。若该组没有已认证有效样本，则不得推导或豁免
恢复包络，候选组只能按原始 literal policy 通过。样本不足记录不能伪造指标，也不能通过
删行、补值或换 seed 取得通过。

归因用稳定的 event、origin subsystem/mechanism、lifecycle、replacement 与
industry-at-entry 身份连接 Target、Order、Tranche、Fill。原因文本不作为归因键；
`realized_pnl + open_pnl = final_equity - initial_cash` 必须和最终权益对账。cash drag
与 paired risk avoidance 只作诊断，不能计入 exact accounting PnL，且 post-exit
诊断不能读取窗口结束后的价格。

六个窗口在 CI 中独立分片，最终 `if: always()` aggregator 检查精确 shard set、
234 条记录、HEAD、源码、配置、冻结数据、runtime/lock、universe、industry、window、
scenario 和 causal evidence 身份，再调用冻结 policy/evidence validator。任何分片或
聚合失败都会使独立的 `Generalization Acceptance` 结论失败。

## 证据边界

可靠报告至少应记录：

- Git 提交和生产源码 SHA-256；
- 数据快照、清单和校验摘要；
- Python 3.12 完整版本、NumPy/pandas/uv 版本和 `uv.lock` SHA-256；
- 证券池、时间区间、初始资金和执行契约；
- 完整参数摘要；
- 每个场景的原始指标；
- 生成时间和验证结果。

只报告最优股票池、最优区间或平均值会掩盖薄弱场景。参数选择与最终验证应使用不同区间或不同证券子集。

### 四系统冻结横向基线

`benchmarks/current_heads_competitor_matrix.json` 冻结实施开始时 uquant、aquant、
qwenquant 和 trade 的远程 HEAD，在完全相同的数据、信号时点、next-open、费用滑点、
T+1 和股票池合同下形成 1,056 个逐 Cell 结果。矩阵固定包含 120 个官方池 Cell 和 936
个泛化 Cell；`REPLAY_ERROR` 与 `INSUFFICIENT_SAMPLE` 都是必须保留的证据状态，不能
删行、补值或用不匹配当前身份的数字替代。源码、依赖、适配器、数据、配置和运行时身份分别绑定到
registry、矩阵顶层和每个 Cell，CI 会通过 `python -m research.current_heads` 独立重算
行数、身份、状态、摘要与聚合。

该矩阵用于 Risk Sentinel 融合前的版本基线和后续归因，不是自动推广门，也不替代
历史已认证 champion。矩阵里某个系统领先或落后都不改变既有生产政策；历史横向
结果同样不保证未来。完整审阅、隔离运行说明和已知限制见
[`artifacts/current_heads/analysis.md`](../artifacts/current_heads/analysis.md)。

## Future holdout 与人工执行证据

历史选择和所有冻结 benchmark 最后使用 `2026-08-05`；future holdout 自
`2026-08-06` 起放在独立目录。最后 in-sample 日的收盘决策若在次日成交，属于
holdout。未来 session 未导入时观察与指标必须为 null，不允许伪造 holdout 数值。
固定观察里程碑为 `20 / 40 / 60` 个交易日；首个正式评审需累计 `40--60` 个交易日，
观察结果不能反向修改参数。起始账户必须
匹配已审阅的完整连续回放 SHA-256；观察期评分必须由确定性回放重算，调用方提供的
独立分数文件即使重新封签也不能进入验收。

实际人工执行另由 observational、append-only、broker-independent journal 记录计划
价格、次日开盘、真实成交、人工跳过和实现滑点。它与回放/holdout 评分分离，也不能
写入决策或账户状态；日常系统仍是盘后人工运行、核对并辅助下单。

`benchmarks/future_holdout_lane_registry.json` 为追加式候选登记簿。每条 Lane 固定
真实启用日、完整 Git commit、生产与 Sentinel 源码、有效配置、数据合同、Python、
NumPy、pandas、uv 和锁文件摘要；已开始观察的身份不得修改或删除，新 Lane 不得从已
观察日期回填。新增 source epoch 或候选必须从真实启用日向前登记，不能凭空创建历史
观察。`artifacts/holdout/lane_validation.json` 明确记录样本量、下一里程碑和七个正式
评分；少于 20 日时这些评分全部为 `null`，诊断指标也不得伪装为正式评分。

## 如何判断改动是否安全

对注释、文档或工程质量改动，先证明是否影响可执行输入：

1. 纯 Markdown 运行链接、术语、命令和受影响治理测试；
2. Python 注释/docstring 改动比较去除 docstring 后的 AST，并运行受影响静态检查；
3. 默认配置、冻结数据、打包或运行时输入发生变化时，验证对应摘要和合同；
4. 只有行为身份无法证明不变时，才升级为完整 Engineering、性能和泛化验证。

对策略改动则不能要求指标完全相同，但必须预先定义允许的收益、回撤和交易成本边界，并使用未参与选择的场景复核。

### 模型风险与候选治理

- **候选**只表示值得验证的代码/配置身份，不是生产 champion；搜索结果不得自动写默认值。
- **Champion** 必须绑定代码、配置、数据、证券全集、运行时和完整性能/泛化证据，并通过
  独立审查后才能替换当前生产身份。
- 任何默认参数、风险权限、账户/订单语义或经济分支变化都使旧矩阵身份失效；纯文档与经
  AST 证明等价的注释改动只更新源码身份和受影响工程证据。
- Future Holdout 的 `20/40/60` session 是观察里程碑；达到里程碑允许评审，不自动触发推广。
- 若出现数据完整性、经济账本、风险权限、账户身份或真实执行差异，停止推广并保留失败证据；
  只有非行为性文案和可选清理可以 deferred。

`artifacts/phase1/diagnostics/phase1-history.bundle` 为逐日 first-divergence 与反向
消融提供内容寻址的 commit/source/config/trace 证据；replay 必须先校验 bundle，
再从干净 clone 复放所引用的证据提交。

## 局限与偏差

- 历史结果不保证未来收益；
- 幸存者偏差、行业映射偏差和证券池选择会影响结论；
- 前复权数据无法完全表达真实现金分红过程；
- 次日开盘模型不包含完整排队和冲击成本；
- 参数和风险阈值可能适应已观察市场结构；
- 极端停牌、连续涨跌停和流动性枯竭可能使实际仓位偏离目标；
- 小样本急跌区间的统计不稳定，应与更多压力场景共同使用。

Risk Sentinel 的冻结晋级、拒绝候选与 Evidence Closure 固定结果保存在
`artifacts/sentinel/`。这些历史数值用于审计，不替代本页的长期绩效合同，也不自动授予
新的生产权限。
