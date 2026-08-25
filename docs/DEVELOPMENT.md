# 开发指南

## 环境

项目只支持 Python 3.12；本地开发、CI、绩效复现和证据生成必须使用同一 3.12 锁定环境。
产品仍是 2023+ A 股 AI 产业链的人工日频决策辅助；更早行情只作特征 warm-up，
开发工具不得把研究输出接入人工账户或下单路径。

```bash
python -m pip install uv
uv sync --frozen --extra dev
```

若默认缓存目录不可写，可为单次任务指定专用目录：

```bash
UV_CACHE_DIR=/tmp/uquant-uv-cache uv sync --frozen --extra dev
```

依赖必须来自 `uv.lock`。不要在不同检查路径中混用未锁定依赖。

## 代码边界

- `uquant/` 是生产包，不能导入 `research/`；
- `ProductionEngine.decide()` 是日报和回放的共同决策入口；
- `PortfolioAllocator` 是目标权重的唯一所有者；
- `RiskAssessment.target_gross_cap` 是总仓上限的唯一出口；
- `report.py` 只渲染，不改变账户或组合；
- `research/` 接收调用方提供的观测和回调，不写生产配置；
- `validation/` 失败关闭，不创建占位基线；
- 配置默认值只定义在 `SystemConfig`。

新增模块前先确认职责不能放入现有边界。不要复制第二套决策、执行或账户逻辑。

构建发布物时，setuptools 只发现 `uquant*`。`research/` 是仓库内离线工具，不是安装后
可依赖的公共包；脚本、测试、证据、冻结数据和文档也不进入 wheel。`requirements.txt`
及 `full_package_v1` 保持 `KEEP_AUTHORITATIVE`；当前 `production_wheel_v1` source epoch
已登记 wheel 边界。后续发布面变化必须创建新 epoch，并按 no-backfill 向前追加身份。

## 常用检查

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run pytest --cov=uquant --cov-report=term-missing --cov-report=xml
uv run python -m compileall -q uquant scripts research tests
uv run python -m build
uv run bandit -q -r uquant research scripts
uv export --frozen --no-dev --no-emit-project --no-hashes \
  --output-file /tmp/uquant-requirements.txt
uv run pip-audit --requirement /tmp/uquant-requirements.txt
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --profile full \
  --output benchmarks/ai_era_performance.json
```

`benchmarks/ai_era_performance.json` 是从待验收 HEAD 生成的 CI artifact，已被
Git 忽略；发布证据必须由 checkout 后的命令重建，不能提交一份自引用旧结果。

分支覆盖率门槛为 85%。任何命令失败都应单独处理，不能由另一项成功抵消。

### 独立 CI 结论

每个 PR 和 `main` push 必须得到以下三个稳定结果：

| 必需结论 | 组成 |
|---|---|
| `Engineering` | `quality`、`security` 与原生 `Windows smoke` 都成功后才成功；summary 总是运行 |
| `Phase 1 Performance` | 未删减的 `promotion --profile full`、精确 HEAD 与完整 provenance |
| `Phase 2 Generalization` | 六个官方窗口分片全部完成后的 234-record policy/evidence 聚合 |

不得为必需结论添加 path filter、`continue-on-error`、失败转成功或可取消矩阵。失败
分片也必须上传 JSON；`if: always()` aggregator 下载精确六件 artifact，拒绝缺失、
额外、旧 HEAD、来源/配置/数据/runtime/universe/industry 身份漂移及任何 policy 失败。
`Engineering gates` 也支持从 GitHub Actions 手动触发；quality 使用
`validate-static-lanes` 验证跟踪的零观察基线，并验证默认真实执行 Journal、checkpoint、
本地 Lane 报告和生产观察备份保持未跟踪，哈希链与单命令入口可加载，然后继续执行完整测试
和覆盖率门。本地非零观察只由 `report-lanes` 生成，不能反写静态 CI 工件。
生产观察入口的回归合同还覆盖：输出/备份路径及硬链接别名在副作用前拒绝、非空 Journal
必须有可信 checkpoint、运行前备份即时读回、最终 receipt 被 manifest 绑定，以及同一
仓库/账户的完整事务跨进程串行化。

## 测试放置

| 改动 | 至少需要的测试 |
|---|---|
| 特征或评分 | 手算边界、缺失历史和因果时点 |
| 风险状态 | 触发、确认、恢复、复发和上限 |
| 组合分配 | 总仓、单票、持仓数、集中度和确定性 |
| 执行 | T+1、涨跌停、手数、费用、现金和部分成交 |
| 账户 | 序列化、原子失败、引用一致性和幂等 |
| 数据 | 重复日期、坏值、前缀摘要和追加 |
| 验证 | 缺字段、重复键、摘要漂移和运行中修改 |
| 缺陷修复 | 先复现失败，再实现最小修复 |

测试应验证外部可观察行为，不要只断言私有实现或源码文本。预期值应独立手算，避免用被测逻辑生成自己的期望。

## 注释与文档

- 模块 docstring 说明职责和边界；
- 公共类、函数和方法说明契约、返回值、副作用与重要异常；
- 复杂私有函数说明不变量和失败方式；
- 行内注释解释因果时点、状态机、交易限制和非显然业务原因；
- 不逐行翻译代码，不在注释中记录开发时间线；
- 修改行为时同步更新 README、主题文档和参数说明；
- 示例命令必须使用真实 CLI 参数并能在仓库根目录运行。

## 策略改动流程

1. 明确要改善的指标和不可退化指标；
2. 写出失败用例或可复现证据；
3. 做最小改动并运行直接受影响的测试；
4. 在有意义的 milestone 运行受影响模块、场景或窗口；
5. 候选字节冻结后运行一次完整 Engineering；
6. 运行不可拆分的 full AI-era Phase 1 绩效门；
7. 运行六个固定 2023+ 窗口的 Phase 2 Generalization 门，不改变种子、池或样本失败；
8. 审查配置、代码、日报、归因和文档是否一致。

参数搜索只能生成候选，不能自动写入生产默认值。最终选择必须有独立验证证据。

## 工程质量改动流程

对重命名、拆分、注释、文档和工具改动：

1. 明确受影响文件、链接、命令和治理合同；
2. 批量完成同一主题修改；
3. 若触及 Python，使用去除 docstring 的 AST 比较可执行结构；
4. 运行链接、术语、命令和受影响测试；
5. 只有共享构建或可执行输入发生变化时才扩大到完整质量门；
6. 审查是否意外触及默认配置、数据、信号、仓位、订单或统计口径。

无法证明等价时，把它视为策略改动并执行更严格的经济性验证。

## 代码审查清单

- 输入是否在边界处校验，错误是否包含足够上下文；
- 日期、参考成员和特征是否严格点时；
- 状态是否只有一个所有者并被完整持久化；
- 风险上限是否可能被机会或恢复路径覆盖；
- 买入是否可能在资金来源卖出失败时提前执行；
- T+1、涨跌停、容量和部分成交是否保持意图一致；
- 集合和字典遍历是否影响确定性；
- 异常是否被过宽捕获或静默忽略；
- 文件写入是否原子，外部输入是否拒绝不安全路径；
- 测试是否覆盖失败路径，而非只覆盖正常路径；
- 注释是否解释原因，文档是否与当前默认值一致。

审查意见必须先验证实际调用路径。可能改变策略行为的修复需要独立证据，不能混入纯质量任务。

代码/测试/helper 的物理行数、函数长度、branch point 与 CLI 行数是治理信号，用来决定
未来是否值得重构，不是为了 998/999/1000 行反复压缩的单独发布门。生产经济、数据完整性、
单一权限、源码身份、Packaging、Windows、CI 和明确接受项仍是硬阻断；private-import
scanner 只约束真实仓库的开发期架构边界，不是任意 Python 对象图的安全沙箱。

## 提交前

```bash
git diff --check
git status --short
```

确认：

- 没有未解释的可执行语义变化；
- 没有修改冻结数据或基线；
- 没有放宽测试、覆盖率或验证条件；
- 没有把 2023 年以前的 warm-up 行计入经济指标或发布门槛；
- 没有改变 canonical 34-stock AI universe、固定随机池、attribution 或 holdout 合约；
- 文档和代码使用一致术语；
- 新公共接口有清晰契约；
- 验证范围与实际影响相称，并有本次运行证据。
