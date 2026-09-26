# PR #92 续作原件（2026-09-25）

本目录新增证据，不覆盖 remediation-20260925 或 data/frozen。

本地生产源码 a1c2d390ab3f15fb00e771b7e1b2fd548441c0c3；续作仅修改文档、
远端真实数据验证作业及本目录脚本，没有修改生产策略/合同/冻结输入。
Python 3.12.13、numpy 2.5.1、pandas 3.0.5、最终 uv 0.11.33。

- 初次哨兵 160 通过 / 11 失败：浅克隆缺少固定历史证据 7fcf9562，原始失败日志保留。
- 补取后，CI artifact 校验 41/41 通过（runner/provenance、失败保留、汇总读回）。
- projection 附加检查 7 通过 / 5 失败：4 项缺少其他封存历史提交，1 项 uv 版本不匹配。
  未修改封存验收或放宽环境。历史提交相关 4 项留给 fetch-depth:0 的 CI，不冒称本地通过。
- 锁定 uv 后，账户/schema/公司行动、原失败 projection 与当前 runtime 共 9/9 通过。
  原初次哨兵其余成功项继续复用；不重跑未受影响的整个项目测试。
- Baostock 0.9.4 本地真实取数失败：服务器连接失败/Broken pipe，CLI 返回 1。
  没有发布快照，不将构造测试当成真实数据验证。

`verify_company_actions.py` 在新快照上使用真实分红/送转事件及原始价格，
人工构造研究持仓验证股数、成本、应收/现金、理论除权权益、幂等及序列化读回。
这是实际事件上的会计路径验证，不是券商真实账户对账，也不验证股息税的所有账户身份。
脚本必须同时发现现金分红与送转事实才通过；报错也写 result.json。
Extended Economic 的独立 real-company-actions job 保存新快照、账户与失败日志。

配股维持明确不支持；三个机制保留，理由见 PROJECT_STATE.md 与 OPERATIONS.md。
正式矩阵及 required checks 的实时结果、对应最终 HEAD 和下载原件回执更新到 PR #92。
未经完整运行和读回不能声称 15×/23.284× 或完整验收通过。
