"""Finish explicit exports and row typing; document the tightened broker inputs."""
import ast

path='uquant/application/__init__.py'
source=(ROOT/path).read_text()
exports=sorted({alias.asname or alias.name for node in ast.parse(source).body
                if isinstance(node,ast.ImportFrom) and node.module!='__future__'
                for alias in node.names} | {'ENGINE_PUBLIC_NAMES'})
write(path,source+'\n__all__ = '+repr(tuple(exports))+'\n')
replace('uquant/opportunity.py','from dataclasses import dataclass\n','from dataclasses import dataclass\nfrom typing import cast\n')
replace('uquant/opportunity.py','    broad_row = broad.loc[date]\n','    broad_row = cast(pd.Series, broad.loc[date])\n')
replace('uquant/opportunity.py','    tech_row = tech.loc[date]\n','    tech_row = cast(pd.Series, tech.loc[date])\n')
replace('tests/test_code_audit_state.py','lambda account: calls.append(', 'lambda account, calls=calls: calls.append(')
replace('tests/test_code_audit_state.py','lambda account, symbol: calls.append(', 'lambda account, symbol, calls=calls: calls.append(')
replace('artifacts/code-audit-fix/verify_candidate.py', "'--select','I,F401','--fix'", "'--select','I,F401,UP034,RUF021','--fix'")
replace('docs/CONFIGURATION.md','`account-init`、`daily`、`backtest` 共用可选 `--config settings.json`。',
        '`account-init`、`account-sync`、`daily`、`backtest` 共用可选 `--config settings.json`。')
replace('docs/OPERATIONS.md','快照顶层至少包含：', '''使用自定义配置初始化的账户，同步时也必须提供相同的 `--config settings.json`；
`daily --broker-snapshot` 会将同一有效配置传入对账与决策，两者不能各用一套限制。

券商 JSON 必须是对象，重复键会被拒绝。`cash` 必须明确给出：零现金合法，缺失现金不是零。
日期先解析再比较时间先后，新导入的快照和成交日期规范化为 `YYYY-MM-DD`；旧账户记录不回写。

快照顶层至少包含：''')
replace('docs/ARCHITECTURE.md','`engine.py` 是 application 编排的公共委托入口；', '''组合类的方法在类定义中显式声明，包初始化不再动态装配方法。`engine.py` 使用普通函数和
方法委托 application，保留仍有效的旧导入路径与类序列化身份，不在运行时修改 docstring
缩进或函数注解。普通转仓和修复来源通过 `models/ordinary_state.py` 集中访问既有账户键；
它是账户的类型化视图，不拥有第二份持久状态。风险状态清理读取结构化原因，历史事件缺少
原因码时仅在读取边界兼容旧格式。领涨报告的 `factor_profile` 描述实际用于评分的机会状态，
不把随后新分类的状态冒称评分输入。

`engine.py` 是 application 编排的公共委托入口；''')
# Prior green risk tests remain applicable: this patch changes no decision math.
replace('artifacts/code-audit-fix/verify_candidate.py', "check('diff-check',['git','diff','--check'])", """check('paired-replay',['uv','run','--no-sync','python','artifacts/code-audit-fix/replay_pair.py'],timeout=1050)
check('diff-check',['git','diff','--check'])""")
