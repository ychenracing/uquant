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
- **可恢复账户**：账户保存订单、成交、持仓生命周期、战略授冠意图、战略所有权周期、账户资本修复、风险状态和数据指纹，并使用原子写入。
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
性能验收验证六个完整 AI-era 窗口；泛化验收在固定全集、行业、移除核心与随机池场景中
检查收益、回撤、订单、换手、集中度和归因一致性。失败场景、样本不足、证券池、seed、
统计口径和冻结 champion 都不能为了让候选通过而改写。

经济账本必须满足 `realized_pnl + open_pnl = final_equity - initial_cash`。只有治理为
`ECONOMIC` 参数可以进入策略选择；`MARKET_RULE`、`SAFETY` 和 `DERIVED` 字段不得被当作
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
| `uquant holdout-*`、`execution-journal` | 单步 Holdout 与 Journal 操作 | 不作为 operator 默认工作流 |

### 1. 初始化账户

```bash
uv run uquant account-init \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-20 \
  --cash 2000000 \
  --output account_state.json
```

账户会绑定当前数据前缀和生产代码指纹。账户文件必须使用 schema 8；其他整数版本由
`UnsupportedAccountSchemaError` 拒绝，恢复方式见[运行手册](docs/OPERATIONS.md)。

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

## 战略授冠边界

战略候选观察与资本部署是两项独立职责。系统在 `RISK_OFF`、`CRISIS`、
`freeze_new_risk` 或资本预算阻塞期间仍会只读更新候选、资格路线、证据摘要和连续确认，
但不会因此生成 Target、Order 或 Fill。只有当前风险、机会、资金、执行和账户状态共同允许时，
`PortfolioAllocator` 才能从已确认观察创建唯一的 `StrategicGrantIntent`；风险模块仍独占
`target_gross_cap`。

授冠意图以确定性 `grant_id` 绑定证券、资格证据、账户和生产源码身份。停牌、涨停、容量、
手数、暂时现金不足、部分成交、待确认订单或重启只会暂停同一授冠的执行；恢复时按真实未成交
数量重新经过风险和组合分配。候选或原授权证据失效、数据身份变化、永久退出
允许证券池、观察窗口耗尽或发现其他活动战略 owner 时，旧授冠会明确终结并撤销陈旧订单。

`StrategicEpoch` 是与授冠意图分离的持久化所有权账本。授冠创建时可以登记未成交的
`PROBE`，但只有同一证券、grant、event 和 epoch 身份的真实正向 Fill 才能激活所有权；
战略身份账本任意时刻最多一个 `ACTIVE` epoch；其他独立合格证券可以同时获得普通核心
资本。唯一组合统一计算实际现金、挂单、行业、相关性与风险预算，不要求先清空健康旧持仓。
旧战略身份结清后可以创建新 grant；真正退出的证券须重新确认自身资格，不再冻结其他
候选的确认进度。同一 owner 重新获得所有权也必须经过
新的资格、grant、Target、Order、Fill 和 epoch，不能修改旧 epoch 的证券身份。

战略上下文显式区分可交易、资格参考和风险参考三类 universe。只有可交易成员可以产生
Target 和 Order；资格参考只提供同行、行业 breadth 与见证证据；风险参考只提供 broad、tech
和风险锚。角色中预期但缺少当日因果数据时失败关闭，有意移出角色集合的成员不进入 coverage
分母。资格以 `FULL_COHORT`、`STRONG_PAIR` 或 `ABSOLUTE_SINGLE` 的独立证据族 quorum
确认；后两者及账户修复后的重新进入只能从受限 probe 开始，不能直接获得 95% 权重。

全现金账户的资本修复时钟属于账户 damage episode，不属于某个候选。预算业务层级
`0 / 1 / 2 / 3` 分别要求 `20 / 40 / 60 / 60` 个健康交易日；候选切换不会清零这个账户时钟，
但每个候选仍须独立完成原资格确认。账户达到 `READY` 后只为当日合格候选签发一次性、
确定性 authorization，并继续通过唯一的 Risk 和 `PortfolioAllocator` 生成受限 Target。

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
| `uquant/account/` | schema 8 编解码、账户校验、经济/代码身份与原子持久化 |
| `uquant/risk_sentinel/` | 独立风险证据、Coverage 与 `FREEZE_ONLY` 映射 |
| `uquant/contracts/` | 共享不可变合同、严格 JSON 与资源身份 |
| `uquant/broker.py`、`report.py` | 券商对账与只读日报渲染 |
| `uquant/validation/` | 数据完整性、AI-era 性能和泛化门禁 |
| `uquant/engine.py`、`portfolio_{leaders,strategic,recovery}.py` | 委托到 `application/` 与 `portfolio/` 所有者的公共入口 |
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

本地验证按影响面从 L1 开始，只有下一级无法证明安全时才升级；完整 L4 是稳定候选的
一次性验收门，不是每次文档或小修订的内循环。以下是纯文档/构建治理改动的 L1 示例：

```bash
uv run pytest -q tests/test_reproducible_wheel_build.py \
  tests/architecture/test_repository_governance.py
uv run ruff check scripts/build_reproducible_wheel.py \
  tests/test_reproducible_wheel_build.py
uv run python -m compileall -q uquant scripts research tests
```

其他改动应把路径替换为直接受影响的测试和模块。L1→L4 的升级条件、完整开发、构建、
安全和发布命令只在[开发指南](docs/DEVELOPMENT.md)维护；性能与泛化经济门、窗口与证据解释
只在[性能与证据](docs/PERFORMANCE.md)维护，避免命令副本漂移。

## 使用限制

uquant 是研究和交易决策辅助软件，不构成投资建议，也不保证未来收益。日频模型无法处理盘中突发事件；历史开盘成交模型也不能完全复现真实排队、冲击成本和人工执行。每日使用前应核对公司行动、停复牌、涨跌停、数据完整性、可卖数量和实际订单状态。
