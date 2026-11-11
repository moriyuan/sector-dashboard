import json
from datetime import datetime

import pytest

from backend.provider import DataError, Eastmoney
from backend.public_data import PublicData
from backend.store import Store, TZ
from backend.ths import ThsConcepts, today_quote


DAY='2026-11-03'
PREVIOUS='2026-11-02'
STAMP=DAY+'T11:30:00+08:00'


def clock(monkeypatch,hour=12,minute=0):
    import backend.public_data as public
    import backend.ths as ths
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,11,3,hour,minute,tzinfo=TZ)
    monkeypatch.setattr(public,'datetime',Clock)
    monkeypatch.setattr(ths,'datetime',Clock)


def provider(tmp_path,monkeypatch):
    clock(monkeypatch)
    store=Store(tmp_path/'db')
    store.observe('calendar',[PREVIOUS,DAY,'2026-11-04'])
    p=PublicData(store)
    p.begin_session(DAY,'midday')
    return p,store


def quote(code='600001',stamp=STAMP):
    return dict(code=code,name='测试',price=11,change=10,amount=10000,source_time=stamp)


def ths_response(clock='1130'):
    return 'cb('+json.dumps({'bk_885611':{'1':'20261103','7':'10','8':'12','9':'9','11':'11',
        '19':'321000000','dt':clock,'name':'概念甲'}})+')'


def test_midday_session_requires_current_trading_day_and_lunch_window(tmp_path,monkeypatch):
    p,s=provider(tmp_path,monkeypatch)
    assert p.session_mode==p.ths.session_mode=='midday'
    for hour,minute in [(11,59),(13,0),(17,0)]:
        clock(monkeypatch,hour,minute)
        with pytest.raises(DataError,match='午间采集'):p.begin_session(DAY,'midday')
    clock(monkeypatch)
    with pytest.raises(DataError,match='午间采集'):p.begin_session(PREVIOUS,'midday')
    s.observe('calendar',[PREVIOUS,'2026-11-04'])
    with pytest.raises(DataError,match='午间采集'):p.begin_session(DAY,'midday')
    p.begin_session(PREVIOUS)  # Existing close/backfill contract remains supported.
    assert p.session_mode=='close'


def test_midday_quotes_exclude_stale_afternoon_and_other_dates(tmp_path,monkeypatch):
    p,s=provider(tmp_path,monkeypatch)
    p.directory=lambda:['sh'+str(600001+i) for i in range(21)]
    for bad in (DAY+'T11:24:59+08:00',DAY+'T13:00:00+08:00',PREVIOUS+'T15:00:00+08:00'):
        p.quotes=lambda symbols:[quote(code[2:],bad if i==0 else STAMP) for i,code in enumerate(symbols)]
        rows=p.stocks()
        assert rows[0]['price'] is None and rows[0]['quote_status']=='stale_or_unavailable'
        assert all(r['price']==11 for r in rows[1:])
    p.quotes=lambda symbols:[quote(code[2:],DAY+'T15:00:00+08:00') for code in symbols]
    with pytest.raises(DataError,match='时段'):p.stocks()
    p.quotes=lambda symbols:[quote(code[2:],DAY+'T13:00:00+08:00') for code in symbols]
    with pytest.raises(DataError,match='时段'):p.indices()


def test_midday_board_and_flow_fallbacks_never_read_close(tmp_path,monkeypatch):
    p,s=provider(tmp_path,monkeypatch)
    monkeypatch.setattr(Eastmoney,'boards',lambda *args:(_ for _ in ()).throw(DataError('offline')))
    monkeypatch.setattr(Eastmoney,'flows',lambda *args:(_ for _ in ()).throw(DataError('offline')))
    s.save('industry',DAY,'close',{'rows':[quote('BK0001',DAY+'T15:00:00+08:00')]})
    s.save('flow_industry',DAY,'close',{'rows':{'BK0001':{'today':999}}})
    with pytest.raises(DataError,match='目录'):p.boards('industry')
    with pytest.raises(DataError):p.flows('industry')
    s.save('industry',DAY,'midday',{'rows':[quote('BK0001')]})
    s.save('flow_industry',DAY,'midday',{'rows':{'BK0001':{'today':123}}})
    assert p.boards('industry')[0]['source_time']==STAMP
    assert p.flows('industry')['BK0001']['today']==123


def test_midday_f10_members_capture_current_day(tmp_path,monkeypatch):
    p,s=provider(tmp_path,monkeypatch)
    row=dict(BOARD_CODE='1',BOARD_NAME='题材',SECURITY_CODE='600001')
    p.public_text=lambda *a,**k:json.dumps({'success':True,'result':{'count':1,'pages':1,'data':[row]}})
    assert p.membership_batch([dict(code='BK0001',name='题材')],DAY)['verified']==1
    assert s.observed('members:BK0001:'+DAY)==['600001']


def test_ths_midday_timestamp_contract_preserves_close_default():
    bar,stamp=today_quote(ths_response(),'885611','概念甲',DAY,'midday')
    assert stamp==STAMP and bar['amount']==321000000
    for bad in ('1124','1300','1530'):
        with pytest.raises(DataError,match='源时间'):today_quote(ths_response(bad),'885611','概念甲',DAY,'midday')
    with pytest.raises(DataError,match='源时间'):today_quote(ths_response(),'885611','概念甲',DAY)


def test_ths_midday_bar_and_quote_cache_are_isolated_from_close(tmp_path,monkeypatch):
    p,s=provider(tmp_path,monkeypatch)
    ths=p.ths
    row=dict(code='THS301558',name='概念甲',ths_cid='301558',expected_count=1)
    previous=[dict(date=PREVIOUS,open=9,high=11,low=8,close=10,amount=100,change=1)]
    old_bars=s.observe('bars:'+row['code'],previous)
    s.observe('ths_quote:'+row['code']+':'+DAY,dict(row,price=99,captured_at=DAY+'T16:30:00+08:00'))
    urls=[]
    def read(url,day):
        urls.append(url)
        if 'today.js' in url:return ths_response()
        if '/detail/' in url:return '<input id="requestQuery" value="code/301558"><input id="clid" value="885611">'
        raise AssertionError('Complete prior bars should be reused at noon')
    ths.read=read
    result=ths.quote(row,DAY)
    assert result['price']==11 and result['change']==pytest.approx(10)
    assert s.observed('bars:'+row['code'])==previous
    assert s.observation_record('bars:'+row['code'])['id']==old_bars
    assert s.observed('ths_quote:'+row['code']+':'+DAY)['price']==99
    assert s.observed('ths_quote:'+row['code']+':'+DAY+':midday')['price']==11
    assert len(urls)==2


def test_ths_midday_new_history_excludes_unfinished_today(tmp_path,monkeypatch):
    p,s=provider(tmp_path,monkeypatch)
    row=dict(code='THS301558',name='概念甲',ths_cid='301558',expected_count=1)
    def read(url,day):
        if 'today.js' in url:return ths_response()
        if '/last.js' in url:return 'cb({"data":"20261102,9,11,8,10,1,100;20261103,10,12,9,11,2,200"})'
        return '<input id="requestQuery" value="code/301558"><input id="clid" value="885611">'
    p.ths.read=read
    assert p.ths.quote(row,DAY)['price']==11
    assert [b['date'] for b in s.observed('bars:'+row['code'])]==[PREVIOUS]


def test_ths_midday_members_and_future_cache_checks(tmp_path,monkeypatch):
    p,s=provider(tmp_path,monkeypatch)
    row=dict(code='THS301558',ths_cid='301558',expected_count=1)
    p.ths.detail=lambda *a:dict(members=['600001'],pages=1,index_code='885611')
    assert p.ths.members(row,DAY)==['600001']
    assert p.ths.valid_capture(DAY+'T12:01:00+08:00',DAY)
    assert not p.ths.valid_capture(DAY+'T15:30:00+08:00',DAY)
    clock(monkeypatch,13)
    with pytest.raises(DataError,match='午间采集'):p.ths.members(row,DAY)
