# A股 AI 产业链统一量化系统：全面替代三项目验收报告

> 本文定义新融合项目什么时候有资格真正替代 qwenquant、AQuant、trade。
>
> 核心原则：**没有通过统一、严格、可复现的验收，就不能因为“融合了更多功能”而宣布新系统更好。**
>
> “全面超越”必须在**统一数据、统一执行模型、统一费用、统一股票池、统一时间窗口**下比较。

---

# 1. 验收总则

新项目只有在以下四层全部通过后，才允许替代旧三个项目：

```text
A. 正确性
B. 生产一致性
C. 策略性能
D. 鲁棒性与样本外
```

任何一层出现 P0 失败：

```text
整体不通过
```

---

# 2. “全面超越”的严格定义

必须先明确一个事实：

> 不存在能够事先保证未来每个市场阶段同时获得最高收益、最低回撤、最少交易的策略。

因此本验收中的“全面超越”定义为：

## 2.1 单元级绝不能被明显支配

对于每一个正式测试单元，新系统不得相对任何旧项目同时出现：

```text
收益更低
AND
回撤更高
AND
交易更多
```

如果发生：

```text
Dominated Cell
```

则该单元验收失败。

---

## 2.2 核心指标必须非劣

对正式 Primary Benchmark：

### 收益

新系统最终财富至少达到三个旧项目中最佳可比项目的：

```text
99%
```

注意是**最终财富保留率**，不是简单收益百分点差。

### 最大回撤

不得比三个旧项目中最佳可比回撤恶化超过：

```text
0.5 percentage point
```

### 真实账户订单

不得比三个旧项目中最少可比 account orders 增加：

```text
max(2笔, 5%)
```

这是最终替代旧系统时的“终极严格门”。

研发 Candidate 阶段可以适当放宽，但 Production Replacement 阶段不能放宽。

---

## 2.3 整体还必须明确领先

仅仅每格都做到 99% 非劣，还不足以替代三个项目。

最终还必须满足：

- ≥60% primary cells：收益最佳或距离最佳 ≤1%财富；
- ≥60% primary cells：DD 最佳或距离最佳 ≤0.5pp；
- ≥70% primary cells：account orders 最佳或距离最佳 ≤5%；
- primary cells 中 `dominated_cells = 0`；
- 关键熊市/急跌单元至少有一个风险维度明显优于全部旧项目；
- random/add-drop stress 至少达到 trade 最新水平；
- leader/reference 稳定性至少达到 AQuant；
- daily execution simplicity 至少达到 qwenquant。

---

# 3. Benchmark 必须完全统一

## 3.1 数据

三旧项目与新项目统一读取同一冻结数据集。

股票：

```text
QFQ OHLCV
```

指数：

```text
raw / unadjusted
```

必须保存：

- DATA_MANIFEST；
- SHA-256；
- source；
- generated_at；
- symbol list。

---

## 3.2 执行模型

正式比较只允许：

```text
T 日收盘形成决策
T+1 最早可交易开盘执行
```

禁止把：

- AQuant `resting_stop_intraday`；
- 理想盘中止损；

与手动次日开盘混在同一主排名。

条件单模型只能做 secondary research benchmark。

---

## 3.3 费用

统一：

- 佣金；
- 最低佣金；
- 印花税；
- 过户费；
- 滑点；
- 100股整手；
- 科创板最小买入；
- 涨跌停；
- 停牌；
- 成交额容量。

---

## 3.4 账户约束

所有项目统一：

```text
initial cash = 2,000,000
gross <= 100%
single name <= 60%
max positions <= 6
no leverage
no short
```

---

# 4. 正式股票池矩阵

## 4.1 固定规模

至少：

- 1；
- 3；
- 5；
- 9；
- 15；
- 22；
- 32。

## 4.2 行业结构池

至少：

- optical concentrated；
- equipment concentrated；
- material concentrated；
- memory / compute；
- diversified AI；
- high correlation；
- low correlation；
- mature leader heavy；
- emerging leader heavy；
- loser heavy。

## 4.3 扰动池

必须包括：

- prefix；
- leave-one-out；
- add-one；
- replace-one；
- permutation；
- random subsets。

## 4.4 随机池

每个规模至少 50 个固定种子样本：

```text
3 / 5 / 9 / 15 / 22 / 32
```

至少 3 个 seeds。

总随机样本建议：

```text
>= 900
```

---

# 5. 正式行情窗口

必须至少覆盖：

## 2018 Bear

验证真正系统性熊市。

## 2020 Crash

验证快速 V 型危机。

## 2021 High-Vol / Tech Rotation

验证高位震荡与结构分化。

## 2022 Bear

验证持续弱市。

## 2023 Mixed

验证无明显单边趋势。

## 2024 Choppy

验证震荡。

## 2025-04 ~ 2026-06 AI Bull

验证主升浪收益。

## 2026-07 Acute Selloff

验证风险识别、回撤与恢复。

## Continuous Full Cycle

至少覆盖：

```text
2018 -> 2026
```

若数据完整。

---

# 6. 正式指标

每个测试单元必须输出：

## 6.1 收益类

- total return；
- final wealth；
- CAGR；
- excess return；
- Calmar；
- Sharpe。

## 6.2 回撤类

- max DD；
- rolling DD p95；
- max DD duration；
- worst 20d；
- worst 60d；
- peak-to-recovery days。

## 6.3 交易类

- account orders；
- round trips；
- gross turnover；
- annual turnover；
- median holding days；
- fees；
- slippage cost。

## 6.4 风险识别

- first CAUTION；
- first RISK_OFF；
- first reduce；
- lead to 10% DD；
- lead to 15% DD；
- false positives；
- false-positive days；
- bull opportunity cost；
- recovery delay。

## 6.5 归因

- core；
- Add1；
- Add2；
- satellite；
- recovery；
- rotation；
- risk cuts；
- false exit regret；
- replacement spread。

---

# 7. A类：正确性验收

## A1. 无未来数据

所有：

```text
decision[t]
```

只能使用：

```text
data <= close[t]
```

### 标准

任何 future mutation 不得改变历史 t 时点决策。

必须有专门 mutation test。

---

## A2. Next-open 执行

T 日信号不能在 T 日成交。

### 标准

所有正式订单：

```text
fill_date > signal_date
```

---

## A3. T+1

当天买入不能当天卖出。

必须按 tranche 校验。

---

## A4. 涨跌停

- 涨停不可虚构买入；
- 跌停不可虚构卖出；
- 连续封板必须保留未成交状态。

---

## A5. 停牌

不允许用旧价格假成交。

---

## A6. 资金

任意时刻：

```text
cash >= 0
gross <= 100%
```

---

## A7. 单票

订单成交后的 projected weight：

```text
<= 60%
```

不能只检查订单前。

---

## A8. 最大持仓数

真实账户：

```text
<= 6
```

---

## A9. 费用

所有成交费用必须一致且可复算。

---

## A10. 决定性

同数据、同配置、同账户：

```text
decision_digest identical
```

---

# 8. B类：生产一致性验收

## B1. Backtest 与 Daily 同源

禁止：

- backtest 一套策略；
- daily 另一套简化策略。

同一 as-of、同一账户必须得到相同 target。

---

## B2. Account Replay

必须逐日：

```text
close -> decision -> next open fill -> state persist
```

不能整段回测一次算完后再假装日频。

---

## B3. Risk State 持久化

必须保存并恢复：

- risk；
- opportunity；
- shock；
- cooldown；
- leader tenure；
- candidate tenure；
- account peaks。

重启后决策不得改变。

---

## B4. Fail Closed

缺失：

- index；
- reference；
- correlation；
- account state；
- hash；

不得默认为健康。

---

## B5. One Account Target

每只股票每天最多一个最终 target。

任何 alpha/risk 模块都不能直接成交。

---

# 9. C类：牛市收益验收

## C1. Primary Bull Wealth

统一 2025-04～2026-06 矩阵中：

```text
wealth_new >= 99% * best_comparable_wealth(qwenquant, AQuant, trade)
```

对每个 primary pool 都必须成立。

---

## C2. 总体领先率

至少：

```text
>= 60%
```

的 primary bull cells：

新系统财富必须为最佳或在最佳 1%内。

---

## C3. 不能用更多交易换小幅收益

若财富提升 <5%：

```text
account orders increase <= 5%
```

若 account orders 增加 >5%，财富必须提高至少5%。

---

## C4. Mature Leader

对历史超级赢家：

- 不得因短期 rank drift 频繁退出；
- false exit regret 必须优于三个旧项目中位水平。

---

# 10. D类：回撤验收

## D1. 固定 Primary

目标：

```text
DD <= 18%
```

stretch：

```text
15%~17%
```

至少不得比最佳旧项目 +0.5pp。

---

## D2. Random

必须：

```text
random p90 DD < 20%
random worst DD < 25%
```

且不得弱于 trade 最新正式 stress。

---

## D3. 熊市

2022：

最低要求：

- 每个 primary pool 不被最佳旧项目明显支配；
- p90 DD <=22%；
- worst DD <=28%。

stretch：

```text
五池中位收益 >= 0
```

---

## D4. 2026-07

目标：

```text
每个 primary pool loss/DD < 17%
```

stretch：

```text
< 14%~15%
```

同时 RiskUtility 至少优于三个旧项目。

---

# 11. E类：交易次数验收

## E1. Fixed Pools

目标：

- 3只：不高于 qwenquant；
- 5只：不高于 qwenquant；
- 9/15/22/32：尽量不高于 qwenquant +5%。

如收益显著更高，才允许少量增加。

硬规则：

```text
+2 orders 或 +5%
```

除非财富提高 ≥5%。

---

## E2. Random

```text
p90 account orders <= trade 最新 p90
```

并持续追求更低。

---

## E3. 内部信号与真实账户订单分离

正式用户成本只看：

```text
broker/account orders
```

但内部事件数也必须记录，防止隐藏 churn 改变状态机。

---

# 12. F类：Risk Lead-Time 验收

## F1. 2026-07

新系统第一次有效 CAUTION：

不得晚于三个旧项目中最早有效风险预警的可比水平。

---

## F2. 风险提前

所有正式风险事件：

```text
median lead_to_10pct_dd >= best_old
```

如果无法更早，则必须以更低 false positive 证明更高 RiskUtility。

---

## F3. False Positive

牛市中：

- CAUTION 可以偶发；
- RISK_OFF / CRISIS 误报必须非常少。

建议目标：

```text
false RISK_OFF events <= 2/year
```

最终按统一 benchmark 校准。

---

## F4. Bull Opportunity Cost

risk 模块造成的牛市财富损失：

```text
<= 2%
```

否则风险提前没有意义。

---

# 13. G类：Leader Intelligence 验收

必须至少达到 AQuant 的优势。

## G1. Reference Stability

修改用户输入池但 reference 不变：

同一股票 leader score 不应大幅漂移。

---

## G2. Future Mutation Invariant

未来数据修改不得改变历史 leader score。

---

## G3. Mature / Emerging Coverage

至少：

- mature 正确识别长期 leader；
- emerging 能处理短历史新 leader；
- unknown 不得因为缺失因子被错误赋高分。

---

## G4. Replacement Quality

Rotation：

```text
20d replacement spread > 0
40d replacement spread > 0
```

中位数必须为正。

---

# 14. H类：Recovery 验收

## H1. V型修复

CRISIS 后不能过度等待导致错过整个 V 反弹。

## H2. Fake Recovery

假修复不能快速满仓。

## H3. Recovery Trades

Recovery 不得演化为高频 bottom fishing。

---

# 15. I类：股票池稳定性验收

这是“任意数量 AI 股票池”的核心。

## I1. Add One

最差 add-one wealth change：

最终门：

```text
>= -10%
```

stretch：

```text
>= -5%
```

---

## I2. Remove One

同样：

```text
>= -10%
```

---

## I3. Permutation

同一成员不同输入顺序：

结果必须完全一致或只有浮点误差。

---

## I4. Size Boundary

9→10、12→13、15→16 等不能出现参数断崖。

---

# 16. J类：参数稳健性验收

## J1. ±5%

关键参数单独 ±5%：

不得产生大幅性能崩塌。

## J2. ±10%

不得出现明显 cliff。

## J3. Pair Perturbation

少量关键参数做双变量扰动。

## J4. Pareto

Production 候选必须位于：

- return；
- DD；
- trades；

三目标 Pareto 前沿或近前沿。

---

# 17. K类：过拟合验收

## K1. Nested Walk Forward

训练与验证严格分开。

## K2. Promotion Holdback

必须保留一个完全不参与调参的最终 promotion set。

## K3. PBO

记录 Probability of Backtest Overfitting。

不得只挑最佳参数、不披露试验空间。

## K4. DSR

报告 Deflated Sharpe Ratio。

---

# 18. L类：成本压力验收

## L1. Double Cost

双倍费用后财富衰减必须可接受。

## L2. Slippage

至少测试：

- 0.1%；
- 0.2%；
- 0.3%。

## L3. Capacity

降低最大成交量参与率后不能策略崩塌。

---

# 19. M类：极端执行验收

必须测试：

- 连续跌停；
- 连续涨停；
- 停牌；
- 开盘大 gap；
- 数据缺失；
- reference 缺失；
- 账户文件损坏；
- 风险状态来自未来日期；
- 部分成交；
- 卖出释放资金后再买；
- 科创板最小手数。

---

# 20. N类：每日使用体验验收

因为新项目目标是替代三个旧项目。

## N1. 单命令

必须可以：

```bash
python -m unified_ai_quant daily ...
```

一次输出全部。

## N2. 一页报告

必须包含：

- Opportunity；
- Risk；
- Target Gross；
- Target K；
- 持仓动作；
- 风险摘要；
- 明日订单。

## N3. 不依赖旧项目

正式运行不得：

- import qwenquant；
- import aquant；
- import trade；
- shell 调用三个旧仓库。

---

# 21. O类：三项目优势专项验收

## 必须超过 qwenquant 的地方

- 未知池 tail DD；
- 熊市稳定性；
- risk lead time；
- 同等或更低 account orders。

## 必须达到 AQuant 的地方

- leader quality；
- mature winner hold；
- candidate/reference stability；
- strong-trend low-DD 能力。

## 必须达到 trade 的地方

- universe stress；
- random p90 DD；
- add/drop stability；
- fail-closed risk evidence；
- ProductionReplay robustness。

---

# 22. 最终替代门

以下全部必须为 TRUE：

```text
Correctness = PASS
Production replay = PASS
No future leakage = PASS
Bull non-inferiority = PASS
Bear non-inferiority = PASS
Choppy non-inferiority = PASS
Acute risk = PASS
Trade count = PASS
Random stress = PASS
Add/drop = PASS
Leader quality = PASS
Risk lead-time = PASS
Parameter stability = PASS
Holdback = PASS
No dependency on old projects = PASS
```

才能宣布：

> 新系统替代 qwenquant / AQuant / trade。

---

# 23. 发布等级

## Research

可以失败，不用于日常。

## Candidate

通过正确性与基础性能，但不能替代旧系统。

## Production

通过本文全部 hard gates 后才能替代旧项目。

---

# 24. “全面超越”最终自动判定表

建议生成：

| Cell | New | qwenquant | AQuant | trade | Return Rank | DD Rank | Trade Rank | Dominated? |
|---|---:|---:|---:|---:|---:|---:|---:|---|

最终必须：

```text
dominated_cells = 0
```

同时：

```text
best_or_near_best_return_cells >= 60%
best_or_near_best_dd_cells >= 60%
best_or_near_best_trade_cells >= 70%
```

再满足：

```text
random stress >= trade
leader quality >= AQuant
daily simplicity >= qwenquant
```

才算真正全面替代。

---

# 25. 最重要的验收纪律

1. 不允许为了过门修改旧项目结果；
2. 不允许只挑新系统赢的股票池；
3. 不允许删掉失败窗口；
4. 不允许把条件单模型和手动模型混比；
5. 不允许在最终 holdback 上继续调参；
6. 不允许总体平均很好掩盖单个股票池崩塌；
7. 不允许用更多交易换一点收益而不披露；
8. 不允许只有回测，没有 daily replay；
9. 不允许 validation artifact 与代码 hash 不一致；
10. 不允许旧三个项目未统一执行口径就宣布全面超越。

---

# 26. 推荐研发目标值

这些是研发目标，不是未来收益保证。

## Bull

- 3/5/9/15/22/32：进入旧三项目最佳财富 99%以内；
- 大多数单元成为第一或并列第一。

## DD

- Fixed primary：15%～18%；
- Random p90：<20%；
- Worst：<25%。

## 2026-07

- Fixed primary：<17%；
- stretch：<14%～15%。

## Bear

- 2022 中位收益 >=0；
- worst DD <28%。

## Trades

- 接近或低于 qwenquant；
- random p90 不高于 trade；
- 13/22真实订单显著低于 trade。

## Add/Drop

- worst <10%；
- stretch <5%。

---

# 27. 最终原则

“全面超越”不能靠写进 README。

它只能靠：

> **同一数据、同一账户、同一执行、同一股票池、同一窗口下，新系统在收益、回撤、真实交易、风险提前、股票池鲁棒性、Leader 质量六个维度全部通过硬门。**

只要任一核心能力仍明显落后，新项目就应该继续处于 Candidate，而不是替代三个旧项目。
