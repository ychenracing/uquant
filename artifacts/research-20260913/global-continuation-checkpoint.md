# uquant PR #67 全局续作检查点（执行环境中断）

这是用户已授权的原任务续接：继续实施、验收，通过全部实际适用条件后正常合并 main；无需重复确认、不得重复完成项。本检查点因平台明确返回 environment_offline / exec-server transport disconnected 而保存，不是经济任务完成。

## 当前状态

仓库 ychenracing/uquant，PR #67，分支 codex/targeted-generalization-research，OPEN Draft 未合并。
当前生产代码/测试/文档远端提交 6d9881a26ac6e855876001d178740f2e301a8aa9；本检查点提交只追加本文件。
对应本地 d598cedbc4f9fdcb0626af18dabd2cac46befb12，tree a5103570f5de2a6c196a599ac9b43b6d913964d2。
经济源码指纹 1c4304a929537f34e2321fd13338112b541629b4868f81783dafd2274d3c9ebb。
main 最后实时核验 df45b4d7d9ea290ae953115afbb73140270537c2。
依赖 PR65 仍 OPEN、未 merged，HEAD fefdb01dd68f184f535a588f0d38aa2cbc5fb8e7；此前已核验是PR67祖先，不能称PR65已合并。
最终技术差异审查 df45b4d→d598ced 未发现新的可复现阻断问题；独立审查不是完整经济验收。

## 保留机制

1. 普通持仓原有连续结构退出，在当前参考有效时额外要求自身 ret60 < min(0, tech_ret60)。相对强势或绝对正收益趋势不因新入场成熟资格回落而机械平仓。原时钟、ATR/灾难及全局风险退出保持。
2. risk.market_book 保留原 tech_ret60 缺失回退用于旧消费者，额外输出 tech_ret60_observed；新持仓退出消费者仅在其严格为 True 时使用参考。缺失/NaN/inf沿用原退出，真实零不误判缺失。
3. 已确认同业恢复成员共享不足新增资金时，新增请求额×当日 LeaderScore.score 加权，逐名封顶原请求并分配封顶余量。任何成员评分非有限或非正，整组沿用原请求比例。既有持仓/买单预留不重分，仍取实际现金、总仓风险cap、同业.92余额最小值，原最小交易与集中度拒绝后余量不转送其他人。
   文件 uquant/portfolio/recovery/current_cohort.py::_fresh_leadership_budget/_fund_members。
   不改变资格、危机、Sentinel、恢复额度/原请求、交易费用和证券角色。

h1/bull原生producer 9df2d08146d8484a427a9af6437bedba1c28aebd（remote c1114367288a4978a33b7aeded0b2b837aafcaef，tree05766513e7c5e2882cb102bbb4774130e5d504a4）。d598ced/6d9881a仅后续文档和预留测试；复用要保留原producer，不能给原件换身份。

## 已完成首轮全局检查：同一当前经济源码

| 场景 | 财富 | 最大回撤 | 订单/其他 | 判定 |
|---|---:|---:|---|---|
| a/h1_2024 |1.9085269240679252|.156742775678121|8单，急性区间也通过|PASS_FIXED_UNIT|
| a/bull |11.655466908159205|.15999500872658612|12单|PASS_FIXED_UNIT|
| cross-industry-crowning |2.1075436118495725|.1847500395374222|2真实周期|PASS诊断|
| remove-sz300308 |1.67271295058803|.18998210833258178|2真实周期|PASS诊断|
| remove-sz300502 |1.9358985339748003|.2705604922628324|2真实周期|PASS诊断|
| champion-5 |27.222037469364185|.27146973146234554|14单|PASS诊断|
| full/continuous_ai_era |32.933849484453205|.27147361646156465|20单|原read_case+check_metrics PASS|
| no_optical/h1_2023 |1.34166540178326|.23423157206392808|10单|原read_case+check_metrics PASS|
| remove_all_three/h2_2024 |1.7542444772455033|.10738365289163887|7单|原read_case+check_metrics PASS|

full费用68983.883993596、滑点103985.04709999826；所有raw账目、身份、实际成交按原runner核对，不只看进程退出。
bull前后：原相对退出候选11.561380631253794→当前11.655466908159205，费用23585.646292414→23784.747781590002，滑点35901.69119999885→36199.935899998825；仍12单。
首次新增394股数8200→6900、502股数5700→6600；后续危机/恢复有真实账户交互，不以事后加法冒充新回放。
跨行业相比更早c01财富2.28757/DD11.5868%有所取舍，当前值仍过原门；不得隐去。

## 完整验收进度与剩余

已读回正式Ownership：
- champion整片 PASS：champion-5、report-13；
- critical整片 PASS：remove308、remove394；
- continuity整片 PASS：remove502、same-industry-crowning、cross-industry-crowning、failed-first-grant。
- ghost-a最后INCOMPLETE，首个remove-sh603688 PASS；ghost-b最后INCOMPLETE，首个remove-sz002409 PASS。不能称完整Ownership已通过。

工程：
- 本次资金分配定向+复杂度18项PASS；另不等请求/已有预留组合测试与旧资金测试15项PASS。
- 新参考有效性缺失测试先RED后修复；18相关退出/结构测试PASS；此前相关70项/架构边界检查已在原PR记录。
- 全仓Ruff PASS，mypy uquant scripts research：328源码PASS。
- 全量pytest应用左右片与architecture仍执行中，85%分支覆盖未得结果。不要把进度百分比当覆盖率。
- 独立审查两次及本次完整未审差异均未发现阻断；建议的不等请求/挂单用例已补。

远端可恢复Grant：
- c111436的Strategic Grant Acceptance run34739018685 completed success。
- job103675404497：Grant测试、Champion baseline/native eligibility replay和上传全部success。
- artifact10311968308，名称strategic-grant-acceptance-34739018685-attempt-1，117461 bytes，
  GitHub公布digest sha256:eba424a67fd93a3d4758453bb535d0d166cb0be0fc3c81a72e8da73e8a732a25。
- 当前环境中断，尚未下载ZIP独立解析最终report；恢复时优先取得并核验，不重复该经济回放。
- 6d9881a远端四工作流最后queued：Ownership34739179910、Grant34739179932、Engineering34739180005、Absolute34739180018。>10min不等、不取消追绿；新完成失败仍应修复。历史120f42d等经济失败已处理，勿重做。

已启动但未获得最终结果：
- Absolute固定8片（champion、loo-a..f、recovery-and-reachability），run-id local-global-20260913，attempt1，四worker队列；final未运行。
- 全量Performance promotion --profile full，已把h1/bull同身份native缓存复制到共享cache；完整结果未得。
- 本地Grant（远端现在已有成功，先读回避免再次开跑）。
- cross-AI robustness plan固定64 specs，已启动new-nominal-champion/no_optical/remove_all_three；
  成对起点、成本、固定邻域/删控制证明、最大贡献者删除以及其余nominal窗口尚待完成。不能将首轮9项当最终矩阵。

## 全局研究反证：不要重复

- inverse-vol共享恢复资金：remote a113cde/localdead37f；bull11.5001860206低于两门，已整项撤回。此前joint-full等原件保留自己的旧producer。
- 提前成熟毕业：remote49fd481/localb963e25；bull7.0271044706/15单。释放anchor引发普通cap卖出和后续危机路径变化；整项撤回，不补特殊cap豁免。
- 同步普通恢复拆开市场票数：只读前缀证明10/17和10/20 helper=true但Sentinel仍冻结；10/16 held_damage_ratio=1。改外层票数无效，未实施。
- 减持排序旧方案：2db881e研究卡确认旧“先少改单”已有bull11.19756反证，现有优先utility提升到11.56138；locked RECOVERY已与CORE同级，不再误诊标签直接导致优先卖出。
- f4自身当前资格保护和H长期退出排除，旧附件/PR有明确反证，不重启无新证据的方向。

## 新的具体验收runner缺口（未修改、未宣称修复）

冻结cross-AI生产503fa218a298ffa5a573922a5b9cbf0a98885d8f已恢复独立工作树；source
1b1b9e2a60a9899e14bb910bb0a836136912aaa5c2bcfe3b2e142d2c40cf819d
与冻结cross_ai_stage1_baselines.json完全一致，旧config已导出，robustness计划成功生成。
research/cross_ai_robustness.py::_runner_bindings目前仅把current universe import替换成old default别名（286f467的已有适配）。
但current cross_ai_strategy.py顶层新增uquant.validation.parameter_policy.validation_engine，503fa21无此模块；
robustness/acceptance还涉及frozen_policy_config、evidence_source，完整旧角色导入/调用闭包仍需检查。
不可向旧uquant注入假模块、复制当前生产模块或更改source identity绕过。
下一步应在research运输层核实最小显式baseline binding：
保留旧uquant字节及fingerprint、实际旧config与runtime，先导入及小输入等价再长回放。
先查seal覆盖范围，尽量只修旧角色binding；当前raw可以复用必须依据真实等价，不能擅自换runner/source标签。
受环境断连影响，独立审查者也未读完整闭包；这是待验证的具体阻塞，不是已完成补丁。

## 恢复与保全

原研究截至227bc（不含leadership新raw）已持久保存且独立materialize读回：
uquant-pr67-global-research-evidence-20260913.tar.gz，
127372997 bytes，SHA256 2f794b97f34569021a1009bbc90589945aace1cd3dd2125f745103c8beae2995，
Library libfile_2bdf1e37a8488191aadbb594cb7ae86f v0，
file_000000007fa881f58c63496edf916b7e。
验证2310成员、2309文件SHA及长度，含相对退出、两个拒绝实验、retained三缺口、原始bull和同步权限诊断。
旧原件及报告仍见artifacts/research-20260913/continuation-index.json，历史H/276见archive-index.json，不改写原索引。

新leadership原件最后都在原工作区，尚未新打包上传；代码及本文件事实在GitHub，不能称所有新raw已远端保全。
工作区 /workspace/scratch/c347917f5f00；主工作树uquant-capital，最后已知clean，无未推送代码。
数据/缓存/日志均在evidence/：
leadership-{bull,h1}/native-cache与*-result.json；
leadership-{cross,remove308,remove502,champion}.json及*-cache；
leadership-full、leadership-no-optical-h1-2023、leadership-remove-three-h2-2024完整observations/identity/result/final_account及*-checked.json；
leadership-ownership-{champion,critical,continuity,ghost-a,ghost-b}.json及leadership-ownership-cache；
leadership-engineering-{left,right,architecture}.{log,xml}，leadership-coverage-{left,right}；
leadership-absolute/absolute-generalization-local-global-20260913-attempt-1-<shard>/manifest.json，
leadership-absolute-cache、leadership-absolute-orchestration.log；
leadership-{grant,promotion}.json、对应cache/log；
leadership-robustness-plan.json、leadership-robustness/、cross-baseline-config.json。

运行时：Python3.12.13/NumPy2.5.1/pandas3.0.5/uv0.11.33，
uv.lock SHA4accf16535b5ac95b831c9289e0ad2ff21282dc5dfae3f05dd0fb095089d6a61。
命令用 PATH=/workspace/scratch/c347917f5f00/bin:$PATH uv run --frozen（uv固定symlink），不要用全局错误版本跑经济。
旧baseline工作树uquant-baseline@9bb5842（原promotion牛市已精确复现），uquant-cross-baseline@503fa21。
历史bundle evidence/promotion-baseline-history.bundle源自7fc历史phase1 bundle，已verify/fetch恢复，勿重复下载。

中断时实际执行服务已报exec-server transport disconnected，备用node入口也报409 environment_offline。
曾发出只对本任务Absolute/cross robustness Python进程SIGSTOP的减压命令，待读回，不能断言已执行。
恢复时检查evidence/temporarily-paused-replays.json及/proc实际cwd/命令，只对确认属于本任务且确已暂停的进程恢复；不要误动其他任务。
最后运行session：工程97274/97195/39208；Ownership29121/34934/99090/33958/68059；
Absolute orchestration69655；Grant45401；promotion60923；三个nominal23486/24862/6472。
这些ID仅旧环境恢复线索，不能在新环境盲用。已结束诊断session大多已close。
停止新增并发；恢复后限制昂贵native至2个（原容器8CPU/20GiB），避免同时全测试与十多个回放。
无安全任务取消/进程停止成功的证据，不声称后台全停。

## 最小续接顺序

1. 读AGENTS/PROJECT_STATE，实时核验PR/main/工作树与执行环境；授权持续有效，不因检查点重开题或问确认。
2. 若旧evidence可访问，先保全新leadership raw并独立读回；逐个读取已产生结果和覆盖率，不重复已完整的同身份账户。
3. 优先下载上述Grant成功产物；接收现有完整验收结果，按具体失败修复。不可只认绿灯/exit0。
4. 修正已证实的旧baseline runner职责缺口后再跑旧成对控制；不改生产旧源、冻结合同或容差。
5. 完成同一最终候选全部适用工程85%branch、Grant、Ownership、Absolute8片/34LOO/final、
   promotion/full、cross-AI名义/成本/邻域/起点/贡献删除及实际平台和依赖要求。
6. 稳定修复才扩大验证，保存coherent commit→非强推原PR→direct ref核验；全部验收满足后正常合并main并核验。
   若新经济反证出现，按证据找下一授权方向，不能失败就停或强制合并。

## 冻结边界

A股AI现金多头，无杠杆/空头/自动下单；盘后决策、下个可交易日执行。
既有样本/角色移除/benchmark/seed/费用滑点/真实账户与股份责任不变，研究截至2026-08-05，之前数据只作合法预热，不能用受保护未来选择规则。
不reset/clean/rebase/force-push、丢弃未知成果或变造证据，不拓股票池/数据平台/第二引擎，不重启PR62。
h1既有wealth门1.6966895478463722、急性收益门.06390679898215934、DD门.161742775678121；
bull既有wealth门11.614003355098303、DD门.16891058946975523；
全池/冠军15倍及其他消费者自己的作用域保持，40单等既有授权不得新叠容差。
最新全局附件 UQUANT_GLOBAL_GENERALIZATION_CONTINUATION_PROMPT_20260913(1).md 是任务合同，优先于更早两轮/只盯h1-bull限制。
