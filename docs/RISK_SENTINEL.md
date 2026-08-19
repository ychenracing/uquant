# Independent Risk Sentinel Shadow Mode

Independent Risk Sentinel 默认仍是与生产决策隔离的只读观察器。它读取 canonical 34-stock
AI universe、点时行业、冻结行情、两个指数和已有账户快照，只输出独立 JSON/Markdown
证据。它不调用 `ProductionEngine`，不生成目标、订单或成交，不写账户，也不改变正式
`RiskAssessment`、组合分配或执行行为。Phase 4 增加了受控的 Freeze-only 集成代码，但
候选未通过经济硬门，默认模式已回到 `SHADOW`，生产决策不会求值 Sentinel。

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

uquant 正式风险负责生产状态机与经济行为。Sentinel 只描述同日可见的跨市场、等权子行业、
领导者、当前持仓和已有资本高水位证据。工件同时保留正式风险状态、同日正式风险事件与双方
证据家族差异，供人工分析。非默认 `FREEZE_ONLY` 模式下，唯一允许的映射位于
`uquant.assess_risk()`，且只能设置现有 `freeze_new_risk`；不能修改正式风险状态、总仓上限、
减仓级别或冲击状态，也不能直接产生目标、卖单、风险动作或账户状态。该候选当前不得晋级。
由于 Phase 4 尚无双方逐家族、逐交易日的点时首次证据载体，“更早”路径失败关闭；只有同日
新增 family 能提供增量资格，输出会明确记录 `sentinel_earlier_supported=false`。当前账户和
行业状态也不回填成历史确认，`confirmation_history_trusted=false`；常规两日确认不取得权限，
仅 severe-direct 窄例外可绕过确认天数。

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
