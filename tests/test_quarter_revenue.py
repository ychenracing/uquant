from research.quarter_revenue import available_quarters, quarter_panel


def report(period, index, date, revenue, prior, growth, cells=None):
    return dict(symbol='sh600000',period=period,period_index=index,disclosed_date=date,
                cumulative_revenue_reported=revenue,prior_revenue_reported=prior,
                cumulative_revenue_yoy_percent=growth,revenue_rows=[cells] if cells else [],
                comparable_prior_basis_verified=True,revenue_unit='CNY',status='verified_numeric_original',sha256=date,source_url=date)

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
    a.update(standalone_quarter_verified=True, standalone_quarter_yoy_percent=-10)
    revised=dict(a,disclosed_date='2023-11-01',source_url='revision',revenue_rows=[['营业收入','90','80','100','25']])
    assert quarter_panel([revised,a])[0]['quarter_yoy_percent']==-10


def test_restatement_basis_must_be_verified_before_cumulative_subtraction():
    first = report('2022Q3', 3, '2022-10-28', 447209964.72, 349498796.17, 27.96)
    annual = report('2022FY', 4, '2023-03-31', 595657795.8, 503551819.77, 18.29)
    annual['comparable_prior_basis_verified'] = False
    assert quarter_panel([first, annual])[0]['quarter_yoy_percent'] is None


def test_incidental_five_column_table_is_not_quarter_evidence():
    row = report('2023Q3', 3, '2023-10-28', 120611.07, 88963.29, 35.57,
                 ['营业收入', '120,611.07', '88,963.29', '35.57%', '收入增长'])
    assert quarter_panel([row])[0]['quarter_yoy_percent'] is None
