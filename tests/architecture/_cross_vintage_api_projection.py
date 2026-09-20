"""Exact reviewed API additions; the sealed API inventory remains unchanged."""
from copy import deepcopy

from ._analysis import canonical_sha256


def cross_vintage_api_projection(contract):
    expected = deepcopy(contract)

    def insert_field(items, previous, name):
        assert name not in [item['name'] for item in items]
        index = next(i for i, item in enumerate(items) if item['name'] == previous)
        added = dict(items[index], name=name)
        items.insert(index + 1, added)

    schema = expected['account_state_schema']
    for key in ('field_order', 'serialized_key_order'):
        assert 'deployed_peak' not in schema[key]
        schema[key].insert(schema[key].index('capital_peak') + 1, 'deployed_peak')
    schema['empty_state']['deployed_peak'] = schema['empty_state']['cash']
    schema['empty_state_sha256'] = canonical_sha256(schema['empty_state'])
    modules = expected['modules']
    for name in ('uquant.models', 'uquant.models.account', 'uquant.types'):
        module = modules[name]
        insert_field(module['classes']['AccountState']['signature']['parameters'], 'capital_peak', 'deployed_peak')
        insert_field(module['dataclasses']['AccountState']['fields'], 'capital_peak', 'deployed_peak')
    capital = modules['uquant.risk.capital']['functions']
    for name in ('_apply_capital_overlays', 'apply_capital_overlays',
                 '_observe_capital_budget', 'observe_capital_budget',
                 '_capital_budget_repair_drawdown_confirmed', 'capital_budget_repair_drawdown_confirmed'):
        parameters = capital[name]['parameters']
        historical = 'capital_drawdown' if 'repair_drawdown' in name else 'capital_dd'
        current = 'operating_drawdown' if 'repair_drawdown' in name else 'operating_dd'
        assert sum(p['name'] == historical for p in parameters) == 1
        if 'observe_capital_budget' not in name:
            parameters[:] = [p for p in parameters if p['name'] != historical]
        parameter = next(p for p in parameters if p['name'] == current)
        parameter['name'] = current.replace('operating', 'deployed')
    market = modules['uquant.risk.market_book']
    insert_field(market['classes']['MarketBookEvidence']['signature']['parameters'], 'capital_dd', 'deployed_dd')
    insert_field(market['dataclasses']['MarketBookEvidence']['fields'], 'capital_dd', 'deployed_dd')
    for module, name in (('uquant.risk.transition_resolution', 'resolve_risk_transition'),
                         ('uquant.risk.transitions', '_resolve_risk_transition')):
        insert_field(modules[module]['functions'][name]['parameters'], 'capital_dd', 'deployed_dd')
    return expected
