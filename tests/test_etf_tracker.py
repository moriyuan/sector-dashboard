from datetime import datetime
import json
import pytest
from fastapi.testclient import TestClient
from backend import etf_tracker as et
from backend.store import Store, TZ
from backend.app import create_app

def test_flow_volume_and_missingness():
    a=et.metrics({'shares':15e8,'nav':2,'amount':3e8},{'shares':14e8,'nav':2},[2e8]*20)
    assert a['net']==2e8 and a['multiple']==1.5 and a['heavy'] is True
    assert et.metrics({'shares':14e8,'nav':2},{'shares':15e8},[])['net']==-2e8
    z=et.metrics({'shares':0,'nav':2,'amount':0},{'shares':0},[2]*20)
    assert z['net']==0 and z['multiple']==0 and z['heavy'] is False
    for base in ([None]*20,[2]*19,[0]*20):
        assert et.metrics({'amount':9},{},base)['multiple'] is None
    assert et.metrics({'shares':5,'nav':None},{'shares':2},[])['net'] is None
    for today,prev in [({'shares':5,'nav':1,'event':'拆分'},{'shares':2,'nav':2}),({'shares':5,'nav':1},{'shares':2,'nav':2})]:
        assert et.metrics(today,prev,[])['net'] is None
    assert et.summary([{'net':None,'heavy':None}])['net'] is None
    assert et.summary([{'net':0,'heavy':False}])['net']==0

def test_share_parsers_dates_and_units(monkeypatch):
    data={'sqlId':'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L','pageHelp':{'total':1},
        'result':[{'STAT_DATE':'2026-11-02','SEC_CODE':'510300','TOT_VOL':'1,234.5'}]}
    monkeypatch.setattr(et,'get',lambda *a:json.dumps(data))
    assert et.sse_shares('2026-11-02')['510300']==12345000
    with pytest.raises(ValueError):et.sse_shares('2026-10-30')
    data['pageHelp']['total']=2
    with pytest.raises(ValueError):et.sse_shares('2026-11-02')
    def page(url,ref,params):
        i=params['PAGENO']
        return json.dumps([dict(metadata=dict(catalogid='scsj_fund_jjgm',pageno=i,pagecount=2,recordcount=2),
            data=[dict(size_date=f'2026-10-{29+i}',fund_code='159915',current_size='5.2')])])
    monkeypatch.setattr(et,'get',page)
    assert et.sz_shares('159915','2026-10-29','2026-11-02')=={'2026-10-30':52000,'2026-10-31':52000}
    with pytest.raises(ValueError):et.sz_shares('159919','2026-10-29','2026-11-02')

def test_quotes_units_identity_and_no_nav_execution(monkeypatch):
    data={'data':{'sh510300':{'day':[['2026-11-02','4','4','4.1','3.9','100',{},'', '4']]}}}
    monkeypatch.setattr(et,'get',lambda *a:json.dumps(data))
    assert et.quotes('510300','2026-11-02','2026-11-02')['2026-11-02']==dict(amount=40000,volume=10000)
    data['data']['sh510300']['day'][0][8]='400'
    with pytest.raises(ValueError):et.quotes('510300','2026-11-02','2026-11-02')
    monkeypatch.setattr(et,'get',lambda *a:'var fS_code="510300"; var Data_netWorthTrend = [{"x":1793548800000,"y":4.0928,"unitMoney":""}]; ignored();')
    assert et.nav_history('510300','2026-11-02','2026-11-02')['2026-11-02']['nav']==4.0928
    with pytest.raises(ValueError):et.nav_history('510050','2026-11-02','2026-11-02')

def test_collection_revision_recovery_and_historical_api(tmp_path,monkeypatch):
    store=Store(tmp_path/'s')
    cal=['2026-10-30','2026-11-02','2026-11-03']
    store.observe('calendar',cal)
    monkeypatch.setattr(et,'FUNDS',{'510300':('沪深300','华泰柏瑞'),'159915':('创业板指','易方达')})
    monkeypatch.setattr(et,'sse_shares',lambda d:{'510300':100 if d==cal[0] else 110})
    monkeypatch.setattr(et,'sz_shares',lambda *a:{cal[0]:100,cal[1]:90})
    monkeypatch.setattr(et,'quotes',lambda *a:{d:dict(amount=5e8,volume=5e8) for d in cal[:2]})
    monkeypatch.setattr(et,'nav_history',lambda *a:{d:dict(nav=2,event='') for d in cal[:2]})
    close=datetime(2026,11,2,19,tzinfo=TZ)
    first=et.collect(store,cal[1],now=close)[0]
    assert et.collect(store,cal[1],now=close)[0]['revision']==first['revision']
    view=et.day_view(store,cal[1])
    assert view['summary']['net']==0 and view['rows'][1]['provisional']
    def fail(*a):raise ValueError('source failure')
    monkeypatch.setattr(et,'sz_shares',fail)
    et.collect(store,cal[1],now=datetime(2026,11,3,10,tzinfo=TZ))
    v=et.day_view(store,cal[1])
    assert v['rows'][1]['provisional'] and v['rows'][1]['retained']==['shares','prev_shares']
    assert v['summary']['net']==0 and v['issues']
    with TestClient(create_app(store,collect=False)) as client:
        assert client.get('/api/etf-tracker',params={'date':cal[1]}).json()['summary']['net']==0
        assert client.get('/api/etf-tracker',params={'date':cal[0]}).json()['rows']==[]
        assert client.get('/api/etf-tracker',params={'date':'bad'}).status_code==422
    with pytest.raises(ValueError):et.collect(store,cal[2],now=close)

def test_scheduler_sessions():
    c=['2026-10-30','2026-11-02','2026-11-03']
    at=lambda h:datetime(2026,11,2,h,tzinfo=TZ)
    assert et.due_session(at(8),c) is None
    assert et.due_session(at(10),c)==('2026-10-30','次晨确认')
    assert et.due_session(at(17),c)==('2026-11-02','盘后')
    assert et.due_session(at(20),c)==('2026-11-02','晚间复核')
    assert et.due_session(datetime(2026,11,1,20,tzinfo=TZ),c) is None
