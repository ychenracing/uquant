# 当前系统

uquant 使用唯一日频生产决策链，为 A 股 AI 产业链现金多头账户生成盘后建议，下一交易日由人工核对执行。

- 当前安装与运行入口：[README](README.md)、[运行手册](docs/OPERATIONS.md)。
- 策略和权限：[策略](docs/STRATEGY.md)、[架构](docs/ARCHITECTURE.md)。
- 当前验收入口：[验收链](docs/ACCEPTANCE.md)。工程通过不代表经济达标，完整泛化能力应以适用身份的原始结果为准。
- 可变提交、PR、检查和验收状态从 GitHub 及对应运行产物读取。原始结果保留生产者身份，不改贴当前源码身份。
- Future Holdout 只接受真实、顺序追加的新交易日；尚未观察的数据不生成分数。
