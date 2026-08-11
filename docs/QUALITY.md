# 工程质量门禁

本文把“工程质量”定义为仓库能够自动执行、失败时能够阻断发布的控制，而不是 README 中的自我评价。对照快照为 `ychenracing/qwenquant@0b3681e10b75425ad8600e75835677a6a125ed13`；评分规则为完整自动门禁 1 分、只有部分能力但未完整阻断 0.5 分、缺失 0 分。

## 1.1.0 自动门禁快照评分

| 控制项 | uquant 1.1.0 | qwenquant 快照 | 判定依据 |
|---|---:|---:|---|
| Python 3.11 / 3.12 矩阵 | 1 | 1 | 两个仓库都在两个运行时测试 |
| 所有 CI 路径使用同一冻结依赖图 | 1 | 0.5 | uquant 全部使用 `uv.lock --frozen`；对照策略任务仍安装非锁定 `requirements.txt` |
| 扩展 lint 规则 | 1 | 0.5 | uquant 启用 E/F/W、B、UP、SIM、PERF、RUF；对照仅 E/F/W |
| 严格静态类型阻断 | 1 | 0 | uquant 对生产包、脚本和离线 research primitives 执行 strict mypy |
| 分支覆盖率下限 | 1 | 0 | uquant 按 branch 统计并以 85% 阻断 |
| 生成式性质测试 | 1 | 0 | uquant 使用 Hypothesis 验证组合、费用和代码规范化不变量 |
| wheel 与 sdist 构建 | 1 | 0 | uquant 在 CI 构建两类发布物 |
| 静态安全扫描 | 1 | 0 | uquant 运行 Bandit |
| 生产依赖漏洞审计 | 1 | 0 | uquant 从冻结锁导出生产依赖并运行 pip-audit |
| 强化冻结数据清单 | 1 | 0.5 | uquant 三方核对 manifest/SHA256SUMS/目录并拒绝符号链接和不安全名称；对照有基础哈希和反向目录检查 |
| 版本化账户 schema 与显式迁移 | 1 | 0.5 | uquant 当前 schema v3 拒绝静默升级并保存迁移时间、前后 schema 和代码哈希；对照存在字段兼容迁移但没有版本化确认门 |
| 多市场阶段经济性晋级 | 1 | 0.5 | uquant schema v3 以数据/矩阵/执行/历史 reference/候选提交 provenance 锁定 35 单元，并约束财富、回撤、真实订单、换手和急跌收益；对照有财富/回撤/订单门但没有同一冻结换手契约 |
| 自动依赖更新 | 1 | 0 | uquant 为 Python 和 Actions 配置每周 Dependabot |
| CI 并发、超时和证据制品 | 1 | 0.5 | uquant 有并发取消、分任务超时、覆盖率及晋级报告制品；对照只有部分任务超时 |
| **合计** | **14 / 14** | **4 / 14** | **100% 对 28.6%** |

这个分数只比较可检查的发布控制。qwenquant 已有的基础测试、编译检查、数据哈希和策略验证都按“完整”或“部分”计入，没有因项目名称而忽略。

## 新增经济验证框架的状态

下列能力在当前代码中有严格 loader、CLI 和失败路径单元测试，普通 pytest 会覆盖这些代码契约；但真实全矩阵回放命令不计入上面的 1.1.0 分数，也尚未接入强制 CI：

| 能力 | 已实现 | 当前阻塞 |
|---|---|---|
| 泛化/PDI gate | 确定性 remove-one/pairs/all、no-optical、行业单池/平衡池、random 6/12/24、窗口前 leave-top-k；聚合 PDI、行业 PnL、财富/回撤/订单分位数 | 缺少经过真实全场景重放与独立评审的 frozen baseline |
| 全周期竞品 gate | 严格校验 5 池 × 7 窗口 × 3 项目共 105 单元、commit/adapter/data/execution provenance、best-of-three 来源和逐 cell 阈值 | 现有牛市 reference 只有 15 单元，缺少三项目全周期真实证据 |

两个入口都 fail closed 且不含 baseline 写入 API。没有真实 reference 时不把它们标成“自动发布门已完成”，也不通过生成占位 JSON 来提高评分。

## 复杂度与测试证据

| 指标 | uquant 1.1.0 | qwenquant 快照 |
|---|---:|---:|
| 生产 Python 行数 | 7,821 | 13,504 |
| 最大生产模块 | 1,164 行 | 1,490 行 |
| 测试 Python 行数 | 3,661 | 3,179 |
| 显式测试函数 | 92 | 53 |
| 分支覆盖阻断线 | 85% | 未设置 |

表中行数、测试数与 88.09% 分支覆盖率是 1.1.0 发布制品的归档快照，不代表当前工作树。当前候选应以本次 CI 生成的覆盖率、测试清单和制品为准，文档不预填一个尚未执行的“最新”数字。

行数少本身不是质量结论；这里用于确认职责拆分没有靠复制第二套策略实现。uquant 的 `PortfolioAllocator` 仍是唯一目标权重所有者，实盘与回放仍共用 `ProductionEngine.decide()`。

## 发布门

工程工作流在每次 `main` 推送和 Pull Request 上执行；Ruff、strict mypy 和 compileall 均覆盖 `research/`，但覆盖率门仍以生产 `uquant` 为统计对象：

```text
Ruff → strict mypy → frozen-data integrity → pytest branch coverage
      → compileall → wheel/sdist build
Bandit → frozen production dependency export → pip-audit
```

策略工作流当前独立执行数据清单和 schema v3 promotion 矩阵。promotion 要求候选生产源码已提交，并在运行前后复核源码、数据和 baseline 未变化；历史 observed reference 必须由固定的已评审祖先 commit 还原，矩阵、数据、执行与阈值还要通过祖先契约的单向收紧检查，不能以自重算 fingerprint 替代评审。泛化和全周期竞品入口已可调用，但在真实 reference 完成评审前不接入强制 CI，并会明确失败；这不是通过缺省，而是避免把虚构 baseline 固化成发布证据。拆开两类任务可以清楚区分“代码正确但绩效退化”和“绩效看似很好但工程契约失败”，任一失败都不能被另一类结果抵消。

## 维护规则

- 新策略状态必须进入账户 schema v3（或后续显式版本），并提供显式迁移；
- 新参数必须有失败边界测试和文档说明；
- 新风险能力默认自动工作，但只能向唯一风险/组合所有者提供证据或上限；
- 生产参考篮子必须保持为评审后的 `STABLE_REFERENCE_UNIVERSE`；研究扩展不得改变生产百分位、数据要求或缓存键，晋级必须显式评审；
- 离线 `research/` 只能接收调用方提供的观测/回调，不得被 `uquant/` 导入或成为第二个生产执行入口；
- 策略改动必须运行 quick 晋级，发布前运行 full 晋级；
- 先验/泛化改动必须检查 PDI 与尾部分位数；竞品宣称必须有完整 105 单元来源锁定 gate；
- promotion、泛化与竞品 reference 缺失或漂移必须 fail closed，禁止自动接受当前结果；
- 不得通过放宽基线、修改订单口径或删掉失败股票池来修复退化；
- 代码、注释、配置、日报和 Markdown 对同一行为必须使用一致描述。
