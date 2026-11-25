import copy
import importlib.util
import json
import time
from datetime import datetime
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parent
from . import terminal as preview


class Fake:
    def __init__(self):
        self.fail=False
        self.calls=[]
        self.delay=.04
    def close(self):pass
    def fetch_emotion(self,day):
        time.sleep(self.delay)
        return {'highest':1,'limit_up':1,'temperature':25}
    def fetch_market_mood(self,day):
        return {'values':{'SZJS':500,'XDJS':4000}}
    def fetch_rise_fall(self,day):
        return {'limit_up':1,'limit_down':20,'break_rate':50,'failed_limit':1}
    def fetch_strength_board(self,day,**kwargs):
        if self.fail:raise preview.SourceError('temporary board error')
        return [{'code':'801001','name':'芯片','strength':99,'change':1,'rank':1}]
    def fetch_drawdowns(self,day):return {'rows':[]}
    def fetch_limit_pool(self,day,level):
        raw=[None]*23
        raw[21],raw[22],raw[11]=10,10,100000
        return [{'code':'600001','name':'甲','boards':1,'limit_timestamp':1,'limit_time':'09:30:00',
                 'theme':'芯片','tags':'芯片','raw':raw}]
    def fetch_quote(self,code,day):
        self.calls.append(code)
        time.sleep(self.delay)
        return {'code':code,'price':10,'change':10,'amount':100000,'reason':'原因',
                'source_time':preview.stamp(),'raw':{'real':{'high_px':10,'low_px':9,'turnover_ratio':1}}}
    def fetch_sector_trend(self,code,day):
        return {'code':code,'raw':{'preclose_px':10,'trend':[['09:30',10]]}}


def complete(engine):
    deadline=time.monotonic()+3
    while engine.state()['job']['state']=='running' and time.monotonic()<deadline:time.sleep(.01)
    assert engine.state()['job']['state']!='running'
    return engine.state()['job']


@pytest.fixture
def engine(tmp_path):
    seed=json.loads((ROOT/'test-seed.json').read_text(encoding='utf-8'))
    seed['date']=preview.now().date().isoformat()
    result=preview.RefreshEngine(preview.SnapshotArchive(tmp_path/'history.sqlite3'),seed,Fake())
    yield result
    result.close()


def test_refresh_acknowledges_immediately_and_deduplicates(engine):
    started=time.perf_counter()
    a=engine.request('600001')
    assert time.perf_counter()-started<.1
    b=engine.request('600001')
    assert b['joined_existing_job'] and a['job']['id']==b['job']['id']
    job=complete(engine)
    assert job['state']=='complete',job
    assert engine.request()['reused_recent_result']
    assert engine.source.calls==['600001']
    assert engine.archive.stats()['frames']==1


def test_bulk_prices_publish_before_optional_details(engine):
    engine.source.delay=.2
    engine.request()
    job=complete(engine)
    assert job['metrics']['core_ms']<job['metrics']['total_ms']
    stock=engine.data()['data']['stocks'][0]
    assert stock['quote']['price_basis']=='current_limit_pool'
    assert stock['quote']['source_time'] is None
    assert stock['quote']['detail_source_time'] is not None


def test_failed_module_preserves_old_values_and_status(engine):
    engine.request();complete(engine)
    old=engine.data()['data']['boards']
    engine.last_started-=11
    engine.source.fail=True
    engine.request();job=complete(engine)
    assert job['state']=='degraded'
    view=engine.data()['data']
    assert view['boards']==old
    assert not view['sectionStatus']['boards']['ok']
    assert view['sectionStatus']['boards']['cached']


def test_archive_deduplicates_without_losing_timepoints(tmp_path):
    archive=preview.SnapshotArchive(tmp_path/'history.sqlite3')
    feeds={'boards':{'data':{'rows':['same']*100},'ok':True,'received_at':'one'}}
    view={'date':'2026-11-19','stocks':[],'value':'same'*100}
    archive.save('2026-11-19','manual','one',feeds,view,{})
    first=archive.stats()
    archive.save('2026-11-19','manual','two',feeds,view,{})
    second=archive.stats()
    assert second['frames']==2
    assert first['unique_bodies']==second['unique_bodies']==2
    assert second['compressed_bytes']<second['raw_bytes']
    restored,restored_feeds=archive.restore('2026-11-19')
    assert restored==view and restored_feeds['boards']['data']==feeds['boards']['data']
    assert archive.restore('2026-11-20')==(None,{})


def test_old_quote_not_relabelled_as_current(engine):
    original=engine.source.fetch_quote
    def stale(code,day):
        data=original(code,day)
        data['source_time']=preview.now().replace(hour=8,minute=0).isoformat()
        return data
    engine.source.fetch_quote=stale
    engine.request();job=complete(engine)
    assert job['state']=='degraded'
    assert any('盘口价格时间过旧' in error for error in job['errors'])


def test_calendar_pause():
    assert preview.active_time(datetime(2026,11,19,10,0,tzinfo=preview.TZ))
    assert not preview.active_time(datetime(2026,11,19,12,0,tzinfo=preview.TZ))
    assert not preview.active_time(datetime(2026,11,19,15,1,tzinfo=preview.TZ))
