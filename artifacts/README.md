# 历史证据索引

`artifacts/` 保存冻结运行结果、接受/拒绝决定、诊断样例和可恢复证据。它们用于回答“当时
验证了什么”，不是当前生产配置、操作命令或权限的权威来源。当前系统以 [README](../README.md)、
[架构说明](../docs/ARCHITECTURE.md)、[运行手册](../docs/OPERATIONS.md)、
[性能与证据](../docs/PERFORMANCE.md)和[Risk Sentinel](../docs/RISK_SENTINEL.md)为准。

| 证据集 | 用途 | 权威级别 | 当前说明 |
|---|---|---|---|
| [四系统当前 HEAD 基线](current_heads/analysis.md) | 冻结横向比较与后续归因起点 | `HISTORICAL_EVIDENCE` | [性能与证据](../docs/PERFORMANCE.md) |
| [Future Holdout 零观察基线](holdout/README.md) | Lane/Journal 静态合同 | `HISTORICAL_EVIDENCE` | [Future Holdout](../docs/HOLDOUT.md) |
| [Phase 1 修复前证据](phase1/before/README.md) | 修复前后诊断 | `HISTORICAL_EVIDENCE` | [性能与证据](../docs/PERFORMANCE.md) |
| [Phase 2 消融结论](phase2/ablations/conclusions.md) | 子系统保留/删除证据 | `HISTORICAL_EVIDENCE` | [策略与风控](../docs/STRATEGY.md) |
| [Phase 2 最终接受](phase2/final-acceptance.md) | 当时的接受与 provenance | `HISTORICAL_EVIDENCE` | [性能与证据](../docs/PERFORMANCE.md) |
| [Sentinel Evidence Closure](sentinel/evidence_closure/README.md) | 证据家族增量闭合 | `HISTORICAL_EVIDENCE` | [Risk Sentinel](../docs/RISK_SENTINEL.md) |
| [Sentinel Freeze-only](sentinel/freeze_only/README.md) | `FREEZE_ONLY` 经济等价证据 | `HISTORICAL_EVIDENCE` | [Risk Sentinel](../docs/RISK_SENTINEL.md) |
| [Sentinel Risk Differential](sentinel/risk_differential/README.md) | 三方差分、反事实与拒绝决定 | `HISTORICAL_EVIDENCE` | [Risk Sentinel](../docs/RISK_SENTINEL.md) |
| [Risk Differential 人类分析](sentinel/risk_differential/analysis.md) | 冻结差分结果解释 | `HISTORICAL_EVIDENCE` | [Risk Sentinel](../docs/RISK_SENTINEL.md) |
| [`production_wheel_v2` 身份证据](architecture_refactor/source_epoch_v2.json) | 当前非经济源码身份、wheel 与迁移边界 | `KEEP_AUTHORITATIVE` | [源码身份 ADR](../docs/decisions/0002-source-identity-and-holdout-epochs.md) |

静态日报 Markdown 只是冻结输出样例，不定义当前 renderer schema：
[Evidence Closure 样例](sentinel/evidence_closure/daily_report_example.md)和
[Sentinel Phase 6 样例](sentinel/phase6/daily_report_sentinel_sample.md)。读取样例前必须先核对
其相邻 machine seal、提交和日期；日常输出字段以当前 `uquant daily` 为准。

历史证据不得为适配当前 HEAD 而重写数值。若当前代码、数据、配置或运行时身份变化，应生成
新的前向 evidence/epoch，并保留旧文件；无法确认用途或引用时使用 `UNRESOLVED_KEEP`，不要
用删除制造“清理完成”。
