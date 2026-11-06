from copy import deepcopy
from backend.relay import estimate, model_info


def stock(**overrides):
    return dict(dict(code='000001',name='样本',is_st=False,price=11,pre_close=10,upper_limit=11,amount=1e8,change=10,boards=1,limit_status='sealed'),**overrides)


def test_probability_has_evidence_and_is_not_individual_calibration():
    p=estimate(stock(),'2026-11-03','close')
    assert p['status']=='estimated' and .16<p['probability']<.17
    assert p['n']==11604 and p['interval'][0]<p['probability']<p['interval'][1]
    assert p['audit_n']==1115 and p['group']=='10:1'
    assert model_info()['label_end']=='2026-11-02'
    assert estimate(stock(boards=1.0),'2026-11-03','close')['probability']==p['probability']
    assert estimate(stock(boards=1.5),'2026-11-03','close')['status']=='unknown_height'


def test_unknowns_are_never_zero_or_a_fallback_probability():
    for row,status in [(stock(is_st=True),'excluded_st'),(stock(limit_status='not_sealed'),'not_sealed'),
        (stock(boards=None,limit_status='sealed_height_unknown'),'unknown_height'),
        (stock(limit_status='source_conflict'),'source_conflict'),(stock(price=10.9),'unconfirmed'),
        (stock(upper_limit=None),'unconfirmed'),(stock(upper_limit=12,price=12),'unsupported_regime'),
        (stock(code='300001',upper_limit=12,price=12,boards=3),'insufficient_samples'),
        (stock(code='300001',upper_limit=12,price=12,boards=2),'insufficient_validation')]:
        result=estimate(row,'2026-11-03','close')
        assert result['status']==status and result['probability'] is None


def test_date_and_mode_boundary_preserve_input():
    row=stock();original=deepcopy(row)
    assert estimate(row,'2026-11-03','midday')['status']=='close_only'
    assert estimate(row,'2026-10-30','close')['status']=='historical_unavailable'
    assert estimate(row,'2026-12-03','close')['status']=='model_expired'
    assert estimate(row,'2026-11-02','close')['retrospective'] is True
    assert row==original


def test_api_enriches_only_selected_revision_and_keeps_snapshot_intact(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.store import Store
    s=Store(tmp_path/'db');row=stock(industry=[],concepts=[])
    rev=s.save('gainers','2026-11-03','close',{'rows':[row]})
    s.save('gainers','2026-11-03','close',{'rows':[dict(row,limit_status='not_sealed')]})
    with TestClient(create_app(s,collect=False)) as client:
        old=client.get(f'/api/gainers?date=2026-11-03&revision={rev}').json()
        assert old['rows'][0]['relay']['status']=='estimated'
        assert client.get('/api/gainers?date=2026-11-03').json()['rows'][0]['relay']['probability'] is None
        assert client.get('/api/gainers?date=2026-10-30').json()['rows']==[]
    assert 'relay' not in s.snapshot('gainers','2026-11-03','close',rev)['payload']['rows'][0]
