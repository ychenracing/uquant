# 当前验收链

## PR92：固定 C3 的累计比较政策

2026-09-26 连续任务授权的比较规则已接入现有 Economic、Performance、Absolute
final 与 Ownership 分片入口。冻结合同为
`artifacts/remediation-validation-20260926/industry-v2-research/continuous/CONTRACT_V1.json`；
其中事先要求补齐的原生 LOO/Ownership 参照在同目录 `C3_NATIVE_REQUESTS.json`。
固定 307 个别名按请求身份去重为 272 个账户，六族为完整池 6、全剔除 34、
仅交易剔除/子行业 67、短窗 39、随机池 120、压力 6。族内等权、族间等权；
Ownership 全剔除不能与 Economic 仅交易剔除合并。

| 规则 | 当前有效判定 | 旧结果处理 |
|---|---|---|
| 历史 champion 逐项不退化 | 替换为相对固定 C3 财富比≥0.95、回撤增量≤0.01 | 仍计算并保留原失败，不改写历史原件 |
| 六族累计预算 | 财富几何比≥0.98、平均回撤不升、纯退化权重≤20% | 缺账户不能通过；重复账户不能稀释损失 |
| 主账户/冠军财富 | 15×硬底线 | 23.28417871275582×仅保留历史判定 |
| 绝对风险与收益 | 原窗口回撤、急跌、随机尾部、正收益、容量和执行规则继续生效 | 不能因新相对政策而豁免 |
| Performance A / year_2024 | 当前实际 protected gate 的有效财富底线1.55826×继续生效 | 来源虽为冻结参照，当前调用属于受保护硬门，不能再打折 |
| 配置迁移 | 仅精确匹配 `benchmarks/pr92_v2_config_migration.json` 的授权变化 | `behavior_equivalent=false`，不把当前经济结果伪装为旧 champion |
| 固定历史必须两 owner | `remove-sz300502` 的独立 C3 审计证明无合法确认机会时，允许一条真实完整链 | 明示历史两 owner覆盖不足和旧失败；受控真实接替能力仍为硬门 |

逐指标持平容差为冻结合同中的绝对 `1e-12`。财富下降且风险下降是受控交换，
须有事先经济目的；双优改善须有对应真实事件证据。未优化 V2 的累计变化、
同输入 main 比较、全部原始账户、成本/换手/集中和旧硬门，仍是独立交付义务。

`uquant.validation.pr92_tradeoffs.evaluate_c3_matrix` 聚合原生验证后导出的账户。
每条记录含 `alias`、`metrics`、经济源码 `economic_source_sha256`、原件
`raw_sha256`，改善另附 `causal_evidence`，交换另附 `preregistered_purpose`。
这里的经济源码身份不是含验证器的 Performance/Economic 整体源码摘要。
使用现有 CI 产物入口计算完整取舍预算：

```bash
uv run python -m uquant.validation.ci_artifacts c3-budget \
  --accounts /tmp/uquant-acceptance/native-validated-accounts.json \
  --report-output /tmp/uquant-acceptance/c3-budget.json
```

输入是 `{"accounts": [...]}`。该命令只计算取舍预算，明确输出
`native_evidence_validation=REQUIRED_SEPARATELY`、`final_acceptance=false`。
调用者须先通过各原生入口验证原始账户、请求/配置/数据/运行环境与真实生产者，
不能把手工填入的指标或该命令退出0当作完整验收。最终合并仍须所有原生硬门、
六族预算、事件归因、V2/main 对照、必要工程检查与仓库保护共同满足。

历史机会审计原件由 `C3_OPPORTUNITY_AUDIT.json` 摘要绑定。四账户各观察869个会话，
宽口径机会最长连续1日，未达到原规则3日确认；另一次候选出现时新风险被冻结。
此结论不由新候选资格收窄产生，也不代表未来没有接替机会。Absolute 的原七项
字面政策函数仍可重评历史；正式 final 使用 `aggregate_c3_acceptance`，将逐账户
预算绑定到完整指标组件并保留流式读取，受控跨行业接替和失败恢复仍独立验证。

本轮 C4–C8 与消融均未达到全部有效硬门，不能合并。失败策略保持在独立研究分支；
当前 PR 生产策略继续保持 C3 和行业 V2。最新实证与原件索引沿用 PR92 顶部记录。

跨年份旧账户恢复任务使用预登记的 [CROSS_VINTAGE_CONTRACT.json](../artifacts/branch-integration/CROSS_VINTAGE_CONTRACT.json)，并沿用原 23 场景的固定门槛与相对基线。[交付报告](../artifacts/branch-integration/start-date-research/CROSS_VINTAGE_DELIVERY_REPORT.md) 记录原生账户、真实旧 checkpoint 升级、成本、确认、先前失败及其原始生产者。R26 也修正了普通持仓被误算为第二个战略 cohort 成员的问题；已观察的备用确认不冒称新的未见数据。原件回执和实际合并状态另行记录。此范围通过不代表完整 Absolute 矩阵通过，也不替代仓库保护。

`benchmarks/absolute_generalization_acceptance_contract.json` 保留 Absolute 原数值与输入合同，
PR92的比较方式和条件历史语义由上述固定C3规则补充。其独立摘要保护窗口、34 只证券、
分片成员、数值约束和输入身份。候选身份在加载时从实际
checkout 验证取得，不写回政策，也不改变判定规则。

## 预检

在仓库根目录使用 Python 3.12.13、uv 0.11.33 与锁定依赖：

```bash
uv sync --frozen --extra dev
uv run python scripts/run_absolute_generalization_acceptance.py \
  --shard champion --preflight-only --run-id local --run-attempt 1 \
  --output /tmp/uquant-acceptance/manifest.json \
  --cache-dir /tmp/uquant-acceptance-cache --data-dir data/frozen
```

预检核对实际经济源码、整个包、验证运行器、配置、数据、成员、锁文件与实际 Python、
NumPy、pandas、uv 版本。工作区源码必须等于已提交的 checkout。CI 使用可信事件 SHA：
push 对应事件提交，PR 对应测试合并提交，merge queue 对应 merge group 提交。
工作流在昂贵分片之前执行预检；预检通过不代表经济通过。

## 真实执行与产物

去掉 `--preflight-only` 即运行选定分片。固定分片为 `champion`、`loo-a` 至 `loo-f`、
`recovery-and-reachability`。运行窗口和证券成员由政策定义，不接受调用方缩短或替换。

单证券诊断示例：

```bash
uv run python scripts/run_absolute_generalization_acceptance.py \
  --shard loo-a --symbol sh600487 --run-id local --run-attempt 1 \
  --output /tmp/uquant-loo/manifest.json \
  --cache-dir /tmp/uquant-acceptance-cache --data-dir data/frozen
```

诊断 manifest 的 `mode=targeted`，不能用于正式 final 聚合。原始回放、账户和事件链先保留，
再核对会计和 Target/Order/Fill 身份、封存、严格读回。结果仍保留其原始生产者 HEAD/tree。
缓存只有完整身份一致才复用；旧结果不冒用当前源码。输出路径已存在时拒绝覆盖，另选新的
运行目录和 attempt。

恢复证据中的每个已关闭 epoch 绑定一次首次物理成交。同一订单可以跨日等待、部分成交及取消；
其创建日取首次决策观察并必须等于 signal_date，订单终态与最终账户账本一致。
后续增量买入不能替代首次激活证据；缺失首次成交、创建日或最终账户身份仍拒绝封存。
独立平账修复夹具用间歇分歧行情隔离更快的连续风险修复路径；日历长度包含不健康日，
验收仍严格要求20/40/60/60次健康观察及同一修复episode的完整READY序列。

## 聚合

正式结果需要同一运行、同一 attempt 的完整八个 canonical 分片。目录格式为
`absolute-generalization-<run-id>-attempt-<attempt>-<shard>/manifest.json`。

```bash
uv run python scripts/run_absolute_generalization_acceptance.py \
  --shard final --run-id local --run-attempt 1 \
  --output /tmp/uquant-summary/report.json \
  --shard-root /tmp/uquant-shards --artifact-prefix absolute-generalization \
  --upstream-result success
```

聚合重新验证封存摘要、实际 checkout、独立政策、运行环境、数据和成员；拒绝缺片、错片、
混合 attempt、跨候选、伪造通过声明和 targeted 结果。`passed` 必须同时满足
`runner_success` 与 `capability_pass`。合法亏损或其他经济 NOT_MET 如实保留，不能改为通过。

## 失败语义

- 预检或证据校验异常：非零退出，保留带根因、生产者和运行身份的 `*.diagnostic.json`，
  不伪造有效经济 seal。
- 执行异常：非零退出并保留原始错误；错误 manifest 明确为 `ERROR`，不是有效经济账户。
- 完整聚合但经济不达标：封存 report 保留具体失败组件，`passed=false`，非零退出。

Actions 只上传实际生成并读回的产物；预检或分片执行失败时保留原始失败，不继续下载和
聚合不存在的有效分片。必要检查不会被忽略，历史红色运行也不会因新提交而被改写。

## C2 简化替换

[八场景比较](../artifacts/unified-allocation/C2_ACCEPTED_DELIVERY.md)与[验收合同](../artifacts/unified-allocation/ACCEPTANCE_C2_ACCEPTED.json)记录正式配对结果。六个标准场景的成本后终值相对基线几何平均为 103.67%，八场景最低为 97.34%，最大回撤增量均为零。2025 场景订单数由 9 增至 16，剔除 `sz300502` 场景由 45 增至 61；这两个场景的换手和成本也增加。以上结果限于已运行的八场景，不代表完整 Absolute 或未来泛化表现。

## 当前冠军财富口径与候选 C 原件

现行主账户／冠军财富底线由 `uquant.validation.acceptance_tolerance.principal_wealth_floor`
统一评估，Absolute 和 Strategic Ownership 均使用 **15×**，含本金、不叠加其他财富容忍。
Ownership 合同中保留的 `23.28417871275582×` 是历史门槛的原始记录，不能直接作为
当前冠军拒绝条件；冠军最大回撤仍限 30%，其他所有权、账本及归因校验各按原适用规则执行。
测试夹具覆盖低于 15×、恰好 15× 与高于 15× 的边界。

候选 C 的完整正式 Absolute 原件由 `115a151761f458da056b6118fa41a2339ec49914`
生产，八片和 final 通过，34/34 单元、七组件通过；冠军财富
`21.868143196721125×`、最大回撤 `26.628444%`。正式 final 的 SHA-256 为
`3a7c66fc3429aa9e7f9de18ee70cec749c449a49d2bc46a8f214320a2deb9ef3`，
原始身份和旧失败均保留。工程修复的验证和可复用证据范围见
[工程验收收尾记录](../artifacts/engineering-acceptance-closure/DELIVERY_REPORT.md)；
该原件不改贴后续 HEAD 身份。

## 历史完整结果与身份修复

main `58d4b9c` 的 [Actions 35628142765](https://github.com/ychenracing/uquant/actions/runs/35628142765)
已完成八个 canonical 分片。保留的[原始聚合报告](../artifacts/operational-continuity/ABSOLUTE_MAIN_REPORT.json)
记录 34/34 有效单元、零缺片/重复/回放错误，以及 `runner_success=true`、
`capability_pass=true`、`passed=true`。这更新了 PR80 交付当时的“完整 Absolute 尚未完成”状态；
PR80 的历史报告不回写。

本轮核对同时发现原报告的配置身份错误：`effective_config_sha256` 复制了冻结政策的
`adf8c123…`，但原源码的独立预检要求、实际默认配置均为 `4d9c3495…`。
现由同一合同加载出口从 `DEFAULT_CONFIG` 计算实际摘要，分片、单元、缓存及 final 聚合
沿用该出口。冻结政策字节、阈值、窗口和成员保持不变，原结果不重新封签或改贴身份。
旧配置标注不能作为新严格验收的运行配置证明；其实际完成的数值结果、原 HEAD 和失败/通过
声明仍如实保留。这是证据身份缺陷，不是经济 NOT_MET，也不是原回放运行失败。

本轮复用未变化的经济决策、配置、数据与锁文件证据，单独验证上述身份投影及观察事务路径。
修复后完整新身份矩阵与真实 Future Holdout 仍须有各自原始结果才能宣布通过；
本轮不因工程修复宣称收益提高。详见[本轮交付记录](../artifacts/operational-continuity/DELIVERY_REPORT.md)。
