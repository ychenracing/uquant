# 实现修正（非经济修订）

1073ae1 首版删除 recovery_cohort_locked 写入，但 risk_reduction._risk_retention_vector 也读取此字段。违背本轮保留风险规则的边界。其2024结果2.7633x不能作为正式候选验收；首次分配差异仍可诊断，但后续风险归因存在混杂。原件保留 c1/，中止后续无效批次与分歧观察，不改写已有结果。

恢复原 _prune_anchors 与 _commit_recovery_cohort 对该字段的写入，并使统一分配后的实际已资助成员清理同步更新此字段。风险保留优先级使用同一规则，不能消除持久字段；只取消分配器读取此字段来阻止新机会。无风险源码修改。资金清理对应原 _fund_members 调用 _prune_anchors 的时机。目标验收不变，重新运行同一假设。
