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


## PR #92 改造续作（2026-09-25）

当前任务入口：[PR #92](https://github.com/ychenracing/uquant/pull/92)，分支
`cursor/uquant-remediation-59b9`；实时 HEAD、required checks、扩展矩阵的 run ID、
最终结果和原件位置在该 PR 更新，不以本文保存时的状态代替实时检查。

- 当前授权保留 95% strategic owner、15× 与 23.284× 门槛；不增加前瞻等待、单票集中度或
  剔除赢家门禁。方案分支 `50213d6` 中与此冲突的建议不执行。
- 已有约 4,186 + 725 项、ruff/mypy 通过是上一个代理在 `a1c2d39` 的报告，本轮不冒充重新全测。
- 复用 `artifacts/remediation-20260925/` 的诊断，生产者 `2626035`、Python 3.12.3，
  范围为指定历史池和窗口，不能当作最终 HEAD 的正式经济验收。
- 配股结论：暂不支持；原因、误记风险与停用边界见 `docs/OPERATIONS.md`。
- 机制去留：保留 `confidence_sizing_enabled`、`industry_rotation_enabled`、
  `regime_factor_blend_enabled`。两组消融没有成交差异，但真实调用仍分别承担成熟周期定仓、
  行业接替和领涨因子倾斜。未证明所有可达状态都等价；本 PR 不删除，不创建无改动候选，
  不将两组结果冒称完整删除验收。将来若删除须另建候选并完整验证原门槛。
- 真实分红/送转：本地 Baostock 登录失败原件保留。远端 `3271d27` / run `36156642533`
  生成独立 2024 年快照，4 次真实分红（含 2 次转增）验证通过；使用人工构造研究持仓，
  非真实券商账户，不代表所有税务身份。快照、账户和日志 ZIP 已校验长度及 SHA-256。
  结果见 `artifacts/remediation-validation-20260926/real-company-actions.json`。
- 哨兵最初因浅克隆缺少固定证据提交 `7fcf9562` 失败，补取后读回验证测试通过。
  全部正式矩阵结果未出齐前，不宣称 15×/23.284× 或完整泛化验收通过。
- `3271d27` / Python 3.12.13 的 34 股回放（2023-01-03 至 2026-06-30）：29.5525×，
  最大回撤 26.6430%，成交账户订单 40。原始输出压缩保存并读回一致。参考策略复用既有
  原始身份，不冒充重跑；详见 `artifacts/remediation-validation-20260926/relative-full34.json`。
  点时股票池未研究，固定历史池选择偏差仍然存在。
- `3271d27` 扩展 Performance 失败：历史读回浅克隆缺证据提交；无回撤路径的 Calmar
  合法为空，却被报告器强制当数值。修复完整拉取及空值报告，不替换为零或更改门槛。
  Absolute 旧提交 `f623a88` 的恢复投影拒绝新增竞价未成交事件；仅补合法活动状态。
  修复后的最终 SHA、重跑和失败原件以 PR 为准，当前不宣称完整验收通过。

本轮运行记录使用 `.cloud-task-journal`，本地原件只是临时副本；已回读的远端保全状态在 PR 记录。
