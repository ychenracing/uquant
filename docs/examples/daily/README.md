# 日扫报告示例

以下文件由 `tests/test_daily_report_examples.py` 中的原生 `Decision`、账户和订单测试对象，经当前报告渲染器生成。日期为 2025-01-06，均为测试夹具，不是今日交易建议；顶部标识是示例标签，不属于策略判断。

| 场景 | Markdown | 离线 HTML |
| --- | --- | --- |
| 有买入意图，等待下一可交易日核对 | [buy.md](buy.md) | [buy.html](buy.html) |
| 候选通过部分检查，但新增风险被冻结 | [frozen.md](frozen.md) | [frozen.html](frozen.html) |
| 有清仓意图，原订单部分成交且最近执行受涨跌停阻止 | [sell-blocked.md](sell-blocked.md) | [sell-blocked.html](sell-blocked.html) |

已验证行动摘要、部分支持证据、冻结含义、未结清余量、原执行日期及双格式转义。浏览器安全策略阻止本次本地页面预览，未完成桌面或移动端截图验收。HTML 使用响应式布局，不依赖网络、脚本、字体下载或外部样式。

在仓库根目录重新生成：

```bash
PYTHONPATH=tests uv run python -c 'from test_daily_report_examples import write_examples; write_examples("docs/examples/daily")'
```

正常使用方式见 [配置](../../CONFIGURATION.md) 和 [操作手册](../../OPERATIONS.md)。报告中的“目标”“意图”“最近执行记录”分别表示不同事实；过去的执行阻碍不能预测下一开盘能否成交。
