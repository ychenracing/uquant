"""Give opportunity-stage results names and concrete domain types."""
import ast
import copy

path='uquant/opportunity.py'
source=(ROOT/path).read_text()
tree=ast.parse(source)
functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
market_fields={'bear_trend':'bool','breadth20_ratio':'float','breadth60_ratio':'float',
               'broad_bull':'bool','broad_ret1':'float','broad_row':'pd.Series',
               'tech_bull':'bool','tech_ret1':'float','tech_row':'pd.Series'}
evidence_fields={'mature_count':'int','recent_crash':'bool','regime':'Opportunity','score_gap':'float','stable':'bool'}
annotations={'account':'AccountState','broad':'pd.DataFrame','cfg':'SystemConfig',
             'date':'pd.Timestamp','reference_context':'ReferenceContext | None',
             'reference_panel':'dict[str, pd.DataFrame]','tech':'pd.DataFrame',
             'leaders':'dict[str, LeaderScore]','risk':'Risk','evidence':'int','run':'int',
             'fast_flip':'bool',**market_fields}

def typed(function):
    function=copy.deepcopy(function)
    for arg in (*function.args.args,*function.args.kwonlyargs):
        if arg.arg in annotations:
            arg.annotation=ast.parse(annotations[arg.arg],mode='eval').body
    return function

def result(function,name,fields):
    node=function.body[-1]
    assert isinstance(node,ast.Return) and isinstance(node.value,ast.Tuple)
    items=node.value.elts
    wanted=[]
    for item in items:
        assert isinstance(item,ast.Name)
        if item.id in fields: wanted.append(ast.keyword(arg=item.id,value=item))
    assert {k.arg for k in wanted}==set(fields)
    node.value=ast.Call(func=ast.Name(id=name,ctx=ast.Load()),args=[],keywords=wanted)
    function.returns=ast.Name(id=name,ctx=ast.Load())

collect=typed(functions['_collect_opportunity_market_evidence'])
result(collect,'_OpportunityMarket',market_fields)
transition=typed(functions['_transition_opportunity_regime'])
# Regime transition consumes only state, signed evidence and its streak.
transition.args.kwonlyargs=[a for a in transition.args.kwonlyargs if a.arg in {'account','evidence','fast_flip','run'}]
transition.args.kw_defaults=[None]*len(transition.args.kwonlyargs)
transition.body=[n for n in transition.body if not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'score_gap','mature_count','tech_history','recent_crash'} for t in n.targets))]
transition.body[-1]=ast.Return(value=ast.Name(id='regime',ctx=ast.Load()))
transition.returns=ast.Name(id='Opportunity',ctx=ast.Load())
evaluate=typed(functions['_evaluate_opportunity_evidence'])
for i,node in enumerate(evaluate.body):
    if (isinstance(node,ast.Assign) and isinstance(node.value,ast.Call)
            and isinstance(node.value.func,ast.Name) and node.value.func.id=='_transition_opportunity_regime'):
        node.targets=[ast.Name(id='regime',ctx=ast.Store())]
        node.value.keywords=[k for k in node.value.keywords if k.arg in {'account','evidence','fast_flip','run'}]
        evaluate.body[i+1:i+1]=ast.parse('''mature_count = sum(item.mature for item in leaders.values())
score_gap = ranked[0] - ranked[2] if len(ranked) >= 3 else (ranked[0] if ranked else 0.0)
tech_history = tech.loc[:date, "close"]
recent_crash = False''').body
        break
else: raise AssertionError('missing transition call')
evaluate.body=[n for n in evaluate.body if not (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='recovery_key' for t in n.targets))]
result(evaluate,'_OpportunityEvidence',evidence_fields)
classify=copy.deepcopy(functions['classify_opportunity'])
for i,node in enumerate(classify.body):
    if isinstance(node,ast.Assign) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name):
        if node.value.func.id=='_collect_opportunity_market_evidence':
            node.targets=[ast.Name(id='market',ctx=ast.Store())]
        if node.value.func.id=='_evaluate_opportunity_evidence':
            node.targets=[ast.Name(id='assessment',ctx=ast.Store())]
class NamedResults(ast.NodeTransformer):
    def visit_Name(self,node):
        if not isinstance(node.ctx,ast.Load): return node
        if node.id in market_fields:
            return ast.copy_location(ast.Attribute(value=ast.Name(id='market',ctx=ast.Load()),attr=node.id,ctx=ast.Load()),node)
        if node.id in evidence_fields:
            return ast.copy_location(ast.Attribute(value=ast.Name(id='assessment',ctx=ast.Load()),attr=node.id,ctx=ast.Load()),node)
        if node.id=='recovery_key': return ast.copy_location(ast.Constant('recovery_stable'),node)
        return node
classify=NamedResults().visit(classify)
records=[]
for name,fields in (('_OpportunityMarket',market_fields),('_OpportunityEvidence',evidence_fields)):
    records.append('@dataclass(frozen=True, slots=True)\nclass '+name+':\n'+''.join('    '+key+': '+typ+'\n' for key,typ in fields.items()))
prefix=source[:source.index('def _collect_opportunity_market_evidence')]
prefix=prefix.replace('from typing import Any','from dataclasses import dataclass')
code=prefix+'\n\n'.join(records)+'\n\n\n'+'\n\n\n'.join(ast.unparse(ast.fix_missing_locations(n)) for n in (collect,transition,evaluate,classify))+'\n'
write(path,code)
