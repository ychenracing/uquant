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

日常按一个闭环执行：备份运行前账户 → 核对当日行情和同一券商账户的完整快照 →
执行一次带 `--broker-snapshot` 的 `daily` → 核对日报与保存后的账户 → 人工处理真实订单 →
下次用真实成交和取消确认对账。`daily` 已包含快照同步，不必再先执行一次 `account-sync`；
后者用于单独对账或切换预演。保存运行后账户、快照和日报，按账户与决策日分别归档。
命令中的历史日期和示例股票池用于展示参数，日常使用须明确指定自己的完整股票池、
行情目录和决策日。无订单也是有效决策，不能仅为产生买单而反复运行。

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

`ORDINARY_MARKET_EVIDENCE_UNAVAILABLE` 表示两个指数的 120 日收益证据不完整或非有限。
证据完整但两项均不为正时，普通新候选只共享默认 20% 初始额度减去普通持仓及 BUY 承诺
后的余量；未成交 SELL 不释放额度。`ORDINARY_INITIAL_CAPITAL_BELOW_TRADE_MINIMUM` 表示
原名义均分份额低于最小交易权重，不应先卖旧仓凑这次买入。至少一项为正时沿用原入场预算。
这个检查不强制把已有普通敞口减至 20%，不取消原合格订单，也不替代战略授权、真实现金
修复或独立回撤许可；没有额外等待天数或持久标记需要手工重置。

日报只展示本次 `Decision` 与传入 `AccountState`，不重新排序、授予资格或分配资金。
候选资格为 YES 或阻塞清除都不是下单授权，必须存在本次 Decision 的 BUY 意图。
已记录的 `reference_coverage_or_confirmation` 表示参考覆盖或确认尚未满足；
`insufficient_executable_capital` 表示部署记录的可执行资本不足；`unresolved_execution_capacity`
表示执行责任尚未结清。每个原因只适用于记录中点名的候选，不扩展到其他股票。

普通 CORE 逐股资格可来自当前生产成熟度及真实既有确认，或当前共享战略证书。
`ordinary_trend_participation` 仅限制新战略 grant，不代表该证券禁止普通买入。
普通趋势新增资金另需当前共同趋势确认及 `Risk.NORMAL`、无冻结、实际现金、槽位和全部集中度
约束。`COMMON_TREND_NOT_CONFIRMED` 表示共同证据未成立；资格 YES 仍不是资金许可。
完整共同确认与预算规则见[策略说明](STRATEGY.md#普通领涨)。当前机会标签本身不足以买入，
也不代替真实现金修复授权；已有普通持仓不会被重新贴为战略 grant/epoch。

`pending current quality` 展示普通待成交 BUY 本次逐股资格。普通趋势待单还会检查当前
共同确认；证据失效则取消未成交余量并撤销其陈旧普通恢复权，重启后不能借旧权补买。
已成交份额按持有规则管理，当前发现资格下降本身不触发清仓。真正的现金修复订单另行
核验其原始严格资格、order/event 和实际授权，解除冻结也不改用普通成熟证书续资。
最终 Sentinel 冻结及资本限制仍可拒绝个股和共同证据均合格的普通目标。
`RESTORATION_EPISODE_NOT_LINKED_TO_HOLDING` 表示当前连续持仓未能关联记录的风险事件，
应核对真实成交与风险事件；不能补写历史来启用旧恢复权。连续持有的健康风险缩减仓位则按
恢复条件管理，不因新入场的成熟度不足而自动失去恢复资格。

独立长期回撤新入场通道已退休，不能把 CAUTION、现金充足或质量 READY 自行解释成可买。
历史 `BOUNDED_PULLBACK_AUTHORIZED` 不会生成新的替代买单；
`PULLBACK_PERMISSION_CLOSED` 表示历史原余单的当前资格、风险或完整预算已
失效，应取消余量并保留已成交仓位。`PULLBACK_NOT_GRADUATED` 表示长期结构持仓尚未
转入普通成熟管理，不能借恢复权加仓；当前 MA20 弱势本身不是该原始持有依据的退出。
已确认 MA120 破坏及相对真实均价的灾难损失按策略生成实际 SELL，不能把目标当作成交。
原回撤余单仍核验当前质量、原订单身份、风险及完整预算，不得删账或换单绕过检查；
MA120 与成本灾难退出继续保护实际持仓。

账户 schema 8 的 `lifecycle_events` 增加明确的入场许可与成熟转换事件种类；提交前的许可
事件没有虚构 `shares`，成熟转换保存当时成交序列长度作为观察边界。完整事件、原订单与
真实成交必须一起保存。旧解码器不认识这些种类时会拒绝读取，不得删事件降级。迁移代码
身份不重写历史凭证；新源码不自动继承旧源码未成交订单的新增权限，实际余单须按待运行源码
核验并取消不再有效部分。历史已成交持仓的原始依据与单向成熟状态仍保留。

换仓的 `transfer feasibility after settlement` 只展示记录的卖出完成后预算估计：
`TRANSFER_BELOW_TRADE_MINIMUM` 表示拟转出量不足最小交易权重；
`TRANSFER_CANNOT_FUND_ADMISSION` 表示全部拟卖出完成后仍不足以支持新入场；
`FEASIBLE_AFTER_SETTLEMENT` 只表示这项必要检查通过。投影不是现有现金或真实成交，
不能据此提前买入；卖出回传后仍由下一次正常 `daily` 重新检查资格和全部预算。

普通 CORE 新增集合按原有效资格及单一未成熟槽位规则筛选，等分原趋势名义预算（默认 80%），
单名入场上限 50%；未成熟共享证书成员另受原 20% 上限。已成交持仓及仍有效的原订单
不因成熟度标签变化而自动退出或改写。
实际增量至少达到原最小交易权重 5%。最终手数、金额、费用、现金预留及
共同风险预算仍由原规则判断，受限候选留下的资金不重新分给同批其他候选。FULL_COHORT 没有已确认主导者时，
尚未成熟的成员各自最多取得 20% 目标，未用额度留作现金；后来成熟本身不自动加仓。
单一主导者与真实现金修复仍按各自原始授权核对，不能从这条普通预算说明推导额外额度。

分配器在实际判断分支中记录资格拒绝原因，以及本次意图计算时现金、总仓、单名、行业和
相关性预算的剩余空间。它们使用同一顺序资金账本，各行数值不能相加当作另一份预算。
未进入资金检查的候选明确显示“未评估”。最终目标和订单在 Risk 限制及订单对账后写入
Decision；Sentinel 冻结期间的临时规划不会被展示为买入授权。订单规划器同时记录真实的
不交易区间或取消待确认原因，报告层不另算规则。历史 Decision 缺少这些记录时，日报仍
明确标记未知；涨跌停、停牌、容量和成交障碍以实际执行记录为准。
风险/上限变化、资格与参考覆盖变化、真实成交/取消确认和现金变化都需要下一次正常 `daily`
重新评估；日报不预测某一价格或分数将触发买入。未成交卖单的预期收入不作为可用现金。

战略 `ACTIVE` 表示首笔匹配 owner 的真实正股数 BUY 已形成所有权，不代表额外资本权限。
FULL_COHORT 首笔成交完成部署；Pair/Single 后续异日真实 BUY 才完成部署并推进风险世代。
真实激活后的所有权在持仓与执行责任结清后记为 `CLOSED`；未激活授权才记为 `EXPIRED`。
授权本身仍可过期，不能将所有权结清解释为完成部署，也不追改旧账户的终结历史。
Pair/Single 的首笔订单即使完整成交，grant 仍显示 `PARTIALLY_FILLED`，应同时
核对真实股数、订单剩余责任与 epoch，不能仅凭这个名称判断仍有余单。首次入场已经成交、
原 BUY 的经济剩余量为零、无本地 BUY 挂单且无迟到成交责任时，当前入场质量下降只阻止
继续部署，不因此清仓已有份额。增加目标或升级仍须重新满足原见证组的当前资格、连续确认和原 Risk/资本限制；
持续持有则接受既有战略灾难止损、已启用的 ATR 止盈保护和风险减仓规则，不能由持仓事实自动取得加仓权限。
核对战略组合是否已启动持有管理时，除原有各成员达到目标 95% 的权重判据外，也可
核对全体当前成员的原生 `STRATEGIC_COHORT` BUY：订单实际完整成交、正股数成交总量
与订单一致，event/epoch/证券身份匹配，且没有相关 BUY 挂单、未终结或迟到执行责任。
当前持仓必须属于同一 epoch；owner 使用原 grant，其他成员保持空 grant，不向它们
复制 owner 授冠。满足完整成交证明后，收盘权重因价格漂移低于目标不代表原部署未完成，
不为此重复补仓；仅 `FILLED` 标签、单一成员成交或目标意图不足以形成该证明。此读取
不手工推进 CORE/ACTIVE，也不改变部分成交余量与真实风险退出的处理。

已完整完成首次入场但 grant 尚未完成部署的 Pair/Single 仓位，还遵守普通 CORE 的结构退出确认：
价格与近期收益均满足原退化条件，并达到原连续确认和最短持有要求，才提出
清仓；相对成熟度不能否决已确认的绝对价格退化，既有赢家保护条件不变。
战略仓位仍使用 `STRATEGIC_TRAILING_EXIT` 并保留原 grant/event/epoch 归因；
实际卖出和剩余责任结清前不提前结束 epoch。已完成部署的持仓沿用其持有规则。

已完整建仓但尚未完成部署的单一战略持仓达到既有浮盈门槛时，也可能产生 `STRATEGIC_PROFIT_LOCK`
减仓。对这类仓位，保护上限为
`min(strategic_dominant_retained_gross, 原始首次入场订单.target_weight)`。原始订单须
对应当前 grant/epoch/owner 在 `first_fill_session` 的首笔正股数 BUY；缺失或冲突时
不启用该保护。后续扩仓可以调整 grant/epoch 的目标，它们不能代替原始订单预算。
相较此前按阶段比例计算的上限，新上限可能升高或降低，不能视为始终更保守或等价修复。
这不完成部署，也不授予加仓权限；已完成部署的持有规则不变。更紧的独立风险目标仍可要求
减仓，须按本次真实订单机制对账，不能把其他机制的卖出记作已经执行利润保护。
ATR 与这项 CORE 利润保护通过原订单的实际完整 SELL、同一 epoch/grant 和原生 FIFO
批次来源核对结算。相同减仓目标已结算后，不因价格漂移重复执行；部分成交、未确认或
迟到执行责任、后续 BUY 和新的减仓目标仍按当前事实处理。仅有 `FILLED` 标签或存续
批次日期不足以证明旧目标已经结清。
若当前 ATR 计划目标不高于这项利润保护上限，且原生同一资本身份已完整兑现该计划，
后来的较宽松软利润保护不再重复减仓。仍记录原 ATR 成交，不伪记利润保护成交。
这不表示当前权重低于保护上限；价格上涨后保留更多股份，可能增加回吐。更低的新保护上限、
未结算执行、后续 BUY 或身份不符均不能复用旧计划。

战略软 ATR 退出计划尚未结清时，信号连续为真不逐日重复推进；有效行情下先解除、再触发
才继续降低计划目标。当前目标实际完整成交后保留剩余股份，不再因普通 ATR 新边沿累减。
缺失或非有限行情不重置触发状态。冲量全档退出、损伤修复后的升级退出、灾难止损、利润
保护和独立风险上限仍有效。放弃后续软减仓可能增加利润回吐，并非风险约束的替代品。
切换与重启保留已记录分段状态和真实成交证明，
不能清空状态来跳过或重放减仓。

普通连续持仓已有的受保护恢复权，可在 Base Risk 明确允许的资本或慢性风险修复条件下，
使用当前现金恢复到本次总仓上限内；它不向已清仓股票或新候选授予入场权限。Sentinel
冻结仍阻止增加资本。普通待成交 BUY 若当前资格失效，其旧恢复权一并撤销；取消订单、
重启或解除冻结后都不能通过该旧权重新买入。历史恢复组合的成员形态不再单独决定
当前连续持仓的恢复权；实际恢复仍受同一行业、相关性和现金预算约束。

全现金账户完成原有 20/40/60 个健康 session 的分级修复后，当前独立确认合格的普通
CORE 可使用一次受限重新进入权限。它仍须满足当前入场资格、风险、参考覆盖和共同
资金预算；订单保持 `LEADER_SELECTION` / `CORE`，grant/epoch 为空。原生正目标 BUY
完成归因并登记为 `SUBMITTED` 后即消费该次权限，账户记录原 order/event 引用；下一
开盘确定手数前 `requested_shares=0` 是未定量的真实意图，不是成交或免消费标记。
未登记订单的空计划或拒绝计划不消费；已登记订单后来因手数不足或人工取消而终止，
须重新积累修复，不能沿用原已达标计数。部分成交在当前资格和风险仍允许时使用同一
order/event 继续原剩余责任；重启、替换身份或解除冻结不会增加授权额度。

若尚有未部署资本的 grant 失效，撤销只针对其 BUY 余量：保留已成交仓位，目标不高于
当前实有权重与原目标中的较小值，后续仍可按持有规则减少。券商取消确认与迟到成交继续
按原订单对账；已存在的独立风险 SELL 保留原 `order_id`、`event_id`、信号日期、机制及
归因身份，不随 BUY 撤销重新生成。日常所有目标共用唯一分配入口和同一本资金账本；
持仓退出、换仓时钟与合法风险恢复仍在各自条件满足时执行。

同步反转 FULL_COHORT 申请新战略授权时，若没有已确认的双成员主导者，仍须当前普通
市场机会开放；仅有同步反转资格不能绕过 `WEAK/CHOPPY`。日报原因
`strategic_market_opportunity_required` 表示这条新部署限制。硬持续主线的既有早期许可、
严格单名/双名资格和真实现金修复授权仍按原条件判断。已存在的 grant、部分成交与持有
沿原证据复核，不因后来机会关闭追溯抹除身份。

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

## 切换日常使用的账户

CLI 通过 `--account` 文件路径选择账户，没有全局“当前券商账户”切换开关。每个账户使用
独立目录保存账户、快照、日报和备份；切换时一并核对这些路径及完整股票池，不能只替换
现金或持仓，也不能将回测账户改名后作为真实账户。快照格式没有券商账号匹配校验，
必须人工核对导出来源，不能仅凭文件名或本地 `account_identity` 判断券商账户归属。

继续已有账户时使用该账户最近可信的完整状态，包括订单、成交、风险修复与所有权历史。
真正新建空账户才使用 `account-init`，输出到新路径；该命令会保存输出文件，不能指向已有
账户。已有真实持仓但缺少系统历史时，券商快照不能补造策略资格、grant 或 epoch，
应先核清初始化边界，不能复制另一个账户的授权状态。

每个账户同一时间只运行一个写入命令。切回旧账户前，先对账离开期间发生的真实成交、
取消和当前现金持仓，再选择晚于其 `last_successful_run` 的决策日。仅切换账户文件无需
`account-code-migrate`；出现代码身份差异时按下一节核对账户源码身份，不能手改哈希绕过。

## 账户源码身份核对

先确认待切换提交完成[开发指南](DEVELOPMENT.md)与[性能与证据](PERFORMANCE.md)要求的验收。
诊断结果不能作为正式验收通过或激活记录。先在独立副本上完成以下预演，再由 operator
选择正式切换的 session 边界。

1. 暂停新增人工下单，记录在途订单；完成实际成交对账后，保存完整账户、券商快照、使用的
   数据摘要、源代码提交和配置摘要。将账户复制为独立目录中的
   `cutover_review/account.before.json`，保留只读原件；副本、报告、数据和快照不能互相别名。
2. 用经核验的同一发布提交和 `uv sync --frozen` 建立环境。严格读取副本，核对 schema 8 和
   原股票池/数据身份；不能把 `account-init` 的空账户代替已有真实账户。
   自定义配置须移除已无执行路径的十个字段：`leader_cycle_confirm_days`、
   `leader_cycle_min_mature`、`leader_cycle_min_score`、`leader_cycle_impulse_return`、
   `leader_cycle_impulse_index_return`、`leader_cycle_impulse_breadth`、
   `leader_cycle_min_market_ret120`、`leader_cycle_impulse_min_market_ret120`、
   `strategic_epoch_cooldown_sessions`、`strategic_epoch_min_symbol_change`。
   它们作为未知配置项明确拒绝，不能依赖静默忽略。冻结敏感性合同中的五个字段继续保留：
   `leader_tenure_days`、`strategic_reversal_min_ret5`、`strategic_reversal_max_tech_ret120`、
   `strategic_dominant_profit_lock_mfe`、`strategic_dominant_retained_gross`；切换时保留其配置值。
3. 只对副本执行代码身份重绑定：

```bash
uv run uquant account-code-migrate \
  --account cutover_review/account.before.json \
  --output cutover_review/account.candidate.json \
  --acknowledge-code-change
```

命令只更新 `code_hash` 并追加 `code_identity_only` 审计事件；输出
`economic_state_sha256`。迁移审计中执行前、落盘后和严格重载后的经济摘要必须一致。
此命令不转换策略语义，也不证明待运行源码的经济验收通过；已经绑定同一代码时不要重复迁移。

4. 核对原 `account_identity`、订单/成交/event/grant/epoch 身份、订单序号和归因引用全部保留；
   现金、股数、成本、费用、可卖批次和在途订单全部保留，原始 grant 事件不得改写为新候选。
   历史 grant/epoch 仍用于真实成交
   归因与幂等，不再代表对整个账户资本的永久经济独占。
   `CORE` 与 `ACTIVE` 按这些真实成交和原 epoch 保留；不得把已成交 CORE 改回未入场，
   也不得为跳过确认把它手工升级为 ACTIVE。撤销待确认和独立风险 SELL 的身份一并保留。
   同时保留利润保护和 ATR 的原订单、完整成交、`sold_tranches` 与对应 BUY 来源，以及
   首笔实际 BUY 对应的原始订单目标、epoch 身份和历史持仓峰值。不得用后续变更的
   grant/epoch 目标补写初始预算。账户校验读取这些事实确认结算；不要为启用或跳过保护
   清空档位、改写 grant/epoch、重置峰值或补造成交。沿用现有代码身份迁移和经济摘要
   核对，不需要另建账户或 schema 迁移。
5. 账户高水位、资本损伤与修复 streak、风险事件、保护/恢复权重及授权证据全部保留。
   schema 8 的 `strategic_cash_rearm` 包含可选嵌套 `consumed_order`，仅保存已消费普通
   订单的 order/event 引用，与战略 grant 消费互斥。旧账户缺少此项时按 `None` 读取，
   不从历史订单猜补消费事实。解码器严格拒绝未知键；不要删除该项绕过校验。保留完整账户副本、账本和高水位，
   使用与账户 schema 相符且已验收的代码。账户顶层 schema 仍为 8，代码身份迁移不补造普通消费或修复历史。
   缺失的 `independent_core` 计数从零开始，由新观察逐日建立，不从旧路线计数或当前持仓猜补。
   单名战略授冠须同时取得原路线与严格观察各 4 日确认；按严格单名证据申请普通核心首次
   入场按原严格资格确认；普通成熟证书使用实际 `leader_tenure_days` 确认，共享证书保留原要求。
   `ordinary_market_session/streak` 已无执行读取；旧记录保留，不回填。普通成熟入口可凭
   当天强冲量或健康长周期市场证据申请；经历板块风控后，两种快捷入口均须重新积累
   `leader_tenure_days` 个健康确认日。真实持仓恢复和资金修复另按各自权限判断。
   资金修复的成熟入口另要求连续可信成熟计数；旧账户缺少
   `ordinary_repair_maturity_session` 或 `ordinary_repair_maturity:<symbol>` 时从零建立，
   不能拿原 leader tenure 补齐。保留 `ordinary_repair_capital_active` 和对应真实持仓、
   未成交责任，不能通过清空标记释放仍在使用的共享预算。
   保留真实账户账本和已有可信观察记录；新确认的
   冷启动不改变持续持有和合法风险恢复的管理条件。其他新增资格/交接确认
   同样不得补造，也不能通过换候选、清空旧字段、重设最高权益跳过修复。
   若原始状态缺失、身份冲突或新语义无法可靠恢复，先恢复可信完整备份并定位缺口；
   `account-code-migrate` 不能补造这些事实。
   普通恢复权还须关联 `last_shock_date` 所在的连续持仓。FIFO 耗尽旧批次时使用完整真实
   成交核对，不修改存续批次日期来制造关联。普通陈旧记录在日常判断中不生效，记录仍可
   保留；新入场取得预算时清除该证券的旧权利，新冲击则合并有效旧权利与当时实际持仓。
   已完成初始部署的战略成员实际清仓后，旧成员目标与恢复权在正常决策中退役；新入场
   重新确认当前资格。持续持有的风险压缩仓位仍可恢复。保留原订单、成交和 epoch 归属，
   未结算的撤单或迟到成交责任仍须处理；普通重新入场不继承旧战略身份。
   不因看到旧字段就认定有恢复权限，也不在切换时手工批量清空恢复状态。
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
   正式 Future Holdout 的旧 source epoch、账户与 Journal 继续封存；待运行源码如需激活，按
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
| `strategic grant expired` | 未部署资本的候选、路线或身份约束失效 | 核对 `expiry_reason`、已成交份额与 BUY 取消/迟到成交责任；保留独立风险 SELL，不得手工扶正第二名 |

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

原始审计证据由不可变 Git 提交保存。仓库内的审计校验通过
`uquant.validation.evidence_source.evidence_root()` 读取并核验 Git 对象，将数据解包到独立目录，
不执行历史源码、不覆盖当前账户或冻结输入。完整克隆保留这些 Git 对象；生产运行不依赖审计数据。
跨 AI 对照入口也从该来源读取冻结基线，并继续核验合同中记录的文件 SHA256；缺失或摘要不符时拒绝继续。
证据中的生产者、配置、数据、失败结论和 seal 保持原样，不能作为新运行结果重新贴签。

冻结数据清单位于 `data/frozen/DATA_MANIFEST.json`，源码表面定义位于
`benchmarks/source_surface_registry.json`。Future Holdout 遵守 no-backfill：新交易日只能
追加到当前 epoch，不能把后见数据或新打包身份写回已封存基线。

## 发布前检查

发布命令、静态检查和 CI 结论由[开发指南](DEVELOPMENT.md)唯一维护；经济门、窗口和
证据解释由[性能与证据](PERFORMANCE.md)唯一维护。operator 发布前只需确认使用的是待发布
HEAD、生成证据没有误提交、账户已备份，并且所需 GitHub 结论均绑定同一提交。
