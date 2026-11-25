import json
import sqlite3
import threading
from datetime import datetime,timedelta

import pytest
from .test_fast_preview import preview
from backend.store import TZ
from .wind_core import WindStore
from .full_pool_quotes import FullPoolQuotes,BasicQuoteSource,phase
from . import full_pool_quotes as mod

NOW=datetime(2026,11,23,9,5,tzinfo=TZ)
VIEW={'date':'2026-11-20','capturedAt':'2026-11-20T15:00:00+08:00','stocks':[]}


class Transport:
    def set_deadline(self,value):pass


class Source:
    def __init__(self):self.transport=Transport();self.calls=[];self.fail=set();self.no_time=set();self.day='2026-11-20';self.gate=None
    def verified_day(self,today,minimum):return self.day
    def basic_quote(self,code,day):
        self.calls.append(code)
        if self.gate:self.gate.wait(2)
        if code in self.fail:raise ValueError('接口暂不可用')
        return dict(code=code,name='样本',price=10,change=-3,quote_day=day,
                    source_time=None if code in self.no_time else day+'T15:45:00+08:00')
    def close(self):pass


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'clock',lambda:NOW)
    registry=WindStore(tmp_path/'registry.sqlite3')
    rows=[dict(code=str(600001+i),name='样本'+str(i)) for i in range(10)]
    registry.accept('wind',dict(day='2026-11-20',rows=rows,total=len(rows),complete=True,verified=True))
    source=Source();tracker=FullPoolQuotes(tmp_path/'quotes.sqlite3',registry,source=source)
    yield tracker,source,registry
    tracker.close()


def complete(tracker):tracker.current_future.result(timeout=5)


def test_startup_covers_entire_pool_and_keeps_old_quotes_out_of_evidence(setup):
    tracker,source,registry=setup
    assert tracker.tick(VIEW,NOW);complete(tracker)
    assert len(source.calls)==10 and len(tracker.last_known(NOW.isoformat()))==10
    assert tracker.available('2026-11-23',NOW.isoformat())=={}
    assert tracker.status()['updated']==10
    assert not tracker.tick(VIEW,NOW+timedelta(minutes=1))
    tracker.schedule('2026-11-23',NOW.isoformat(),registry.listing())
    assert len(source.calls)==10


def test_minute_refresh_and_inflight_coalescing(setup):
    tracker,source,_=setup
    source.gate=threading.Event()
    current=NOW.replace(hour=10)
    assert tracker.tick(VIEW,current)
    assert not tracker.tick(VIEW,current+timedelta(minutes=1))
    source.gate.set();complete(tracker)
    assert not tracker.tick(VIEW,current)
    assert tracker.tick(VIEW,current+timedelta(minutes=1));complete(tracker)
    assert len(source.calls)==20


def test_new_member_and_deletion_without_waiting_for_analysis(setup):
    tracker,source,registry=setup
    tracker.tick(VIEW,NOW);complete(tracker)
    registry.identify([dict(code='600099',name='新成员')])
    registry.mutate('add',['600099'])
    tracker.tick(VIEW,NOW);complete(tracker)
    assert source.calls[-1]=='600099' and len(source.calls)==11
    registry.mutate('remove',['600001'])
    tracker.tick(VIEW,NOW.replace(hour=10));complete(tracker)
    assert source.calls.count('600001')==1


def test_failure_preserves_valid_cached_values_and_records_reason(setup):
    tracker,source,_=setup
    tracker.tick(VIEW,NOW);complete(tracker)
    before=tracker.last_known(NOW.isoformat())['600001']
    source.fail={'600001'}
    tracker.tick(VIEW,NOW.replace(hour=10));complete(tracker)
    assert tracker.last_known(NOW.isoformat())['600001']==before
    assert tracker.attempts()['600001']['state']=='failed'
    assert tracker.status()['failed']==1 and tracker.status()['updated']==9


def test_missing_supplier_time_is_displayable_but_never_realtime_evidence(setup):
    tracker,source,_=setup;source.no_time={'600001'}
    tracker.tick(VIEW,NOW);complete(tracker)
    quote=tracker.last_known(NOW.isoformat())['600001']
    assert quote['source_time'] is None and quote['received_at']
    assert '600001' not in tracker.available(source.day,NOW.isoformat())


def test_calendar_failure_leaves_prior_cache_and_reports_failure(setup):
    tracker,source,_=setup
    tracker.tick(VIEW,NOW);complete(tracker)
    source.verified_day=lambda *args:(_ for _ in ()).throw(ValueError('日历未更新'))
    tracker.tick(VIEW,NOW.replace(hour=10));complete(tracker)
    assert len(tracker.last_known(NOW.isoformat()))==10
    assert tracker.status()['failed']==10 and '日历' in tracker.status()['error']


def test_main_refresh_has_priority(setup):
    tracker,source,_=setup;tracker.main_busy=lambda:True
    assert not tracker.tick(VIEW,NOW) and not source.calls


def test_latest_price_reuse_and_point_in_time_isolation(setup):
    tracker,source,_=setup;source.day='2026-11-23'
    t=(NOW-timedelta(seconds=20)).isoformat()
    with sqlite3.connect(tracker.path) as db:
        for r in tracker.registry.listing():
            q=dict(code=r['code'],name=r['name'],price=10,change=2,quote_day=source.day,source_time=t)
            db.execute('INSERT INTO core_quotes VALUES(?,?,?,?,?)',(source.day,r['code'],t,t,json.dumps(q)))
    assert tracker.last_known((NOW-timedelta(minutes=1)).isoformat())=={}
    tracker.tick(dict(VIEW,date=source.day),NOW);complete(tracker)
    assert not source.calls and tracker.status()['reused']==10


def test_phase_boundaries():
    assert phase(NOW)=='盘前'
    assert phase(NOW.replace(hour=11,minute=40))=='午休'
    assert phase(NOW.replace(hour=15,minute=2))=='收盘后'
    assert phase(NOW.replace(hour=13,minute=0))=='盘中'


def test_basic_source_validates_identity_calendar_and_optional_time():
    class Adapter(BasicQuoteSource):
        def __init__(self):self.data={'day':'2026-11-20','code':'600001','name':'样本','real':{'last_px':10,'px_change_rate':1,'time':None}}
        def _app(self,*args,**kw):return self.data
        def _post(self,*args):return {'info':[{'Day':'2026-11-20'},{'Day':'2026-11-19'}]}
    source=Adapter()
    assert source.verified_day('2026-11-23','2026-11-20')=='2026-11-20'
    with pytest.raises(ValueError):source.verified_day('2026-11-23','2026-11-23')
    assert source.basic_quote('600001','2026-11-20')['source_time'] is None
    source.data['code']='600002'
    with pytest.raises(ValueError):source.basic_quote('600001','2026-11-20')


def test_auction_live_day_can_advance_before_history_and_individual_day_is_preserved():
    class Adapter(BasicQuoteSource):
        def __init__(self):self.quote_day='20261123'
        def _post(self,*args):return {'info':[{'Day':'2026-11-20'}]}
        def fetch_emotion(self,day):return {'highest':1}
        def _app(self,*args,**kw):return {'day':self.quote_day,'code':'600001','name':'样本','real':{'last_px':10,'px_change_rate':3,'time':91501000}}
    source=Adapter()
    assert source.verified_day('2026-11-23','2026-11-20')=='2026-11-23'
    source._post=lambda *args:(_ for _ in ()).throw(ValueError('模拟暂时离线'))
    assert source.verified_day('2026-11-23','2026-11-20')=='2026-11-23'
    q=source.basic_quote('600001','2026-11-23')
    assert q['quote_day']=='2026-11-23' and q['quote_phase']=='集合竞价'
    source.quote_day='20261120'
    assert source.basic_quote('600001','2026-11-23')['quote_day']=='2026-11-20'
    source.quote_day='20261124'
    with pytest.raises(ValueError):source.basic_quote('600001','2026-11-23')
