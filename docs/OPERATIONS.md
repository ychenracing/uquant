# 运行手册

## 每日运行原则

uquant 只用于 2023 年以来 A 股 AI 产业链的现金多头日频决策，适合盘后人工触发。建议固定在数据更新完成、券商成交确认后执行。`daily` 生成下一交易日订单意图并保存账户，不会向券商发送订单；使用者必须人工核对并决定是否下单。

首次部署先完成四项检查：使用 Python 3.12 和 `uv sync --frozen` 建立锁定环境；验证
`data/frozen` 清单；初始化 schema 8 账户并保存独立备份；确认券商快照能完整提供现金、持仓、
可卖数量和成交。Future Holdout 还必须单独准备数据目录、回放账户、Journal checkpoint
和备份目录，见[Future Holdout](HOLDOUT.md)。

每次运行前确认：

- 系统日期、时区和目标交易日正确；
- 股票为前复权行情，指数为不复权行情；
- CSV 已追加完成且没有回写已使用历史；
- 券商现金、持仓、当日可卖数量和成交完整；
- 没有未人工确认的公司行动、停复牌或证券代码变化。

策略中的 `date`/`as_of` 表示交易所 session，不携带时区；人工 Journal 的
`recorded-at` 与 `actual-time` 必须使用带 offset 的 ISO-8601 时间，A 股操作通常为
`+08:00`。股票前复权行情不会自动生成分红、送转、配股或代码变更对应的真实现金与股数，
发生公司行动时必须先和券商事实对账，不能直接沿用旧账户和旧复权前缀。

## 操作入口与权限

| 场景 | 使用入口 | 不应使用 |
|---|---|---|
| 日常账户和日报 | `uquant account-*`、`uquant daily` | 研究脚本或独立 Sentinel 代替日报 |
| 历史回放 | `uquant backtest` | 把回放成交写入真实账户 |
| 生产观察事务 | `python -m scripts.production_observation` | 分步脚本绕过备份和 receipt |
| Holdout/Lane/Journal | `python -m scripts.future_holdout` | 绕过事务入口拼接日常自动化 |
| Sentinel 故障诊断 | `uquant-sentinel` | 把 Shadow 意见转成卖单或 cap |

`uquant holdout-*` 与 `execution-journal` 提供单步操作；operator 日常流程使用上表中的事务入口。

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

账户文件必须是 schema 8，并包含当前 `AccountState` 的必需字段。整数
`schema_version` 不是 8 时，读取和保存都会抛出 `UnsupportedAccountSchemaError`，错误消息给出
收到的版本和期望版本。应恢复已核验的 schema 8 备份或用 `account-init` 新建账户；不要手工
修改 `schema_version` 或 `code_hash`。

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

最小完整示例：

```json
{
  "as_of": "2026-08-06",
  "cash": 1049000.0,
  "positions": [
    {"symbol": "sz300308", "shares": 100, "sellable_shares": 0, "avg_cost": 951.0}
  ],
  "fills": [
    {
      "fill_id": "broker-fill-20260806-001",
      "order_id": "O000000001",
      "fill_date": "2026-08-06",
      "execution_sequence": 1,
      "symbol": "sz300308",
      "side": "BUY",
      "shares": 100,
      "price": 951.0,
      "commission": 5.0,
      "stamp_duty": 0.0,
      "transfer_fee": 0.951,
      "slippage_cost": 0.0,
      "remaining_shares": 0,
      "final": true
    }
  ],
  "orders": []
}
```

费用字段未提供时按 `0` 读取；若券商能提供，应写入真实值。`gross_value` 可省略并按
`shares × price` 推导，提供时必须与该乘积在容差内一致。

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
- 战略订单和成交必须与账户订单保持相同的 `symbol`、`event_id` 和 `grant_id`；不一致时整次
  对账失败关闭，不得忽略或猜测。
- 部分成交保留原物理订单、事件标识和已登记数量，继续执行实际剩余股数。只有真实的终态
  或目标约束变化才进入取消、替换流程；迟到成交仍归属原订单，不能伪造取消确认释放资金。

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

1. 决策日期、`Holdings and capital` 中的实有股数、成本、现金和券商快照日期；
2. Risk 总仓上限、系统总仓上限与 `Target Gross`；目标仓位不是实际持仓；
3. `Targets` 的既有原因，以及 `Tomorrow` 的真实订单意图、`order_id` 和原因码；
4. `Candidate explanation` 中每个候选的确认、最终目标、真实订单、分支阻塞和资金限制；
5. Risk/Sentinel 的证据、冻结权限和账户资本修复状态。

日报只展示本次 `Decision` 与传入 `AccountState`，不重新排序、授予资格或分配资金。
候选资格为 YES 或阻塞清除都不是下单授权，必须存在本次 Decision 的 BUY 意图。
已记录的 `reference_coverage_or_confirmation` 表示参考覆盖或确认尚未满足；
`insufficient_executable_capital` 表示部署记录的可执行资本不足；`unresolved_execution_capacity`
表示执行责任尚未结清。每个原因只适用于记录中点名的候选，不扩展到其他股票。

`pending current quality` 展示普通部分成交挂单本次记录的继续买入资格：仍须成熟、当前
至少一路有效并满足市场质量条件，确认要求为 1 日；首次入场仍要求连续 5 日。
`PENDING_CORE_BUY_ALREADY_EVALUATED` 仅表示普通挂单已由原分支评估，不能遮住实际的
`NOT_MATURE`、`CONFIRMATION_INCOMPLETE` 等拒绝原因。最终 Sentinel 冻结和
`CAPITAL_LIMIT` 仍优先解释为何未增加目标；当前资格通过本身不代表可执行。
`RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING` 表示当前连续持仓未能关联记录的风险事件，
应核对真实成交与风险事件；不能补写历史来启用旧恢复权。连续持有的健康风险缩减仓位则按
恢复条件管理，不因新入场的成熟度不足而自动失去恢复资格。

分配器在实际判断分支中记录资格拒绝原因，以及本次意图计算时现金、总仓、单名、行业和
相关性预算的剩余空间。它们使用同一顺序资金账本，各行数值不能相加当作另一份预算。
未进入资金检查的候选明确显示“未评估”。最终目标和订单在 Risk 限制及订单对账后写入
Decision；Sentinel 冻结期间的临时规划不会被展示为买入授权。订单规划器同时记录真实的
不交易区间或取消待确认原因，报告层不另算规则。历史 Decision 缺少这些记录时，日报仍
明确标记未知；涨跌停、停牌、容量和成交障碍以实际执行记录为准。
风险/上限变化、资格与参考覆盖变化、真实成交/取消确认和现金变化都需要下一次正常 `daily`
重新评估；日报不预测某一价格或分数将触发买入。未成交卖单的预期收入不作为可用现金。

## 人工执行闭环

1. 运行 `daily`，记录意图；
2. 在券商端人工检查价格、涨跌停和可卖数量；
3. 人工下单，不修改系统 `order_id` 对应关系；
4. 收盘后导出完整成交和持仓快照；
5. 使用 `account-sync` 对账；
6. 再运行日报前确认没有未解释差异。

若人工决定不执行某个意图，应保留真实账户状态，并按订单生命周期显式取消或替换；不要伪造成交来消除挂单。

### 外部执行 journal

人工闭环可另写 observational、append-only、broker-independent Journal，逐笔记录计划、
下一交易日开盘、实际成交和人工跳过。它不连接券商、不回写 `account_state.json`，也不
作为候选排序、风险状态或参数输入；命令、checkpoint 与恢复规则见
[Future Holdout](HOLDOUT.md)。

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

## Future Holdout

真实未来数据、Lane、确定性回放、人工执行 Journal、checkpoint、Risk Differential 观察和
失败恢复采用独立合同，不能与冻结数据或生产账户混用。日常入口为：

```bash
uv run python -m scripts.production_observation run --help
```

未观察时正式评分必须为 `null`；新 session 只能向前追加并遵守 no-backfill；观察结果不能
反向调参或扩大生产权限。完整准备清单、命令和恢复步骤见
[Future Holdout](HOLDOUT.md)。

## Risk Sentinel 日报融合

生产默认是 `FREEZE_ONLY`。`uquant daily` 在唯一日报中显示 Mode、Level、Coverage、
Confidence、Owner、Risk Families、AI Industry Risk 和受限结论；日常不再运行独立
Sentinel CLI。Sentinel 最多设置现有 `RiskAssessment.freeze_new_risk`，不能直接 SELL、
降低 `target_gross_cap`、创建第二账户或增加账户字段。

Risk Differential 与 counterfactual 只作观察，不得转换成人工卖单、gross-cap override 或
配置变更；命令与里程碑规则见 [Future Holdout](HOLDOUT.md)。工程合同验证和离线故障
诊断见 [Risk Sentinel](RISK_SENTINEL.md)。

## 统一核心版本的一次性切换

先确认待切换提交完成[开发指南](DEVELOPMENT.md)与[性能与证据](PERFORMANCE.md)要求的验收。
诊断 checkpoint 不是正式验收通过或激活记录。先在独立副本上完成以下预演，再由 operator
选择正式切换的 session 边界。

1. 暂停新增人工下单，记录在途订单；在旧版本正常对账后，保存完整账户、券商快照、使用的
   数据摘要、源代码提交和配置摘要。将账户复制为独立目录中的
   `cutover_review/account.before.json`，保留只读原件；副本、报告、数据和快照不能互相别名。
2. 用经核验的同一发布提交和 `uv sync --frozen` 建立环境。严格读取副本，核对 schema 8 和
   原股票池/数据身份；不能把 `account-init` 的空账户代替已有真实账户。
3. 只对副本执行代码身份重绑定：

```bash
uv run uquant account-code-migrate \
  --account cutover_review/account.before.json \
  --output cutover_review/account.candidate.json \
  --acknowledge-code-change
```

命令只更新 `code_hash` 并追加 `code_identity_only` 审计事件；输出
`economic_state_sha256`。迁移审计中执行前、落盘后和严格重载后的经济摘要必须一致。
此命令不转换策略语义，也不证明新版本经济验收通过；已经绑定同一代码时不要重复迁移。

4. 核对原 `account_identity`、订单/成交/event/grant/epoch 身份、订单序号和归因引用全部保留；
   现金、股数、成本、费用、可卖批次和在途订单全部保留。历史 grant/epoch 仍用于真实成交
   归因与幂等，不再代表对整个账户资本的永久经济独占。
5. 账户高水位、资本损伤与修复 streak、风险事件、保护/恢复权重及授权证据全部保留。
   新增的逐候选资格/交接确认若没有真实历史观察，只能由后续因果交易日逐次建立；不得从
   当前持仓反推确认次数，或通过换候选、清空旧字段、重设最高权益跳过修复。
   若原始状态缺失、身份冲突或新语义无法可靠恢复，先恢复可信完整备份并定位缺口；
   `account-code-migrate` 不能补造这些事实。
   普通恢复权还须关联 `last_shock_date` 所在的连续持仓。FIFO 耗尽旧批次时使用完整真实
   成交核对，不修改存续批次日期来制造关联。已清仓证券重新通过首次资格并取得入场预算后，
   分配器才清除该证券陈旧的普通保护权重；切换时不手工批量清空恢复状态。
6. 将完整券商快照复制到 `cutover_review/broker_snapshot.json`，只在候选副本对账：

```bash
uv run uquant account-sync \
  --account cutover_review/account.candidate.json \
  --snapshot cutover_review/broker_snapshot.json
```

再按上文 `daily` 命令运行：账户改为 `cutover_review/account.candidate.json`，输出改为
`cutover_review/daily.md`，使用原完整股票池和经核验的数据目录；决策日必须晚于该副本的
`last_successful_run`，且不能早于已对账事实。离线策略验收只使用截至 2026-08-05 的授权窗口；
2026-08-06 起的受保护数据不用于本次改版预演、调参或补历史证明。

7. 人工核对副本账本与真实券商事实、目标/订单原因、风险上限和部分成交剩余责任；未取得
   取消确认的 BUY 不得因切换清空或复制。只有 operator 明确接受该发布版本与逐笔执行责任
   后，才在选定 session 边界切换正式运行路径；保留原账户和完整切换前后副本，不自动覆盖。
   正式 Future Holdout 的旧 source epoch、账户与 Journal 继续封存；新版本如需激活，按
   [Future Holdout](HOLDOUT.md)建立有明确生效日的新绑定，禁止回填或重写旧观察记录。

Base Risk 继续负责账户风险状态、总仓压缩、资本损伤修复和硬风险退出；Sentinel 只有现有
`FREEZE_ONLY` 的新增风险冻结权限。唯一 PortfolioAllocator 在这些边界内生成目标，订单
仍由真实成交结算。operator 负责券商下单、价格/可卖数量核对和完整成交回传，不能把日报、
候选资格或人工判断当作绕过 Risk 的授权。

## 常见故障

| 现象 | 含义 | 处理 |
|---|---|---|
| `data hash differs` | 已使用历史数据发生变化 | 恢复可信文件并重新验证 |
| `production code hash differs` | 账户绑定的代码与当前运行代码不同 | 备份 schema 8 账户，执行 `account-code-migrate` 并核对经济状态摘要 |
| `UnsupportedAccountSchemaError` | 账户的整数 `schema_version` 不是 8 | 恢复已核验的 schema 8 备份，或用 `account-init` 新建账户 |
| `unknown order_id` | 券商成交无法对应系统订单 | 修正快照或人工调查 |
| `duplicate fill_id` | 重复成交经济字段不一致 | 修正导出来源，禁止覆盖 |
| `insufficient common history` | 必需证券共同历史不足 | 补齐数据或调整开始日期 |
| `reference coverage` | 参考篮子缺失或不可见 | 补齐参考数据，不使用小样本替代 |
| `limit blocked` | 一字涨跌停阻塞 | 保留意图，等待可交易日 |
| `T+1 blocked` | 当日买入不可卖 | 以券商可卖数量为准 |
| `capacity blocked` | 参与率或手数不足 | 降低计划规模或等待流动性 |
| `strategic deployment blocked` | 候选资格仍在，但风险、机会、资金或执行状态不允许部署 | 保留账户和观察状态，解除阻塞后重新运行 `daily` |
| `strategic grant expired` | 候选、路线、数据身份、允许证券池或物理归因身份约束失效 | 核对 `expiry_reason` 和陈旧订单已终结；不得手工扶正第二名 |

停牌、涨停、容量、手数、暂时现金不足、订单待确认和下一交易日不可交易属于可恢复执行阻塞，
不会重置候选资格或改变 `grant_id`。阻塞解除后的每次重试仍必须由正常 `daily` 路径重新通过
Risk 和 `PortfolioAllocator`；不要手工复制订单、修改候选证券或清除授冠状态。

错误发生后先保留账户、快照、日报和日志副本，再调查根因。不要直接编辑持仓、订单或成交数组来绕过校验。

所有外部文件都按不可信输入处理。账户、数据、Journal、checkpoint、输出和备份路径不得
通过相同路径、符号链接或硬链接互相别名；严格 JSON 拒绝重复键和非有限数值。生产写入
使用进程锁、临时文件、刷盘和原子替换，但 operator 仍必须把备份放在独立目录。

## 备份与恢复

建议至少保留：

- 每日运行前后的账户文件；
- 对应券商快照；
- 当日日报；
- 使用的数据清单与摘要；
- 当前 Git 提交和生产源码摘要。

账户保存使用原子替换，但仍应把备份写到独立目录。恢复时先在副本上运行 `account-sync` 和 `daily`，确认输出一致后再切换。

重启前先检查持久账户的 `last_successful_run`、已接受成交和在途订单。若该 session 的
`daily` 已成功保存账户，即使报告输出失败，也不能在已推进账户上再次运行同一日；引擎会
拒绝重复或倒退 session。需要重建报告时，在独立副本中从原运行前账户、同一代码/配置、
同一行情前缀和同一券商快照重现，再与已保存账户及订单身份比较，不重复下单。
下一次正常运行沿用成功保存的账户，先对账真实后续成交，再进入下一允许 session。
部分成交和迟到成交始终沿用原 `order_id`/`fill_id`/grant/event/epoch 引用；不得重置序号或
重新初始化账户来消除挂单。如果失败边界不明确，先保留所有载体，不能选择较空的副本当作
“干净重启”。

单命令生成的 checkpoint 可随时做只读校验：

```bash
uv run python -m scripts.production_observation verify-backup \
  --checkpoint production_observation_backups/2026-08-06
```

校验会重算 manifest 与每个载体（包括最终 `receipt.json`）的 SHA-256/大小，拒绝篡改、缺失和
未登记文件。`PREPARED` 表示只有已读回验证的运行前备份；`COMPLETED` 和 `FAILED` 都必须具备
与 manifest 状态一致且被哈希绑定的 receipt。失败恢复不提供
自动覆盖：先复制 `account.before.json` 到独立恢复目录，在副本上执行 `account-sync` 和
`daily`，核对 Decision Digest、Target、Orders、Fills、Account 与原日报后，再人工决定是否
替换生产账户。`receipt.json` 为 `FAILED` 时按最后成功 step 定位边界，不要删除已经不可变追加的
holdout session。

### 仓库证据的保留与恢复

清理清单只覆盖删除、移动、外置、权限变更候选和高风险证据。一轮引用搜索无法证明
安全删除时标记 `UNRESOLVED_KEEP`，而不是继续猜测；冻结数据、身份注册表、锁文件和
当前治理清单标记 `KEEP_AUTHORITATIVE`。可恢复清单位于
`artifacts/architecture_refactor/cleanup_inventory.json`，并为每个条目记录内容摘要、
引用证据、权限理由、删除或迁移处置和 Git 恢复边界。

三项高风险锚分别是 `artifacts/architecture_refactor/baseline_inventory.json`、
`benchmarks/source_surface_registry.json` 与 `data/frozen/DATA_MANIFEST.json`。
恢复时先在隔离副本中用记录的 Git 对象还原并重算摘要，不覆盖当前账户、冻结数据或
source epoch。Future Holdout 遵守 no-backfill：新交易日只能追加到当前 epoch，不能把
后见数据或新打包身份写回旧基线。

## 发布前检查

发布命令、静态检查和 CI 结论由[开发指南](DEVELOPMENT.md)唯一维护；经济门、窗口和
证据解释由[性能与证据](PERFORMANCE.md)唯一维护。operator 发布前只需确认使用的是待发布
HEAD、生成证据没有误提交、账户已备份，并且所需 GitHub 结论均绑定同一提交。
