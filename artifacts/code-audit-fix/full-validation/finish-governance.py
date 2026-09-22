"""Integrate dependency-phase checks without altering production or raw metrics."""
from pathlib import Path

p=Path('tests/architecture/test_architecture_governance.py')
s=p.read_text()
needle='from ._portfolio_transport import expand_portfolio_allocator_method'
assert s.count(needle)==1
s=s.replace(needle, needle+'\nfrom ._initialization_edges import blocking_architecture_debt')
before='def test_architecture_duplicate_private_helper_debt_is_zero_without_generic_utils() -> None:\n    current = measured_debt(architecture_snapshot())'
after='def test_architecture_duplicate_private_helper_debt_is_zero_without_generic_utils() -> None:\n    current = blocking_architecture_debt(ROOT, measured_debt(architecture_snapshot()))'
assert s.count(before)==1
s=s.replace(before,after)
before='    snapshot = architecture_snapshot()\n    current = measured_debt(snapshot)\n    baseline = json.loads('
after='    snapshot = architecture_snapshot()\n    raw = measured_debt(snapshot)\n    request.node.user_properties.append(("source_dependency_cycles", json.dumps(raw["internal_import_cycles"])))\n    request.node.user_properties.append(("same_name_helpers", json.dumps(raw["duplicate_private_helper_groups"])))\n    current = blocking_architecture_debt(ROOT, raw)\n    baseline = json.loads('
assert s.count(before)==1
s=s.replace(before,after)
p.write_text(s)
