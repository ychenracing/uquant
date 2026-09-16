# 当前验收链

`benchmarks/absolute_generalization_acceptance_contract.json` 是唯一 Absolute 政策文件。
其独立摘要保护窗口、34 只证券、分片成员、数值约束和输入身份。候选身份在加载时从实际
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
