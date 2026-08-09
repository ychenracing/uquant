# Unified AI Quant

一个独立、统一、因果的 A 股 AI 产业链日频量化决策系统。系统只做现金多头，不加杠杆、不做空；收盘后生成唯一组合目标，按下一可交易日开盘模型执行。正式运行不依赖 `qwenquant`、`AQuant` 或 `trade`。

项目只有一个目标：在收益、回撤、交易效率、Leader、Risk、股票池鲁棒性和生产工程等绝大多数方面达到或超过这三个项目。旧 `73/74` 验收报告已经废弃，不能再作为“完成”的依据。

## 当前结果与差距

冻结数据上的当前针对性同合同重放：

| 窗口 | 当前结论 |
|---|---|
| 2025-04-01–2026-06-30 强趋势 | 六个主池财富 13.095x–13.202x，全部超过三旧最佳；DD 约 15.96%–15.98%，订单 10–12。 |
| 2018-01-02–2026-07-20 连续周期 | 六池中五池收益超过三旧最佳，六池订单都更少；a 为 36.846x，略低于 qwenquant 38.063x。 |
| 连续周期 DD | 28%–40%，尚未达到 trade 约 19%–21% 的最佳档，是最普遍的缺口。 |
| 2021 大轮动 | d/f22/e 收益仍低于旧最佳；F22/E 的 DD 和三池订单数已有优势。 |
| unknown/random | 研究设施存在，但当前代码没有重跑整套 900 random；不引用旧产物冒充当前结论。 |

完整四系统回测表见 [docs/BACKTEST_COMPARISON.md](docs/BACKTEST_COMPARISON.md)，逐项复核见 [docs/IMPLEMENTATION_AUDIT.md](docs/IMPLEMENTATION_AUDIT.md)，Phase 进度见 [docs/PHASE_IMPLEMENTATION_STATUS.md](docs/PHASE_IMPLEMENTATION_STATUS.md)。

## 唯一生产路径

```text
冻结/更新数据 → 因果特征与 Leader → Opportunity + 独立 Risk
              → 唯一 PortfolioAllocator → 每票唯一 Target
              → 次日开盘 ExecutionPlanner → AccountState → Daily Report
```

生产约束：

- 一个 `AccountState`、一个 `ProductionEngine`、一个 `PortfolioAllocator`；
- daily 与 backtest 共用 `decide()`；
- 每只股票每天只有一个最终 target；
- 单票不超过 60%，总仓不超过 100%，最多 6 只；
- T+1、停牌、涨跌停、100 股手数、科创板首次 200 股、费用、滑点、容量和部分成交统一在执行层；
- 账户状态、代码 hash 或历史数据前缀异常时 fail closed；
- 数据 hash 按 as-of 前缀绑定，正常追加未来行情不会破坏已保存账户，历史改写会被拒绝。

## 安装

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,data]'
```

需要 Python 3.11+。AkShare 只用于可选的股票 QFQ 刷新；冻结回放只依赖仓库 CSV。在线 raw-index adapter 与第二数据源尚未实现。

## 真实账户流程

初始化账户，并把 provenance 绑定到指定日期（省略 `--date` 时使用最新共同日期）：

```bash
python -m unified_ai_quant account-init \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-20 \
  --output account_state.json
```

券商快照是现金、持仓、可卖数量和真实成交的权威来源。每笔成交必须引用系统生成的 `order_id`，并提供稳定、幂等的 `fill_id`：

```json
{
  "as_of": "2026-07-21",
  "cash": 1000000.0,
  "positions": [
    {
      "symbol": "sz300308",
      "shares": 5000,
      "sellable_shares": 0,
      "avg_cost": 100.026
    }
  ],
  "fills": [
    {
      "fill_id": "BROKER-20260721-0001",
      "order_id": "O000000001",
      "fill_date": "2026-07-21",
      "symbol": "sz300308",
      "side": "BUY",
      "shares": 5000,
      "price": 100.0,
      "commission": 125.0,
      "transfer_fee": 5.0,
      "final": true
    }
  ]
}
```

可单独同步：

```bash
python -m unified_ai_quant account-sync \
  --account account_state.json \
  --snapshot broker_snapshot_2026-07-21.json
```

也可在收盘决策前同步并生成唯一日报：

```bash
python -m unified_ai_quant daily \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-21 \
  --account account_state.json \
  --broker-snapshot broker_snapshot_2026-07-21.json \
  --output daily_report_2026-07-21.md
```

## 回放与针对性检查

```bash
python -m unified_ai_quant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2018-01-02 --end 2026-07-20

python -m pytest -q \
  tests/test_data_and_leader.py \
  tests/test_execution.py \
  tests/test_engine_contracts.py \
  tests/test_lifecycle_and_risk.py
```

`validation/` 中仍保留 stress、universe perturbation、成本/容量、nested walk-forward、PBO/DSR 和旧项目 adapter，作为研究工具使用；不要把旧验收 JSON 当作当前代码的能力结论。

## 项目结构

| 路径 | 责任 |
|---|---|
| `unified_ai_quant/engine.py` | 唯一生产决策内核和逐日账户回放 |
| `unified_ai_quant/portfolio.py` | 唯一组合分配器、生命周期、迟滞和 rotation |
| `unified_ai_quant/risk.py` | 独立风险雷达、双 DD、Shock/Recovery/Capital Guard |
| `unified_ai_quant/leader.py` | 固定 Reference、Mature/Emerging、置信度与替代证据 |
| `unified_ai_quant/execution.py` | next-open、T+1、涨跌停、容量、费用和部分成交 |
| `unified_ai_quant/broker.py` | 券商权威快照与真实 fill 幂等对账 |
| `unified_ai_quant/account.py` | 原子持久化和 fail-closed 校验 |
| `unified_ai_quant/validation/` | 同合同研究、压力与统计工具，不是第二生产路径 |
| `benchmarks/` | 三旧项目只读冻结基线与历史研究产物 |

## 风险声明

本仓库是量化研究和决策辅助软件，不构成投资建议。真实交易仍需人工复核行情完整性、公司行动、涨跌停、停牌、券商可卖数量和订单状态；历史重放不保证未来结果。
