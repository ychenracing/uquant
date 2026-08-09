# uquant 运行手册

## 每日流程

推荐在收盘数据完整后按以下顺序运行：

1. 更新候选股、固定参考篮子和两个指数的日线数据。
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

初始化时会自动加入固定参考篮子、沪深 300 和科技指数。输出文件包含现金、持仓、风险状态、生命周期、订单账本、数据前缀哈希和代码指纹。

账户文件带显式 `schema_version`。生产命令不会静默接受旧 schema，也不会在加载时偷偷补写新状态。

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
      "final": true
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
- 重复同步同一 `fill_id` 不会重复入账。

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

该工具分段下载早期腾讯行情，只向现有冻结行之前补数据，并在接缝处校验价格一致性。运行前应备份 `data/frozen`；运行后核对 `DATA_MANIFEST.json` 和 `SHA256SUMS`。

正式回放前执行三方清单和逐文件 SHA-256 校验：

```bash
uv run python -m uquant.validation data-manifest --data-dir data/frozen
```

校验器要求目录中的 CSV、清单结果和校验和文件完全同集，并拒绝符号链接、不安全证券名、重复项和字节不一致。

## 升级生产代码

账户绑定 schema 与生产代码指纹，任何 Python 生产模块变化都会使旧指纹失效。升级时：

1. 备份账户文件和最近券商快照；
2. 完成质量门、连续回放和策略晋级矩阵；
3. 先输出到单独文件执行显式迁移：

```bash
uquant account-migrate \
  --account account_state.json \
  --output account_state.migrated.json \
  --acknowledge-code-change
```

4. 对比原文件与迁移文件中的现金、持仓、可卖数量、挂单、成交、数据前缀和策略状态；
5. 对迁移文件运行 `account-sync`，立即同步完整券商快照；
6. 核对迁移审计中的新旧 schema 与代码指纹；
7. 仅在人工确认后，用迁移文件替换日常账户文件。

确认状态完全兼容时也可省略 `--output` 做原子原地迁移，但仍应先备份。迁移不会重新解释历史成交或创建订单；它只在明确确认后补齐新状态、绑定当前代码并留下 UTC 审计记录。不要手工只改 `schema_version` 或 `code_hash`，这样会绕过状态兼容性检查。

## 常见失败

| 错误 | 含义 | 处理 |
|---|---|---|
| `historical data prefix differs` | 已使用历史数据被改写 | 核对数据源、复权、日期和文件内容 |
| `production code hash differs` | 账户来自不同代码状态 | 按升级流程显式迁移并核对账户 |
| `requires explicit migration` | 账户 schema 早于当前生产版本 | 备份后运行 `account-migrate` 并核对审计记录 |
| `reference coverage is insufficient` | 固定参考篮子覆盖不足 | 补齐已上市证券的当日数据 |
| `decision date is not a common index session` | 两个指数日期不一致 | 修正指数数据或决策日期 |
| `unknown account order` | 券商成交无法追溯到系统订单 | 核对 `order_id`，不要伪造映射 |
| `negative cash` | 快照或执行违反现金约束 | 停止运行并核对现金、费用和成交 |

## 备份建议

每次成功运行后保留：

- `account_state.json`；
- 当日券商快照；
- 当日日报；
- 数据清单与校验和；
- 对应 Git 提交号。

账户文件包含完整策略状态，不应在多个副本上并行产生决策后再合并。
