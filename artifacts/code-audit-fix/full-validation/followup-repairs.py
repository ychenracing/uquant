"""Apply the reviewed integration-test updates, not production rule changes."""
from pathlib import Path

ROOT = Path.cwd()

def replace_once(relative, before, after):
    path = ROOT / relative
    source = path.read_text()
    assert source.count(before) == 1, (relative, before)
    path.write_text(source.replace(before, after))

replace_once('tests/_lifecycle_restoration_risk_cases.py',
    '        DEFAULT_CONFIG.market_crisis_gross,\n        4,\n        {},\n        (reason,),',
    '        DEFAULT_CONFIG.market_crisis_gross,\n        4,\n        {"recovery_owner_reset_required": True},\n        (reason,),')
replace_once('tests/architecture/test_current_allocation_book.py',
    'from pathlib import Path', 'import ast\nfrom pathlib import Path')
replace_once('tests/architecture/test_current_allocation_book.py',
    'from ._owner_transport import validate_combined_allocator_topology',
    'from ._owner_transport import validate_combined_allocator_topology\nfrom ._source_mutations import fragment_spans')
replace_once('tests/architecture/test_current_allocation_book.py',
    "'owned, strategic_targets, proposed, committed, cash_room)',\n     'owned, strategic_targets, proposed, committed, 1.0)'",
    "'owned, strategic_targets, proposed, committed, cash_room,)',\n     'owned, strategic_targets, proposed, committed, 1.0,)'" )
replace_once('tests/architecture/test_current_allocation_book.py',
    '    assert source.count(before) == 1\n    with pytest.raises(AssertionError):\n        validate_combined_allocator_topology(root=ROOT, overrides={path: source.replace(before, after)})',
    '    spans = fragment_spans(source, before)\n    assert len(spans) == 1\n    start, end = spans[0]\n    mutated = source[:start] + after + source[end:]\n    ast.parse(mutated)\n    with pytest.raises(AssertionError):\n        validate_combined_allocator_topology(root=ROOT, overrides={path: mutated})')
replace_once('tests/architecture/test_risk_boundaries.py',
    'import pytest', 'import pytest\n\nfrom ._source_mutations import fragment_spans')
replace_once('tests/architecture/test_risk_boundaries.py',
    '"concentrated_break = shock_rearmed and not protected_weights_for_current_episode(account)",\n     "concentrated_break = shock_rearmed and not account.protected_weights"',
    '"concentrated_break = (shock_rearmed and not protected_weights_for_current_episode(account)",\n     "concentrated_break = (shock_rearmed and not account.protected_weights"')
replace_once('tests/architecture/test_risk_boundaries.py',
    '    assert before in source\n    with pytest.raises(AssertionError):\n        _assert_risk_ownership_surface({path: source.replace(before, after, 1)})',
    '    spans = fragment_spans(source, before)\n    assert spans, "negative control must exercise its actual source"\n    start, end = spans[0]\n    mutated = source[:start] + after + source[end:]\n    ast.parse(mutated)\n    with pytest.raises(AssertionError):\n        _assert_risk_ownership_surface({path: mutated})')
