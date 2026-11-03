from fastapi.testclient import TestClient
from backend.app import create_app
from backend.store import Store
from backend.analytics import analyze
from backend.service import Collector


def test_api_dates_modes_and_revision_isolation(tmp_path):
    db=Store(tmp_path/'test.db')
    rows=analyze([{'code':'BK0001','name':'测试行业','price':10,'change':2,'up_ratio':None}],{},[],'2026-10-28','close')
    rev=db.save('industry','2026-10-28','close',{'rows':rows})
    db.save('industry','2026-10-29','live',{'rows':[{'code':'BK0002','name':'次日'}]})
    app=create_app(db,collect=False)
    with TestClient(app) as c:
        assert c.get('/api/sectors?date=2026-10-28&mode=live').status_code==422
        got=c.get('/api/sectors?date=2026-10-28&mode=close').json()
        assert got['rows'][0]['name']=='测试行业'
        assert got['meta']['revision']==rev
        assert c.get(f'/api/sectors?date=2026-10-29&mode=close&revision={rev}').status_code==404
        assert c.get('/api/sectors?date=bad').status_code==422
        assert c.get('/api/sectors?kind=arbitrary').status_code==422
        assert c.get('/api/sectors/bad?date=2026-10-28').status_code==422
        detail=c.get('/api/sectors/BK0001?date=2026-10-28&mode=close').json()
        assert detail['members']==[] and detail['members_available'] is False
        assert detail['flow_history']==[]


def test_index_only_history_never_invents_members_or_latest_flow(tmp_path):
    db=Store(tmp_path/'test.db')
    db.observe('catalog:industry',[{'code':'BK0001','name':'测试行业'}])
    db.observe('bars:BK0001',[{'date':'2026-10-28','close':10,'change':2,'amount':100}])
    db.observe('members:BK0001:2026-10-29',['600001'])
    db.save('flow_industry','2026-10-29','close',{'rows':{'BK0001':{'today':900}}})
    with TestClient(create_app(db,collect=False)) as c:
        listing=c.get('/api/sectors?date=2026-10-28&mode=close').json()
        assert listing['meta']['status']=='index_only'
        assert listing['rows'][0]['heat'] is None
        detail=c.get('/api/sectors/BK0001?date=2026-10-28&mode=close').json()
        assert detail['members']==[] and detail['limit_up'] is None and detail['flow_history']==[]
        assert len(detail['bars'])==1


def test_jobs_run_initially_even_with_long_interval_and_backoff(tmp_path):
    col=Collector(Store(tmp_path/'test.db'))
    called=[]
    assert col.job('calendar',lambda:called.append(1) or ['2026-10-29'],10**12)==['2026-10-29']
    assert col.job('calendar',lambda:called.append(2),10**12) is None
    assert called==[1]
    def fail():raise RuntimeError('source unavailable')
    assert col.job('market',fail) is None
    assert col.failures['market']==1
    assert col.store.events()[0]['ok']==0


def test_independent_index_data_is_visible_without_market_listing(tmp_path):
    db=Store(tmp_path/'test.db')
    db.save('indices','2026-10-29','close',{'rows':[{'code':'000001','name':'上证指数','price':3651.43,'change':-.82}]})
    with TestClient(create_app(db,collect=False)) as c:
        got=c.get('/api/market?date=2026-10-29&mode=close').json()
        assert got['stats'] is None
        assert got['indices'][0]['price']==3651.43
        assert c.get('/api/dates').json()['snapshots'][0]['trade_date']=='2026-10-29'


def test_partial_history_does_not_shrink_ranking_universe(tmp_path):
    db=Store(tmp_path/'test.db')
    db.observe('catalog:concept',[{'code':'BK0001','name':'已回补'},{'code':'BK0002','name':'未回补'}])
    db.observe('bars:BK0001',[{'date':'2026-10-28','close':10,'change':3,'amount':100}])
    with TestClient(create_app(db,collect=False)) as c:
        got=c.get('/api/sectors?date=2026-10-28&kind=concept&concept_source=eastmoney').json()
        assert len(got['rows'])==1
        assert got['rows'][0]['streak'] is None
        assert got['rows'][0]['matrix'][0]['percentile'] is None
