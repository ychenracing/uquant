from uquant.portfolio.quarter_priority import order_known_candidates


def test_missing_slots_ties_and_original_eligibility_are_preserved():
    assert order_known_candidates(['a','missing','b','c'], {'a':1,'b':3,'c':3}) == ['b','missing','c','a']
    assert order_known_candidates(['a','b'], {}) == ['a','b']
    assert order_known_candidates(['a','b'], {'outside':100,'a':2,'b':2}) == ['a','b']
