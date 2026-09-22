from __future__ import annotations

import pytest

from ._analysis import ROOT
from ._initialization_edges import (
    assert_cache_facade_delegation,
    blocking_architecture_debt,
    initialization_cycles,
)


def test_current_import_initialization_is_acyclic() -> None:
    assert initialization_cycles(ROOT) == []


@pytest.mark.parametrize('kind', ['eager', 'bootstrap', 'rebound_type_guard'])
def test_import_initialization_gate_rejects_real_cycles(tmp_path, kind) -> None:
    package = tmp_path / 'uquant'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'a.py').write_text('from . import b\n')
    sources = {
        'eager': 'from . import a\n',
        'bootstrap': 'def load():\n    from . import a\nload()\n',
        'rebound_type_guard': 'from typing import TYPE_CHECKING\nTYPE_CHECKING = True\nif TYPE_CHECKING:\n    from . import a\n',
    }
    (package / 'b.py').write_text(sources[kind])
    assert initialization_cycles(tmp_path)


@pytest.mark.parametrize('before,after', [
    ('path, _RISK_TIMELINE_CACHE_SCHEMA, key=key', 'path, "wrong-schema", key=key'),
    ('timeline=timeline', 'timeline=None'),
    ('return _application.load_risk_timeline_disk_cache', 'return _application.write_risk_timeline_disk_cache'),
])
def test_cache_facade_rejects_semantic_changes(before, after) -> None:
    source = (ROOT / 'uquant/engine.py').read_text()
    assert before in source
    with pytest.raises(AssertionError):
        assert_cache_facade_delegation(ROOT, source.replace(before, after, 1))


def test_new_duplicate_helper_is_not_hidden() -> None:
    unknown = {'name': '_other', 'members': [{'module': 'uquant.engine'}, {'module': 'uquant.data'}]}
    debt = {'internal_import_cycles': [], 'duplicate_private_helper_groups': [unknown]}
    assert blocking_architecture_debt(ROOT, debt)['duplicate_private_helper_groups'] == [unknown]
