# uquant

uquant 是面向 A 股科技产业链的日频量化决策系统。它使用现金多头组合，在收盘后生成目标仓位和下一交易日开盘意图，适合每日人工运行、核对并辅助交易决策。

系统不会连接券商自动下单，不使用杠杆，不做空，也不依赖盘中行情。

## 核心优势

- **同一决策内核**：日报与历史回放都调用 `ProductionEngine.decide()`，避免研究路径与日常路径出现行为差异。
- **严格因果时点**：信号只读取决策日及以前的数据，成交最早发生在下一可交易日开盘。
- **双轴判断**：机会状态决定是否值得承担风险，风险状态独立给出最高仓位；强机会不能覆盖风险上限。
- **组合级风控**：同时约束总仓、单票、持仓数、行业集中度、相关性、流动性和资本回撤。
- **低换手执行**：连续确认、最短持有、替换优势、轮动预算、目标迟滞和订单复用共同过滤无效交易。
- **A 股交易约束**：模拟 T+1、涨跌停、停牌、手数、科创板首次买入、费用、滑点、容量和部分成交。
- **可恢复账户**：账户保存订单、成交、持仓生命周期、风险状态和数据指纹，并使用原子写入。
- **失败关闭验证**：数据、账户、代码指纹或冻结证据不一致时拒绝继续，而不是猜测或静默修正。

## 默认边界

| 约束 | 默认值 |
|---|---:|
| 初始资金 | 2,000,000 元 |
| 最大总仓位 | 100% |
| 单票最大权重 | 60% |
| 最大持仓数 | 6 |
| 行业最大权重 | 75% |
| 最小权重变化 | 5% |
| 最小交易金额 | 20,000 元 |
| 最大成交量参与率 | 0.5% |

## 安装

需要 Python 3.11 或更高版本。推荐使用锁定依赖：

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

日报包含机会状态、风险状态、目标总仓、目标持仓数、逐票目标权重、订单意图和决策证据。生成意图后仍需人工核对券商状态。

### 4. 历史回放

```bash
uv run uquant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2018-01-02 \
  --end 2026-07-20 \
  --output backtest_result.json
```

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
| `uquant/portfolio*.py` | 唯一目标组合及各生命周期职责 |
| `uquant/execution.py` | 次日开盘执行模型与订单生命周期 |
| `uquant/account.py`、`broker.py` | 账户持久化和券商对账 |
| `uquant/validation/` | 数据、绩效和泛化门禁 |
| `research/` | 与生产导入隔离的离线研究工具 |
| `tests/` | 行为、不变量和失败路径测试 |

## 文档导航

- [架构说明](docs/ARCHITECTURE.md)
- [策略与风控](docs/STRATEGY.md)
- [参数参考](docs/CONFIGURATION.md)
- [运行手册](docs/OPERATIONS.md)
- [性能与证据](docs/PERFORMANCE.md)
- [开发指南](docs/DEVELOPMENT.md)
- [质量契约](docs/QUALITY.md)

## 本地质量检查

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts research tests
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run bandit -q -r uquant
```

## 使用限制

uquant 是研究和交易决策辅助软件，不构成投资建议，也不保证未来收益。日频模型无法处理盘中突发事件；历史开盘成交模型也不能完全复现真实排队、冲击成本和人工执行。每日使用前应核对公司行动、停复牌、涨跌停、数据完整性、可卖数量和实际订单状态。
