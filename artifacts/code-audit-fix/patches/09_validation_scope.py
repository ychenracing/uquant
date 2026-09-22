"""Extend risk-driven validation to the structurally changed call paths."""
replace('artifacts/code-audit-fix/verify_candidate.py',
        "changed = subprocess.check_output(['git','diff','--name-only',BASE],text=True).splitlines()",
        "subprocess.run(['git','add','uquant','tests','benchmarks','pyproject.toml'],check=True)\nchanged = subprocess.check_output(['git','diff','--name-only',BASE],text=True).splitlines()")
replace('artifacts/code-audit-fix/verify_candidate.py',
        "check('imports', ['uv','run','--no-sync','ruff','check','--select','I','--fix',*source,'tests/test_code_audit_boundaries.py'])",
        "check('format', ['uv','run','--no-sync','ruff','format',*source,*[p for p in tests if 'code_audit' in p]])\ncheck('imports', ['uv','run','--no-sync','ruff','check','--select','I,F401','--fix',*source,*[p for p in tests if 'code_audit' in p]])")
replace('artifacts/code-audit-fix/verify_candidate.py',
        "check('diff-check',['git','diff','--check'])",
        """check('state-invariants', ['uv','run','--no-sync','pytest','-q',
    'tests/test_sentinel_freeze_new_risk.py','tests/test_risk_transitions.py',
    'tests/test_ordinary_cash_rearm.py','tests/test_repair_capital_custody.py',
    'tests/test_core_residual_capital.py','tests/test_core_bounded_risk_restoration.py'],timeout=600)
check('public-seams',['uv','run','--no-sync','pytest','-q',
    'tests/architecture/test_portfolio_boundaries.py::test_portfolio_historical_class_and_instance_monkeypatch_seams_remain_live',
    'tests/architecture/test_execution_application_boundaries.py::test_execution_engine_imports_when_python_strips_docstrings_and_assertions'],timeout=120)
check('diff-check',['git','diff','--check'])""")
# This directory holds frozen producer/transport scripts, not production imports.
replace('pyproject.toml', '    "artifacts/unified-allocation",\n', '    "artifacts/unified-allocation",\n    "artifacts/code-audit-fix",\n')
# Record all new production bytes after Ruff, including staged files.
replace('artifacts/code-audit-fix/verify_candidate.py',
        "(OUT/'VERIFICATION.json').write_text(encoded)",
        "(OUT/'VERIFICATION_SUMMARY.json').write_text(json.dumps({'run':RUN,'passed':report['passed'],'checks':[{k:r[k] for k in ('name','exit_code','tail')} for r in results]},indent=2)+'\\n')\n(OUT/'VERIFICATION.json').write_text(encoded)")
