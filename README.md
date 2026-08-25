# uquant

uquant 是专门面向 2023 年以来 A 股 AI 产业链的日频量化决策系统。它使用现金多头组合，在收盘后生成目标仓位和下一交易日开盘意图，适合每日人工运行、核对并辅助交易决策。

系统不会连接券商自动下单，不使用杠杆，不做空，也不依赖盘中行情。

生产经济性验收从 `2023-01-01` 开始。更早行情可以保留在数据集中形成均线、ATR 等因果特征，但只能作为 warm-up，不能进入初始权益、收益、回撤、订单、成交或换手统计，也不能成为发布门槛。

## 核心优势

- **同一决策内核**：日报与历史回放都调用 `ProductionEngine.decide()`，避免研究路径与日常路径出现行为差异。
- **严格因果时点**：信号只读取决策日及以前的数据，成交最早发生在下一可交易日开盘。
- **双轴判断**：机会状态决定是否值得承担风险，Base Risk 独立给出风险派生上限；强机会
  不能扩大该上限。唯一有限例外是单一战略主导者在一级预警下保留既有仓位，且不得新增风险。
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

95% 战略主导者权重不是常规入场上限。它只适用于账户中仅有一个已确认战略主导者、
`NORMAL/CAUTION` 且 `reduction_level <= 1`、没有行业/战略损伤/急性撤离 guard、策略本身
也不要求减仓的场景；只能冻结既有敞口，不能新增风险。`CRISIS` 和其他硬风险上限始终生效。

## 验证与演进边界

生产、绩效门和泛化门共用经过摘要保护的 34 只 A 股 AI 产业链证券及点时行业身份。
Phase 1 验证六个完整 AI-era 窗口；Phase 2 在固定全集、行业、移除核心与随机池场景中
检查收益、回撤、订单、换手、集中度和归因一致性。失败场景、样本不足、证券池、seed、
统计口径和冻结 champion 都不能为了让候选通过而改写。

经济账本必须满足 `realized_pnl + open_pnl = final_equity - initial_cash`。只有治理为
`ECONOMIC` 的参数可以进入策略选择；市场规则、安全限制、派生值和兼容字段不得被当作
优化自由度。完整合同见[性能与证据](docs/PERFORMANCE.md)和[参数参考](docs/CONFIGURATION.md)。

Future Holdout 从 `2026-08-06` 起只接受真实、按顺序追加的新 session，遵守 no-backfill；
未观察时正式分数必须为 `null`。人工执行 Journal 独立记录计划、成交、跳过与滑点，不写入
决策或账户状态。完整操作见[Future Holdout](docs/HOLDOUT.md)。

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

操作入口按权限分层，避免把内部构件误当成生产流程：

| 用途 | 权威入口 | 权限 |
|---|---|---|
| 日常账户与决策 | `uquant account-init/account-sync/daily/backtest` | 唯一生产账户与决策入口 |
| Future Holdout 与人工执行证据 | `python -m scripts.production_observation`、`python -m scripts.future_holdout` | 观察、回放与 Journal，不改策略 |
| 独立 Sentinel 诊断 | `uquant-sentinel` | 离线只读 Shadow，不是日常生产步骤 |
| `uquant holdout-*`、`execution-journal` | 兼容/低层构件 | 不作为 operator 默认入口 |

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

真实 Future Holdout 使用 `python -m scripts.production_observation run` 一次性完成输入验证、
运行前备份、session 追加、确定性回放、日报、Lane 报告与 receipt 封存。它仍不连接券商、
自动下单或把观察结果写回模型；完整命令和恢复流程见[Future Holdout](docs/HOLDOUT.md)。

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
| `uquant/application/` | 日报决策、回放、指标、归因和风险时间线编排 |
| `uquant/config/` | 参数模型、默认值、校验与治理分类 |
| `uquant/data.py`、`features.py`、`reference*.py` | 点时数据、因果特征和共享参考上下文 |
| `uquant/industry.py`、`leader.py`、`opportunity.py` | 行业、领涨与机会状态证据 |
| `uquant/market/`、`uquant/risk/` | replay 工作区、Base Risk 评估与状态转换 |
| `uquant/portfolio/` | 唯一目标组合、硬约束与持仓生命周期 |
| `uquant/execution/` | 次日开盘订单、市场约束、费用和成交生命周期 |
| `uquant/account/` | 账户模型、身份校验、迁移与原子持久化 |
| `uquant/risk_sentinel/` | 独立风险证据、Coverage 与 `FREEZE_ONLY` 映射 |
| `uquant/contracts/` | 共享不可变合同、严格 JSON 与资源身份 |
| `uquant/broker.py`、`report.py` | 券商对账与只读日报渲染 |
| `uquant/validation/` | 数据完整性、Phase 1 绩效和 Phase 2 泛化门禁 |
| `uquant/engine.py`、`portfolio_{leaders,strategic,recovery}.py` | 保持旧导入、pickle 与公共 API 的兼容 facade |
| `research/` | 与生产导入隔离的离线研究工具 |
| `scripts/` | 仓库内运维、观察与验证入口，不进入 wheel |
| `tests/` | 行为、不变量和失败路径测试 |

## 文档导航

- [架构说明](docs/ARCHITECTURE.md)
- [策略与风控](docs/STRATEGY.md)
- [参数参考](docs/CONFIGURATION.md)
- [运行手册](docs/OPERATIONS.md)
- [Future Holdout](docs/HOLDOUT.md)
- [性能与证据](docs/PERFORMANCE.md)
- [Risk Sentinel](docs/RISK_SENTINEL.md)
- [开发指南](docs/DEVELOPMENT.md)
- [质量契约](docs/QUALITY.md)
- [经济权限与因果执行决策](docs/decisions/0001-economic-authority-and-causal-execution.md)
- [源码身份与 holdout epoch 决策](docs/decisions/0002-source-identity-and-holdout-epochs.md)
- [历史证据索引](artifacts/README.md)

发布 wheel 只包含生产命名空间 `uquant*`；`research/`、`scripts/`、`tests/`、文档、
验证工件和冻结数据仍保留在仓库中供复现与治理，但不是可安装的生产 API。

## 本地质量检查

```bash
uv run ruff check .
uv run pytest -q
uv run python -m compileall -q uquant scripts research tests
```

完整开发、构建、安全和发布命令只在[开发指南](docs/DEVELOPMENT.md)维护；Phase 1/2
经济门、窗口与证据解释只在[性能与证据](docs/PERFORMANCE.md)维护，避免命令副本漂移。

## 使用限制

uquant 是研究和交易决策辅助软件，不构成投资建议，也不保证未来收益。日频模型无法处理盘中突发事件；历史开盘成交模型也不能完全复现真实排队、冲击成本和人工执行。每日使用前应核对公司行动、停复牌、涨跌停、数据完整性、可卖数量和实际订单状态。
