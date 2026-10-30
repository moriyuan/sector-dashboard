import json
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.provider import DataError, Eastmoney
from backend.public_data import PublicData, tencent_rows
from backend.rankings import at_limit, build_rankings
from backend.service import Collector
from backend.store import Store, TZ

DAY='2026-10-29'
STAMP=DAY+'T16:14:00+08:00'


def quote(code='600001',**kwargs):
    return dict(dict(code=code,name='测试',price=11,change=10,amount=10000,upper_limit=11,lower_limit=9,source_time=STAMP),**kwargs)


def test_tencent_units_timestamp_limits_and_missing_response():
    fields=['']*58
    for i,value in {1:'测试',2:'600001',3:'11',4:'10',30:'20261029161400',32:'10',33:'11',34:'9',37:'1',38:'1.5',47:'11',48:'9',57:'1.2345'}.items():fields[i]=value
    raw='v_sh600001="'+'~'.join(fields)+'";'
    row=tencent_rows(raw,['sh600001'])[0]
    assert row['amount']==12345 and row['source_time']==STAMP
    assert at_limit(row) is True
    assert at_limit(dict(row,price=10.99)) is False
    assert at_limit(dict(row,upper_limit=-1)) is None
    with pytest.raises(DataError,match='缺少'):tencent_rows(raw,['sh600001','sz000001'])


def test_quote_stale_rows_do_not_enter_breadth(tmp_path):
    p=PublicData(Store(tmp_path/'db'),DAY)
    p.directory=lambda:['sh'+str(600000+i) for i in range(21)]
    p.quotes=lambda symbols:[quote(s[2:],source_time=STAMP if i else DAY+'T09:00:00+08:00') for i,s in enumerate(symbols)]
    rows=p.stocks()
    assert len(rows)==21 and rows[0]['price'] is None
    p.quotes=lambda symbols:[quote(s[2:],source_time='2026-10-28T15:00:00+08:00') for s in symbols]
    with pytest.raises(DataError,match='日期'):p.stocks()


def clock(monkeypatch,day=30,hour=1):
    import backend.public_data as module
    class Clock:
        @staticmethod
        def now(tz):return datetime(2026,10,day,hour,tzinfo=TZ)
    monkeypatch.setattr(module,'datetime',Clock)


def test_f10_exact_board_mapping_count_validation_and_history_gate(tmp_path,monkeypatch):
    clock(monkeypatch)
    db=Store(tmp_path/'db');db.observe('calendar',[DAY,'2026-10-30'])
    p=PublicData(db,DAY)
    catalog=[dict(code='BK0001',name='题材',vendor_up=1,vendor_down=0,vendor_flat=0)]
    item=dict(BOARD_CODE='1',BOARD_NAME='题材 ',SECURITY_CODE='600001')
    p.public_text=lambda *a,**k:json.dumps({'success':True,'result':{'count':1,'pages':1,'data':[item]}})
    assert p.membership_batch(catalog,DAY)['verified']==1
    assert db.observed('members:BK0001:'+DAY)==['600001']
    catalog[0]['vendor_down']=1
    assert p.membership_batch(catalog,DAY)['verified']==0
    with pytest.raises(DataError,match='更早历史'):p.membership_batch(catalog,'2026-10-28')
    clock(monkeypatch,hour=10)
    with pytest.raises(DataError,match='下一交易'):p.membership_batch(catalog,DAY)


def test_f10_partial_pages_never_publish_members(tmp_path,monkeypatch):
    clock(monkeypatch)
    db=Store(tmp_path/'db');db.observe('calendar',[DAY,'2026-10-30']);p=PublicData(db,DAY)
    row=dict(BOARD_CODE='1',BOARD_NAME='题材',SECURITY_CODE='600001')
    p.public_text=lambda *a,**k:json.dumps({'success':True,'result':{'count':2,'pages':2,'data':[row]}})
    with pytest.raises(DataError,match='唯一性'):p.membership_batch([dict(code='BK0001',name='题材')],DAY)
    assert db.observed('members:BK0001:'+DAY) is None


def test_board_fallback_never_substitutes_a_different_date(tmp_path,monkeypatch):
    db=Store(tmp_path/'db');db.save('industry','2026-10-28','close',{'rows':[{'code':'BK0001'}]})
    p=PublicData(db,DAY)
    monkeypatch.setattr(Eastmoney,'boards',lambda *a:(_ for _ in ()).throw(DataError('offline')))
    with pytest.raises(DataError,match='没有板块目录'):p.boards('industry')
    db.save('industry',DAY,'close',{'rows':[{'code':'BK0001'}]})
    assert p.boards('industry')[0]['code']=='BK0001'
    assert '未更新' in p.sources['board_industry']


def test_single_board_quote_fallback_units_and_date(tmp_path):
    db=Store(tmp_path/'db');db.observe('catalog:industry',[{'code':'BK0001','name':'测试'}])
    p=PublicData(db,DAY)
    p.get=lambda *a:{'data':dict(f57='BK0001',f58='测试',f43=1234.56,f170=-3.18,f48=100000000,f86=int(datetime.fromisoformat(STAMP).timestamp()))}
    r=p.board_quotes('industry',DAY)[0]
    assert r['amount']==100000000 and r['price']==1234.56 and r['vendor_up'] is None
    with pytest.raises(DataError,match='收盘日'):p.board_quotes('industry','2026-10-28')


def test_rankings_versions_st_membership_and_limit_heights(tmp_path):
    db=Store(tmp_path/'db')
    stocks=[quote(),quote('600002',price=10.9,change=9),quote('600003',name='ST测试'),quote('688001',price=11,change=10)]
    db.save('stocks',DAY,'close',{'rows':stocks,'source':'腾讯'},STAMP)
    db.save('pools',DAY,'close',{'pools':{'up':[{'code':'600001','boards':4}]}})
    member=db.observe('members:BK0001:'+DAY,['600001','688001'])
    meta=db.observe('members_meta:BK0001:'+DAY,{'verified':False})
    def board(mid):return db.save('industry',DAY,'close',{'rows':[{'code':'BK0001','name':'测试板块'}],'input_refs':{'members':{'BK0001':member},'members_meta':{'BK0001':mid}}})
    board(meta);rev=build_rankings(db,DAY)
    assert build_rankings(db,DAY)==rev
    rows={r['code']:r for r in db.snapshot('gainers',DAY,'close')['payload']['rows']}
    assert rows['600001']['boards']==4 and rows['600001']['industry']==[]
    assert rows['600002']['limit_status']=='not_sealed'
    assert rows['688001']['limit_status']=='sealed_height_unknown' and rows['688001']['boards'] is None
    meta=db.observe('members_meta:BK0001:'+DAY,{'verified':True});board(meta);new=build_rankings(db,DAY)
    assert new!=rev
    with TestClient(create_app(db,collect=False)) as c:
        assert c.get('/api/gainers',params={'date':DAY}).json()['total']==3
        assert c.get('/api/gainers',params={'date':DAY,'include_st':True}).json()['total']==4
        assert c.get('/api/gainers',params={'date':DAY,'sector':'BK0001'}).json()['total']==2
        assert c.get('/api/gainers',params={'date':DAY,'revision':rev,'sector':'BK0001'}).json()['total']==0
        assert c.get('/api/gainers',params={'date':'2026-10-28'}).json()['rows']==[]
        assert c.get('/api/gainers',params={'date':'2026-10-28','revision':rev}).status_code==404


def test_unverified_members_never_generate_analysis_or_detail(tmp_path):
    db=Store(tmp_path/'db');db.observe('members:BK0001:'+DAY,['600001']);db.observe('members_meta:BK0001:'+DAY,{'verified':False,'source':'F10'})
    db.save('stocks',DAY,'close',{'rows':[quote()]},STAMP);db.save('market',DAY,'close',{'stats':{'amount':10000}},STAMP)
    class Provider:
        def boards(self,kind):return [dict(code='BK0001',name='测试',price=10,change=1,source_time=STAMP)]
    Collector(db,Provider()).boards('industry')
    row=db.snapshot('industry',DAY,'close')['payload']['rows'][0]
    assert row['heat'] is None and row['amount_share'] is None and row['up_ratio'] is None
    with TestClient(create_app(db,collect=False)) as c:
        data=c.get('/api/sectors/BK0001',params={'date':DAY}).json()
        assert data['members']==[] and data['members_available'] is False and data['limit_up'] is None


def test_pool_quote_conflict_does_not_claim_consecutive_limit(tmp_path):
    db=Store(tmp_path/'db')
    db.save('stocks',DAY,'close',{'rows':[quote(price=10)]},STAMP)
    db.save('pools',DAY,'close',{'pools':{'up':[{'code':'600001','boards':3}]}})
    build_rankings(db,DAY)
    r=db.snapshot('gainers',DAY,'close')['payload']['rows'][0]
    assert r['limit_status']=='source_conflict' and r['boards'] is None


def test_primary_circuit_retries_after_cooldown(tmp_path,monkeypatch):
    import backend.public_data as module
    db=Store(tmp_path/'db');db.save('industry',DAY,'close',{'rows':[{'code':'BK0001','price':1}]})
    p=PublicData(db,DAY);calls=[];ticks=[0]
    monkeypatch.setattr(module.time,'monotonic',lambda:ticks[0])
    def primary(*args):
        calls.append(True)
        if len(calls)==1:raise DataError('offline')
        return [{'code':'BK0001','price':2,'source_time':STAMP}]
    monkeypatch.setattr(Eastmoney,'boards',primary)
    assert p.boards('industry')[0]['price']==1
    ticks[0]=100;assert p.boards('industry')[0]['price']==1
    ticks[0]=301;assert p.boards('industry')[0]['price']==2
    assert len(calls)==2


def test_failed_emotion_and_flow_keep_archive_retryable(tmp_path,monkeypatch):
    import backend.service as module
    class Clock:
        @staticmethod
        def now(tz):return datetime(2026,10,29,17,tzinfo=TZ)
    monkeypatch.setattr(module,'datetime',Clock)
    db=Store(tmp_path/'db');db.observe('calendar',[DAY,'2026-10-30'])
    col=Collector(db)
    def job(name,fn,interval=300):
        if name in ('market','board_industry','board_concept','indices'):
            kind={'board_industry':'industry','board_concept':'concept'}.get(name,name)
            db.save(kind,DAY,'close',{'rows':[]},STAMP)
            with db.connect() as conn:conn.execute('UPDATE snapshots SET collected_at=?',(DAY+'T17:00:00+08:00',))
            return DAY,'close'
        return None
    col.job=job
    assert col.cycle(initial=True)
    assert db.observed('archive:'+DAY+':archive_1630') is None
    assert col.cycle()  # Missing pools/flow must not silently end retries.


def test_missing_member_amount_never_becomes_zero(tmp_path):
    db=Store(tmp_path/'db');db.observe('members:BK0001:'+DAY,['600001'])
    db.save('stocks',DAY,'close',{'rows':[quote(price=None,amount=None,quote_status='stale_or_unavailable')]},STAMP)
    db.save('market',DAY,'close',{'stats':{'amount':10000}},STAMP)
    class Provider:
        def boards(self,kind):return [dict(code='BK0001',price=10,change=1,source_time=STAMP)]
    Collector(db,Provider()).boards('industry')
    assert db.snapshot('industry',DAY,'close')['payload']['rows'][0]['amount_share'] is None


def test_market_coverage_keeps_missing_quotes_in_denominator(tmp_path):
    db=Store(tmp_path/'db')
    class Provider:
        def stocks(self):return [quote(),quote('600002',price=None,amount=None,source_time='2026-10-28T15:00:00+08:00')]
    Collector(db,Provider()).market()
    stats=db.snapshot('market',DAY,'close')['payload']['stats']
    assert stats['coverage']==.5 and stats['quote_coverage']==.5 and stats['amount']==10000


def test_enrichment_publish_survives_job_cooldown(tmp_path,monkeypatch):
    import backend.service as module
    col=Collector(Store(tmp_path/'db'));ticks=[0];published=[]
    class Stop:
        def is_set(self):return ticks[0]>=2
        def wait(self,seconds):ticks[0]+=1
    col.stop=Stop()
    col.cycle=lambda **kwargs:setattr(col,'status','archive_1630')
    col.enrich_one=lambda:ticks[0]==0
    col.job=lambda name,fn:None if ticks[0]==0 else (DAY,'close')
    monkeypatch.setattr(module,'build_rankings',lambda *args:published.append(True))
    col.run()
    assert len(published)==2 and not col.pending_publish
