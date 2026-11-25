import copy
from datetime import date, timedelta

import pytest

from .test_fast_preview import Fake, complete, engine, preview
from .ten_day_source import PreviewSource


DAYS=['2026-11-19','2026-11-18','2026-11-17','2026-11-16','2026-11-13',
      '2026-11-12','2026-11-11','2026-11-10','2026-11-09','2026-11-06']


class Adapter(PreviewSource):
    def __init__(self):
        self.calls=[]
        self.days=DAYS[:]
        self.result={'Count':5574,'List':[
            [str(600001+i),'样本'+str(i),10,60-i,0,0,0,0,0,0,'芯片'] for i in range(6)]}
    def _post(self,url,payload):
        self.calls.append(payload)
        if payload['a']=='ChangeStatistics':
            return {'info':[{'Day':day} for day in self.days]}
        return copy.deepcopy(self.result)


def test_session_window_includes_today_not_ten_calendar_days():
    source=Adapter()
    data=source.fetch_ten_day_leaders('2026-11-19')
    assert data['start']=='2026-11-06'
    assert len(data['trading_days'])==10 and data['trading_days'][-1]=='2026-11-19'
    assert len(data['rows'])==5
    assert source.calls[-1]['DStart']=='2026-11-06'
    assert data['source_time'] is None
    source.fetch_ten_day_leaders('2026-11-19')
    assert len([c for c in source.calls if c['a']=='ChangeStatistics'])==1


@pytest.mark.parametrize('problem',['order','duplicate','short','nan','old-date'])
def test_bad_ranking_is_not_presented_as_top_five(problem):
    source=Adapter()
    if problem=='order':source.result['List'].reverse()
    if problem=='duplicate':source.result['List'][1][0]=source.result['List'][0][0]
    if problem=='short':source.result['List']=source.result['List'][:4]
    if problem=='nan':source.result['List'][0][3]=float('nan')
    if problem=='old-date':source.result['Date']='2026-11-18'
    with pytest.raises(preview.SourceError):source.fetch_ten_day_leaders('2026-11-19')


def test_stale_calendar_rejected():
    source=Adapter();source.days=DAYS[1:]
    with pytest.raises(preview.SourceError):source.fetch_ten_day_leaders('2026-11-19')


def test_new_ranking_keeps_ladder_separate_and_caches_on_failure(engine):
    def fetch(day):
        return {'rows':[{'code':'600002','name':'非涨停股','rank':1,'return_10d':50,
                         'price':10,'theme':'芯片'}], 'start':day,'end':day}
    engine.source.fetch_ten_day_leaders=fetch
    engine.request();job=complete(engine)
    view=engine.data()['data']
    assert job['state']=='complete'
    assert [s['code'] for s in view['stocks']]==['600001']
    assert view['tenDay']['rows'][0]['quote']['change']==10
    assert len(engine.source.calls)==2
    old=copy.deepcopy(view['tenDay'])
    def fail(day):raise preview.SourceError('temporarily unavailable')
    engine.source.fetch_ten_day_leaders=fail
    engine.last_started-=11
    engine.request();job=complete(engine)
    view=engine.data()['data']
    assert job['state']=='degraded' and view['tenDay']==old
    assert view['sectionStatus']['tenDay']['cached']
    assert view['ladderComplete']
