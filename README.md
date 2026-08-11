# uquant

uquant 是面向 A 股科技产业链的日频量化决策系统。它只支持现金多头，不使用杠杆、不做空；在收盘后生成目标组合和下一交易日开盘意图，适合每日人工触发并结合券商账户复核。

仓库只维护一套生产实现：实时决策与历史回放共用 `ProductionEngine.decide()`，不存在并行策略入口或隐藏的第二套下单逻辑。

## 核心约束

- 最大总仓位 100%，单票上限 60%，最多持有 6 只股票；
- 风险判断与机会判断相互独立，最终都汇入唯一组合分配器；
- 信号使用当日及以前的数据，订单最早在下一可交易日开盘执行；
- 统一处理 A 股 T+1、涨跌停、停牌、100 股手数、科创板首次 200 股、费用、滑点、容量和部分成交；
- 小幅目标变化落在迟滞区间内时不交易，降低无效换手；
- 战略 cohort 由相对长期证据动态发现：每个 epoch 都要求 3 个成员、`secular_score >= 0.58`、长期证据置信度和行业置信度合格、20 日收益不低于 -5%、科技指数 120 日收益不高于 20%，且签名连续确认 2 日；没有股票代码先验、240 日绝对收益门槛或短周期反弹 bootstrap；
- 风险锚由跨行业长期证据动态确认，连续转坏、慢性退化和资本回撤预算阶梯默认自动工作；
- 已部署持仓出现重复同步冲击且独立偏离确认时，sector guard 进入 Level-2、把总仓限制为 82%，并与普通 risk-off / crisis 减仓分开归因；
- 风险去仓先冻结新增风险，再稀疏压缩目标；成交层优先退出卫星和后加仓 tranche，保护健康核心；
- 普通领涨路径在 `CHOPPY`/`WEAK` 中把新增机会预算限制为 60%/25%，并在 scout 之后稀疏取消弱证据增量；已有健康 Core 由生命周期或确认风险退出，不因单日机会标签机械卖出；
- 空仓超跌路线只在双指数长期弱势，或一弱一稳且分化充分的过渡修复中工作；过渡路线只接受可晋升的深跌候选。单票战术探针默认最多 60% 且仍受风险上限约束；恢复赢家的 20% MFE / 10% 峰值回撤 trail 不是通用核心止损，风险压缩后的战略权重也只在健康确认后逐票恢复；
- 默认启用行业广度确认、持仓同步冲击保护、置信度仓位和闲置现金 challenger scout，无需人工开关；
- conviction 不等权只在强趋势高置信入场同时通过韧性、相对强度、流动性和票间相关性联合门时启用，否则新核心保持等权；
- 生产评分、广度、风险锚和数据要求只使用评审后的稳定参考篮子；研究扩展参考与生产常量隔离，不能因一次实验自动进入实盘；
- 账户文件、代码指纹或历史数据前缀异常时拒绝继续运行；
- 券商快照是现金、持仓、可卖数量和真实成交的权威来源。

## 环境与安装

需要 Python 3.11 或更高版本。

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,data]'
```

`data` 依赖只在使用 AkShare 刷新股票前复权行情时需要。仓库中的冻结 CSV 可直接用于离线回放。

## 快速开始

### 1. 初始化账户

账户会绑定指定日期之前的数据前缀和当前生产代码指纹。未提供 `--date` 时，使用全部必需证券的最新共同日期。

```bash
uquant account-init \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-20 \
  --cash 2000000 \
  --output account_state.json
```

也可以使用模块入口：

```bash
python -m uquant account-init --help
```

当前账户格式为 schema v3。v3 为 tranche 保存入场证据，为订单与成交保存风险减仓策略，并持久化战略 epoch、动态风险锚、连续风险信号、资本预算和 challenger scout 状态。已有旧版账户不会被静默补字段；升级代码后先备份账户，再显式迁移并核对券商快照：

```bash
uquant account-migrate \
  --account account_state.json \
  --acknowledge-code-change
```

迁移保留现金、持仓、订单、成交、数据前缀和策略状态，并写入不可省略的迁移审计记录。详细流程见[运行手册](docs/OPERATIONS.md)。

### 2. 同步券商快照

```bash
uquant account-sync \
  --account account_state.json \
  --snapshot broker_snapshot.json
```

快照至少包含 `as_of`、`cash`、`positions` 和 `fills`。真实成交必须引用 uquant 已生成的 `order_id`，并提供稳定、可幂等去重的 `fill_id`。完整字段见 [运行手册](docs/OPERATIONS.md)。

### 3. 生成每日决策

```bash
uquant daily \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-21 \
  --account account_state.json \
  --broker-snapshot broker_snapshot.json \
  --output daily_report_2026-07-21.md
```

日报包含机会状态、风险状态、目标总仓位、目标持仓数、每只股票的目标权重、次日订单意图和决策摘要。`daily` 只生成并持久化意图，不会连接券商自动下单。

### 4. 历史回放

```bash
uquant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2018-01-02 \
  --end 2026-07-20 \
  --output backtest_result.json
```

回放使用与每日决策相同的特征、状态机、组合分配和执行模型，输出收益、回撤、风险调整收益、订单、成交、费用、换手、持有期、风险事件和权益曲线。

## 数据约定

每个证券使用一个 UTF-8 CSV，文件名为规范化代码，例如 `sz300308.csv` 或 `sh688008.csv`。必需列：

```text
date,open,high,low,close,volume
```

可选 `amount`；缺失时按 `close × volume` 估算。日期必须严格递增且唯一，价格必须为正，成交量不能为负。股票使用前复权价格，指数使用不复权价格。详细校验和追加规则见 [运行手册](docs/OPERATIONS.md)。

## 代码结构

| 路径 | 职责 |
|---|---|
| `uquant/engine.py` | 唯一生产决策内核、账户回放和绩效统计 |
| `uquant/config.py` | 策略、风险、组合和执行参数的唯一来源 |
| `uquant/data.py` | 点时数据加载、校验、前缀哈希和可选刷新 |
| `uquant/features.py` | 因果趋势、动量、波动和突破特征 |
| `uquant/industry.py` | 点时行业强度、广度、加速度和覆盖置信度 |
| `uquant/leader.py` | 领涨评分、成熟度、置信度和行业证据装配 |
| `uquant/opportunity.py` | 机会状态识别与状态迟滞 |
| `uquant/risk.py` | 风险雷达、冲击/修复状态和仓位上限 |
| `uquant/risk_sector.py` | 已部署持仓的同步冲击与确认修复状态机 |
| `uquant/portfolio.py` | 唯一目标组合编排与硬约束出口 |
| `uquant/portfolio_*.py` | 核心、战略、领涨和恢复策略分层实现 |
| `uquant/execution.py` | 次日开盘执行、市场约束、费用和订单生命周期 |
| `uquant/account.py` | 账户校验和原子持久化 |
| `uquant/broker.py` | 券商快照与真实成交幂等对账 |
| `uquant/report.py` | 只读日报渲染 |
| `uquant/validation/` | 冻结数据、绩效、泛化/PDI 和全周期竞品的 fail-closed 晋级门 |
| `research/` | 与生产导入隔离的候选搜索、消融、参数/股票池压力和退出归因 Python API |
| `benchmarks/` | 版本化绩效基线与比较证据 |
| `scripts/backfill_tencent_history.py` | 冻结行情的有界历史补全工具 |
| `tests/` | 数据、策略状态、风险、执行和账户契约测试 |

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [策略与风控](docs/STRATEGY.md)
- [参数参考](docs/CONFIGURATION.md)
- [运行手册](docs/OPERATIONS.md)
- [开发指南](docs/DEVELOPMENT.md)
- [性能与晋级证据](docs/PERFORMANCE.md)
- [工程质量门禁](docs/QUALITY.md)

## 本地验证

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts research tests
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --profile quick
uv run bandit -q -r uquant
```

泛化验证需要显式给出当前全集、覆盖该全集的股票到行业 JSON、要做移除诊断的历史先验证券，以及经过评审的只读 baseline。下面从版本化 promotion 规范读取真实冻结 Pool E 的 32 只证券，能够承载默认 random 6/12/24 和 leave-top-1/2/3/5；不要换回少于 24 只的示例：

```bash
mapfile -t GENERALIZATION_POOL_E < <(
  uv run python -c \
    'import json; print(*json.load(open("benchmarks/promotion_baseline.json", encoding="utf-8"))["pools"]["e"], sep="\n")'
)
uv run python -m uquant.validation generalization \
  --data-dir data/frozen \
  --universe "${GENERALIZATION_POOL_E[@]}" \
  --industries /path/to/industries.json \
  --prior-symbols sz300308 sz300502 sz300394 \
  --start 2018-01-02 \
  --end 2026-07-20 \
  --baseline /path/to/reviewed-generalization.json

uv run python -m uquant.validation competitor \
  --data-dir data/frozen \
  --reference /path/to/reviewed-competitor-matrix.json
```

两个命令都不会生成或更新 reference。缺少 reference、单元不全、来源或执行口径不匹配时会在启动生产回放前 fail closed；不能用空文件或推测值代替真实评审结果。

## 离线研究 API

`research/` 是仓库内 Python API，不是第二个生产引擎，也没有独立命令行入口。它接收调用方提供的回放观测或回调，提供共享参数候选搜索、Pareto/dominance 门、单能力消融、参数和股票池压力、以及成交后的退出归因；`uquant/` 不导入它。最小导入检查可直接在仓库根目录运行：

```bash
uv run python -c 'from research import enumerate_candidates; print(enumerate_candidates({"choppy_target_gross": (0.50, 0.60)}, base={"weak_gross": 0.25}))'
```

完整研究仍必须把同一候选配置用于所有股票池和窗口，再交给 production promotion、generalization 或 competitor 门验证；研究 API 自身不会写生产配置或 reference。

## 风险声明

uquant 是研究与交易决策辅助软件，不构成投资建议，也不保证未来收益。每日执行前仍应人工核对行情完整性、公司行动、停复牌、涨跌停、券商可卖数量和实际订单状态。
