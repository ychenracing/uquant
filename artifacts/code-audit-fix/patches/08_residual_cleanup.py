"""Remove duplicated work and misleading production-only research names."""
replace('uquant/portfolio/pipeline.py', '_research_caution_probe', '_caution_tactical_probe', count=2)
replace('uquant/portfolio/pipeline.py', 'RESEARCH_CAUTION_TACTICAL_PROBE', 'CAUTION_TACTICAL_PROBE')
replace_function('tests/architecture/test_architecture_governance.py',
                 'test_architecture_test_relocation_inventory_is_exact_and_bidirectional', '')
