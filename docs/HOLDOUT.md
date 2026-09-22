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

## 身份与观察边界

观察必须绑定已审阅的源码、配置、数据及期初账户。当前源码不继承其他生产者的已观察
交易日、收益或通过结论。源码切换前缺失的连续账户路径需要明确的回放证据，不能假设
全现金账户，也不能把回顾性桥接当作未来观察。没有实际券商 Journal 时，不填造券商成交。

## 仓库基线与本地观察

CI 验证当前零观察合同、Lane 注册表及不可变来源中的原始参考结果：

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
Lane 校验输出。后者只证明仓库基线在没有未来数据时诚实地保持
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

## 当前版本的实际启用条件

`report-lanes` 中的 `OBSERVING` 是登记状态，不等于已经取得未来样本。当前仓库登记的
三个 Lane 仍是原始源码身份，静态报告为零观察、正式分数全部 `null`。它们不是当前 main
的自动授权。正式观察前须核对已登记源码、配置、运行时、连续期初账户和真实启用日；
当前账户不能直接替代旧 Lane 的冻结期初账户，也不能仅改摘要使其通过。
缺少这些材料时，继续用 `daily` 和下述人工 Journal 保存真实建议与执行事实；
这些记录不自动算作已通过身份核验的 Future Holdout 分数。

本轮不回填 2026-08-06 以来的缺失记录，不设立新策略候选、不修改旧 Lane、冻结日历或
20/40/60 门槛。尚未保存的历史建议不能根据后来行情补造。未来正式启用仍需要匹配当前
版本的连续账户与前瞻登记；真实数据、账户和券商执行记录未提供时，不能宣布端到端实盘观察验收。

## 建议、执行偏差与后续结果的对应

每个决策日保留原始日报（包括无订单日）、运行前后账户、券商快照和事务 receipt。
日报详细依据中的 Decision Digest、原 `signal_date` 和系统 `order_id` 是核对锚点；
`holdout_decision.json` 属于确定性回放，不应冒充真实账户的建议或券商成交。

人工 Journal 沿用现有格式，无需另建台账服务：

- `plan-id` 使用稳定的“账户代号:系统 order_id”，例如 `cash01:O000000001`；代号不含券商账号。
  将该 ID 与当日日报一起保存。原物理订单延续不重复创建计划；真实替换单使用新的 order_id。
- `decision-date` 取原信号日期，`recorded-at` 取实际记录时间。计划股数取当时人工核对的
  真实计划，价格为当时已知参考价，目标权重不是成交。没有 BUY/SELL 意图时不编造 planned。
- 多次部分成交分别追加 `filled`，填写真实时间、价格、数量和券商订单号；同一计划的
  `next-open` 始终是原计划次日的实际开盘，不随部分成交日期改写。缺失开盘事实时先保存
  券商原件，不能填估计值来满足字段。
- 确认放弃余量后用 `skipped --manual-skip` 记录真实原因，例如手动跳过、停牌、限价未成交、
  资金不足或取消。Journal 的 skipped 不会取消券商订单；账户仍需真实成交/取消确认。
- 每次追加后执行 `journal checkpoint`、`journal verify` 并备份 checkpoint；用
  `journal report` 查看计划、部分成交、人工跳过和已实现滑点。账户后续权益来自真实对账，
  Holdout 收益来自绑定身份的确定性回放，二者分别核对，不将人工执行偏差写回策略参数。

生产观察现在在追加前校验快照日期；日期不符不会写入不可变市场前缀。账户、券商输入、
回放期初账户、Journal、checkpoint、行情、输出和备份必须隔离，报告也不能覆盖冻结数据、
已追加 Holdout 或其 replay checkpoint。同仓库的完整观察事务串行执行，即使账户不同。
该锁不接管直接 `daily`、单步 holdout 命令或外部文件编辑；操作期间保持单一写入者，
不要从多个 checkout 共同写同一生产账户。
