# Independent Risk Sentinel — Production FREEZE_ONLY

生产默认模式是 `FREEZE_ONLY`。Independent Risk Sentinel 读取 canonical 34-stock AI
universe、点时行业、冻结行情、两个指数和已有账户快照，形成独立风险证据；同一份
摘要进入唯一 `uquant daily` 报告。它不能生成目标、订单或成交，不能直接 SELL，不能
修改 `target_gross_cap`，也不能写第二账户或第二状态机。

生产映射仍只有 Phase 4 已晋级的窄 Freeze-only 边界：合格意见最多设置现有
`RiskAssessment.freeze_new_risk`。Phase 6 的完整市场时间线用于因果诊断，但
`risk_sentinel_causal_confirmation_enabled=false`，所以两日可信确认本身没有新增生产
权限。Phase 7 尝试仅启用该权限，因没有实际阻止新增风险而 REJECT，未合并 main。

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

uquant 正式风险负责生产状态机与经济行为。Sentinel 描述跨市场、等权子行业、领导者、
当前持仓和已有资本高水位证据。只有 `market_velocity`、`breadth_structure` 和
`covariance_stress` 可以进入完整历史时间线；`live_book_damage` 与 `capital_damage`
只作当日诊断，当前账户不会回填历史。

Phase 8 Evidence Closure 在同一市场序列上比较双方首次 Family 日期。结果是三个可信
市场 Family 全部为 `DUPLICATE`，`EARLIER=0`、`INCREMENTAL=0`、
`FALSE_POSITIVE=0`。详细机器证据位于
`artifacts/sentinel/evidence_closure/evidence_closure.json`。这项分析不改变 confidence、
确认日、修复日或任何基础风险阈值，也不取得新的 Freeze 权限。

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

以下独立 CLI 仅用于离线审计或故障诊断，不是日常生产步骤：

```bash
uv run uquant-sentinel \
  --data-dir data/frozen \
  --date 2026-08-05 \
  --account account_state.json \
  --output artifacts/sentinel/2026-08-05.json
```

输出目录必须与账户和数据目录分离。账户或数据别名、非共同指数交易日、canonical universe
与点时 reference registry 不一致都会失败关闭。日常只运行一次 `uquant daily`；人工不能把
离线 Sentinel 观察转换成卖单、总仓限制或账户状态。

## 已知边界

- Sentinel 是风险意见，不是收益预测、卖单或止损执行器。
- 缺少某个证券的足够历史会降低 Coverage，而不是用未来数据或较低门槛补齐。
- 离线 Calibration 的精度、召回、提前量和机会成本必须与 Future Holdout 正式评分分开。
- `sentinel_shadow` Holdout Lane 从真实启用日开始，不能回填此前观察。

## Risk Differential Closure 架构

Risk Differential Closure 固定了成熟边界：Base Risk 是唯一生产风险权威；Risk
Sentinel 是独立风险观察器，并只保留既有的窄 `FREEZE_ONLY` 映射；固定提交的
`trade` challenger 仅用于研究差异基准；portfolio counterfactual 仅是研究经济模拟；
任何新能力只能通过不可回填的 Future Holdout 获得新的未知样本证据。本架构不声称
Risk Sentinel 包含 `trade` 的全部执行政策，也不允许 Sentinel 生成 SELL、订单或第二套
资本/冷却/恢复状态机。

机器清单、三方逐日 replay、counterfactual 和 terminal promotion decision 位于
`artifacts/sentinel/risk_differential/`。它们均为 observation/research evidence，不是生产
指令。
