from research.quarter_revenue import quarter_panel, available_quarters

def report(period, index, date, revenue, prior, growth, cells=None):
    return dict(symbol='sh600000',period=period,period_index=index,disclosed_date=date,
                cumulative_revenue_reported=revenue,prior_revenue_reported=prior,
                cumulative_revenue_yoy_percent=growth,revenue_rows=[cells] if cells else [],
                revenue_unit='CNY',status='verified_numeric_original',sha256=date,source_url=date)

def test_difference_waits_for_both_inputs_and_keeps_missing():
    a=report('2023Q1',1,'2023-04-28',120,100,20)
    b=report('2023H1',2,'2023-08-28',300,220,36.36)
    panel=quarter_panel([a,b])
    assert available_quarters(panel,'2023-08-28')['sh600000']['period']=='2023Q1'
    assert available_quarters(panel,'2023-08-29')['sh600000']['quarter_yoy_percent']==50
    a['prior_revenue_reported']=None
    assert quarter_panel([a,b])[0]['quarter_yoy_percent'] is None

def test_q3_is_standalone_not_cumulative_and_later_revision_not_backfilled():
    a=report('2023Q3',3,'2023-10-28',100,80,25,['营业收入','30','-10','100','25'])
    revised=dict(a,disclosed_date='2023-11-01',source_url='revision',revenue_rows=[['营业收入','90','80','100','25']])
    assert quarter_panel([revised,a])[0]['quarter_yoy_percent']==-10
