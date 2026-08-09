# uquant

uquant 是面向 A 股科技产业链的日频量化决策系统。它只支持现金多头，不使用杠杆、不做空；在收盘后生成目标组合和下一交易日开盘意图，适合每日人工触发并结合券商账户复核。

仓库只维护一套生产实现：实时决策与历史回放共用 `ProductionEngine.decide()`，不存在并行策略入口或隐藏的第二套下单逻辑。

## 核心约束

- 最大总仓位 100%，单票上限 60%，最多持有 6 只股票；
- 风险判断与机会判断相互独立，最终都汇入唯一组合分配器；
- 信号使用当日及以前的数据，订单最早在下一可交易日开盘执行；
- 统一处理 A 股 T+1、涨跌停、停牌、100 股手数、科创板首次 200 股、费用、滑点、容量和部分成交；
- 小幅目标变化落在迟滞区间内时不交易，降低无效换手；
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
| `uquant/leader.py` | 领涨评分、成熟度、置信度和行业映射 |
| `uquant/opportunity.py` | 机会状态识别与状态迟滞 |
| `uquant/risk.py` | 风险雷达、冲击/修复状态和仓位上限 |
| `uquant/portfolio.py` | 唯一目标组合、持仓生命周期和轮动控制 |
| `uquant/execution.py` | 次日开盘执行、市场约束、费用和订单生命周期 |
| `uquant/account.py` | 账户校验和原子持久化 |
| `uquant/broker.py` | 券商快照与真实成交幂等对账 |
| `uquant/report.py` | 只读日报渲染 |
| `scripts/backfill_tencent_history.py` | 冻结行情的有界历史补全工具 |
| `tests/` | 数据、策略状态、风险、执行和账户契约测试 |

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [策略与风控](docs/STRATEGY.md)
- [参数参考](docs/CONFIGURATION.md)
- [运行手册](docs/OPERATIONS.md)
- [开发指南](docs/DEVELOPMENT.md)

## 本地验证

```bash
python -m pytest -q
ruff check .
python -m compileall -q uquant scripts tests
```

## 风险声明

uquant 是研究与交易决策辅助软件，不构成投资建议，也不保证未来收益。每日执行前仍应人工核对行情完整性、公司行动、停复牌、涨跌停、券商可卖数量和实际订单状态。
