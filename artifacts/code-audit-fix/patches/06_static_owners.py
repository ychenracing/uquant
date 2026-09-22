"""Replace import-time method construction with statically declared delegates."""
import ast
import copy
import textwrap

owners = {
    'uquant/portfolio/allocator.py': ('PortfolioAllocator', {
        '_confirmed_recovery_gross': (None, '_confirmed_recovery_gross'),
        '_risk_attribution_mechanism': ('.risk_reduction', 'risk_attribution_mechanism'),
        '_risk_retention_score': ('.risk_reduction', 'risk_retention_score'),
        '_risk_retention_vector': ('.risk_reduction', 'risk_retention_vector'),
        '_risk_lifecycle_rank': ('.risk_reduction', 'risk_lifecycle_rank'),
        '_subset_retention_vector': ('.risk_reduction', 'subset_retention_vector'),
        '_sparse_risk_reduce': ('.risk_reduction', 'sparse_risk_reduce'),
        '_risk_reduction_metadata': ('.risk_reduction', 'risk_reduction_metadata'),
        '_turnover_aware_sector_cap': ('.risk_reduction', 'turnover_aware_sector_cap'),
        'allocate': (None, 'allocate'),
        '_commit_frozen_exit_state': ('.freeze', 'commit_frozen_exit_state'),
        '_frozen_existing_targets': ('.freeze', 'frozen_existing_targets'),
        '_allocate_strategy': ('.pipeline', 'allocate_strategy'),
    }),
    'uquant/portfolio/leaders/admission.py': ('LeaderPortfolioPolicy', {
        '_cap_opportunity_gross': ('.targets', 'cap_opportunity_gross'),
        '_conviction_shares': (None, '_conviction_shares'),
        '_conviction_evidence_qualified': (None, '_conviction_evidence_qualified'),
        '_session_clock': ('.lifecycle', 'leader_session_clock'),
        '_session_distance': ('.lifecycle', 'leader_session_distance'),
        '_correlations': (None, '_correlations'),
        '_admission_utility': (None, '_admission_utility'),
        '_dynamic_k': (None, '_dynamic_k'),
        '_rotation_allowed': ('.lifecycle', 'leader_rotation_allowed'),
        '_retention_score': ('.lifecycle', 'leader_retention_score'),
        '_leader_lifecycle_exit_confirmed': ('.lifecycle', 'leader_lifecycle_exit_confirmed'),
        '_industry_handoff': ('.lifecycle', 'industry_handoff'),
    }),
    'uquant/portfolio/strategic/discovery.py': ('StrategicPortfolioPolicy', {
        '_bounded_strategic_restore_risk_open': ('.lifecycle', 'bounded_strategic_restore_risk_open'),
        '_retire_strategic_member': ('.lifecycle', 'retire_strategic_member'),
        '_initialize_strategic_cohort': (None, '_initialize_strategic_cohort'),
        '_strategic_cohort_targets': ('.lifecycle', 'strategic_cohort_targets'),
    }),
}
for path, (classname, routes) in owners.items():
    source = (ROOT/path).read_text()
    cls = next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name==classname)
    block = next(n for n in cls.body if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='TYPE_CHECKING')
    assert {n.name for n in block.body} == set(routes)
    methods = []
    for method in block.body:
        method = copy.deepcopy(method)
        module, target = routes[method.name]
        positional = [arg.arg for arg in (*method.args.posonlyargs,*method.args.args)]
        kwargs = [arg.arg+'='+arg.arg for arg in method.args.kwonlyargs]
        assert method.args.vararg is None and method.args.kwarg is None
        body = (f'from {module} import {target}\n' if module else '')
        body += f'return {target}({", ".join(positional+kwargs)})'
        method.body = ast.parse(body).body
        methods.append(textwrap.indent(ast.unparse(method),'    '))
    lines = source.splitlines(keepends=True)
    write(path, ''.join(lines[:block.lineno-1])+'\n\n'.join(methods)+'\n'+''.join(lines[block.end_lineno:]))
    text = (ROOT/path).read_text()
    if text.count('TYPE_CHECKING') == 1:
        text = text.replace('from typing import TYPE_CHECKING\n','').replace('TYPE_CHECKING, ','')
        write(path,text)

write('uquant/portfolio/__init__.py', '''"""The single, statically declared portfolio allocator."""
from ..portfolio_core import current_weights, effective_n
from .allocator import PortfolioAllocator

__all__ = ("PortfolioAllocator", "current_weights", "effective_n")
''')
write('uquant/portfolio/leaders/__init__.py', '''"""Public leader-policy interface."""
from .admission import LeaderPortfolioPolicy as LeaderPortfolioPolicy
''')
write('uquant/portfolio/strategic/__init__.py', '''"""Public strategic-policy interface."""
from .discovery import StrategicPortfolioPolicy as StrategicPortfolioPolicy
''')
replace('uquant/portfolio/allocator.py', 'from ..portfolio_recovery import RecoveryPortfolioPolicy', 'from .recovery import RecoveryPortfolioPolicy')
replace('uquant/portfolio/leaders/admission.py', 'from ...portfolio_strategic import StrategicPortfolioPolicy', 'from ..strategic import StrategicPortfolioPolicy')
replace('uquant/portfolio/recovery/admission.py', 'from ...portfolio_leaders import LeaderPortfolioPolicy', 'from ..leaders import LeaderPortfolioPolicy')

# Materialize the already reviewed engine signatures and delegates as ordinary
# definitions. Runtime dependencies remain the existing engine module seams.
app_path='uquant/application/__init__.py'
app=(ROOT/app_path).read_text()
tree=ast.parse(app)
functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
engine_path='uquant/engine.py'
engine=(ROOT/engine_path).read_text()

def materialize(factory, inner, name, replacements):
    function=copy.deepcopy(next(n for n in functions[factory].body if isinstance(n,ast.FunctionDef) and n.name==inner))
    function.name=name
    for arg in function.args.args:
        if arg.arg=='self': arg.annotation=None
    source=ast.unparse(function)
    for old,new in replacements.items(): source=source.replace(old,new)
    return source

cache_load=materialize('bind_risk_timeline_disk_cache','load','_load_risk_timeline_disk_cache',{
    'load_risk_timeline_disk_cache(':'_application.load_risk_timeline_disk_cache(',
    'cache_schema()':'_RISK_TIMELINE_CACHE_SCHEMA',
})
# Do not accidentally qualify the new definition's own name.
cache_load=cache_load.replace('def __application.load_risk_timeline_disk_cache','def _load_risk_timeline_disk_cache')
cache_write=materialize('bind_risk_timeline_disk_cache','write','_write_risk_timeline_disk_cache',{
    'write_risk_timeline_disk_cache(':'_application.write_risk_timeline_disk_cache(',
    'cache_schema()':'_RISK_TIMELINE_CACHE_SCHEMA',
})
cache_write=cache_write.replace('def __application.write_risk_timeline_disk_cache','def _write_risk_timeline_disk_cache')
attribution=materialize('bind_target_attribution','bound','_attach_target_attribution',{
    'return attach_target_attribution(':'return _application.attach_target_attribution(',
    'legacy_industry()':'_LEGACY_INDUSTRY', 'legacy_manifest_sha256()':'_LEGACY_MANIFEST_SHA256',
})
start=engine.index('_load_risk_timeline_disk_cache, _write_risk_timeline_disk_cache = (')
end=engine.index('attach_target_attribution = _attach_target_attribution',start)
engine=engine[:start]+cache_load+'\n\n\n'+cache_write+'\n\n\n'+attribution+'\n\n\n'+engine[end:]
resolve={
    'assess_risk_fn()':'assess_risk', 'evaluate_sentinel_fn()':'evaluate_sentinel',
    'reconcile_account_orders_fn()':'reconcile_account_orders',
    'code_fingerprint_fn()':'code_fingerprint', 'attach_target_attribution_fn()':'_attach_target_attribution',
    'timeline_builder()':'build_risk_evidence_timeline', 'native_timeline_builder()':'_RISK_TIMELINE_BUILDER',
    'shared_timeline_cache()':'_SHARED_RISK_TIMELINE_CACHE',
    'load_disk_cache_fn()':'_load_risk_timeline_disk_cache', 'write_disk_cache_fn()':'_write_risk_timeline_disk_cache',
    'performance_metrics_fn()':'performance_metrics',
}
# Longer replacements first so native_timeline_builder is not partially rewritten.
resolve=dict(sorted(resolve.items(),key=lambda item:-len(item[0])))
method_specs=(('bind_causal_risk_timeline','_causal_risk_timeline','causal_risk_timeline'),
              ('bind_engine_decision','decide','run_decision'),
              ('bind_engine_observed_decision','_observe_decision','run_observed_decision'),
              ('bind_engine_backtest','backtest','run_backtest'))
methods=[]
for factory,name,target in method_specs:
    text=materialize(factory,'bound',name,{**resolve, f'return {target}(':f'return _application.{target}('})
    if name=='_observe_decision': text=text.replace('-> Any:', '-> _application.ObservedDecisionResult:')
    methods.append(textwrap.indent(text,'    '))
start=engine.index('    _causal_risk_timeline = _application.bind_causal_risk_timeline(')
engine=engine[:start]+'\n\n'.join(methods)+'''\n\n    equity = _application.mark_equity
    _mark_account_positions = _application.mark_account_positions
    deterministic_decision = _application.deterministic_decision
'''
engine=engine.replace('    SystemConfig,\n','    SystemConfig,\n    AIUniverse,\n    AccountState,\n    Decision,\n    PendingOrder,\n    Target,\n    StrategicUniverseDeclaration,\n')
write(engine_path,engine+'\n')

# Keep the application package as a plain explicit dependency boundary.
imports=[]
for node in tree.body:
    if isinstance(node,ast.ImportFrom):
        if node.module in {'collections.abc','pathlib','typing'}: continue
        if node.module == '__future__':
            imports.append(ast.unparse(node))
            continue
        for alias in node.names:
            alias.asname=alias.asname or alias.name
        imports.append(ast.unparse(node))
    elif isinstance(node,ast.Import): imports.append(ast.unparse(node))
public=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ENGINE_PUBLIC_NAMES' for t in n.targets))
write(app_path, '"""Explicit application orchestration dependencies for the engine facade."""\n\n'+'\n'.join(imports)+'\nfrom .decision import _DecisionResult as ObservedDecisionResult\n\n'+ast.unparse(public)+'\n')
