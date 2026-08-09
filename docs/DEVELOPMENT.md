# uquant 开发指南

## 环境

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,data]'
```

## 修改位置

| 需求 | 应修改 |
|---|---|
| 数据源、字段或点时边界 | `uquant/data.py` |
| 指标和因果特征 | `uquant/features.py` |
| 行业汇总证据 | `uquant/industry.py` |
| 领涨评分或行业映射 | `uquant/leader.py` |
| 机会状态 | `uquant/opportunity.py` |
| 持仓同步冲击证据 | `uquant/risk_sector.py` |
| 风险状态和唯一仓位上限 | `uquant/risk.py` |
| 目标权重总编排 | `uquant/portfolio.py` |
| 组合硬约束和状态工具 | `uquant/portfolio_core.py` |
| 战略、领涨、恢复策略 | `uquant/portfolio_strategic.py`、`portfolio_leaders.py`、`portfolio_recovery.py` |
| 市场微观规则和成交 | `uquant/execution.py` |
| 券商导入和对账 | `uquant/broker.py` |
| 命令行编排 | `uquant/cli.py` |
| 展示文字 | `uquant/report.py` |
| 数据与策略发布门 | `uquant/validation/`、`benchmarks/` |

不要在日报、CLI 或券商同步中添加独立策略判断。

## 编码约定

- Python 3.11+，类型标注覆盖公开接口；
- 生产配置使用不可变 dataclass；
- 公开类和函数说明职责、输入边界或状态副作用；
- 注释解释因果、状态所有权和市场约束，不重复代码表面含义；
- 证券代码进入核心逻辑前统一规范化；
- 金额、股数、权重和日期校验应尽早失败；
- 生产状态更新必须确定、可序列化、可重复回放；
- 不使用未来数据补齐当日特征或成交价格；
- 不静默吞掉数据、账户或执行错误。

## 测试层次

### 快速检查

```bash
uv run ruff check .
uv run mypy uquant scripts
uv run python -m compileall -q uquant scripts tests
uv run python -m uquant.validation data-manifest --data-dir data/frozen
```

### 完整测试

```bash
uv run pytest --cov=uquant --cov-report=term-missing
```

覆盖率按分支统计，仓库门槛为 85%。生产包使用 strict mypy；Ruff 同时检查错误、导入、bugbear、升级、简化、性能和 Ruff 专属规则。

核心测试覆盖：

- 数据格式、证券规范化、点时特征和领涨评分；
- 账户哈希、历史追加、历史改写拒绝和决策确定性；
- T+1、涨跌停、停牌、容量、费用、部分成交和订单状态；
- 券商成交幂等、可卖数量和账户权威字段；
- 机会迟滞、动态持仓数、加仓、替换和轮动预算；
- 冲击、恢复、资本回撤、集中破坏和锚点生命周期；
- 行业信号的顺序不变性、稀疏覆盖收缩和换挡确认；
- 持仓同步冲击的确认、持久化与确认修复；
- 账户 schema 显式迁移、冻结清单和晋级基线失败路径；
- Hypothesis 生成的有效持仓数、费用单调性和证券代码规范化性质。

### 安全与构建

```bash
uv run bandit -q -r uquant
uv export --frozen --no-dev --no-emit-project --no-hashes \
  --output-file /tmp/uquant-requirements.txt
uv run pip-audit --requirement /tmp/uquant-requirements.txt
uv run python -m build
```

CI 在 Python 3.11 和 3.12 上运行质量门，并由 Dependabot维护 Python 与 GitHub Actions 依赖。生产依赖审计只读取锁定导出，不把开发工具误计为运行依赖。

## 新增策略规则的检查清单

1. 规则是否只依赖决策日及以前的数据？
2. 它属于机会、风险、组合还是执行层？
3. 是否引入第二个目标权重所有者？
4. 是否可能绕过总仓、单票、持仓数或 T+1？
5. 是否需要持久状态，状态能否完整序列化？
6. 同一输入和账户能否得到相同决策摘要？
7. 数据缺失时是安全失败还是无声降级？
8. 是否增加无效换手或重复订单？
9. 是否补充正常、边界、反例和状态连续性测试？
10. 文档、参数说明和日报文字是否与实际行为一致？
11. 是否在版本化晋级矩阵中同时约束财富、回撤、真实订单和换手？

## 回放检查

影响策略或风险的修改至少应检查：

- 一个连续多年度窗口；
- 一个强趋势窗口；
- 一个快速下跌与修复窗口；
- 一个震荡或轮动窗口；
- 不同股票池规模；
- 订单、费用、回撤和未成交意图。

回放结果用于发现退化和路径错误，不能替代真实未来数据。参数修改应基于多个窗口和多个股票池，不应为单个日期写特例。

策略候选必须运行：

```bash
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --baseline benchmarks/promotion_baseline.json \
  --profile quick
```

`full` 配置用于发布前或定时验证。更新基线不是修复失败的手段；只有冻结数据、执行口径或已经通过评审的生产结果变化时，才可同时更新数值和来源说明。

## 发布前检查

```bash
uv run ruff check .
uv run mypy uquant scripts
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts tests
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run python -m uquant.validation promotion --data-dir data/frozen --profile quick
uv run bandit -q -r uquant
uv run python -m build
uquant --help
uquant backtest --help
```

同时确认：

- `git status` 中没有账户、日报、临时结果或缓存；
- README 命令与 CLI 一致；
- 包名、命令名和导入路径均为 `uquant`；
- 数据文件未被无意改写；
- 新代码没有改变硬风险约束；
- 生产账户升级步骤已经准备并人工核对。
