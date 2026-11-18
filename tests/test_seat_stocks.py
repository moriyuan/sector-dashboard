import json
from fastapi.testclient import TestClient
from fastapi import FastAPI
from backend.store import Store
from backend.seat_stocks import archive, router, raw_events


def test_all_directions_kept_without_returns_and_filters(tmp_path):
    path=tmp_path/'participants.sqlite3'
    rows=[dict(code=code,name=name,date='2026-11-13',period=period,profile=profile,
               seat='席位甲',seat_code='1',reason='日涨幅',buy=10,sell=20,net=net,
               net_return=.9,win_rate=1,entry_state='entry_skipped')
          for code,name,period,profile,net in [('000001','*ST测试','single','甲',-10),
              ('000002','股票乙','three','乙',10),('920223','北交所股票','single','甲',0)]]
    archive(path,rows);archive(path,rows)
    app=FastAPI();app.include_router(router(Store(tmp_path/'market.sqlite3'),path))
    with TestClient(app) as c:
        data=c.get('/api/seat-stocks').json()
        assert data['total']==3 and data['meta']['event_count']==3
        assert c.get('/api/seat-stocks?profile=甲').json()['total']==2
        assert c.get('/api/seat-stocks?period=three').json()['total']==1
        assert c.get('/api/seat-stocks?q=ST').json()['rows'][0]['code']=='000001'
        detail=c.get('/api/seat-stocks/000001').json()
        assert detail['rows'][0]['net']==-10
        assert all(field not in json.dumps(detail) for field in ('net_return','win_rate','entry_state'))
        assert c.get('/api/seat-stocks?page=2&page_size=2').json()['total']==3
        assert len(c.get('/api/seat-stocks?page=2&page_size=2').json()['rows'])==1
        assert c.get('/api/seat-stocks?page=0').status_code==422
        assert c.get('/api/seat-stocks?period=bad').status_code==422
        assert c.get('/api/seat-stocks/bad').status_code==422
        assert c.get('/api/seat-stocks/600000').status_code==404
        assert c.post('/api/seat-stocks').status_code==405


def test_missing_archive_is_explicit(tmp_path):
    app=FastAPI();app.include_router(router(Store(tmp_path/'market.sqlite3'),tmp_path/'missing'))
    with TestClient(app) as c:
        assert c.get('/api/seat-stocks').json()['meta']['archive_available'] is False
