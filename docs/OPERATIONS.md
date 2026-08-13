# 运行手册

## 每日运行原则

uquant 适合盘后人工触发。建议固定在数据更新完成、券商成交确认后执行。`daily` 生成订单意图并保存账户，不会向券商发送订单。

每次运行前确认：

- 系统日期、时区和目标交易日正确；
- 股票为前复权行情，指数为不复权行情；
- CSV 已追加完成且没有回写已使用历史；
- 券商现金、持仓、当日可卖数量和成交完整；
- 没有未人工确认的公司行动、停复牌或证券代码变化。

## 数据目录

文件名使用规范代码，例如 `sz300308.csv`。必需列为：

```text
date,open,high,low,close,volume
```

可选列 `amount`。先验证冻结数据：

```bash
uv run python -m uquant.validation data-manifest --data-dir data/frozen
```

刷新股票行情需要 `data` 依赖。刷新只能追加，若数据源改变历史前缀，系统会停止并要求人工核查。

## 账户初始化

```bash
uv run uquant account-init \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-20 \
  --cash 2000000 \
  --output account_state.json
```

未提供 `--date` 时使用所有必需证券的最新共同日期。账户同时记录数据摘要和当前生产代码指纹。

账户文件必须符合 `ACCOUNT_SCHEMA_VERSION` 定义的当前数据契约。需要规范化不符合当前字段约束的账户时，先备份，然后显式确认代码指纹：

```bash
cp account_state.json account_state.before-conversion.json
uv run uquant account-migrate \
  --account account_state.json \
  --output account_state.normalized.json \
  --acknowledge-code-change
```

转换后必须核对现金、股数、成本、可卖数量、挂单、成交、数据日期和策略状态，再使用新文件。不要手工只改 `schema_version` 或 `code_hash`。

## 券商快照

每日决策前推荐先执行：

```bash
uv run uquant account-sync \
  --account account_state.json \
  --snapshot broker_snapshot.json
```

快照顶层至少包含：

| 字段 | 含义 |
|---|---|
| `as_of` | 快照日期 |
| `cash` | 可用现金 |
| `positions` | 持仓、成本和当日可卖数量 |
| `fills` | 已发生的真实成交 |

每笔成交必须包含系统生成的 `order_id`、稳定且唯一的 `fill_id`、证券、方向、股数、价格、费用、成交日期、`remaining_shares` 和 `final`。同日多笔新增成交必须有唯一 `execution_sequence`。

对账规则：

- 重复 `fill_id` 只有经济字段完全相同才视为幂等；
- 未知订单、证券或方向不一致会拒绝整份快照；
- 终态订单不能继续新增成交；
- 跨日成交按日期排序，同日成交按序号排序；
- 券商现金、股数、成本和可卖数量覆盖账户现实字段；
- 机会、风险和持仓生命周期仍由系统状态保存。

## 生成日报

```bash
uv run uquant daily \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --date 2026-07-21 \
  --account account_state.json \
  --broker-snapshot broker_snapshot.json \
  --output daily_report_2026-07-21.md
```

建议按以下顺序阅读：

1. 数据日期与账户摘要；
2. 机会状态、风险状态和总仓上限；
3. 当前持仓与目标权重；
4. 买卖意图、原因码和风险优先级；
5. 被迟滞、T+1、停牌、涨跌停、容量或现金阻塞的意图；
6. 风险证据、恢复状态和决策警告。

## 人工执行闭环

1. 运行 `daily`，记录意图；
2. 在券商端人工检查价格、涨跌停和可卖数量；
3. 人工下单，不修改系统 `order_id` 对应关系；
4. 收盘后导出完整成交和持仓快照；
5. 使用 `account-sync` 对账；
6. 再运行日报前确认没有未解释差异。

若人工决定不执行某个意图，应保留真实账户状态，并按订单生命周期显式取消或替换；不要伪造成交来消除挂单。

## 历史回放

```bash
uv run uquant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2018-01-02 \
  --end 2026-07-20 \
  --output backtest_result.json
```

开始日期前的数据只用于形成指标；上市前证券不可见。回放执行模型和每日决策共用同一引擎。

## 常见故障

| 现象 | 含义 | 处理 |
|---|---|---|
| `data hash differs` | 已使用历史数据发生变化 | 恢复可信文件并重新验证 |
| `production code hash differs` | 账户绑定的代码与当前运行代码不同 | 备份账户，执行兼容转换并核对 |
| 账户 schema 不符合当前契约 | 账户结构或字段不完整 | 使用 `account-migrate` 明确转换 |
| `unknown order_id` | 券商成交无法对应系统订单 | 修正快照或人工调查 |
| `duplicate fill_id` | 重复成交经济字段不一致 | 修正导出来源，禁止覆盖 |
| `insufficient common history` | 必需证券共同历史不足 | 补齐数据或调整开始日期 |
| `reference coverage` | 参考篮子缺失或不可见 | 补齐参考数据，不使用小样本替代 |
| `limit blocked` | 一字涨跌停阻塞 | 保留意图，等待可交易日 |
| `T+1 blocked` | 当日买入不可卖 | 以券商可卖数量为准 |
| `capacity blocked` | 参与率或手数不足 | 降低计划规模或等待流动性 |

错误发生后先保留账户、快照、日报和日志副本，再调查根因。不要直接编辑持仓、订单或成交数组来绕过校验。

## 备份与恢复

建议至少保留：

- 每日运行前后的账户文件；
- 对应券商快照；
- 当日日报；
- 使用的数据清单与摘要；
- 当前 Git 提交和生产源码摘要。

账户保存使用原子替换，但仍应把备份写到独立目录。恢复时先在副本上运行 `account-sync` 和 `daily`，确认输出一致后再切换。

## 发布前检查

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts research tests
uv run python -m build
uv run bandit -q -r uquant
```

策略或参数发生变化时还必须运行冻结绩效门。注释和文档工作也要证明可执行 AST 与回测指标保持不变。
