# 股份与现金输入

`DataStore` 在数据目录含 `ACCOUNT_INPUT.json` 时核验原始来源和行情文件，再为原始 OHLC
附加因果信号列。`ProductionEngine.backtest()` 在开盘成交前处理除权与开盘到账，在收盘
决策前处理收盘到账和登记日权益；研究回放调用同一执行与账户所有者。

这项价格和会计能力不授予新的业务资格或交易权限。生产点时成员、风险限制、交易容量、
T+1 和手数约束继续生效。缺少完整资格或公司行动依据的证券区间不能认定为已验收输入。

## 输入约束

清单顶层字段为 `schema`、`start`、`end`、`sessions`、`files`、`sources`、`coverage`、
`actions`、`tax_debits`；`schema` 为 `historical-raw-shares`，终点不晚于 `2026-08-05`。

| 字段 | 内容与校验 |
|---|---|
| `sessions` | 有序、唯一的交易所日期；证券缺行须逐日列出未上市或停牌原始依据 |
| `files` | 证券代码到原始 CSV 字节 SHA-256 的映射 |
| `sources` | 来源 ID 到目录内文件路径、HTTPS 原始 URL、字节 SHA-256 的映射 |
| `coverage` | 每只证券的 `reviewed_from`、`reviewed_through`、`action_sources`、`absent_sessions` |
| `actions` | `CorporateAction` 的全部显式字段；实际现金／股数比例与交易所除权参考比例分开 |
| `tax_debits` | `DividendTaxDebit` 的身份、行动 ID、实际日期、开盘／收盘时点、金额和来源 |

股票 CSV 须有原始 `date,open,high,low,close,volume,amount,reported_change`。
`reported_change` 为行情源报告的涨跌金额，用于还原该日交易所参考前收价。
成交额与成交量不以估算或零值替代。除权参考无法与已审实施公告解释一致时拒绝载入。
指数无需 `reported_change`，其交易日和原始文件仍须核验。
研究快照的 `DATA_MANIFEST.json` 还须用 `account_input_sha256` 绑定整个账户输入清单，
独立读回同时核对这项身份；只固定 CSV 哈希不足以证明公司行动来源未被替换。

`signal_open/high/low/close` 只在已披露行动实际除权时向前链接，过去行不因未来行动重算。
指标计算使用信号序列，并把均线、ATR 和价格阈值换回当日原价单位；实际成交和市值始终
使用原始价格，成交量按实际股份比例保持可比。数据前缀身份同时绑定当时可用的行动条款。

## 权益与会计

登记日收盘持仓决定权益。除权日确认分红应收和股份权利；未到账应收不能支付买单。
现金按明确的 `payment_date/payment_phase` 入账，股份按 `share_available_date` 交付并
保留原始买入归属和总成本。股份权利计入净值与经济敞口，交付不会再次计入收益。

卖出时按法定 FIFO 持有期确认红利税负，与策略选择卖出哪个持仓批次分开。实际现金扣款
只消费来源绑定的扣款记录；不能把卖出日自动当作券商扣款日。公告只给支付日期时，不能
据此声称已经证明日内到账时点。测试中的收盘到账是显式账户情境，不是公告披露字段。

现金、股数、权益、税负和处理游标随账户保存。重复调用幂等；遗漏已知处理日、逆序恢复、
改写来源条款或缺少登记日依据均拒绝。需要股东排名分配的零碎股、缺失税务性质的送转和
非零应税送股不以猜测金额入账。带公司行动状态的账户拒绝券商快照覆盖。

## 可重复的小案例

锁定开发环境安装完成后，在仓库根目录运行：

```bash
python -m pytest -q tests/test_corporate_actions.py::test_reviewed_original_bytes_feed_native_engine_execution_and_nav
python -m pytest -q tests/test_corporate_action_attribution.py tests/test_historical_account_input.py
```

案例包含澜起科技 2023 年实施公告原始 PDF、来源哈希及真实行情。它检查原始字节进入
数据读取、原生成交、分红权益、现金和 NAV、账户读回，并验证来源改写和输入缺行被拒绝。
其公告为[2023 年 8 月 5 日权益分派实施公告](https://static.cninfo.com.cn/finalpage/2023-08-05/1217467414.PDF)。

小案例只证明所覆盖的来源与账户边界。完整研究基线还必须覆盖全部目标成员、应有交易日、
全部适用行动和点时资格，并独立读回原始输入、成交、现金、费用、持仓与归因。冻结前复权
回放、部分窗口或对税款到账时点的假设，均不能替代完整真实股份基线。
