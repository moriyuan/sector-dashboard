from datetime import datetime

from fastapi.testclient import TestClient

from backend.analytics import analyze, pool_stats
from backend.app import create_app
from backend.provider import DataError
from backend.service import Collector, bar_checkpoint, close_session
import pytest
from backend.store import Store, TZ


def test_bar_checkpoints_separate_intraday_and_two_archives():
    def at(t):return datetime.fromisoformat('2026-10-29T'+t).replace(tzinfo=TZ)
    assert len({bar_checkpoint(at(t)) for t in ('10:00','15:30','16:30')})==3
    assert bar_checkpoint(at('10:00'))==bar_checkpoint(at('14:00'))


def test_intraday_candle_excluded_then_fetched_at_close(tmp_path,monkeypatch):
    import backend.service as service
    current=[datetime(2026,10,29,10,tzinfo=TZ)]
    class Clock:
        @staticmethod
        def now(tz):return current[0]
    class Provider:
        def bars(self,code):return [dict(date='2026-10-28',close=90),dict(date='2026-10-29',close=100 if current[0].hour==10 else 110)]
    monkeypatch.setattr(service,'datetime',Clock)
    db=Store(tmp_path/'test.db')
    db.observe('calendar',['2026-10-29','2026-10-30'])
    col=Collector(db,Provider());col.catalog={'industry':[{'code':'BK0001'}]}
    assert not col.enrich_one()
    assert db.observed('bars:BK0001') is None
    current[0]=datetime(2026,10,29,15,30,tzinfo=TZ)
    col.enrich_next=0;col.last.clear()
    assert col.enrich_one()
    assert db.observed('bars:BK0001')[-1]['close']==110


def test_stale_components_do_not_generate_current_breadth(tmp_path):
    db=Store(tmp_path/'test.db')
    day='2026-10-29'
    db.observe('members:BK0001:'+day,['600001'])
    db.save('stocks',day,'live',{'rows':[dict(code='600001',name='甲',price=10,change=1,amount=100)]},day+'T09:31:00+08:00')
    db.save('market',day,'live',{'stats':{'amount':100}},day+'T09:31:00+08:00')
    class Provider:
        def boards(self,kind):return [dict(code='BK0001',name='行业',price=100,change=1,source_time=day+'T14:55:00+08:00')]
    with pytest.raises(DataError,match='仅采集收盘数据'):
        Collector(db,Provider()).boards('industry')
    assert db.snapshot('industry',day,'live') is None
    assert db.snapshot('industry',day,'close') is None


def test_partial_pool_failure_preserves_previous_evidence(tmp_path):
    db=Store(tmp_path/'test.db')
    up=[{'code':'600001','boards':2}];broken=[{'code':'600002'}]
    revision=db.save('pools','2026-10-29','close',{'pools':{'up':up,'broken':broken,'down':[]},'stats':pool_stats(up,broken,[])})
    class Provider:
        def pool(self,kind,day):
            if kind=='broken':raise DataError('test failure')
            return up if kind=='up' else []
    Collector(db,Provider()).pools('2026-10-29','close')
    got=db.snapshot('pools','2026-10-29','close')
    assert got['id']==revision and got['payload']['stats']['broken_ratio']==50
    assert next(e for e in db.events() if e['job']=='pools')['ok']==0


def test_fixed_board_revision_pins_prior_flow_revisions(tmp_path):
    db=Store(tmp_path/'test.db')
    db.save('flow_industry','2026-10-28','close',{'rows':{'BK0001':{'today':10}}})
    rows=analyze([dict(code='BK0001',price=100,change=1)],{},[],'2026-10-29','close')
    revision=db.save('industry','2026-10-29','close',{'rows':rows})
    db.save('flow_industry','2026-10-28','close',{'rows':{'BK0001':{'today':999}}})
    with TestClient(create_app(db,collect=False)) as c:
        got=c.get(f'/api/sectors/BK0001?date=2026-10-29&revision={revision}').json()
        assert got['flow_history'][0]['today']==10


@pytest.mark.parametrize('clock,calendar,allowed', [
    ('10:00',['2026-10-29','2026-10-30'],False),
    ('12:00',['2026-10-29','2026-10-30'],True),
    ('15:20',['2026-10-29','2026-10-30'],False),
    ('15:30',['2026-10-29','2026-10-30'],True),
    ('16:30',['2026-10-29','2026-10-30'],True),
    ('18:00',['2026-10-30'],False),
    ('18:00',[],False),
])
def test_scheduled_sessions_gate_startup(tmp_path,monkeypatch,clock,calendar,allowed):
    import backend.service as service
    current=datetime.fromisoformat('2026-10-29T'+clock).replace(tzinfo=TZ)
    class Clock:
        @staticmethod
        def now(tz):return current
    monkeypatch.setattr(service,'datetime',Clock)
    db=Store(tmp_path/'test.db');db.observe('calendar',calendar)
    col=Collector(db,object());calls=[]
    def job(name,fn,interval=300):
        calls.append(name)
        return None
    col.job=job
    assert col.cycle(initial=True) is allowed
    assert ('market' in calls) is allowed
    if not allowed:
        assert calls==['calendar']
        assert col.enrich_one() is False


def test_close_session_rejects_unfinished_source():
    with pytest.raises(DataError):close_session('2026-10-29T14:59:59+08:00')
    assert close_session('2026-10-29T15:00:00+08:00')==('2026-10-29','close')


def test_api_exposes_only_close_dates_and_status(tmp_path):
    db=Store(tmp_path/'test.db')
    db.save('industry','2026-10-28','close',{'rows':[]})
    db.save('industry','2026-10-29','live',{'rows':[]})
    with TestClient(create_app(db,collect=False)) as client:
        dates=client.get('/api/dates').json()['snapshots']
        assert [d['trade_date'] for d in dates]==['2026-10-28']
        assert client.get('/api/sectors').json()['meta']['trade_date']=='2026-10-28'
        for endpoint in ('/api/sectors','/api/market','/api/sectors/BK0001'):
            assert client.get(endpoint,params={'mode':'live'}).status_code==422
        status=client.get('/api/status').json()
        assert status['mode']=='midday_and_post_close' and status['archive_times']==['15:30','16:30']
        assert status['midday_times']==['12:00']
        assert 'quote_interval' not in status
