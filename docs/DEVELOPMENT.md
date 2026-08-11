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
| point-in-time 参考注册表与共享参考证据 | `uquant/reference_registry.py`、`uquant/reference.py` |
| 领涨评分、未知行业因果推断 | `uquant/leader.py` |
| 机会状态 | `uquant/opportunity.py` |
| 持仓同步冲击证据 | `uquant/risk_sector.py` |
| 动态风险锚、连续退化、风险状态和唯一仓位上限 | `uquant/risk.py` |
| 目标权重总编排 | `uquant/portfolio.py` |
| 组合硬约束和状态工具 | `uquant/portfolio_core.py` |
| 动态战略 epoch、领涨/scout、恢复策略 | `uquant/portfolio_strategic.py`、`portfolio_leaders.py`、`portfolio_recovery.py` |
| 市场微观规则和成交 | `uquant/execution.py` |
| 券商导入和对账 | `uquant/broker.py` |
| 命令行编排 | `uquant/cli.py` |
| 展示文字 | `uquant/report.py` |
| 数据与策略发布门 | `uquant/validation/`、`benchmarks/` |
| 离线候选搜索、消融、压力和退出归因 | `research/`；不得由 `uquant/` 导入 |

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
uv run mypy uquant scripts research
uv run python -m compileall -q uquant scripts research tests
uv run python -m uquant.validation data-manifest --data-dir data/frozen
```

### 完整测试

```bash
uv run pytest --cov=uquant --cov-report=term-missing
```

覆盖率按分支统计，仓库门槛为 85%。生产包使用 strict mypy；Ruff 同时检查错误、导入、bugbear、升级、简化、性能和 Ruff 专属规则。

核心测试覆盖：

- 数据格式、证券规范化、点时特征和领涨评分；
- point-in-time 参考注册表边界、未来成员拒绝和共享 `ReferenceContext` 的行业均衡证据；
- 账户哈希、历史追加、历史改写拒绝和决策确定性；
- T+1、涨跌停、停牌、容量、费用、部分成交和订单状态；
- 券商成交幂等、可卖数量和账户权威字段；
- 机会迟滞、动态持仓数、加仓、替换和轮动预算；
- `SECULAR` / `EMERGING_SECULAR` 3/2/1 战略 cohort 的证据发现、签名确认、完整退出、冷却和下一 epoch；
- 战略风险压缩逐票 restore、容量阻塞成员不丢失恢复意图和最终策略退出不复活；
- 冲击、恢复、资本回撤、集中破坏和锚点生命周期；
- 动态风险锚、连续 transition/chronic 信号、资本预算阶梯和三级减仓；
- 行业信号的顺序不变性、稀疏覆盖收缩和换挡确认；
- 持仓同步冲击的确认、持久化与确认修复；
- 稀疏 sell-only 风险去仓、`RISK_PRIORITY` late-add-first 和普通 FIFO 不变；
- `CHOPPY`/`WEAK` 对已有及新增领涨目标的最终机会仓位硬上限；
- 高置信仓位、conviction 联合证据门/不合格等权回退、闲置现金 challenger scout 和 incumbent 不被强卖；
- 稳定生产参考与研究扩展参考隔离，研究 tuple 不改变生产篮子或历史补全义务；
- 账户 schema v3 显式迁移、冻结清单和晋级基线失败路径；
- 泛化场景/PDI/分位数、重复或缺失 reference，以及完整竞品来源/矩阵/执行契约失败路径；
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
12. remove-one/pairs/all、no-optical、行业池、random 6/12/24 和 leave-top-k 是否都通过，PDI 与尾部分位数是否退化？
13. leave-top-k 是否只使用窗口前证据，稀疏行业是否诚实标记为 singleton/combined diagnostic？
14. 全周期竞品 gate 是否使用同一数据与执行契约、完整 105 单元和可核验 commit/adapter/raw evidence？
15. 新账户状态是否进入 schema v3 迁移、序列化、确定性摘要和恢复测试？

## 回放检查

影响策略或风险的修改至少应检查：

- 一个连续多年度窗口；
- 一个强趋势窗口；
- 一个快速下跌与修复窗口；
- 一个震荡或轮动窗口；
- 不同股票池规模；
- 移除历史强势证券、no-optical、行业单池/平衡池和确定性随机池；
- 订单、费用、回撤和未成交意图。

回放结果用于发现退化和路径错误，不能替代真实未来数据。参数修改应基于多个窗口和多个股票池，不应为单个日期写特例。

## 离线研究 primitives

`research/` 是仓库本地 Python API，没有独立 CLI，也不会由生产包导入：

- `candidate_runner.py`：以唯一生产引擎生成逐日不可变决策轨迹、首个分歧和完整候选矩阵；
- `statistics.py`：确定性 walk-forward folds、PBO 与 deflated-Sharpe 诊断；
- `candidate_search.py`：确定性共享参数网格、回放观测聚合、dominance 与 Pareto 门；每个 pool/scenario 必须接收同一份扁平配置；
- `ablation.py`：一次关闭一个能力并比较财富、回撤和订单差异；
- `parameter_stress.py`：单参数或受限 factorial 扰动；
- `universe_stress.py`：remove-one/pairs/all、行业、平衡、确定性随机和由调用方提供窗口前排名的 leave-top-k case；
- `trade_attribution.py`：只对已经完成的退出计算 5/10/20/40 日后悔、避免损失和相对收益，不参与生产决策。

可在仓库根目录直接导入，例如：

```bash
uv run python -c 'from research import enumerate_candidates; print(enumerate_candidates({"weak_gross": (0.20, 0.25)}))'
uv run pytest -q tests/test_research.py
```

候选搜索的调用方负责把真实 replay callback 转成 `ReplayObservation`；这些 primitives 不写 `SystemConfig`、baseline 或 reference，也不替代 `uquant.validation` 的 production gates。研究扩展证券只能放在 `EXPANDING_RESEARCH_REFERENCE`/`RESEARCH_REFERENCE_UNIVERSE` 中观察；晋级到生产必须显式修改 `STABLE_REFERENCE_UNIVERSE` 并补齐数据、清单、回放和评审。

策略候选必须运行：

```bash
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --baseline benchmarks/promotion_baseline.json \
  --profile quick
```

`full` 配置用于发布前或定时验证。更新基线不是修复失败的手段；只有冻结数据、执行口径或已经通过评审的生产结果变化时，才可同时更新数值和来源说明。

影响选股先验或市场泛化的候选还必须运行 `generalization`；要宣称全周期优于旧项目时必须运行 `competitor`。这两个命令只接受已评审 reference。以下 Bash 示例从 promotion 规范读取真实冻结 Pool E 的 32 只证券，满足默认 random-24 与 leave-top-5；行业映射和 reference 必须覆盖同一全集：

```bash
mapfile -t GENERALIZATION_POOL_E < <(
  uv run python -c \
    'import json; print(*json.load(open("benchmarks/promotion_baseline.json", encoding="utf-8"))["pools"]["e"], sep="\n")'
)
uv run python -m uquant.validation generalization \
  --data-dir data/frozen \
  --universe "${GENERALIZATION_POOL_E[@]}" \
  --industries /path/to/industries.json \
  --prior-symbols sz300308 sz300502 sz300394 \
  --start 2018-01-02 --end 2026-07-20 \
  --baseline /path/to/reviewed-generalization.json

uv run python -m uquant.validation competitor \
  --data-dir data/frozen \
  --reference /path/to/reviewed-competitor-matrix.json
```

仓库目前没有经过评审的完整泛化 baseline 或 105 单元竞品 reference，因此当前 CI 在经济回放层面只自动强制 manifest 与 promotion；普通测试仍覆盖两个新验证器的严格 schema、聚合和失败路径。缺少真实值时保持 fail closed，而不是在 CI 中生成占位 baseline。reference 完成独立评审后，才应把对应回放命令接入强制工作流。

## 发布前检查

```bash
uv run ruff check .
uv run mypy uquant scripts research
uv run pytest --cov=uquant --cov-report=term-missing
uv run python -m compileall -q uquant scripts research tests
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
- schema v3 迁移步骤已经准备，并人工核对 tranche 入场证据、订单减仓策略、战略 epoch、风险锚/预算和 scout 状态。
