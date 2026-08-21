# 运行手册

## 每日运行原则

uquant 只用于 2023 年以来 A 股 AI 产业链的现金多头日频决策，适合盘后人工触发。建议固定在数据更新完成、券商成交确认后执行。`daily` 生成下一交易日订单意图并保存账户，不会向券商发送订单；使用者必须人工核对并决定是否下单。

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
| `orders` | 可选；券商确认已取消订单（`order_id`、`status=CANCELLED`、`remaining_shares=0`） |

每笔成交必须包含系统生成的 `order_id`、稳定且唯一的 `fill_id`、证券、方向、股数、价格、费用、成交日期、`remaining_shares` 和 `final`。同日多笔新增成交必须有唯一 `execution_sequence`。

对账规则：

- 重复 `fill_id` 只有经济字段完全相同才视为幂等；
- 未知订单、证券或方向不一致会拒绝整份快照；
- 终态订单不能继续新增成交；
- 跨日成交按日期排序，同日成交按序号排序；
- 券商现金、股数、成本和可卖数量覆盖账户现实字段；
- 机会、风险和持仓生命周期仍由系统状态保存。
- Sentinel Freeze 对已进入券商的 BUY 只在 Order Ledger 记录 `CANCEL_REQUESTED`；该外部
  订单不再进入本地可执行 pending，但其非终态 ledger 继续接受券商迟到成交。只有 `orders`
  中的券商取消确认或最终成交才能结束该订单。不得在本地伪造 `CANCELLED`，取消待确认期间
  不得创建同证券替代 BUY；同证券独立 SELL 仍可执行。

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

### 外部执行 journal

人工闭环可另写 observational、append-only、broker-independent journal，逐笔记录
计划价格、下一交易日开盘、实际成交时间/价格/股数、人工跳过和由此计算的滑点。
该文件不连接券商、不回写 `account_state.json`，也不作为候选排序、风险状态或参数
输入；系统账户和完整券商快照仍是生产状态的权威来源。

## 历史回放

```bash
uv run uquant backtest \
  --data-dir data/frozen \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --start 2023-01-03 \
  --end 2026-07-20 \
  --output backtest_result.json
```

数据目录可以保留 2023 年以前的行情，但这些行只用于形成指标 warm-up；上市前证券不可见。初始权益、订单、成交、换手和绩效统计都从 2023+ 回放起点开始。回放执行模型和每日决策共用同一引擎。

## Future holdout

历史研究和冻结 benchmark 最后一天是 `2026-08-05`，future holdout 的第一天是
`2026-08-06`，存放在与 `data/frozen` 隔离的目录。`2026-08-05` 收盘决策若在次日
执行，整个成交归入 holdout。未导入未来 session 时，观察和指标必须保持 null；
不能创建占位收益、沿用历史指标或据观察期表现修改参数。固定里程碑是
`20 / 40 / 60` 个交易日，首次正式评审必须累计 `40--60` 个交易日，并继续保留人工运行、
完整券商对账与外部 journal。起始账户
必须逐字节匹配已审阅的 `continuous_ai_era/full` 回放摘要；未来评分只接受可重放、
可重算的执行证据，独立填写或重新封签的指标 JSON 会被拒绝。

每日先以独立目录导入恰好一个完整市场快照，再生成确定性回放；历史冻结数据、Future
Holdout 数据和人工 Journal 不得共用目录。远程 CI 只验证仓库跟踪的零观察基线，不读取
操作者本地数据：

```bash
uv run python scripts/future_holdout.py validate-static-lanes
```

本地真实观察使用独立、Git 忽略的报告。它按当前 holdout 数据重算观察日、下一里程碑和
Lane 身份；即使已有非零观察，也不会要求修改仓库内的零观察基线：

```bash
uv run python scripts/future_holdout.py report-lanes
```

兼容命令 `validate-lanes` 等同于 `validate-static-lanes`。不要把本地
`future_holdout_lane_report.json` 提交或复制到跟踪的
`artifacts/holdout/lane_validation.json`。

### 唯一生产观察命令

准备好恰好一个交易日、且文件清单与 `data/frozen` 完全一致的市场快照后，日常使用：

```bash
uv run python scripts/production_observation.py run \
  --run-id 2026-08-06 \
  --date 2026-08-06 \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --account account_state.json \
  --data-dir data/live \
  --broker-snapshot broker_snapshot.json \
  --holdout-snapshot-dir incoming/2026-08-06 \
  --holdout-account holdout_prior_close_account.json
```

`holdout_prior_close_account.json` 是合约审阅的 `2026-08-05` 收盘账户，只供确定性 Future
Holdout 重放；日常 `account_state.json` 仍是唯一生产账户。命令严格依次执行：

1. 获取同一仓库/账户的全事务互斥锁，以现有外部 checkpoint 验证 v2 Journal；非空 Journal
   缺少 checkpoint 时拒绝运行；
2. 在 `production_observation_backups/<run-id>/` 保存运行前账户、券商快照、holdout 起始账户、
   Journal 和 checkpoint，并在任何快照追加或账户写入前立即读回验证；
3. 不可变追加一个 holdout 市场 session，并生成确定性 replay/decision；
4. 调用原有 `uquant daily --broker-snapshot`，在同一路径完成账户对账和唯一 Daily Report；
5. 生成本地非零 Lane 报告，原子更新 Journal checkpoint；
6. 归档运行后账户、日报、Journal、replay、decision、Lane 报告和 checkpoint，以 SHA-256
   manifest 绑定最终 `COMPLETED` 或 `FAILED` receipt 后释放互斥锁。

默认日报是 `daily_report_<date>.md`，Lane 报告是
`future_holdout_lane_report.json`。重复的 `run-id` 会被明确拒绝，避免覆盖原始证据；修复输入后
重跑应使用例如 `2026-08-06-retry1`。如果在 holdout 追加后失败，已追加 session 保持不可变；
同一快照可幂等重试，但进入下一 session 前必须先成功生成当前 replay checkpoint。
所有可写输出必须彼此独立，也不能位于备份树或与其硬链接；任何别名都在创建备份前拒绝。

人工 Journal 使用 v2 hash chain，每行包含决策日、计划证券/方向/权重/参考价、次日
开盘、实际成交、人工跳过原因、实现滑点、券商订单 ID、记录时间和前后哈希。它只写
独立 JSONL，不连接券商、不调用 `ProductionEngine`，也不写模型账户：

```bash
uv run python scripts/future_holdout.py journal planned \
  --plan-id 20260805-sz300308-buy \
  --decision-date 2026-08-05 \
  --recorded-at 2026-08-05T15:01:00+08:00 \
  --symbol sz300308 --side BUY --planned-weight 0.08 \
  --planned-price 947.74 --planned-shares 100

# 首条 planned 是唯一显式 bootstrap；追加后立即建立并外存信任锚
uv run python scripts/future_holdout.py journal checkpoint
uv run python scripts/future_holdout.py journal verify

uv run python scripts/future_holdout.py journal filled \
  --plan-id 20260805-sz300308-buy \
  --recorded-at 2026-08-06T09:32:00+08:00 \
  --next-open 950.00 --actual-time 2026-08-06T09:31:05+08:00 \
  --actual-price 951.00 --actual-shares 100 \
  --broker-order-id manual-broker-001

uv run python scripts/future_holdout.py journal report

# 每次追加后更新、外存并验证 checkpoint
uv run python scripts/future_holdout.py journal checkpoint

uv run python scripts/future_holdout.py journal verify
```

跳过时使用 `future_holdout.py journal skipped --manual-skip "原因"`。只能追加事件；不得编辑、
截断或重封既有行。部分成交可追加多条 `filled`，剩余部分最终用 `skipped` 显式收口。
`report` 汇总完整、部分、未成交和跳过计划，以及成交率、实现滑点和按次日开盘名义金额
加权的滑点bps。外部checkpoint应复制到独立存储；`verify` 会检测截断或已保留前缀被重封。
只有空 Journal 可以没有 checkpoint；非空 Journal 缺少 checkpoint 会失败，不能从当前尾部静默
重封。首条 `planned` 后运行 `checkpoint` 是单独可审计的 bootstrap，之后每次追加都应立即更新
并外存信任锚。
`scripts/future_holdout.py journal` 是唯一生产 v2 Journal 入口；`uquant execution-journal`
只保留为旧 v1 数据兼容接口，不用于新增真实证据。默认 Journal 及 checkpoint 已被 Git 忽略，
自定义路径同样不得提交。人工成交仅改变 Journal checkpoint，不能改变模型 Decision Digest、
回放分数或候选晋级。

## Risk Sentinel 日报融合

生产默认是 `FREEZE_ONLY`。`uquant daily` 已在唯一日报中显示 Mode、Level、Coverage、
Confidence、Owner、Risk Families、AI Industry Risk 和受限结论；日常不再要求额外运行
Sentinel CLI。Sentinel 不能直接 SELL、降低 `target_gross_cap`、创建第二账户或增加账户字段。

下列命令只用于工程合同验证或离线故障诊断：

```bash
uv run python -m uquant.risk_sentinel --validate-contracts
uv run uquant-sentinel \
  --data-dir data/frozen \
  --date 2026-08-05 \
  --account account_state.json \
  --output artifacts/sentinel/2026-08-05.json
```

输出不能位于数据目录，也不能覆盖账户；失败运行保留现有最新成功指针。生产映射最多设置
现有 `RiskAssessment.freeze_new_risk`。Phase 6 可信历史的生产权限开关保持 `false`；
Phase 7 独立确认候选已经 REJECT。不要把 Sentinel 观察手工转换为卖单、风险动作或总仓
限制。完整合同见 [RISK_SENTINEL.md](RISK_SENTINEL.md)。

生产源码升级后，先备份账户，再使用显式代码身份迁移：

```bash
uv run uquant account-code-migrate \
  --account account_state.json \
  --acknowledge-code-change
```

命令只更新 `code_hash` 并追加 `code_identity_only` 审计事件；输出
`economic_state_sha256`。迁移前、落盘后和严格重载后的该摘要必须一致。若摘要变化或未显式
确认，迁移失败关闭。通用 schema 转换仍使用单独的 `account-migrate`，不能把两种操作混用。

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

单命令生成的 checkpoint 可随时做只读校验：

```bash
uv run python scripts/production_observation.py verify-backup \
  --checkpoint production_observation_backups/2026-08-06
```

校验会重算 manifest 与每个载体（包括最终 `receipt.json`）的 SHA-256/大小，拒绝篡改、缺失和
未登记文件。`PREPARED` 表示只有已读回验证的运行前备份；`COMPLETED` 和 `FAILED` 都必须具备
与 manifest 状态一致且被哈希绑定的 receipt。失败恢复不提供
自动覆盖：先复制 `account.before.json` 到独立恢复目录，在副本上执行 `account-sync` 和
`daily`，核对 Decision Digest、Target、Orders、Fills、Account 与原日报后，再人工决定是否
替换生产账户。`receipt.json` 为 `FAILED` 时按最后成功 step 定位边界，不要删除已经不可变追加的
holdout session。

## 发布前检查

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run python -m uquant.risk_sentinel --validate-contracts
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts research tests
uv run python -m build
uv run bandit -q -r uquant research scripts
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --profile full \
  --output benchmarks/ai_era_performance.json
```

生成的 `benchmarks/ai_era_performance.json` 是 Git 忽略的 post-checkout 证据；
确认其 provenance 中 `production_commit` 与待发布 HEAD 完全一致，并保留 CI
上传件。不要把旧 artifact 提交进仓库后当作当前版本证明。

发布分支还必须在 GitHub 得到独立的 `Engineering`、`Phase 1 Performance` 与
`Phase 2 Generalization` 成功结论。Phase 1 保持上述 full profile；Phase 2 对六个
固定官方窗口聚合全部 shard，并检查精确 HEAD、完整 provenance、234 条记录、冻结
policy 与所有失败状态。诊断 artifact 即使失败也要保留，任何一个门都不能由另一个
成功抵消。注释和文档工作仍要证明可执行 AST 与 AI-era 指标保持不变。
