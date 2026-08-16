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

### Phase 1 AI-era 阻断绩效门

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

full profile 是 Phase 1 不可拆分的阻断经济性真相，覆盖六个官方窗口：

- `h1_2023`：2023 上半年；
- `h2_2023`：2023 下半年；
- `h1_2024`：2024 上半年；
- `h2_2024`：2024 下半年；
- `bull_crash_2025_2026`：2025 至 2026 牛市与急跌；
- `continuous_ai_era`：从 2023 年首个交易日起连续覆盖整个 AI-era。

门禁同时约束财富、最大回撤、账户订单、换手和压力区间收益；缺失或失败的必需窗口必须失败关闭。证券子集、替代实现和其他研究性压力检查可以辅助诊断，但不能替代、分摊或放行这个统一门禁。失败不能通过删除场景、改写统计口径或放宽已评审阈值解决。

### Phase 2 Generalization 阻断门

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
尾部。policy 逐 cell 对比冻结 champion，并检查 intrinsic floor 与固定随机组的正收益
比例、p10/p90 边界。`REPLAY_ERROR` 和 `INSUFFICIENT_SAMPLE` 是明确证据状态；前者
强制失败，后者不能伪造指标，也不能通过删行、补值或换 seed 取得通过。

归因用稳定的 event、origin subsystem/mechanism、lifecycle、replacement 与
industry-at-entry 身份连接 Target、Order、Tranche、Fill。原因文本不作为归因键；
`realized_pnl + open_pnl = final_equity - initial_cash` 必须和最终权益对账。cash drag
与 paired risk avoidance 只作诊断，不能计入 exact accounting PnL，且 post-exit
诊断不能读取窗口结束后的价格。

六个窗口在 CI 中独立分片，最终 `if: always()` aggregator 检查精确 shard set、
234 条记录、HEAD、源码、配置、冻结数据、runtime/lock、universe、industry、window、
scenario 和 causal evidence 身份，再调用冻结 policy/evidence validator。任何分片或
聚合失败都会使独立的 `Phase 2 Generalization` 结论失败。

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

## Future holdout 与人工执行证据

历史选择和所有冻结 benchmark 最后使用 `2026-08-05`；future holdout 自
`2026-08-06` 起放在独立目录。最后 in-sample 日的收盘决策若在次日成交，属于
holdout。未来 session 未导入时观察与指标必须为 null，不允许伪造 holdout 数值；
首个正式评审需累计 `40--60` 个交易日，观察结果不能反向修改参数。

实际人工执行另由 observational、append-only、broker-independent journal 记录计划
价格、次日开盘、真实成交、人工跳过和实现滑点。它与回放/holdout 评分分离，也不能
写入决策或账户状态；日常系统仍是盘后人工运行、核对并辅助下单。

## 如何判断改动是否安全

对注释、文档或工程质量改动，应同时满足：

1. 去除 docstring 后的 Python AST 与基线相同；
2. 默认配置摘要相同；
3. 冻结数据摘要相同；
4. 完整测试和静态门禁通过；
5. full profile 中每个 AI-era 场景的财富、回撤、订单、换手和压力区间收益逐项相同。

对策略改动则不能要求指标完全相同，但必须预先定义允许的收益、回撤和交易成本边界，并使用未参与选择的场景复核。

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
