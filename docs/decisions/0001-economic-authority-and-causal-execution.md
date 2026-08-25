# ADR 0001：经济权限与因果执行

- 状态：Accepted
- 范围：生产决策、风险、组合、执行与账户

## 决策

生产经济状态严格沿 `Decision → Order → Fill → AccountState` 推进。Base Risk 汇总市场、
持仓和资本证据，并唯一拥有风险派生总仓上限；`PortfolioAllocator` 在该上限内唯一拥有
目标权重。
执行层只落实既有目标，账户层只记录经过约束和对账的订单、成交与状态。

接受一个现有经济行为中的窄例外：账户仅持有单一战略主导者、风险为 `NORMAL/CAUTION`
一级预警、无 sector/strategic/acute guard，且策略本身不要求减仓时，组合层可以保留
既有持仓至战略主导者上限。该例外不新增风险、不创建第二风险状态机，并且不适用于
`CRISIS`、更高减仓等级、多持仓组合或任何明确风险退出。

该例外的机器可核对契约只有以下一个合取条件，文档中的“一级预警”均指这组谓词：

- `live_symbols == {dominant_symbol}`：实际账户只能有一个在持战略主导者；
- Risk 只能是 `NORMAL/CAUTION`，且 `reduction_level <= 1`；
- `sector_guard_active`、`strategic_damage_guard`、`acute_sector_evacuation` 必须全部为假；
- `target_gross >= current_gross`：策略目标不得主动减仓；
- 仅可把既有总仓保留至 `strategic_dominant_max_weight`，不买入补足、不扩大当前总仓。

Risk Sentinel 保持观察者。默认 `FREEZE_ONLY` 只允许冻结新增风险，不能直接卖出、扩大
风险上限、修改目标权重或写账户。报告、研究、验证与恢复工具同样不得建立平行权限。

## 理由与后果

单一所有者让风险收紧、卖出资金来源、T+1、部分成交和账户恢复都能沿稳定身份审计，
并防止机会证据覆盖风险上限。把单一战略主导者的一级预警保留例外写入 ADR，是为了让
已冻结实现、测试和权限说明一致，而不是扩大其适用范围。任何改变参数、阈值、权限或订单/成交语义的提案都必须作为
生产行为变更，以冻结经济证据重新验收；文档与模块拆分不能暗中改变这条链。

相关说明见[架构说明](../ARCHITECTURE.md)、[策略说明](../STRATEGY.md)和
[Risk Sentinel](../RISK_SENTINEL.md)。
