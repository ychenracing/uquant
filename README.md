# uquant

uquant 是专门面向 2023 年以来 A 股 AI 产业链的日频量化决策系统。它使用现金多头组合，在收盘后生成目标仓位和下一交易日开盘意图，适合每日人工运行、核对并辅助交易决策。

系统不会连接券商自动下单，不使用杠杆，不做空，也不依赖盘中行情。

生产经济性验收从 `2023-01-01` 开始。更早行情可以保留在数据集中形成均线、ATR 等因果特征，但只能作为 warm-up，不能进入初始权益、收益、回撤、订单、成交或换手统计，也不能成为发布门槛。

## 核心优势

- **同一决策内核**：日报与历史回放都调用 `ProductionEngine.decide()`，避免研究路径与日常路径出现行为差异。
- **严格因果时点**：信号只读取决策日及以前的数据，成交最早发生在下一可交易日开盘。
- **双轴判断**：机会状态决定是否值得承担风险，风险状态独立给出最高仓位；强机会不能覆盖风险上限。
- **组合级风控**：同时约束总仓、单票、持仓数、行业集中度、相关性、流动性和资本回撤。
- **独立风险观察**：Risk Sentinel 以点时市场证据补充基础风险，并在同一日报中说明
  Coverage、Owner 与风险 Family；它不能直接卖出或修改总仓上限。
- **低换手执行**：连续确认、最短持有、替换优势、轮动预算、目标迟滞和订单复用共同过滤无效交易。
- **A 股交易约束**：模拟 T+1、涨跌停、停牌、手数、科创板首次买入、费用、滑点、容量和部分成交。
- **可恢复账户**：账户保存订单、成交、持仓生命周期、风险状态和数据指纹，并使用原子写入。
- **失败关闭验证**：数据、账户、代码指纹或冻结证据不一致时拒绝继续，而不是猜测或静默修正。

## 默认边界

| 约束 | 默认值 |
|---|---:|
| 初始资金 | 2,000,000 元 |
| 最大总仓位 | 100% |
| 常规单票最大权重 | 60% |
| 证据确认的战略主导者特例上限 | 95% |
| 最大持仓数 | 6 |
| 行业最大权重 | 75% |
| 最小权重变化 | 5% |
| 最小交易金额 | 20,000 元 |
| 最大成交量参与率 | 0.5% |

## 验证与演进边界

生产与泛化共用 `uquant/validation/resources/ai_universe_manifest.json` 中经过内容摘要
保护的 34 只 A 股 AI 产业链证券及其点时行业归属；消费、白酒、新能源或宽基股票
不进入可交易全集。每天仍由使用者人工运行、核对并辅助下单，研究和 CI 不会改变
这一定位。2023 年以前的行始终只是 warm-up。

Phase 2 Generalization 在六个官方窗口分别保留完整全集、逐一/全部移除三只核心、
去 optical、行业均衡、有效子行业和固定随机池。随机契约只允许基准种子 `20260810`、
索引 `0..4`、池大小 `5 / 9 / 15 / 20`；失败种子不替换，样本不足也保留为证据。
报告覆盖 `final_wealth`、`max_drawdown`、`account_orders`、`gross_turnover`、
`annual_turnover`、`top1_concentration`、`top3_concentration` 和 `pnl_hhi`，并按冻结
champion 与不可放宽的政策失败关闭。

经济归因沿稳定事件身份连接 Target、Order、Tranche 和 Fill；展示用原因文字不是
归因键。账本满足 `realized_pnl + open_pnl = final_equity - initial_cash`，而 cash drag
与 risk avoidance 是诊断量，不伪装成会计 PnL。配置字段由 `MARKET_RULE`、`SAFETY`、
`ECONOMIC`、`DERIVED`、`COMPATIBILITY` 五类完整治理，只有 `ECONOMIC` 可进入候选
选择。独立消融的当前结论是 `KEEP=10`、`DELETE=1`、`INCONCLUSIVE=2`。

历史样本截至 `2026-08-05`；独立 future holdout 从 `2026-08-06` 开始，固定里程碑为
`20 / 40 / 60` 个交易日，首次正式评审仍需累计 `40--60` 个交易日。在导入首个
未来交易日以前，观察和指标必须为 null；holdout
表现不得反向调参。另有 observational、append-only、broker-independent 的人工执行
journal 记录计划价、次日开盘、真实成交、人工跳过与滑点，但不写入决策或账户状态。

## 安装

唯一受支持的解释器是 Python 3.12。使用锁定依赖：

```bash
python -m pip install uv
uv sync --frozen --extra dev
```

只有通过 AkShare 刷新股票行情时才需要 `data` 可选依赖：

```bash
uv sync --frozen --extra dev --extra data
```

## 快速开始

### 1. 初始化账户

```bash
uv run uquant account-init \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-20 \
  --cash 2000000 \
  --output account_state.json
```

账户会绑定当前数据前缀和生产代码指纹。

### 2. 同步券商快照

```bash
uv run uquant account-sync \
  --account account_state.json \
  --snapshot broker_snapshot.json
```

券商快照是现金、持仓、当日可卖数量和真实成交的权威来源。完整字段见[运行手册](docs/OPERATIONS.md)。

### 3. 生成盘后决策

```bash
uv run uquant daily \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-21 \
  --account account_state.json \
  --broker-snapshot broker_snapshot.json \
  --output daily_report_2026-07-21.md
```

日报包含机会状态、风险状态、Risk Sentinel、目标总仓、目标持仓数、逐票目标权重、
订单意图和决策证据。日常只运行这一次 `uquant daily`，不需要再运行独立 Sentinel CLI；
生成意图后仍需人工核对券商状态。

### 4. 历史回放

```bash
uv run uquant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2023-01-03 \
  --end 2026-07-20 \
  --output backtest_result.json
```

数据目录可以包含 2023 年以前的行供特征 warm-up；回放的经济账本和指标从给定的 2023+ 起点开始。

## 数据格式

每只证券对应一个 UTF-8 CSV，例如 `sz300308.csv`。必需列：

```text
date,open,high,low,close,volume
```

`amount` 可选；缺失时按 `close × volume` 估算。日期必须唯一且递增，OHLC 必须为正，成交量不得为负。股票使用前复权价格，指数使用不复权价格。

## 项目结构

| 路径 | 职责 |
|---|---|
| `uquant/engine.py` | 唯一生产编排、日报决策和回放 |
| `uquant/config.py` | 参数与约束的唯一来源 |
| `uquant/data.py`、`features.py` | 点时数据与因果特征 |
| `uquant/leader.py`、`industry.py` | 领涨与行业证据 |
| `uquant/opportunity.py`、`risk*.py` | 机会和风险状态 |
| `uquant/risk_sentinel/` | 独立点时风险证据、Coverage 与 Freeze-only 映射 |
| `uquant/portfolio*.py` | 唯一目标组合及各生命周期职责 |
| `uquant/execution.py` | 次日开盘执行模型与订单生命周期 |
| `uquant/account.py`、`broker.py` | 账户持久化和券商对账 |
| `uquant/validation/` | 数据完整性、Phase 1 绩效和 Phase 2 泛化门禁 |
| `research/` | 与生产导入隔离的离线研究工具 |
| `tests/` | 行为、不变量和失败路径测试 |

## 文档导航

- [架构说明](docs/ARCHITECTURE.md)
- [策略与风控](docs/STRATEGY.md)
- [参数参考](docs/CONFIGURATION.md)
- [运行手册](docs/OPERATIONS.md)
- [性能与证据](docs/PERFORMANCE.md)
- [Risk Sentinel](docs/RISK_SENTINEL.md)
- [开发指南](docs/DEVELOPMENT.md)
- [质量契约](docs/QUALITY.md)

## 本地质量检查

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts research tests
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run bandit -q -r uquant research scripts
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --profile full \
  --output benchmarks/ai_era_performance.json
```

该文件是 checkout 后生成并由 CI 上传的运行证据，不纳入 Git；否则文件内的
`production_commit` 会反过来改变提交 SHA，无法与生成它的 HEAD 精确相等。

GitHub 对每个 PR 和 `main` push 独立给出 `Engineering`、`Phase 1 Performance` 和
`Phase 2 Generalization` 三个稳定阻断结论。Phase 2 按六个官方窗口分片，但最终结论
会在所有分片结束后检查精确 HEAD、完整 provenance、234 条记录和冻结政策；任何
缺失记录，或新增/变化的回放错误，以及政策失败都不会被其他成功分片抵消。
已认证 baseline 的回放错误若在候选中恢复为有效 cell，则按[性能与证据](docs/PERFORMANCE.md)中的
共同样本与恢复包络规则验证，不作为“新增/变化的回放错误”处理。

## 使用限制

uquant 是研究和交易决策辅助软件，不构成投资建议，也不保证未来收益。日频模型无法处理盘中突发事件；历史开盘成交模型也不能完全复现真实排队、冲击成本和人工执行。每日使用前应核对公司行动、停复牌、涨跌停、数据完整性、可卖数量和实际订单状态。
