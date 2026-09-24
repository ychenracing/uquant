# 当前系统

uquant 使用唯一日频生产决策链，为 A 股 AI 产业链现金多头账户生成盘后建议，下一交易日由人工核对执行。

- 当前安装与运行入口：[README](README.md)、[运行手册](docs/OPERATIONS.md)。
- 策略和权限：[策略](docs/STRATEGY.md)、[架构](docs/ARCHITECTURE.md)。
- 当前验收入口：[验收链](docs/ACCEPTANCE.md)。工程通过不代表经济达标，完整泛化能力应以适用身份的原始结果为准。
- 可变提交、PR、检查和验收状态从 GitHub 及对应运行产物读取。原始结果保留生产者身份，不改贴当前源码身份。
- Future Holdout 只接受真实、顺序追加的新交易日；尚未观察的数据不生成分数。

- 本轮成熟持仓资本研究：[交付报告](artifacts/targeted-mature-capital/DELIVERY_REPORT.md)。前四配对筛选为NO_GO，保留生产基线；恢复证据投影与夹具修复单独验证，未宣称完整Absolute通过。

- 工程收敛与当前验收：[交付记录](artifacts/operational-continuity/DELIVERY_REPORT.md)。修复观察事务日期/载体/互斥边界及 Absolute 配置身份投影；停止冻结样本收益搜索。已有完整 Absolute 原件已读取，数值通过声明与配置身份缺陷分别记录；未来正式观察仍为零。

- 代码审计 F01–F14 的历史交付：[初次交付](artifacts/code-audit-fix/DELIVERY_REPORT.md)、[完整验证与集成修复](artifacts/code-audit-fix/full-validation/DELIVERY_REPORT.md)。PR #83 是当时的状态入口，不代表最新 main 或检查结果。
- 候选 C 已合并；原正式 Absolute 八片和 final 为 34/34 单元、七组件通过。后续 57 项工程失败、静态检查与 Ownership 口径的逐项收尾见[工程验收收尾记录](artifacts/engineering-acceptance-closure/DELIVERY_REPORT.md)。当前有效冠军财富底线 15×；原 C 生产者和 5.26 GB 的原件身份保留，不能改贴当前 HEAD。实时 PR、检查和 main 仍以 GitHub 为准。
