# uquant

uquant 是专门面向 2023 年以来 A 股 AI 产业链的日频量化决策系统。它使用现金多头组合，在收盘后生成目标仓位和下一交易日开盘意图，适合每日人工运行、核对并辅助交易决策。

系统不会连接券商自动下单，不使用杠杆，不做空，也不依赖盘中行情。

生产经济性验收从 `2023-01-01` 开始。更早行情可以保留在数据集中形成均线、ATR 等因果特征，但只能作为 warm-up，不能进入初始权益、收益、回撤、订单、成交或换手统计，也不能成为发布门槛。

## 核心能力

- **同一决策内核**：日报与历史回放都调用 `ProductionEngine.decide()`，避免研究路径与日常路径出现行为差异。
- **严格因果时点**：信号只读取决策日及以前的数据，成交最早发生在下一可交易日开盘。
- **双轴判断**：机会状态决定是否值得承担风险，Base Risk 独立给出风险派生上限；强机会
  不能扩大该上限。唯一有限例外是单一战略主导者在一级预警下保留既有仓位，且不得新增风险。
- **组合级风控**：同时约束总仓、单票、持仓数、行业集中度、相关性、流动性和资本回撤。
- **独立风险观察**：Risk Sentinel 以点时市场证据补充基础风险，并在同一日报中说明
  资料覆盖、实际限制来源与观察到的风险；它不能直接卖出或修改总仓上限。
- **低换手执行**：连续确认、最短持有、替换优势、轮动预算、目标迟滞和订单复用共同过滤无效交易。
- **A 股交易约束**：模拟 T+1、涨跌停、停牌、手数、科创板首次买入、费用、滑点、容量和部分成交。
- **可恢复账户**：账户保存订单、成交、持仓生命周期、战略授冠意图、战略所有权周期、账户资本修复、风险状态和数据指纹，并使用原子写入。
- **失败关闭验证**：数据、账户、代码指纹或冻结证据不一致时拒绝继续，而不是猜测或静默修正。

## 默认边界

| 约束 | 默认值 |
|---|---:|
| 初始资金 | 2,000,000 元 |
| 最大总仓位 | 100% |
| 普通趋势名义入场预算 | 80%（仍受当前风险预算约束） |
| 常规单票最大权重 | 60% |
| 证据确认的战略主导者特例上限 | 95% |
| 最大持仓数 | 6 |
| 行业最大权重 | 75% |
| 最小权重变化 | 5% |
| 最小交易金额 | 20,000 元 |
| 最大成交量参与率 | 0.5% |

上表是上限或名义设置，不是每次获准投入的仓位。普通新入场同时核验两个指数的有限
120 日收益：至少一项为正且没有仍在使用的普通修复资本时，先取名义入场预算与当前
`target_gross_cap` 的较小值，再分配给合格候选；两项均不为正时，共享默认 20% 初始额度，
扣除实际普通持仓和 BUY 承诺。仍在使用的修复资本按原共享预算执行，不重复领取额度。
资格、现金、挂单、集中度和最小交易门槛仍须同时满足，未成交 SELL 不释放可用资金。
成熟领涨周期按自身确认和权重分配，不把普通均分份额当成所有新单的统一额度；
仍受当前风险和共同资金账本约束。完整规则见[普通领涨](docs/STRATEGY.md#普通领涨)。

95% 战略主导者权重不是常规入场上限。它只适用于账户中仅有一个已确认战略主导者、
`NORMAL/CAUTION` 且 `reduction_level <= 1`、没有行业/战略损伤/急性撤离 guard、策略本身
也不要求减仓的场景；只能冻结既有敞口，不能新增风险。`CRISIS` 和其他硬风险上限始终生效。

## 验证与证据边界

经济证据按各自合同解释：跨年份旧账户恢复、原 23 场景与已登记确认结果，不能替代完整
Absolute 泛化矩阵。当前验收先核对源码、独立政策、数据及运行环境，再执行真实账户回放；
Absolute 正式聚合需要同一运行与 attempt 的完整八分片，区分执行成功与经济达标。
单场景诊断、工程测试通过或已合并 `main` 都不等于完整经济验收通过。
适用合同、原始交付证据入口、命令和失败语义统一见[当前验收链](docs/ACCEPTANCE.md)。

比较回测时必须注明股票池、起止日期、初始资金、成本、源码、配置、数据与运行环境身份。
原结果保留生成时的身份，不能改贴为最新 HEAD 的新运行；更换股票池或起点后不能沿用原分数。

生产、绩效门和泛化门共用经过摘要保护的 34 只 A 股 AI 产业链证券及点时行业身份。
性能验收验证六个完整 AI-era 窗口；泛化验收在固定全集、行业、移除核心与随机池场景中
检查收益、回撤、订单、换手、集中度和归因一致性。失败场景、样本不足、证券池、seed、
统计口径和冻结 champion 都不能为了让候选通过而改写。

经济账本必须满足 `realized_pnl + open_pnl = final_equity - initial_cash`。
生产公开设置共 13 项；固定策略与风险规则不能通过构造或 `override()` 改写。规则变更需要独立策略授权与适用验证。完整合同见[性能与证据](docs/PERFORMANCE.md)和[参数参考](docs/CONFIGURATION.md)。

Future Holdout 从 `2026-08-06` 起只接受真实、按顺序追加的新 session，遵守 no-backfill；
未观察时正式分数必须为 `null`。人工执行 Journal 独立记录计划、成交、跳过与滑点，不写入
决策或账户状态。完整操作见[Future Holdout](docs/HOLDOUT.md)。

## 安装

唯一受支持的解释器是 Python 3.12；正式验收锁定 Python 3.12.13 和 uv 0.11.33。
先确认当前 `python` 使用所需解释器，再在仓库根目录安装锁定依赖：

```bash
python -m pip install "uv==0.11.33"
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

日扫首先显示今天的结论、下一可交易日需核对的真实订单、逐票支持和阻止因素，以及
风险的实际影响。目标不等于订单，订单不等于成交，缺资料不等于安全。
`--html-output daily_report.html` 可同时生成手机和电脑可读的离线报告；不联网、不另跑决策。

需要限制总仓、普通单票或持仓数量时，使用同一个可选 `--config settings.json` 初始化并
运行账户；只接受当前公开设置。范围、账户绑定和错误示例见[参数参考](docs/CONFIGURATION.md#日常加载)。

以下历史日期、冻结数据和示例股票池只展示命令，不是最新信号。日常使用须指定已核验的
当日数据、决策日及完整股票池，并在运行前备份账户。正常闭环是准备行情与券商快照 →
一次 `daily` 完成对账和盘后决策 → 人工核对执行 → 下次同步真实成交。

### 1. 初始化账户

仅首次建立账户时初始化；已有账户继续使用并备份，不要每日新建或重置历史状态。

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

### 2. 准备当日行情和券商快照

核对股票、指数及参考数据的因果前缀，并准备同一账户的完整 `broker_snapshot.json`。
快照是现金、持仓、当日可卖数量和真实成交的权威来源；重复成交、订单身份、迟到成交及
取消确认都要按真实记录对账。字段和示例见[券商快照](docs/OPERATIONS.md#券商快照)。

下一步带 `--broker-snapshot` 的 `daily` 已包含同步，不需要先运行 `account-sync`。
`account-sync` 只用于单独对账或切换预演；已经同步该快照的副本随后运行 `daily` 时，
省略同一份 `--broker-snapshot`，不要重复同步。

### 3. 生成盘后决策

```bash
uv run uquant daily \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-21 \
  --account account_state.json \
  --broker-snapshot broker_snapshot.json \
  --output daily_report_2026-07-21.md \
  --html-output daily_report_2026-07-21.html
```

日报包含机会状态、风险状态、Risk Sentinel、目标总仓、目标持仓数、逐票目标权重、
订单意图和决策证据。日常只运行这一次 `uquant daily`，不需要再运行独立 Sentinel CLI；
生成意图后仍需人工核对券商状态，并归档运行后账户、快照与日报。缺少有效信号或预算时，
没有新订单也是有效结果；不要靠重置账户或反复运行制造买单。

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

授冠意图以确定性 `grant_id` 绑定证券、资格证据、账户、生产源码身份。停牌、涨停、容量、
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

## 账户连续性与风险恢复

账户使用同一现金、持仓、订单与成交账本；以下峰值仅承担不同风险度量，不是三套资金账户：

| 字段 | 含义与重置边界 |
|---|---|
| `capital_peak` | 终身权益高水位，保留全部历史损失，不因修复清零 |
| `operating_peak` | 短周期运行峰值，可在空仓或确认修复时重新定基 |
| `deployed_peak` | 连续实际持仓期间只升不降，真正空仓时以当前权益重新定基 |

预算修复依据当前部署损伤和市场确认，不要求已结清的现金账户先赚回终身历史高点才能
重新准入，也不会用恢复标签抹掉仍在持有的损伤。确认后逐级释放预算；解除新增冻结不等于
恢复满仓。新部署峰值仍不高于终身高水位的危机线时，统一资本覆盖层保留既有危机总仓上限，
只有严格高于该线才解除这一继承上限；其他风险与执行约束继续有效。
旧 schema 8 缺少 `deployed_peak` 时，有正持仓则从已记录资本高水位保守初始化，空仓则用真实现金。
不能通过手改峰值、`schema_version` 或 `code_hash` 绕过校验；升级和恢复见[运行手册](docs/OPERATIONS.md)。

恢复组合首次从空仓建立仍要求当日突破；已有恢复组合的新成员可在原入场窗口内保留突破
证据，但必须继续满足当日结构、深度、流动性和资金权限。已有成员余量仍要求当日突破，
不把等待窗口变成追加许可。战略组合同步破坏要求至少两只仍有实际持仓的战略成员，普通
持仓不能凑足这一人数；单一战略成员仍受尾部、资本预算和市场风险保护。
完整机制见[策略与风控](docs/STRATEGY.md)和[架构说明](docs/ARCHITECTURE.md)。

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
| `uquant/config/` | 公开设置、领域固定规则、完整策略身份与校验 |
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
| `tools/cloud_guard/` | 云端命令记录、局部检查点与外部写入回读记录，不参与策略 |

## 文档导航

- [当前验收链](docs/ACCEPTANCE.md)
- [架构说明](docs/ARCHITECTURE.md)
- [策略与风控](docs/STRATEGY.md)
- [参数参考](docs/CONFIGURATION.md)
- [运行手册](docs/OPERATIONS.md)
- [Future Holdout](docs/HOLDOUT.md)
- [性能与证据](docs/PERFORMANCE.md)
- [Risk Sentinel](docs/RISK_SENTINEL.md)
- [开发指南](docs/DEVELOPMENT.md)
- [质量契约](docs/QUALITY.md)
- [云端执行与恢复](tools/cloud_guard/README.md)
- [经济权限与因果执行决策](docs/decisions/0001-economic-authority-and-causal-execution.md)
- [源码身份与 holdout epoch 决策](docs/decisions/0002-source-identity-and-holdout-epochs.md)
- [历史证据索引](https://github.com/ychenracing/uquant/blob/7fcf9562e6c7f96250811acd80c2dd4ee46485e3/artifacts/README.md)

发布 wheel 只包含生产命名空间 `uquant*`；`research/`、`scripts/`、`tests/`、文档、
验证工件和冻结数据仍保留在仓库中供复现与治理，但不是可安装的生产 API。

## 本地质量检查

按改动选择最小充分验证。纯 README/说明文字修改先核对事实、命令、链接与相关文档契约，
不默认重跑 wheel 构建、全仓编译或完整回测。相关文档检查示例：

```bash
uv run pytest -q tests/architecture/test_repository_governance.py \
  -k 'canonical_docs_have_resolved_internal_links or bounded_dominant_incumbent_exception'
```

代码修改运行失败项及直接受影响的检查；只有现有证据不足或有效合同明确要求时才扩大验证。
未运行、排队、失败与通过必须分开报告，不把未结束的 Actions 当作成功。
L1→L4 的升级条件、完整开发、构建、安全和发布命令只在[开发指南](docs/DEVELOPMENT.md)维护；性能与泛化经济门、窗口与证据解释
只在[性能与证据](docs/PERFORMANCE.md)维护，避免命令副本漂移。

云端工程任务按[cloud guard](tools/cloud_guard/README.md)记录长命令和重要外部写入，并核验
实际远端保存。它不能自动重启模型回合或保证不中断；仅有本地检查点不等于已远端保全。

## 使用限制

uquant 是研究和交易决策辅助软件，不构成投资建议，也不保证未来收益。日频模型无法处理盘中突发事件；历史开盘成交模型也不能完全复现真实排队、冲击成本和人工执行。每日使用前应核对公司行动、停复牌、涨跌停、数据完整性、可卖数量和实际订单状态。
