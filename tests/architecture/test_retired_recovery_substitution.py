"""The old substitution generator cannot re-enter the daily production path."""
import ast
from pathlib import Path

from uquant.portfolio import PortfolioAllocator
from uquant.portfolio_recovery import RecoveryPortfolioPolicy

ROOT = Path(__file__).resolve().parents[2]
RETIRED = {
    '_recovery_anchor_substitution', 'recovery_anchor_substitution',
    '_pending_recovery_substitution_targets', 'pending_recovery_substitution_targets',
    '_confirmed_recovery_substitution_targets', 'confirmed_recovery_substitution_targets',
    '_SubstitutionContext', '_SubstitutionPair',
}


def test_retired_recovery_generator_has_no_executable_references():
    namespace = ast.parse((ROOT / 'uquant/portfolio/recovery/substitution.py').read_text())
    assert len(namespace.body) == 1 and isinstance(namespace.body[0], ast.Expr)
    assert isinstance(namespace.body[0].value, ast.Constant)
    assert isinstance(namespace.body[0].value.value, str)
    assert not hasattr(PortfolioAllocator, '_recovery_anchor_substitution')
    assert not hasattr(RecoveryPortfolioPolicy, '_recovery_anchor_substitution')
    for path in (ROOT / 'uquant').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            value = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.alias)):
                value = node.name
            elif isinstance(node, ast.Name):
                value = node.id
            elif isinstance(node, ast.Attribute):
                value = node.attr
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
            assert value not in RETIRED, (path, value)
