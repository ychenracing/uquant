"""Current moved-book checks must still reject authority and settlement corruption."""
from pathlib import Path

import pytest

from ._owner_transport import validate_combined_allocator_topology

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('path,before,after', [
    ('uquant/portfolio/allocation_book.py',
     'min(self.policy.cfg.max_gross, self.risk.target_gross_cap)', 'self.policy.cfg.max_gross'),
    ('uquant/portfolio/allocation_book.py',
     'gross_cap=self.gross_cap,', 'gross_cap=1.0,'),
    ('uquant/portfolio/allocation_book.py',
     'self.cash_room -= max(0.0, increment - reserved)',
     'self.account.cash -= max(0.0, increment - reserved)'),
    ('uquant/portfolio/pipeline.py',
     'from .allocation_book import AllocationBook',
     'from .capital import AllocationBook'),
    ('uquant/portfolio/pipeline.py',
     'owned, strategic_targets, proposed, committed, cash_room)',
     'owned, strategic_targets, proposed, committed, 1.0)'),
])
def test_moved_book_rejects_authority_mutations(path, before, after):
    source = (ROOT / path).read_text()
    assert source.count(before) == 1
    with pytest.raises(AssertionError):
        validate_combined_allocator_topology(root=ROOT, overrides={path: source.replace(before, after)})


def test_current_book_topology_passes():
    validate_combined_allocator_topology(root=ROOT)
