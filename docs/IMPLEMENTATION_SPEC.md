# A股 AI 产业链统一量化系统：三项目融合实施报告

> 目标：以 qwenquant、AQuant、trade 三个现有项目为知识来源，重新实现一个**唯一生产系统**，最终替代三个旧项目。
>
> 核心优先级：**提高收益 > 降低回撤 > 减少真实账户交易次数 > 工程质量**。
>
> 使用边界：
> - 只做 A 股 AI 产业链；
> - 现金多头，不使用杠杆/融资融券/卖空；
> - 收盘后运行，下一可交易日人工执行；
> - 不接券商自动交易；
> - 单票上限 60%，总仓位上限 100%，最大持仓数 6；
> - 所有生产判断必须因果、可回放、可审计。

---

## 1. 项目最终定位

新项目不是“三个策略投票器”，也不是“三个仓库打包器”，而是重新设计的统一系统：

```text
统一数据
   ↓
统一特征
   ↓
Opportunity Engine      Risk Engine
   ↓                       ↓
Leader / Trend / Recovery / Emerging Alpha
              ↓
       Portfolio Allocator
              ↓
      One Account Target
              ↓
       Execution Planner
              ↓
       Daily One-Page Report
```

最终用户每天只需要看一份结果：

1. 当前机会状态；
2. 当前风险状态；
3. 目标总仓位；
4. 最多 6 个持仓及目标权重；
5. BUY / ADD / HOLD / REDUCE / SELL；
6. 每个动作的主要原因；
7. 风险是否正在升级或恢复。

理论目标是：新项目上线后，用户不再需要运行 qwenquant、AQuant、trade。

---

## 2. 三个项目分别融合什么

### 2.1 qwenquant：生产骨架与低换手执行哲学

必须吸收：

- 单一 Router / 单一账户路径；
- 收盘信号 → 次日人工执行；
- Trend / Recovery / MeanReversion / Defensive 的状态化思想；
- Shock Event；
- 慢性恶化；
- mature winner 保护；
- 无交易区间；
- 经济批次；
- T+1 批次约束；
- 真实账户状态；
- 低频轮动；
- 生产门禁；
- 风险恢复迟滞与连续确认；
- “新机制不得破坏高收益主链”的消融与硬门思想。

不直接复制：

- 现有所有 preset；
- 固定 a/b/c/d/e 历史参数；
- 对未知股票池仍存在高尾部回撤的结构规则。

### 2.2 AQuant：Leader Intelligence 与研究优化框架

必须吸收：

- 固定 AI Reference Universe；
- leader score；
- mature leader / emerging leader 双通道；
- candidate A/B/C；
- relative strength；
- industry strength；
- breakout quality；
- drawdown resilience；
- core + satellite；
- leader tenure；
- challenger replacement；
- rotation edge；
- trade attribution；
- PBO / DSR；
- 参数邻域稳定性；
- nested / walk-forward；
- operating drawdown 与 capital drawdown 双口径；
- `manual_close_next_open` 作为第一生产执行模型。

不直接复制：

- fixed profile 的高收益参数；
- `resting_stop_intraday` 作为主生产模型；
- 大量 default-on 但真实路径零影响的功能。

### 2.3 trade：Risk Radar、Formal Stress 与股票池扰动鲁棒性

必须吸收：

- 独立于用户输入池的 AI 风险篮子；
- 子行业风险篮子；
- breadth；
- 同步下跌；
- MA20 破坏比例；
- correlation / cluster risk；
- low-frequency regime route；
- risk L1/L2/L3；
- tail guard；
- candidate confirmation；
- account-level risk；
- broker/account order 与内部信号分离；
- ProductionReplayEngine 思想；
- prefix / leave-one-out / add-one / random subsets / permutations；
- universe stress；
- fail-closed；
- optimizer promotion 必须绑定生产回放；
- 数据与代码 hash 写入验证证据链。

不直接复制：

- 三个 sleeve 各自持有真实资本；
- 内部高成交路径；
- 按股票数量大量离散分段的风险参数；
- 多个 risk overlay 同时拥有执行权。

---

## 3. 新项目的核心设计原则

### 3.1 只有一个账户

Alpha 模块只能输出：

```text
symbol
alpha_score
confidence
desired_direction
suggested_weight
time_horizon
reason
```

任何策略模块都不能直接拥有“自己的真钱账户”。

### 3.2 每只股票每天只有一个最终目标权重

例如：

```text
300308 -> 45%
300502 -> 25%
300394 -> 15%
688008 -> 0%
Cash   -> 15%
```

真实订单只由：

```text
target_weight - current_weight
```

产生。

禁止：

- 同股同日既买又卖；
- 一个模块加仓、另一个模块减仓；
- 三个策略分别成交；
- 风控层与策略层重复成交。

### 3.3 Opportunity 与 Risk 必须分离

Opportunity 回答“现在适合用什么 alpha”：

```text
STRONG_TREND
TREND
RECOVERY
CHOPPY
WEAK
```

Risk 回答“现在应该承担多少风险”：

```text
NORMAL
CAUTION
RISK_OFF
CRISIS
```

例：

```text
Opportunity = STRONG_TREND
Risk = CAUTION
```

含义：

- mature leader 继续持有；
- 暂停 Add2；
- 暂停低价值 rotation；
- 卫星仓停止新增；
- 不等于清仓。

---

## 4. 数据层设计

### 4.1 正式数据

生产支持：

- 本地 CSV；
- 至少一个联网 A 股数据源；
- 可选第二源交叉校验。

股票使用前复权 OHLCV，指数不复权。

### 4.2 数据契约

每个文件必须验证：

- 日期单调；
- 无重复日期；
- OHLC 合法；
- volume 非负；
- 最新日期一致；
- 停牌可解释；
- 数据 hash；
- 复权口径明确。

任何关键 reference 数据缺失：

```text
fail closed
```

不能把缺失当作 0、正常或低相关。

### 4.3 固定 AI Reference Universe

建议固定 30～50 只，不随用户输入池变化。

至少覆盖：

- 光模块；
- 光器件/光纤；
- 存储接口；
- 芯片设计；
- 国产算力；
- 半导体设备；
- 半导体材料；
- 晶圆制造；
- 封测；
- 测试设备；
- 数据中心配套。

用途：

1. Leader Score 横截面基准；
2. Risk Basket；
3. Subindustry Breadth；
4. 用户股票池结构评估。

禁止根据当前输入股票池动态改 reference。

---

## 5. 特征层

所有特征必须 point-in-time。

### 趋势特征

- 20/60/120 日收益；
- MA20/MA60/MA120；
- Donchian breakout；
- ATR；
- ADX；
- 趋势斜率；
- 5/20 动量差；
- 20/60 动量差。

### Leader 特征

- fixed reference percentile；
- 60/120 日 RS；
- industry RS；
- breakout quality；
- drawdown resilience；
- trend persistence；
- volume expansion；
- score confidence；
- history confidence。

### Risk 特征

- broad/tech index 3/5/10 日速度；
- AI basket breadth；
- below MA20 ratio；
- subindustry breadth；
- correlation shock；
- volatility shock；
- leader failure ratio；
- operating DD；
- capital DD；
- 持仓跌破成本比例；
- concentration / effective N。

---

## 6. Opportunity Engine

### STRONG_TREND

要求多证据：

- 指数中期趋势正；
- AI breadth 良好；
- leader 分数断层明显；
- Top leaders RS 强；
- Risk 非 RISK_OFF/CRISIS。

行为：

- mature leader 为主；
- 允许高集中；
- 允许 Add1；
- 高置信允许 Add2；
- rotation 只在替代优势明确时发生。

### TREND

- 2～5 个核心；
- Add1 正常；
- Add2 更严格；
- 少量 satellite；
- 不主动均值回归。

### RECOVERY

吸收 qwenquant：

- 市场速度修复；
- breadth 停止恶化；
- leader 扩散；
- 先 probe；
- 再确认；
- 失败 cooldown。

先用 20%～40%总风险，连续确认后升到正常 Trend。

### CHOPPY

- 保留真正 mature leader；
- 低频 rotation；
- 允许极受控 mean-reversion；
- 禁止追普通 breakout；
- 使用更宽 no-trade band。

### WEAK

原则：

```text
没有真正逆势 leader -> CASH
```

最多 0～2 个持仓。

候选必须：

- 绝对动量正；
- 相对指数强；
- 相对 AI reference 强；
- 行业不恶化；
- 结构修复。

---

## 7. Risk Engine

### NORMAL

不施加额外干预。

### CAUTION

触发需至少 2 类独立证据：

- AI basket breadth 急降；
- tech index 速度恶化；
- 子行业同步破坏；
- correlation spike；
- leader failure。

动作：

- 停 Add2；
- 停低价值 rotation；
- 停新增 satellite；
- mature leader 不动；
- 总仓不强制下降。

### RISK_OFF

要求更强确认：

- 风险证据持续；
- 组合 DD扩大；
- 多子行业破坏。

动作：

- 先减 satellite；
- 再减弱 core；
- 保留 mature；
- 总仓可降到 60%～80%。

### CRISIS

吸收 qwenquant Shock Event：

```text
SHOCK
FAILED_REPAIR
PERSISTENT_STRESS
RECOVERY
```

动作：

- 总仓 30%～60%；
- 极端时接近 CASH；
- 恢复必须连续确认；
- 不允许单日反弹立即满仓。

---

## 8. Risk Lead-Time 必须成为正式目标

每个风险事件记录：

- market peak；
- first CAUTION；
- first RISK_OFF；
- first risk reduction；
- DD 5%/10%/15%；
- crisis；
- recovery。

正式指标：

```text
lead_days_to_10pct_dd
lead_days_to_15pct_dd
false_positive_days
false_positive_events
bull_opportunity_cost
recovery_delay_cost
```

不能只追“更早”，必须同时惩罚误报、牛市提前减仓和恢复过慢。

---

## 9. Leader Intelligence

### Mature Leader

综合：

```text
leader_score =
  RS
+ industry_RS
+ trend_persistence
+ breakout_quality
+ resilience
+ confidence
```

Mature Leader 特权：

- 不因短期 rank drop 卖出；
- 不因轻微轮动边换出；
- 不做机械再平衡削弱；
- Risk NORMAL 时允许自然集中。

降级需要自身结构破坏或确认风险。

### Emerging Leader

适合：

- 新上市；
- 新主线；
- 历史不足；
- 新周期强突破。

只能作为 SATELLITE：

- 5%～12%；
- 最多1～2只；
- 5～10日内晋升 Core 或退出。

禁止长期小仓占资本。

---

## 10. Portfolio Allocator

### 最大持仓

硬上限 6，实际 K 为 1～6。

由：

- leader count；
- score gap；
- Opportunity；
- Risk；
- effective N；
- correlation；
- 行业集中；

动态决定。

### 有证据的集中

强趋势时可以：

```text
Top1 40%~60%
Top2 20%~35%
其他 0%~20%
```

不要机械持满 6 只。

### Effective N

计算实际独立风险下注数。

6只同一行业高相关股可能只等于2个风险下注。

低 effective N 时：

- 同簇新增需要更高 alpha；
- CAUTION 优先减重复因子；
- 正常牛市不强制卖 leader。

---

## 11. 仓位生命周期

第一版只保留：

```text
CORE
ADD1
ADD2
SATELLITE
RECOVERY
```

### CORE

主收益来源。

### ADD1

必须保留，因为完全取消加仓会损害长期复利。

### ADD2

要求：

- leader 仍强；
- Add1 已有正 MFE；
- Risk NORMAL；
- 行业健康；
- projected concentration 合格。

### SATELLITE

只用于新 leader 探索。

### RECOVERY

只存在于风险修复阶段。

第一版默认不做 Add3/Add4+，除非统一 benchmark 证明其跨池、跨 regime 正贡献。

---

## 12. Rotation

Rotation 不能只按排名。

```text
replacement_edge =
new_alpha
- old_alpha
- transaction_cost
- incumbent_winner_penalty
- same_cluster_penalty
- uncertainty_penalty
```

要求：

- 连续确认；
- 最小持有期；
- mature winner 保护；
- 20日 rotation 次数限制。

可低频选择：

```text
AGGRESSIVE_ALPHA
SELECTIVE
FROZEN
```

---

## 13. No-Trade / Hysteresis

至少三层：

1. 最小经济成交额；
2. 目标权重变化带，例如 `abs(target-current)<5%~8%` 不交易；
3. Replacement Edge 不足不换股。

---

## 14. 真实账户执行

第一生产模型只能是：

```text
close signal -> next tradable open
```

必须模拟：

- T+1；
- 涨跌停；
- 停牌；
- 100股；
- 科创板最小买入；
- 佣金；
- 最低佣金；
- 印花税；
- 过户费；
- 滑点；
- 成交额容量；
- 开盘 gap。

---

## 15. 账户状态

必须持久化：

- cash；
- positions；
- avg cost；
- entry date；
- highest close；
- tranche；
- T+1 sellable shares；
- operating peak；
- capital peak；
- risk/opportunity state；
- cooldown；
- candidate/leader tenure；
- shock event；
- pending actions；
- last successful run；
- data/code hash。

---

## 16. 每日最终输出

只输出一份，例如：

```text
Date: 2026-08-08

Opportunity: STRONG_TREND
Risk: CAUTION
Target Gross: 85%
Target K: 4

300308 HOLD       45%   Mature Leader
300502 ADD1→25%   +8%   Confirmed Leader
300394 HOLD       15%   Core
688008 BLOCKED     0%   Industry Risk
Cash              15%

Risk:
AI Basket L1
Optical NORMAL
Equipment L2
Shock NO
Operating DD 5.2%
Capital DD 7.8%

Tomorrow:
1. 300502 buy 300 shares if open executable
2. no new satellite
3. no rotation
```

用户不需要查看几十个中间指标。

---

## 17. 工程结构建议

不要过度拆文件。

```text
unified_ai_quant/
  config.py
  types.py
  data.py
  features.py
  leader.py
  opportunity.py
  risk.py
  portfolio.py
  execution.py
  engine.py
  account.py
  cli.py
  validation/
```

原则：

- 文件少；
- 决策纯函数优先；
- 状态只保留必要部分；
- 禁止多个版本目录；
- 禁止 `final/latest/v2/v3`；
- 不保留历史策略副本。

---

# 18. 详细实施步骤

## Phase 0：统一 Benchmark，暂时不写策略

先让三个旧项目在完全同一数据与执行模型下跑：

- common universe；
- common costs；
- common next-open；
- common windows；
- common metrics。

输出三项目统一基线。

**没有这一阶段，就不能证明新项目全面超越。**

## Phase 1：最小统一骨架

只实现：

- Data；
- AccountState；
- next-open execution；
- fees；
- T+1；
- Portfolio target；
- daily report；
- deterministic replay。

策略可以很简单。

目标：

```text
一个账户
一个订单层
一个回放器
```

## Phase 2：接入 qwenquant 核心 Trend + Recovery

只接：

- Trend；
- Recovery；
- no-trade zone；
- Core/Add1；
- Shock Event 基础状态。

先让新系统达到 qwenquant 的主要生产能力。

## Phase 3：接入 AQuant Leader Intelligence

加入：

- fixed reference；
- leader score；
- mature/emerging；
- A/B/C；
- leader tenure；
- replacement edge。

目标：

- 提高牛市收益；
- 降低无效轮动；
- 不增加 DD。

## Phase 4：接入 trade Risk Radar

加入：

- independent risk basket；
- subindustry basket；
- breadth；
- correlation；
- risk L1/L2/L3；
- tail evidence。

初期只影响：

- freeze add；
- freeze satellite；
- freeze low-edge rotation。

不立即砍核心。

## Phase 5：建立 Opportunity × Risk 双轴

把所有行为统一到两轴。

删除重复 risk control。

任何模块不得直接成交。

## Phase 6：统一 Portfolio Allocator

加入：

- effective N；
- leader conviction gap；
- dynamic K；
- projected cluster/industry weight；
- concentration；
- target hysteresis。

目标：

- 真实交易次数下降；
- 中大型池收益提高；
- add/drop 稳定。

## Phase 7：Capital DD / Tail Guard

吸收 AQuant 双回撤 + trade tail stress。

保证：

- operating drawdown 不覆盖 capital risk；
- capital risk 不因窗口滚出而消失；
- CRISIS 后恢复必须确认。

## Phase 8：严格 Validation / Promotion

每个模块只有：

```text
OFF
SHADOW
CANDIDATE
PRODUCTION
```

正式硬门通过才能进生产。

## Phase 9：移除旧项目依赖

最终新项目：

- 不 import qwenquant；
- 不 import aquant；
- 不 import trade；
- 不 shell 调用旧项目；
- 所有逻辑在新仓库独立实现。

旧项目只作为 benchmark。

---

# 19. 明确不能做 / 不应该做

1. **不能三项目投票**：禁止 `2 BUY > 1 SELL`。
2. **不能复制所有模块**：增加交易且不增收益、不降 DD 的功能必须删。
3. **不能保留三个真实账户**：只能有一个 AccountState。
4. **不能保留多个生产执行模型**：主生产只认 manual close → next open。
5. **不能把固定历史高收益参数直接搬过来**：必须重新统一验证。
6. **不能按股票数量硬切大量参数**：优先用 effective N、correlation、reference coverage、Opportunity、Risk。
7. **不能为了低回撤全局收紧止损**：成熟 AI leader 正常波动很大。
8. **不能为了高收益无限 pyramiding**：第一版最多 Add1/Add2。
9. **不能每天重选 leader**：必须 tenure + replacement edge。
10. **不能使用未来完整样本调动态参数**：所有选择必须 point-in-time。
11. **不能忽略幸存者偏差**：必须做 universe 扰动与历史可见性压力。
12. **不能修改黄金基线掩盖回退**：门禁失败就回退改造。
13. **不能让多个风险层拥有执行权**：Risk Engine 只输出唯一风险约束。
14. **不能用更高交易次数换小幅收益**：必须有明确经济边际。
15. **不能在最终 holdback 上继续调参**。

---

# 20. 最终成功定义

新项目只有满足以下条件才算成功：

1. 用户每天只看一个系统；
2. 牛市收益达到三个旧项目最强区域；
3. 熊市/震荡不比旧系统差；
4. 急跌风险识别更早；
5. DD 不高于现有最优一档；
6. 真实 account orders 接近或低于 qwenquant；
7. unknown pool stress 达到 trade 水平；
8. leader intelligence 达到 AQuant 水平；
9. add/drop 不敏感；
10. 完全不依赖旧项目运行。

---

# 21. 推荐开发优先级

第一优先：

```text
统一 Benchmark
统一账户
统一执行
统一 Target
```

第二优先：

```text
Mature Leader
Trend / Recovery
Risk Radar
```

第三优先：

```text
Dynamic K
Rotation
Satellite
Add2
```

最后才做：

```text
工程美化
复杂 UI
更多策略
```

---

# 22. 最终一句话

新项目真正应该融合的是：

> **qwenquant 的“低换手生产执行”  
> + AQuant 的“选对并拿住最强 Leader”  
> + trade 的“尽早发现系统性风险并经受股票池扰动压力”。**

而不是把三份代码合并到一个目录。
