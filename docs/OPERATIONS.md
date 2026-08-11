# uquant 运行手册

## 每日流程

推荐在收盘数据完整后按以下顺序运行：

1. 更新候选股、评审后的稳定生产参考篮子和两个指数的日线数据；离线研究扩展参考不是生产数据依赖。
2. 检查 CSV 最后日期、复权方式和异常值。
3. 从券商导出现金、持仓、可卖数量和当日成交。
4. 运行 `account-sync`，确认成交全部引用已知订单。
5. 运行 `daily` 生成目标与下一交易日意图。
6. 人工复核公司行动、停复牌、涨跌停和券商状态。
7. 下一交易日执行后，把真实成交写入新的券商快照。

## 数据文件

### 命名

- 上海：`sh` + 六位代码，例如 `sh688008.csv`；
- 深圳：`sz` + 六位代码，例如 `sz300308.csv`；
- 北京：`bj` + 六位代码；
- 宽基指数：`sh000300.csv`；
- 科技指数：`sh000682.csv`。

### 字段

```csv
date,open,high,low,close,volume,amount
2026-07-20,100.0,105.0,99.0,103.0,1234567,126000000
```

要求：

- `date` 可被 pandas 解析，严格递增且不重复；
- `open/high/low/close` 为正数，并满足合法 OHLC 关系；
- `volume` 非负；
- `amount` 可省略，省略时由收盘价和成交量估算；
- 股票使用前复权，指数使用原始点位；
- 同一次运行中的必需证券必须存在至少两个共同交易日。

### 追加与历史改写

账户保存的是上次成功日期以前的链式前缀哈希。可以在 CSV 尾部追加未来日期；不得修改、删除或重排已经进入账户决策的数据。若前缀变化，系统会拒绝运行，应先确认数据源、复权和公司行动，而不是直接覆盖账户哈希。

## 账户初始化

```bash
uquant account-init \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 \
  --date 2026-07-20 \
  --cash 2000000 \
  --output account_state.json
```

初始化时会自动加入 `STABLE_REFERENCE_UNIVERSE`、沪深 300 和科技指数。`EXPANDING_RESEARCH_REFERENCE`/`RESEARCH_REFERENCE_UNIVERSE` 不会被生产 CLI 自动加载，不能借研究实验改变账户数据指纹。输出文件包含现金、持仓、风险状态、生命周期、订单账本、数据前缀哈希和代码指纹。

账户文件带显式 `schema_version`，当前版本是 v3。v3 的 tranche 保存入场分数、置信度、市场状态、行业强度和 MFE/MAE，订单与成交保存减仓策略；账户还保存战略 epoch、上一 cohort、动态风险锚候选、连续风险信号、慢性退化、资本预算和 challenger scout。生产命令不会静默接受旧 schema，也不会在加载时偷偷补写新状态。

## 券商快照格式

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
      "final": true,
      "remaining_shares": 0
    }
  ]
}
```

字段规则：

- `as_of` 必须为 ISO 日期，且不能早于账户最后成功日期；
- `cash`、股数、成本和费用不能为负；
- `sellable_shares` 不能超过持仓股数；
- 每个 `fill_id` 在账户生命周期内唯一；
- `order_id` 必须已存在于账户订单账本；
- 成交证券与方向必须和原订单一致；
- 每笔成交必须显式提供布尔值 `final` 和非负整数 `remaining_shares`；完成成交要求 `final=true` 且剩余量为 0，部分成交要求 `final=false` 且剩余量大于 0；
- 同一成交日有多笔新成交时，每笔必须提供在该批新成交中唯一的正整数 `execution_sequence`。系统按日期和该序号处理，不使用 JSON 数组顺序；同一订单的同日增量快照还必须带回该订单当日已导入成交及其原序号；
- 重复同步同一 `fill_id` 不会重复入账。

持仓快照只对现金、总股数、平均成本和可卖股数具有权威性。`lifecycle`、`highest_close` 和经济 lot 由策略引擎及可追溯成交拥有；快照即使携带这些字段，也只能通过格式校验，不能覆盖已有策略状态。无法由已知订单/lot 解释的外部持仓会按 `CORE`、快照日入场、平均成本作为最高价的降级默认建 lot，并写入 `economic_lot_degraded` 对账事件，必须人工复核来源。

## 每日决策

```bash
uquant daily \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 \
  --date 2026-07-21 \
  --account account_state.json \
  --broker-snapshot broker_snapshot.json \
  --output daily_report.md
```

成功后，账户文件会原子更新，新的 `pending_orders` 会保存稳定订单号。日报只是同一决策对象的渲染，不会重新计算或修改权重。

## 回放

```bash
uquant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 \
  --start 2018-01-02 \
  --end 2026-07-20 \
  --output result.json
```

开始和结束日期必须落在共同数据范围内。回放会从初始现金创建空账户，逐日先执行已有次日订单，再按收盘数据生成新决策。末日尚未成交的意图会计入 `pending_orders`，不会伪装成成交。

## 历史补全工具

```bash
python scripts/backfill_tencent_history.py \
  --data-dir data/frozen \
  --workers 8
```

该工具分段下载早期腾讯行情，只向现有冻结行之前补数据，并在接缝处校验价格一致性。只有在稳定生产篮子中且在快照边界前已可见的参考证券，以及指数，承担缺少长历史即失败的义务；未评审的研究扩展证券不会扩大生产补全范围。运行前应备份 `data/frozen`；运行后核对 `DATA_MANIFEST.json` 和 `SHA256SUMS`。

正式回放前执行三方清单和逐文件 SHA-256 校验：

```bash
uv run python -m uquant.validation data-manifest --data-dir data/frozen
```

校验器要求目录中的 CSV、清单结果和校验和文件完全同集，并拒绝符号链接、不安全证券名、重复项和字节不一致。

## 发布验证

常规策略回归使用版本化 promotion baseline：

```bash
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --baseline benchmarks/promotion_baseline.json \
  --profile quick \
  --output promotion-report.json
```

订单门槛是 reference 订单的 `max(+1, +5%)`，2026-06-30～07-20 急跌收益不得低于 -3%。`full` 用于 main、定时和发布前验证。

泛化验证需要一份覆盖全部 universe 的 `symbol -> industry` JSON，并显式区分当前 universe 与仅用于移除诊断的历史先验证券。以下 Bash 示例直接读取版本化 Pool E 的 32 个真实冻结证券，满足默认 random-24 与 leave-top-5 的最小全集要求；reference 必须由同一 Pool E 场景完成真实回放和评审：

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
  --start 2018-01-02 --end 2026-07-20 \
  --baseline /path/to/reviewed-generalization.json \
  --output generalization-report.json
```

完整竞品 gate 使用 105 单元、带来源与执行契约的真实 reference：

```bash
uv run python -m uquant.validation competitor \
  --data-dir data/frozen \
  --reference /path/to/reviewed-competitor-matrix.json \
  --output competitor-report.json
```

泛化和竞品命令是只读验证器，不提供“接受当前结果”或写 baseline 的选项。reference 缺失、JSON 重复键、cell 不全、场景指纹/数据/commit/adapter/执行契约漂移时应停止；不要复制空模板、牛市局部 reference 或手工推测值来绕过失败。

## 升级生产代码

账户绑定 schema v3 与生产代码指纹，任何 Python 生产模块变化都会使旧指纹失效。升级时：

1. 备份账户文件和最近券商快照；
2. 完成质量门、连续回放和策略晋级矩阵；
3. 先输出到单独文件执行显式迁移：

```bash
uquant account-migrate \
  --account account_state.json \
  --output account_state.migrated.json \
  --acknowledge-code-change
```

4. 对比原文件与迁移文件中的现金、持仓、可卖数量、挂单、成交、数据前缀和策略状态；v3 还要核对 tranche 入场证据、订单/成交减仓策略、战略 epoch/历史成员/逐票 restore 权重、风险锚/候选 streak、连续风险信号、慢性级别、资本预算和 scout 状态；
5. 对迁移文件运行 `account-sync`，立即同步完整券商快照；
6. 核对迁移审计中的新旧 schema 与代码指纹；
7. 仅在人工确认后，用迁移文件替换日常账户文件。

确认状态完全兼容时也可省略 `--output` 做原子原地迁移，但仍应先备份。迁移不会重新解释历史成交或创建订单；缺少 v3 tranche 证据时只会按迁移代码中定义的保守默认推导，不会杜撰历史评分。它只在明确确认后补齐新状态、绑定当前代码并留下 UTC 审计记录。不要手工只改 `schema_version` 或 `code_hash`，这样会绕过状态兼容性检查。

## 常见失败

| 错误 | 含义 | 处理 |
|---|---|---|
| `historical data prefix differs` | 已使用历史数据被改写 | 核对数据源、复权、日期和文件内容 |
| `production code hash differs` | 账户来自不同代码状态 | 按升级流程显式迁移并核对账户 |
| `requires explicit migration` | 账户 schema 早于当前生产版本 | 备份后运行 `account-migrate` 并核对审计记录 |
| `reference coverage is insufficient` | 稳定生产参考篮子覆盖不足 | 补齐已上市稳定参考证券的当日数据；不要用研究扩展 tuple 改写生产篮子来绕过 |
| `decision date is not a common index session` | 两个指数日期不一致 | 修正指数数据或决策日期 |
| `unknown account order` | 券商成交无法追溯到系统订单 | 核对 `order_id`，不要伪造映射 |
| `requires explicit ...` / `execution_sequence` | 成交完成状态不完整，或同日多成交缺少确定顺序 | 补齐 `final`、`remaining_shares`；同日多成交按券商执行顺序提供唯一序号 |
| `negative cash` | 快照或执行违反现金约束 | 停止运行并核对现金、费用和成交 |
| `generalization gate is fail-closed` | 评审后的泛化 reference 不存在 | 完成真实全场景重放和评审后再显式加入 reference；不要自动生成 |
| `competitor gate is fail-closed` | 完整 105 单元竞品 reference 不存在 | 获取三项目真实 adapter 输出并评审来源；牛市 15 单元不能替代 |
| `provenance mismatch` / `execution-contract mismatch` | reference 与本地数据、adapter 或执行口径不同 | 停止比较并重建同口径证据，不要修改 hash 让它通过 |

## 备份建议

每次成功运行后保留：

- `account_state.json`；
- 当日券商快照；
- 当日日报；
- 数据清单与校验和；
- 对应 Git 提交号。

账户文件包含完整策略状态，不应在多个副本上并行产生决策后再合并。
