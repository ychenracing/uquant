"""F05-F06/F14: invalidate dependent caches and report the profile actually used."""
replace('uquant/engine.py', '            workspace.replace_data_store(cast(DataStore, value))\n', '''            workspace.replace_data_store(cast(DataStore, value))
            self._leader_score_cache.clear()
            self._reversal_observation_cache.clear()
            self._risk_timeline_cache_key = None
            self._risk_timeline_cache = None
''')
replace('uquant/application/decision.py', '    cfg: SystemConfig\n    reference_context: ReferenceContext\n', '    cfg: SystemConfig\n    scoring_opportunity: str\n    reference_context: ReferenceContext\n')
replace('uquant/application/decision.py', '    decision_cfg = _decision_config_for_universe(len(inputs.user_symbols), self.cfg)\n', '    decision_cfg = self.cfg\n')
replace('uquant/application/decision.py', '        cfg=decision_cfg,\n        reference_context=reference_context,\n', '        cfg=decision_cfg,\n        scoring_opportunity=account.opportunity,\n        reference_context=reference_context,\n')
replace('uquant/application/decision.py', '''        if opportunity in {Opportunity.STRONG_TREND, Opportunity.TREND}
        else "RECOVERY"
        if opportunity is Opportunity.RECOVERY
''', '''        if market.scoring_opportunity in {Opportunity.STRONG_TREND.value, Opportunity.TREND.value}
        else "RECOVERY"
        if market.scoring_opportunity == Opportunity.RECOVERY.value
''')
replace('uquant/leader.py', '    if opportunity is Opportunity.RECOVERY.value or opportunity == Opportunity.RECOVERY.value:\n', '    if opportunity == Opportunity.RECOVERY.value:\n')
