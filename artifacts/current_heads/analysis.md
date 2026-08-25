# 四个当前 HEAD 统一基线审阅

> **历史证据快照：** 本文解释 2026-08-18 冻结的四系统矩阵，不描述当前仓库 HEAD，
> 也不是自动推广门。当前生产合同见 [`docs/PERFORMANCE.md`](../../docs/PERFORMANCE.md)。

日期：2026-08-18  
证据类别：Risk Sentinel 改造前、当前 HEAD 横向基线  
用途：后续行为变化的归因起点；不是自动推广门，也不替代已认证 champion

## 固定源码身份

| 系统 | 仓库 | 当前 HEAD | Tree SHA | 执行权限 |
|---|---|---|---|---|
| uquant | `ychenracing/uquant` | `ea24f1837f8b7f2d91e73a5d3c70875f2ea98015` | `ba60ec93b03288e06260d6795b5ad1d1f65063ff` | 本阶段只增加验证层 |
| aquant | `ychenracing/aquant` | `55009a628515a0d612034c132bc90d21cf720c25` | `f983c73fd3822e29693e1ce41ec51fd325402161` | 只读 |
| qwenquant | `ychenracing/qwenquant` | `63e05fe7adc2eae67d78e2cfca6222f88e041d89` | `d2c44a8f68a466518a135e54dfa0fdbbb4df0ead` | 只读 |
| trade | `ychenracing/trade` | `2066fbf0f99be94142c5d0cb0b6c99d276c2472d` | `0967ca2b05971da6f5a0334086747c4d0a39479b` | 只读 |

这些身份来自实施开始时的远程读回，不使用历史工件中的记忆值。各仓库 Python 源码、
依赖文件和适配器摘要见 `benchmarks/current_heads_source_registry.json`；竞争仓库没有提交、
推送或工作树修改。

## 相同合同

四个系统都使用冻结的 A 股 AI 产业链数据、2,000,000 元初始资金、收盘 t 信号、下一可
交易日开盘执行、现金多头、股票前复权、指数不复权，以及相同 T+1、手数、科创板、
停牌、涨跌停、费用、滑点和容量约束。六个官方窗口及 Acute Window、a-e 官方池、
完整泛化场景和固定随机种子均由
`benchmarks/current_heads_comparison_contract.json` 以 SHA-256 封签。

每个系统共有 264 个预注册 Cell：30 个官方池 Cell 和 234 个泛化记录。总矩阵固定为
1,056 个 Cell，其中官方池 120 个、泛化 936 个。每行保留 13 项统一指标或互斥的
`REPLAY_ERROR` / `INSUFFICIENT_SAMPLE`，以及源码、数据、配置、运行时和证据摘要。

## uquant 改造前基线

冻结对象是 `ea24f1837f8b7f2d91e73a5d3c70875f2ea98015`，并以只读 tag
`pre-risk-sentinel-20260818` 指向该提交。基线包含 Phase 1 六窗口官方结果、Phase 2
234 条记录、45 个决策等价场景、订单、Fill、换手、Acute Return、集中度、风险状态和
经济归因。完整身份见 `artifacts/current_heads/provenance_report.json`。

冻结运行通过 Engineering Gate（1,276 tests，branch coverage 85.31%）、Phase 1 full
45/45 受检 Cell 和 Phase 2 234/234 正式记录。生产源码摘要为
`e1d878716983d2a2f0c872d80d4cae4e7906065354d879ba7ab99538a529407c`，有效配置摘要为
`ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13`。

历史 champion `cf8fecff76564fd4ed87faa0da336a06d433fd93` 与当前 HEAD 的 Phase 1 决策在
`a/bull` 首次分叉。该结果在本阶段仅作诊断，不能冒充当前 HEAD 证据，也没有通过改变
当前参数来消除。

## 可重复性与失败保留

qwenquant 和 trade 的两次正式批次输出逐字节一致。aquant 首次使用复用 worker 的两次
试跑出现同状态总数但 Cell 身份不同的首分叉：
`official_pool/aquant/continuous_ai_era/d`。原始两次输出和首分叉报告保留在
`artifacts/current_heads/diagnostics/`。根因是 aquant 在导入期绑定数据目录和静态交易
日历，复用进程会把前一窗口状态带入后一 Cell。适配层因此只对 aquant 使用 spawn 且
每个 Cell 新进程；没有修改 aquant 源码、参数、入口或经济逻辑。

正式矩阵中的每个 Replay Error 和 Insufficient Sample 都保留为原始状态，没有删行、
补值、换 seed 或回退到旧版本数字。最终状态计数和两次运行摘要由矩阵工件自身提供，
并由 `python -m research.current_heads` 独立重算校验。

两份最终矩阵逐字节一致，文件 SHA-256 均为
`75e93f9dad03c51eede3756f52db2cd560c7ecd16e52d6cf37a950e5fb6fcae3`。完整矩阵为
828 `SUCCESS`、60 `REPLAY_ERROR`、168 `INSUFFICIENT_SAMPLE`：uquant、aquant、
qwenquant 各为 222/0/42，trade 为 162/60/42。60 个 Replay Error 全部属于 trade，
原样保留错误类别和信息。

## Provenance

- 数据快照：`20260809T094222Z-causal-tech-index-rebase`
- manifest SHA-256：`343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d`
- checksums SHA-256：`ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29`
- `uv.lock` SHA-256：`4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61`
- uquant runtime：Python 3.12.13、NumPy 2.5.1、pandas 3.0.5、uv 0.11.33
- qwenquant runtime：Python 3.12.13、NumPy 1.26.4、pandas 3.0.5
- trade runtime：Python 3.12.13、NumPy 2.5.1、pandas 3.0.5
- aquant runtime：Python 3.12.13、NumPy 2.2.6、pandas 2.3.3

## 证据边界与限制

当前矩阵用于融合前基线，不是自动推广门；系统在某些窗口不领先不会使本阶段失败。
历史横向结果不保证未来。幸存者偏差、行业映射和股票池选择会影响结论；前复权、次日
开盘模型与容量约束仍不能完整模拟现金分红、排队和真实冲击成本。qwenquant 的 lock
只固定直接依赖，隔离安装还解析出 python-dateutil 与 six 1.17.0，因此 registry 和运行时
摘要必须共同用于重放。历史 champion 分叉和 aquant 进程复用分叉均保留，不作豁免。
