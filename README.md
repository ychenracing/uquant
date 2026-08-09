# Unified AI Quant

一个独立、统一、因果的 A 股 AI 产业链日频量化决策系统。系统仅做现金多头，不加杠杆、不做空；收盘后生成唯一组合目标，并按下一可交易日开盘价模型执行。正式运行不依赖 `qwenquant`、`aquant` 或 `trade`。

> 当前严格验收结论：**NOT FULLY ACCEPTED / CANDIDATE（73/74）**。除 K2 单次 promotion holdback 外，正确性、完整三方矩阵、生产回放、牛/熊/震荡期、风险提前量、交易次数、随机压力、add/drop、Leader、参数稳定性及旧依赖清除均已通过。2026-07-21 至 2026-08-05 的已封存窗口被正确地消费一次并真实失败；修复后的同窗重放仅作诊断，不能改写历史 K2。修复候选已冻结，新 K2 已预注册为 2026-08-06 至 2026-08-21 的 12-session 未来窗口，但本地数据仍截至 2026-08-05，因此尚不能合法评估。详见 [ACCEPTANCE_REPORT.md](ACCEPTANCE_REPORT.md) 和 [Promotion Holdback Postmortem](docs/PROMOTION_POSTMORTEM.md)。

当前共同 pool-b（2025-04-01 至 2026-06-30）实测为 `13.1098x / 15.96% DD / 10 orders`，高于三个旧系统的最佳财富 `12.7595x`；五个固定池的 bull non-inferiority、DD 和订单门均通过。963 个压力场景包含 900 个确定性随机股票池；参数/成本/容量矩阵为 180 个实验，另有 54 个 nested walk-forward 单元。这些成绩不能覆盖 K2 的真实失败，因此项目没有标记为 Production。

## 统一生产路径

```text
冻结/更新数据 → 特征与 Leader → Opportunity + 独立 Risk
              → 唯一 PortfolioAllocator → 每票唯一 Target
              → 次日开盘 ExecutionPlanner → AccountState → Daily Report
```

生产约束只有一套：

- 一个 `AccountState`；
- 一个 `ProductionEngine`，日常运行和回测共用 `decide()`；
- 一个 `PortfolioAllocator`，Alpha 和 Risk 都不能直接下单；
- 每只股票每天一个最终 target；
- 一份最终 Daily Report；
- 单票不超过 60%，总仓不超过 100%，最多 6 只；
- T+1、停牌、涨跌停、100 股手数、科创板首次 200 股、费用、滑点、容量和部分成交均在执行层处理；
- 账户、数据哈希或时间状态异常时 fail closed。

## 安装

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev,data]'
```

Python 3.11 及以上版本。`akshare` 只用于可选的数据刷新；冻结回放只依赖仓库内 CSV。

## 日常运行

先初始化真实账户文件：

```bash
python -m unified_ai_quant account-init \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --output account_state.json
```

收盘后生成次日计划：

```bash
python -m unified_ai_quant daily \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-28 \
  --account account_state.json \
  --output daily_report_2026-07-28.md
```

回测与验收：

```bash
python -m unified_ai_quant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2025-04-01 --end 2026-06-30

python -m pytest -q
python -m unified_ai_quant validate --data-dir data/frozen --output-dir .
```

验收程序只在所有门通过时返回 0；当前会按设计返回 1，并生成机器可读的 `acceptance_results.json` 和审阅版 `ACCEPTANCE_REPORT.md`。

## 项目结构

| 路径 | 责任 |
|---|---|
| `unified_ai_quant/engine.py` | 唯一生产决策内核和逐日 Account Replay |
| `unified_ai_quant/portfolio.py` | 唯一组合分配器、迟滞和生命周期 |
| `unified_ai_quant/risk.py` | 独立风险雷达、行业/相关性/回撤状态 |
| `unified_ai_quant/leader.py` | 固定 Reference、Mature/Emerging、置信度 |
| `unified_ai_quant/execution.py` | 次日开盘、T+1、涨跌停、容量、费用 |
| `unified_ai_quant/account.py` | 原子持久化与 fail-closed 校验 |
| `unified_ai_quant/validation/` | 963 场景压力、参数/成本/容量、Nested Walk Forward、PBO/DSR 和严格验收器 |
| `benchmarks/` | 三旧项目只读冻结指纹及 Phase 0 实跑基线 |
| `data/frozen/` | 统一冻结行情及哈希清单 |
| `docs/` | 原始实施规范、验收规范和阶段证据 |

## 可复现证据

- `benchmarks/BENCHMARK_LOCK.json`：三个旧仓库最新 `main` 的远端提交与冻结快照指纹；
- `benchmarks/legacy_common_adapter.json`：3 个旧系统 × 5 个固定池 × 9 个窗口，共 135 个 common-contract 实跑单元；
- `acceptance_results.json`：每项 `PASS/FAIL + actual + threshold + evidence`；
- `ACCEPTANCE_REPORT.md`：替代门、失败根因和实测结果；
- `stress_results.json`：963 个当前生产引擎场景（含 900 random、add/remove、边界和结构池）；
- `robustness_results.json`：180 个参数/成本/容量实验和 54 个嵌套走步单元；
- `benchmarks/promotion_holdback_result.json`：不可改写的单次 K2 失败证据；
- `benchmarks/PROMOTION_HOLDBACK_NEXT.json`：修复候选与新未来 12-session K2 的冻结预登记；
- `docs/PHASE_IMPLEMENTATION_STATUS.md`：Phase 0–9 的实现与验收状态。
- `docs/IMPLEMENTATION_AUDIT.md`：实施报告逐项映射、证据和未落实项。

## 风险声明

本仓库是量化研究和决策辅助软件，不构成投资建议。真实交易需要人工复核订单、数据完整性、公司行动、涨跌停、停牌和券商规则；历史回放不保证未来结果。
