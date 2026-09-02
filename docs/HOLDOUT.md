# Future Holdout 与人工执行证据

Future Holdout 用真实、未参与选择的新交易日验证候选是否继续有效。它是观察与评分平面，
不拥有生产参数、账户、订单或成交权限。

## 不可变边界

- 历史研究与冻结 benchmark 截止 `2026-08-05`；holdout 从 `2026-08-06` 开始。
- 最后一个样本内收盘决策若在次日成交，整个成交归入 holdout。
- 新 session 只能按日期顺序追加，不能回填、覆盖、重封或删除已观察前缀。
- 未导入真实 session 时，观察数为零，正式评分必须为 `null`。
- 固定观察里程碑是 `20 / 40 / 60` 个交易日；首次正式评审需要累计 `40--60` 日。
- 观察结果不得反向修改参数、阈值、证券池、窗口或生产权限。

历史冻结数据、holdout 数据、生产账户、回放账户、人工 Journal 和备份目录必须彼此隔离。
起始 holdout 账户必须匹配已审阅的连续回放摘要；正式评分只能由确定性回放重算，调用方
提供的独立分数文件即使重新封签也不能进入验收。

## 2026-09-02 身份与观察边界

当前紧凑证据合同为
[`post_generalization_trust_closure_checkpoint_a.json`](../benchmarks/post_generalization_trust_closure_checkpoint_a.json)。
冻结候选 `c47367bba64c827fe18f788c9a3650e13ece306f` 的决策源码摘要为
`f9c78557e38342c5a994f19fde63352f635ac37c5d2d7a187ba410b98caa1aed`；
`42f6cbdfcf3c3e396200758f80485b49b9e245bf` 首次把它绑定到 holdout 合同，
`4be0ad2e8b2f44bad03042c05ddded0bc1c7a3aa` 是最新直接验证该绑定并重建精确期初账户的提交。

该冻结身份拥有 `2026-08-06..2026-09-01` 的 19 个完整 session。数据覆盖为
`19 x 36 = 684` 条，缺失和额外 session 均为零；holdout 数据摘要为
`b7abda36c77a90397c9947aaa8130c3bd7525ca574c193e0898ad6ab505b6d0b`，来源清单的
canonical 摘要为 `a60a6e2c7f17fa216d31105fe292a5a4b8534c0679a9b3795abec92d632d737c`。
采集时点、交易日历、覆盖、点时延续和冻结数据无污染均已核对。期初账户为 schema 5，
canonical 摘要 `251c90cef356821547c633c69595371aa857a704d8ea21e5119be16136ac0fc8`，
现金与权益均为 `49019323.60580173`，无持仓或挂单；其 12 个订单和 15 个 fill 是历史模型
账户记录，不是券商事实。

当前 `main` 身份 `886a72179a7bfdef9a3e3165548df59e9d92aa89` 的决策源码摘要为
`e0331925a7d199d60464c69080ade0abf831e106dcb1bef0afd6b74baaccc10f`，配置摘要为
`dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5`。它在
`2026-09-02` 当日收盘前发布，因此截至证据截点拥有 0 个完整 prospective session；对前述
19 日的运行只能标记为 `RETROSPECTIVE_BRIDGE_NOT_FUTURE_HOLDOUT`，不得作为当前身份的
Future Holdout。首个经济等价候选 session 是 `2026-09-02`，只有收盘完整后按序追加才算观察。

当前身份的三个 869-session bounded replay（three-core、remove-all-three、no-optical）仅是
`2023-01-03..2026-08-05` 的诊断证据，均非 authoritative acceptance，也不是 Future
Holdout。其原始 account codec 报错是因为当前 `FULL_COHORT` 语义允许非 owner cohort row
共用 epoch 且 grant 留空；既有 Absolute helper 只在证据 deep copy 上归一化，归一化副本为
`VALID`。后续若增加当前身份原生 codec 支持或移除该 shim，属于证据管线清理，不是策略变更。

冻结 19 日结果为全现金：期末权益不变、收益和回撤为零、无模型订单或 fill，且每天均为
日收益 `0`、现金率 `1`、gross exposure `0`、`WEAK / RISK_OFF`、target gross `0`，并冻结
新风险、启用 sector guard；这些是覆盖全部 19 个 session 的常量时间线，不保存逐日原始行。
没有实际券商执行 Journal，
因此不能填造券商 fill 数；里程碑状态严格为 `INTERIM — MILESTONE NOT YET REACHED`。
年化换手、挂单、执行阻断、owner switch 和新 grant/epoch 均为零，行业暴露为空；期初与
期末 owner 均为 `sz300308`。冻结 schema 不含正式 grant/epoch 语义，观察期也没有 failed
grant retry、recovery 或 rearm 事件，不能从新版字段反向补造。
这份聚合证据不保存逐日原始数据，不允许 backfill、反向调参或取得生产权限。

## 仓库基线与本地观察

CI 只验证仓库中跟踪的零观察合同和 Lane 注册表：

```bash
uv run python -m scripts.future_holdout validate-static-lanes
```

静态 Lane 校验命令是 `validate-static-lanes`。脚本必须通过 `python -m` 执行，避免文件路径
入口与包导入行为漂移。

真实观察数据和本地 Lane 报告均被 Git 忽略。查看当前观察数、下一里程碑和 Lane 身份：

```bash
uv run python -m scripts.future_holdout report-lanes
```

不要把本地 `future_holdout_lane_report.json` 写回
`artifacts/holdout/lane_validation.json`。后者只证明仓库基线在没有未来数据时诚实地保持
零观察和 null 分数。

## 唯一生产观察入口

准备恰好一个交易日、且文件清单与冻结数据一致的完整市场快照后运行：

```bash
uv run python -m scripts.production_observation run \
  --run-id 2026-08-06 \
  --date 2026-08-06 \
  --symbols sz300308 sz300502 sz300394 sh688008 sh603986 \
  --account account_state.json \
  --data-dir data/live \
  --broker-snapshot broker_snapshot.json \
  --holdout-snapshot-dir incoming/2026-08-06 \
  --holdout-account holdout_prior_close_account.json
```

命令在同一事务锁内完成以下步骤：

1. 验证 Journal、外部 checkpoint、输入路径与账户身份；
2. 在 `production_observation_backups/<run-id>/` 保存并读回运行前证据；
3. 不可变追加一个市场 session，生成确定性 replay 与 decision；
4. 调用同一 `uquant daily` 路径完成账户对账和日报；
5. 更新本地 Lane 报告和 Journal checkpoint；
6. 用 SHA-256 manifest 封存 `COMPLETED` 或 `FAILED` receipt。

重复 `run-id` 会被拒绝。修复输入后使用新的重试标识；若 session 已成功追加，重试只能
幂等复用该 session，不能覆盖它。

## 人工执行 Journal

Journal 是 observational、append-only、broker-independent 的真实执行记录。它保存计划、
次日开盘、实际成交、人工跳过、实现滑点和券商订单 ID，但不调用决策引擎，也不写账户。
`decision-date` 是交易所 session；`recorded-at`、`actual-time` 必须是带 UTC offset 的
ISO-8601 时间，A 股人工执行通常使用 `+08:00`，不得用无时区本地时间补录。

```bash
uv run python -m scripts.future_holdout journal planned \
  --plan-id 20260805-sz300308-buy \
  --decision-date 2026-08-05 \
  --recorded-at 2026-08-05T15:01:00+08:00 \
  --symbol sz300308 --side BUY --planned-weight 0.08 \
  --planned-price 947.74 --planned-shares 100

uv run python -m scripts.future_holdout journal checkpoint
uv run python -m scripts.future_holdout journal verify

uv run python -m scripts.future_holdout journal filled \
  --plan-id 20260805-sz300308-buy \
  --recorded-at 2026-08-06T09:32:00+08:00 \
  --next-open 950.00 --actual-time 2026-08-06T09:31:05+08:00 \
  --actual-price 951.00 --actual-shares 100 \
  --broker-order-id manual-broker-001

uv run python -m scripts.future_holdout journal checkpoint
uv run python -m scripts.future_holdout journal verify
uv run python -m scripts.future_holdout journal report
```

首条 `planned` 后立即建立并外存 checkpoint；以后每次追加都更新并外存。部分成交可以有
多条 `filled`，剩余部分必须用 `skipped --manual-skip "原因"` 收口。非空 Journal 缺少
checkpoint 时失败关闭，不能从当前尾部静默重建信任锚。

## Risk Differential 观察

Risk Differential 是研究观察，不是生产指令。只有冻结日历中且 holdout 数据确实存在的
session 才能追加；调用方不能提供自填风险事实：

```bash
uv run python -m scripts.future_holdout append-risk-differential \
  --trade-root /path/to/pinned/trade-checkout \
  --date 2026-08-24
```

`trade` checkout 必须匹配注册的 Git、源码和 lock 身份。前 19 个真实 session 的正式分数
保持 `null`；达到里程碑后的汇总仍不能改变生产参数或权限。

## 失败恢复

验证备份 checkpoint：

```bash
uv run python -m scripts.production_observation verify-backup \
  --checkpoint production_observation_backups/2026-08-06
```

失败后不要直接覆盖生产账户或删除已追加 session。先复制 `account.before.json` 到独立恢复
目录，在副本上执行 `account-sync` 和 `daily`，核对 Decision Digest、Targets、Orders、
Fills 与 Account，再人工决定是否替换生产账户。`FAILED` receipt 按最后成功步骤定位边界。

日常账户与日报操作见[运行手册](OPERATIONS.md)，评分和证据口径见[性能与证据](PERFORMANCE.md)。
