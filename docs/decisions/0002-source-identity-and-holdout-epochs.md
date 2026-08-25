# ADR 0002：源码身份与 holdout epoch

- 状态：Accepted
- 范围：发布包、冻结证据、Future Holdout 与恢复

## 决策

生产发布包只包含 `uquant*`。仓库内的研究工具、脚本、测试、文档、基准、工件与数据仍
用于复现和审查，但不是 wheel API。`requirements.txt`、冻结数据清单和源码注册表保持
`KEEP_AUTHORITATIVE`。清理候选只有在当前权限已经被权威文档、ADR 或同目录 evidence
替代，并保留可验证的 Git 恢复边界后，才能从 `UNRESOLVED_KEEP` 转为删除或迁移。

任何合法的源码或发布边界变化都创建新的 source epoch。Future Holdout、账户代码身份和
验证证据只能从该边界向前追加，遵守 no-backfill；不得用新文件、新依赖或后见市场数据
重写旧 epoch。当前 `production_wheel_v1` 已登记生产 wheel 成员、锁文件和源码摘要，
状态为 `ACTIVE_FOR_NEW_ACCOUNTS`；既有账户只能通过显式 code-identity migration 前进。

## 恢复与证据

高风险恢复从 `artifacts/architecture_refactor/baseline_inventory.json`、
`benchmarks/source_surface_registry.json` 和 `data/frozen/DATA_MANIFEST.json` 开始，先在
隔离副本中核对 Git 对象、字节大小与 SHA-256，再决定是否恢复。清理清单保留每个候选的
引用、权限理由和恢复命令；删除不是完成条件，也不能替代可验证的保留决定。

操作细节见[运行手册](../OPERATIONS.md)，质量门见[质量契约](../QUALITY.md)。
