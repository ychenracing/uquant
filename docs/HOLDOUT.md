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

## 仓库基线与本地观察

CI 只验证仓库中跟踪的零观察合同和 Lane 注册表：

```bash
uv run python -m scripts.future_holdout validate-static-lanes
```

`validate-lanes` 只保留为旧自动化的兼容别名；新文档、CI 和 operator 命令统一使用
`validate-static-lanes`。脚本必须通过 `python -m` 执行，避免文件路径入口与包导入行为漂移。

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
