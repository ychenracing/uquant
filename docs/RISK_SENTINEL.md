# Independent Risk Sentinel FREEZE_ONLY Evidence Mode

独立 Sentinel CLI 仍是只读观察器：它读取 canonical 34-stock AI universe、点时行业、
冻结行情、两个指数和已有账户快照，只输出独立 JSON/Markdown 证据，不生成目标、订单或
成交，也不写账户。普通 uquant Daily Report 同时通过 `ProductionEngine` 计算同源 Sentinel
证据；生产默认模式是受控的 `FREEZE_ONLY`，唯一允许的经济映射是设置现有
`RiskAssessment.freeze_new_risk`，不能取得卖出、总仓、减仓或账户写入权限。

Phase 6 已加入从完整 warm-up 市场序列重算的、不可变且不依赖 AccountState 的双方点时
Family 时间线，并将其收进普通 Daily Report；但
`risk_sentinel_causal_confirmation_enabled` 默认保持 `false`，所以可信两日确认和更早
Family 在 Phase 6 只作诊断，不取得新生产权限。现有 severe-direct 窄例外保持原样。

## 证据与 Coverage

Sentinel 对每个证券先计算 5 日收益、MA20 状态和短期波动率扩张，再在子行业内部使用
中位数/比例等稳健统计，最后对子行业等权聚合。因此证券数量较多的行业不会仅凭成员数
支配风险意见。证据分为六个互斥计票家族：市场速度、宽度结构、协方差压力、领导者损伤、
当前持仓损伤和账户资本损伤；同一家族在一次判断中最多计一票。

Coverage Confidence 固定为：

```text
0.45 * component_observation
+ 0.35 * subindustry_coverage
+ 0.20 * held_industry_mapping
```

`READY` 表示索引和成分都有足够的因果 warm-up；新证券、部分缺失、持仓行业无法映射等
情况产生 `DEGRADED`；关键指数缺失或过期产生 `NOT_READY`。Coverage 不足不能形成更安全
结论：`NOT_READY` 不提供正常总仓建议，并明确冻结新增风险只是 Shadow 意见，不是生产动作。

## 与 uquant 基础风险的差异

uquant 正式风险负责生产状态机与经济行为。Sentinel 描述同日可见的跨市场、等权子行业、
领导者、当前持仓和已有资本高水位证据；工件同时保留正式风险状态、同日正式风险事件与双方
证据家族差异。`FREEZE_ONLY` 的唯一映射位于 `uquant.assess_risk()`，且只能设置现有
`freeze_new_risk`；不能修改正式风险状态、总仓上限、减仓级别、冲击状态或资本预算，也不能
直接产生目标、卖单、风险动作或账户状态。

Phase 6 时间线只信任 `market_velocity`、`breadth_structure` 和
`covariance_stress` 的完整市场前缀；双方使用同一交易日历和数据前缀，并重算首次 Family
日期、当日增量和可信更早 Family。`live_book_damage` 与 `capital_damage` 只作当日诊断，
当前账户、持仓或资本状态不会回填历史。Phase 7 曾锁定启用 causal confirmation 的唯一候选，
但小门没有实际阻止新增风险，已正式 REJECT；生产 `main` 继续使用 Phase 6 的关闭开关。

## 离线 Calibration 边界

`benchmarks/risk_sentinel_calibration_contract.json` 预注册 1/3/5/10/20 日结果、20 日
最大回撤、5 日提前量和牛市静默定义。只有 `calibration.py` 能读取事件后的结果，而且必须
传入显式 `evaluation_end`；窗口末之后的行情不可见。Calibration 不被实时 service、opinion
或评估 CLI 导入，也不得用于观察期内反向调整阈值。

## 工件与 Provenance

每次成功输出包含 Sentinel assessment、与基础风险的差异、账户只读声明，以及 commit、
Sentinel 源码、有效配置、数据前缀、universe、账户字节、Python/NumPy/pandas/uv/lock 的
SHA-256 或版本身份。JSON 使用 canonical seal；同输入同提交重复运行的 JSON、Markdown 与
`latest_success.json` 必须逐字节一致。失败运行不得更新 `latest_success.json`。

先验证静态合同和导入隔离：

```bash
uv run python -m uquant.risk_sentinel --validate-contracts
```

运行某一已存在的 canonical 数据交易日：

```bash
uv run uquant-sentinel \
  --data-dir data/frozen \
  --date 2026-08-05 \
  --account account_state.json \
  --output artifacts/sentinel/2026-08-05.json
```

输出目录必须与账户和数据目录分离。账户或数据别名、非共同指数交易日、canonical universe
与点时 reference registry 不一致都会失败关闭。Shadow 输出只供观察；人工不能把其中的
总仓或冻结建议写回模型账户。

## 已知边界

- Sentinel 是风险意见，不是收益预测、卖单或止损执行器。
- 缺少某个证券的足够历史会降低 Coverage，而不是用未来数据或较低门槛补齐。
- 离线 Calibration 的精度、召回、提前量和机会成本必须与 Future Holdout 正式评分分开。
- `sentinel_shadow` Holdout Lane 从真实启用日开始，不能回填此前观察。

## Phase 7 独立 Freeze 最终结论

Phase 7 在 `711af1179aa72ce48ca3a6af58ecddb3a029a7ce` 上预先锁定唯一候选变化：
`risk_sentinel_causal_confirmation_enabled: false -> true`。其余参数固定为
`FREEZE_ONLY / 0.80 / 2 日确认 / 3 日修复 / severe-direct 保持启用 / gross-cap 禁止`；
只有 `breadth_structure`、`covariance_stress` 和 `market_velocity` 可进入历史权限确认，
`live_book_damage` 与 `capital_damage` 仍仅是当日诊断。

锁定的三个小门 Cell 中，`a/h1_2024` 在 2024-06-25 出现一次非 severe-direct、
Coverage READY、confidence 1.0、可信连续 2 日且基础风险未 Freeze 的 Sentinel 独立
Freeze。然而当天没有新开、加仓、卫星、Recovery、Rotation 或未成交新增风险 BUY，实际
阻止新增风险数为 0；另两个 Cell 没有独立 Freeze。候选因此没有证明最低增量价值，正式
结论为 **REJECT**。

候选开关只保留在拒绝证据分支，不得合并到生产 `main`。Phase 6 的生产默认 `false`
保持不变；不创建 Sentinel Future Holdout Lane，不打稳定 Tag，不搜索参数，也不重启
gross-cap。完整机器可读证据位于 `artifacts/sentinel/exclusive_freeze/`。
