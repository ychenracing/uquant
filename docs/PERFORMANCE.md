# 性能与晋级证据

本文只记录可重放的历史证据，不把历史收益写成未来承诺。所有 uquant 数值来自 `ProductionEngine` 的收盘决策、下一共同交易日开盘执行路径，使用 200 万元初始资金、实际费用、滑点、手数、涨跌停、停牌和 T+1 约束。

## 冻结矩阵

`benchmarks/promotion_baseline.json` 固定 5 个股票池、7 个市场阶段和 35 个单元，同时约束最终财富、最大回撤、真实账户订单与年化换手；包含急跌区间的单元还约束区间收益。默认晋级容差为财富不低于基线 99%、回撤最多增加 0.5 个百分点、订单最多增加 `max(1, 5%)`、换手最多增加 `max(0.25, 5%)`。五个 `through_july` 单元的急跌收益门槛均为 -3%。版本 2 policy 另外硬性要求所选 profile 的 continuous 回撤中位数不超过 28%、最差值不超过 35%，并要求 `choppy_2024` 最差回撤不超过 18%；profile 没有对应单元也会失败，不能绕开聚合门槛。

Promotion baseline 外层使用 schema v3：`data` 锁定 snapshot、实际文件数及 manifest/SHA256SUMS 摘要，`dataset` 锁定 pools/scenarios/profiles 矩阵，`execution` 锁定 close-t/next-open、上市前不可见与初始资金，`reference` 则把既有 observed 指标追溯到仓库路径、历史 commit、当时生产源码 SHA-256 和 observed-only SHA-256。验证器还把 reference 身份固定到已评审的 `ea4fb1c` 祖先：当前 pools/scenarios/profiles 必须与该提交一致，冻结数据与执行本金必须匹配代码内的已评审契约，旧 policy 只能收紧，新增聚合回撤上限和历史 urgent floor 也只能收紧；因此不能靠重算同一 JSON 的 fingerprint 来放宽门槛。外层 validation fingerprint 将 provenance、policy、矩阵和全部 references 一次性绑定；重复 JSON 键、NaN、多余/缺失字段、历史提交不可验证或任一指纹漂移都在回放前失败。候选源码必须已提交，报告单独记录候选 commit/源码摘要；回放结束时再次核对 baseline、生产源码和完整冻结数据，运行中改写任何一项都会失败。这个迁移没有刷新 observed 数值，急跌目标等 policy 仍与 observed 来源分开。

| 市场阶段 | 区间 | 五池财富中位数 | 五池回撤中位数 | 五池订单中位数 | 补充证据 |
|---|---|---:|---:|---:|---|
| 熊市 | 2022-01-04～2022-12-30 | 1.0024x | 4.50% | 6 | 4/5 池不亏；最差回撤 11.96% |
| 震荡市 | 2024-01-02～2024-12-31 | 1.7489x | 20.57% | 23 | 五池结果一致，没有用扩池改变排名口径 |
| 牛市 | 2025-04-01～2026-06-30 | 12.9566x | 15.96% | 10 | 五池财富全部超过冻结旧项目最佳值 |
| 牛市至急跌 | 2025-04-01～2026-07-20 | 12.1698x | 15.96% | 13 | 2026-06-30～07-20 最差区间收益 -6.08% |
| 连续周期 | 2018-01-02～2026-07-20 | 38.5399x | 30.87% | 84 | 五池财富范围 25.03x～66.72x |

## 牛市统一执行口径比较

旧项目数值冻结在 `benchmarks/competitor_bull_reference.json`。比较对象为 qwenquant、AQuant 和 trade 的锁定提交，均经相同 close-t / next-open 适配器运行。当前数据快照只重建了科技指数的因果链式点位；清单记录的日收益最大差异为 `7.24e-10`，不会改变这里的收益比较。

| 池 | uquant 财富 | 旧项目最佳财富 | 财富优势 | uquant 回撤 | 旧项目最佳回撤 | uquant 订单 | qwenquant 订单 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A / 3 只 | 12.9566x | 9.2619x | +39.89% | 15.96% | 17.54% | 10 | 12 |
| B / 5 只 | 12.9566x | 12.7595x | +1.54% | 15.96% | 15.52% | 10 | 9 |
| C / 9 只 | 12.9566x | 10.1485x | +27.67% | 15.96% | 15.79% | 10 | 13 |
| D / 15 只 | 13.0639x | 11.2732x | +15.88% | 15.98% | 18.62% | 12 | 27 |
| E / 32 只 | 13.0639x | 11.6947x | +11.71% | 15.98% | 17.27% | 12 | 26 |

五池回撤均不高于 18%，并且不超过旧项目最佳回撤 0.5 个百分点。池 B 的财富优势不足 5% 时，真实订单只比 qwenquant 多 1 单；其余四池订单不高于 qwenquant。因此没有用明显增加交易次数换取小幅收益。

## 本轮改造的回归结果

| 池 | 改造前连续财富 | 改造后连续财富 | 变化 | 改造前订单 | 改造后订单 |
|---|---:|---:|---:|---:|---:|
| A | 36.8463x | 38.5399x | +4.60% | 53 | 53 |
| B | 23.9296x | 25.0304x | +4.60% | 84 | 84 |
| C | 36.6821x | 38.3749x | +4.61% | 68 | 68 |
| D | 63.7846x | 66.7198x | +4.60% | 86 | 86 |
| E | 52.2520x | 54.6566x | +4.60% | 132 | 132 |

牛市财富相对本轮改造前下降约 1.0%，最大回撤不变，订单不增加；同期 2026 年 7 月急跌损失由约 11.1% 收窄到约 6.1%，连续周期财富提高约 4.6%。这属于明确披露的收益/尾部风险权衡，而不是隐藏在总体均值中的退化。

上表和“冻结矩阵”表中的财富、回撤、订单及 -6.08% 是既有 observed 证据，本次没有改写。-3% 是更严格的候选晋级目标；旧 observed 结果本身不因此被重新标成已达标。候选只有用同一执行路径重放且急跌收益实际不低于 -3% 才能通过。

## 2026-08-11 alpha-recovery 候选复核

本轮在同一冻结数据和执行口径上重放完整 35 单元。A/2024、C/2023、D/连续三个报告目标达到；B/连续为 31.4605x、21.86%、54 单，虽然相对冻结 25.0304x 提高 25.7%、回撤和订单也改善，但低于报告提出的 35.54x 理想目标，不能写成“四项目标全部达到”。扩展全集 E 的新 cohort 宽限只在配置全集达到 20 只时自动启用，使连续周期达到 69.3387x、回撤降到 25.86%，同时避免把同一宽限用于 15 只 D 后造成 25% 的收益退化。

| 单元 | 最终财富 | 最大回撤 | 真实订单 | 年化换手 | 冻结 policy 结论 |
|---|---:|---:|---:|---:|---|
| A / 牛市 | 12.9069x | 16.39% | 9 | 11.5814 | 通过 |
| A / 2024 震荡 | 1.7385x | 19.40% | 22 | 2.7343 | 单元通过；18% 震荡聚合回撤线仍有 1.40 个百分点缺口 |
| B / 连续周期 | 31.4605x | 21.86% | 54 | 6.8055 | 超过冻结结果，但低于报告 35.54x 理想目标 |
| C / 2022 熊市 | 1.1652x | 4.47% | 6 | 3.4688 | 正收益、低回撤、低交易，通过冻结单元门槛 |
| C / 2023 混合 | 3.3544x | 28.45% | 15 | 4.0261 | 财富超过 3.3290x 目标；回撤高于上一候选，未隐藏该取舍 |
| D / 连续周期 | 84.2596x | 27.78% | 89 | 21.4438 | 超过报告与冻结财富目标，订单不增加；换手金额仍偏高 |
| E / 2021 轮动 | 1.2495x | 12.23% | 10 | 3.6418 | 通过 |
| E / 2022 熊市 | 1.1193x | 11.50% | 10 | 4.5166 | 正收益；订单和换手增加，完整 policy 结论以 full 报告为准 |
| E / 牛市 | 13.8435x | 16.39% | 12 | 13.1265 | 通过 |
| E / 牛市至急跌 | 13.6071x | 16.39% | 15 | 16.7411 | 通过；急跌区间 -1.71% |
| E / 连续周期 | 69.3387x | 25.86% | 98 | 16.8496 | 收益高于冻结值，回撤低于 28%；扩展全集宽限自动生效 |

完整矩阵的连续周期财富为 A 29.6588x、B 31.4605x、C 40.9987x、D 84.2596x、E 69.3387x；回撤分别为 26.36%、21.86%、22.80%、27.78%、25.86%。A 的长期财富仍低于旧冻结证据，B 低于报告理想目标；D/2024 的普通广池兼容结果为 1.3722x，E/2024 的扩展全集宽限结果为 1.7660x。A/B/C 的 7 月区间为 -7.50%，只有 D/E 达到 -1.71%，因此不得声称 full promotion 全部通过。代码保留这些失败而没有放宽 frozen policy；收益、回撤、订单的具体取舍必须以生成的 full 报告为准。

## 泛化与先验依赖诊断

`uquant.validation.generalization` 使用同一生产引擎构造以下确定性场景：逐个/逐对/全部移除历史先验证券、移除 optical、按行业单独运行、行业平衡池、random 6/12/24，以及 leave-top-1/2/3/5。leave-top-k 排名只使用窗口开始前的 120 个可见交易日，不能按回放结果反选赢家。历史不足的新上市证券会明确记录为 `ineligible_symbols`，不进入窗口前排名和 top-k 移除集合，但仍保留在全集及其他重放场景中；可比较证券不足两个时验证失败，不能用不可比样本制造排名。

报告同时输出：

- `PDI_1 = max(0, 1 - 最差 remove-one 财富 / 全集财富)`；
- `PDI_3 = max(0, 1 - remove-all 财富 / 全集财富)`；
- 基线场景的带符号行业 PnL 占比；
- 财富 p10/median、回撤 p90/worst、订单 median/p90，并按场景族分别聚合。

成员不足的行业不会伪装成稳定行业池：单成员明确标记 `singleton`，多个稀疏行业的合并结果标记 `combined_sparse`。行业映射必须与传入 universe 精确一一覆盖，缺失和多余证券都会在构造任何场景前失败；证据日期、eligible/ineligible 成员也进入场景指纹和报告。`no_optical` 和每个 `industry_only` 结果还必须有实际成交形成的非光模块/对应行业 `CORE` 或 `STRATEGIC` 暴露，报告逐项记录部署证券与 lifecycle，正财富但长期持有现金不再算通过。

Generalization baseline schema v3 同时锁定数据 snapshot、manifest/SHA256SUMS 摘要、完整 universe/行业/先验证券/窗口、close-t/next-open 执行契约、初始资金，以及已提交生产源码的 commit 与源码 SHA-256；整个 provenance 另有不可分割的 validation fingerprint。缺字段、摘要变化、运行参数变化、未提交生产源码或运行中改写 reference 都会在重放前尽可能早地失败。`remove_all_priors` 除正收益与回撤门槛外，还必须达到 baseline 内经过评审且带 reference repository/path/commit/SHA-256 来源的 competitor-best 财富至少 95%；代码不会补造该值。

reference 必须与场景指纹逐项一致，重复、缺失、多余或运行中被改写都会失败。仓库当前没有经过真实完整重放评审且具备上述 provenance 和 competitor-best 指标的 `generalization_baseline.json`，因此 CLI 默认 fail closed，不会自动写一份“当前结果即基线”。

## 全周期竞品 gate

`uquant.validation.competitor` 要求 A～E 五池 × `rotation_2021`、`bear_2022`、`mixed_2023`、`choppy_2024`、`bull_2025_2026`、`acute_2026_07`、`continuous` 七窗口 × aquant/qwenquant/trade 三项目，共 105 个 reference 单元。每个项目必须锁定 40 位 commit、adapter 路径与 SHA-256、入口/profile/config/runtime/raw evidence；reference 还必须绑定数据 manifest/checksums/dataset 和完整执行契约。

best-of-three 对财富、回撤、订单分别保留来源，不能把三家最优字段拼成不存在的“第四个策略”。急跌窗口从 2025-04-01 warm replay，在 2026-06-30 收盘权益作为起点重新计算局部财富与回撤；订单按 `(2026-06-30, 2026-07-20]` 内存在成交的唯一账户订单计数，避免空仓冷启动和 partial fill 重复计数。

现有 `benchmarks/competitor_bull_reference.json` 只有牛市 15 个单元，不能冒充完整 105 单元 gate。仓库没有可核验的完整三项目全周期真实值，因此没有创建 `competitor_matrix_reference.json`；缺 reference、任何 cell、来源字段或执行契约时命令都会 fail closed。

## 复现

```bash
uv run python -m uquant.validation data-manifest --data-dir data/frozen
uv run python -m uquant.validation promotion \
  --data-dir data/frozen \
  --baseline benchmarks/promotion_baseline.json \
  --profile quick

# 下列两项只有在真实回放结果完成评审并冻结后才可运行；缺失时应失败。
# Bash 从 promotion 规范读取真实冻结 Pool E（32 只），满足默认
# random 6/12/24 和 leave-top-1/2/3/5 的全集规模要求。
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

生产源码、基线、冻结数据、依赖锁或工作流发生变化的 Pull Request，以及 `main` 推送和每周定时任务，都运行 `full`。`quick` 只用于人工本地诊断，不能作为合并或发布证据。修改基线不是修复失败的方法；只有冻结数据、执行口径或已经通过评审的生产结果发生变化时，才能同时更新数值和来源说明。
