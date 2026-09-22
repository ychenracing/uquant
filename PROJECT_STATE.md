# 当前系统

uquant 使用唯一日频生产决策链，为 A 股 AI 产业链现金多头账户生成盘后建议，下一交易日由人工核对执行。

- 当前安装与运行入口：[README](README.md)、[运行手册](docs/OPERATIONS.md)。
- 策略和权限：[策略](docs/STRATEGY.md)、[架构](docs/ARCHITECTURE.md)。
- 当前验收入口：[验收链](docs/ACCEPTANCE.md)。工程通过不代表经济达标，完整泛化能力应以适用身份的原始结果为准。
- 可变提交、PR、检查和验收状态从 GitHub 及对应运行产物读取。原始结果保留生产者身份，不改贴当前源码身份。
- Future Holdout 只接受真实、顺序追加的新交易日；尚未观察的数据不生成分数。

- 本轮成熟持仓资本研究：[交付报告](artifacts/targeted-mature-capital/DELIVERY_REPORT.md)。前四配对筛选为NO_GO，保留生产基线；恢复证据投影与夹具修复单独验证，未宣称完整Absolute通过。

- 工程收敛与当前验收：[交付记录](artifacts/operational-continuity/DELIVERY_REPORT.md)。修复观察事务日期/载体/互斥边界及 Absolute 配置身份投影；停止冻结样本收益搜索。已有完整 Absolute 原件已读取，数值通过声明与配置身份缺陷分别记录；未来正式观察仍为零。

- 代码审计 F01–F14 与完整检查：[初次交付](artifacts/code-audit-fix/DELIVERY_REPORT.md)、[完整验证与集成修复](artifacts/code-audit-fix/full-validation/DELIVERY_REPORT.md)。完整 Absolute 34/34 通过、应用覆盖率 87.43%；原失败记录与生产者身份保留。最新 PR/main 和排队中的完整重验状态见 PR #83，不把未完成 CI 记为通过。
