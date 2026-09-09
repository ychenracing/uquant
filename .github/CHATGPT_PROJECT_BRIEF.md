# ChatGPT Project Brief

> 本文件只保存长期稳定、仓库级的信息。当前任务、临时分支、SHA、测试状态和执行进度应保存在当前 Pull Request 正文中。

## 1. Project

- 项目名称：uquant
- GitHub 仓库：`ychenracing/uquant`
- 默认分支：`main`
- 技术栈：Python 3.12、uv、NumPy、pandas、pytest、Ruff、mypy
- 系统定位：面向 A 股 AI 产业链的现金多头、日频量化决策辅助系统。
- 操作边界：盘后决策、下一可交易日执行、人工核对与辅助下单；不连接券商自动下单，不使用杠杆，不做空，也不依赖盘中行情。

## 2. Purpose and Non-Goals

uquant 使用同一生产决策内核完成历史回放和日报决策，提供机会、风险、组合、执行、账户、独立 Risk Sentinel、Future Holdout 与可复现验证证据。

长期非目标：

- 自动托管账户、自动下单、盘中交易、做空或使用杠杆；
- 把 AI 产业链之外的研究直接扩大为生产证券范围；
- 让报告、研究、Sentinel 或执行层建立第二条决策、组合、风险或账户路径；
- 用历史回测承诺未来收益；
- 为使候选通过而改写冻结 champion、窗口、证券池、seed、统计口径或失败证据。

## 3. Architecture and Module Boundaries

生产链路为点时行情、因果特征、行业与领涨证据、机会与风险双轴、唯一目标组合、次日开盘订单意图、成交与账户状态。

- `uquant/application/`：`ProductionEngine.decide()`、回放、指标、归因和生产用例编排。
- `uquant/market/`、`data.py`、`features.py`、`reference*.py`：点时市场工作区、因果数据与共享截面上下文。
- `industry.py`、`leader.py`、`opportunity.py`：行业、领涨和机会状态证据。
- `uquant/risk/` 与相关风险模块：Base Risk 状态、资本损伤和唯一风险派生总仓上限。
- `uquant/portfolio/`：`PortfolioAllocator`、唯一目标组合、硬约束和持仓生命周期。
- `uquant/execution/`：订单规划、交易约束、费用、部分成交和挂单生命周期。
- `uquant/account/`：唯一生产账户状态、schema 8 编解码、身份校验、代码身份重绑定和原子持久化。
- `uquant/risk_sentinel/`：独立风险证据与窄 `FREEZE_ONLY` 映射，不是第二个风险 owner。
- `uquant/contracts/`、`uquant/validation/`：不可变合同、严格 JSON、数据与验收门。
- `research/`：调用方驱动的离线研究，不进入生产导入。
- `scripts/`：仓库内运维、观察和验证入口，不进入 wheel。
- `engine.py` 与公共委托入口：调用当前所有者，不承载第二套实现。

## 4. Non-Negotiable Constraints

- `ProductionEngine.decide()` 是日报与回放的共同决策入口。
- Base Risk 独占风险派生的 `target_gross_cap`；机会、Sentinel、组合和执行不得扩大该上限。
- `PortfolioAllocator` 是目标权重的唯一所有者。
- `AccountState` 是生产经济状态的唯一持久化 owner；Journal、Holdout 和 Sentinel 不得写第二账户。
- 信号只读取决策日及以前的数据，成交最早发生在下一可交易日开盘。
- 模拟并守卫 T+1、停牌、涨跌停、手数、费用、滑点、容量和部分成交。
- 生产经济统计只覆盖 AI-era；更早行情只能用于因果特征 warm-up。
- 只允许现金多头，不使用杠杆、不做空；总仓、单票、持仓数、行业集中度和流动性硬约束必须保留。
- Strategic Grant、Strategic Epoch、资本修复、universe 角色和 owner 唯一性必须保持确定性身份与失败关闭。
- 数据、账户、配置、源码身份、冻结证据或经济账本不一致时必须停止，不得猜测或静默修正。
- Future Holdout 只接受真实按序追加观测，禁止 backfill、调参或获取生产权限。
- 研究、质量和治理改动不得改变策略信号、目标、订单、成交、绩效口径或生产经济行为。

## 5. Authoritative Sources

- 项目定位、操作入口、结构与文档导航：`README.md`
- 仓库执行、授权、恢复与渐进式验证约定：`AGENTS.md`
- 权限、数据流、状态与模块所有权：`docs/ARCHITECTURE.md`
- 策略、机会、风险和组合语义：`docs/STRATEGY.md`
- 参数事实与治理分类：`docs/CONFIGURATION.md`
- 日常操作、账户与故障恢复：`docs/OPERATIONS.md`
- Future Holdout、Journal 与 no-backfill：`docs/HOLDOUT.md`
- 性能、泛化和证据边界：`docs/PERFORMANCE.md`
- 工程命令和提交检查：`docs/DEVELOPMENT.md`
- 质量与行为保持合同：`docs/QUALITY.md`
- Sentinel 权限与 Coverage：`docs/RISK_SENTINEL.md`
- 经济权限和源码身份决策：`docs/decisions/`
- 依赖与工具配置：`pyproject.toml`、`uv.lock`
- CI 定义：`.github/workflows/`

## 6. Standard Commands

以下命令由 README 与开发指南支持：

```bash
python -m pip install uv
uv sync --frozen --extra dev
uv run ruff check .
uv run mypy uquant scripts research
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run pytest --cov=uquant --cov-report=term-missing --cov-report=xml
uv run python -m compileall -q uquant scripts research tests
uv run bandit -q -r uquant research scripts
```

稳定最终候选的完整工程、性能、泛化、战略授冠和战略所有权验收必须按 `docs/DEVELOPMENT.md`、`docs/PERFORMANCE.md` 与对应 workflow 的当前命令执行。日常修改从 L1 直接受影响检查开始，只有证据不足时升级到 L2、L3 和 L4。

## 7. Important Paths

- `uquant/application/`：生产决策与回放编排。
- `uquant/config/`：配置模型、默认值、校验和治理分类。
- `uquant/market/`、`uquant/risk/`：点时市场与 Base Risk。
- `uquant/portfolio/`：唯一目标组合和持仓生命周期。
- `uquant/execution/`：订单、交易约束和成交。
- `uquant/account/`：账户身份与原子持久化。
- `uquant/risk_sentinel/`：独立只读风险证据。
- `uquant/contracts/`：共享合同和资源身份。
- `uquant/validation/`：数据、性能、泛化与证据门。
- `research/`：生产隔离的离线研究。
- `scripts/`：运维、观察、构建与验收入口。
- `tests/`：单元、集成、性质、架构与失败路径测试。
- `data/frozen/`：冻结生产验证数据。
- `benchmarks/`、`artifacts/`：验收合同、基线与历史证据。
- `.github/workflows/`：工程、发布候选与各类经济验收自动化。

## 8. CI and Acceptance Entry Points

Pull Request 和默认分支自动化包括：

- Engineering：质量、安全、测试分片、Windows smoke 与汇总结论。
- Strategic Grant Acceptance：授冠意图、schema 8 账户、恢复与资格路径。
- Strategic Ownership Acceptance：多 epoch、successor、universe 角色、资本修复、重复授冠和失败恢复。
- Performance Acceptance：完整生产性能、精确源码身份与 provenance。
- Absolute Generalization Acceptance：固定八 shard、34 个 canonical LOO 场景与 production
  recovery/reachability raw evidence 的自动阻断聚合；最终通过是 runner 与七组件能力的合取。
- Extended Economic Matrix Diagnostics：原六窗口 234-cell 相对泛化矩阵，仅手动诊断；
  compile anchor 只证明冻结策略/参考身份，不能冒充当前能力结果。
- 扩展经济矩阵保留为手动 workflow，不得以删减场景替代完整验收。

Definition of Done：适用工程与经济门通过；分支覆盖率满足仓库合同；证据绑定待验收源码、配置、数据和运行时；未运行项明确标记；没有未解决的正确性、安全、数据完整性、经济回归或阻断审查问题。

## 9. Prohibited Actions

- 不得建立第二个生产决策入口、风险上限 owner、组合 owner、账户状态或执行经济真相。
- 不得让 Opportunity、Sentinel、报告、研究或执行层扩大 Base Risk 上限。
- 不得让研究输出直接写入生产配置、账户、目标、订单或成交。
- 不得绕过点时数据、T+1、交易约束、数据摘要、源码身份或失败关闭。
- 不得改写冻结 champion、官方窗口、证券池、seed、统计口径、验证合同或失败证据以获得绿色。
- 不得回填 Future Holdout、伪造 Journal、占位基线、测试结果或券商事实。
- 不得提交账户状态、券商快照、执行 Journal、访问凭据、密钥或其他用户数据。
- 不得从陈旧构建目录生成身份发布物，或让研究、脚本、测试、证据和冻结数据进入生产 wheel。
- 不得擅自改写 Git 历史、force push、丢弃未知工作或覆盖无关改动。
- 不得根据旧聊天猜测当前分支、SHA、PR 或 CI 状态。

## 10. Context Loading Protocol

1. 新开发任务可以直接使用自然语言提出，不要求预先填写固定 Prompt。
2. 涉及仓库判断或实施时，先读取根目录及适用的 `AGENTS.md`，再按任务需要读取本文件与相关权威文档。
3. 搜索与任务相关的开放 PR、分支和 Issue。
4. 如果存在匹配工作，从现有现场原地继续。
5. 当前动态任务状态默认维护在 Pull Request 正文。
6. 不强制普通单 PR 任务创建 Issue。
7. 优先读取目标代码、直接调用者、相关测试和直接相关配置。
8. 只有证据不足、状态冲突或影响范围扩大时才扩大读取。
9. 不默认加载完整仓库、完整聊天、完整日志或全部 GitHub Actions 历史。
10. 长对话交接按需使用可用的 `conversation-continuity-guard`；技能不可用时按 `AGENTS.md` 保存进展和交接，不因此阻断可继续的工作。GitHub 当前现场仍是远端状态权威来源。

## 11. References

- `README.md`
- `AGENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/STRATEGY.md`
- `docs/CONFIGURATION.md`
- `docs/OPERATIONS.md`
- `docs/HOLDOUT.md`
- `docs/PERFORMANCE.md`
- `docs/DEVELOPMENT.md`
- `docs/QUALITY.md`
- `docs/RISK_SENTINEL.md`
- `docs/decisions/`
- `pyproject.toml`
- `.github/workflows/`
