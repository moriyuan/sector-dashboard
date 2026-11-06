from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend import financing as fin
from backend.app import create_app
from backend.store import Store, TZ


def point(day, sh=100e8, sz=80e8):
    return dict(date=day, sh_balance=sh, sz_balance=sz, balance=sh+sz if sh is not None and sz is not None else None)


def test_weekly_stock_is_last_trading_day_not_sum_and_current_week_is_provisional():
    days=['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30','2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06']
    rows=[point(d, (100+i)*1e8) for i,d in enumerate(days[:6])]
    result=fin.weekly(rows, days, '2026-11-03', count=2)
    assert result[0]['date']=='2026-10-30' and result[0]['balance']==184e8
    assert result[1]['date']=='2026-11-02' and result[1]['balance']==185e8
    assert result[1]['change']==1e8 and result[1]['provisional'] is True


def test_weekly_holiday_and_missing_final_day_do_not_fabricate_values():
    days=['2026-09-25','2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09','2026-10-12']
    rows=[point(d) for d in days if d!='2026-10-09']
    result=fin.weekly(rows, days, '2026-10-12', count=4)
    assert result[1]['week_end']=='2026-10-02' and result[1]['date']=='2026-09-30'
    assert result[2]['balance'] is None and result[2]['missing'] is True
    assert result[3]['change'] is None  # Do not compare across a missing week.
    assert fin.weekly([], [], '2026-10-12', count=4)==[]


def test_ended_week_with_latest_thursday_is_a_gap_not_a_completed_point():
    days=['2026-10-26','2026-10-29','2026-10-30','2026-11-02']
    rows=[point('2026-10-29')]
    result=fin.weekly(rows, days, '2026-11-02', count=2)
    assert result[0]['balance'] is None and result[0]['missing']


def test_source_contract_units_pagination_duplicates_and_exchange_identity(monkeypatch):
    calls=[]
    def get(params):
        calls.append(params)
        page=int(params['pageNumber'])
        return {'success':True,'result':{'pages':2,'count':2,'data':[
            {'DIM_DATE':f'2026-10-{31-page:02d} 00:00:00','SCDM':'007','RZYE':'1192247511122.50'}]}}
    monkeypatch.setattr(fin,'get',get)
    rows,raw=fin.fetch_market('sh','2026-10-01','2026-11-02')
    assert len(rows)==len(raw)==2 and rows['2026-10-30']==1192247511122.5
    assert calls[0]['reportName']=='RPTA_WEB_RZRQ_LSSH'
    def bad(params):
        data=get(params)
        data['result']['data'][0]['DIM_DATE']='2026-10-30 00:00:00'
        return data
    monkeypatch.setattr(fin,'get',bad)
    with pytest.raises(ValueError,match='重复'):fin.fetch_market('sh','2026-10-01','2026-11-02')
    monkeypatch.setattr(fin,'get',lambda p:{'success':True,'result':{'pages':1,'count':1,'data':[{'DIM_DATE':'2026-10-30','SCDM':'001','RZYE':1}]}})
    with pytest.raises(ValueError,match='市场'):fin.fetch_market('sh','2026-10-01','2026-11-02')


def test_series_requires_both_markets_and_failed_collection_preserves_revision(tmp_path,monkeypatch):
    store=Store(tmp_path/'db')
    store.observe('calendar',['2026-10-30','2026-11-02','2026-11-03'])
    def fetch(exchange,start,end):
        rows={'2026-10-30':100e8}
        if exchange=='sh':rows['2026-11-02']=110e8
        return rows,[]
    monkeypatch.setattr(fin,'fetch_market',fetch)
    now=datetime(2026,11,3,12,tzinfo=TZ)
    fin.collect(store,now)
    record=store.observation_record(fin.KEY)
    assert record['payload']['rows'][-1]['balance'] is None
    def fail(*args):raise ValueError('source unavailable')
    monkeypatch.setattr(fin,'fetch_market',fail)
    with pytest.raises(ValueError):fin.collect(store,now)
    assert store.observation_record(fin.KEY)['id']==record['id']


def test_version_cutoff_backfill_and_market_api_date_isolation(tmp_path,monkeypatch):
    store=Store(tmp_path/'db')
    store.observe('calendar',['2026-10-23','2026-10-30','2026-11-02','2026-11-03','2026-11-06'])
    now=['2026-11-03T09:15:00+08:00']
    monkeypatch.setattr('backend.store.now_iso',lambda:now[0])
    first=store.observe(fin.KEY,dict(rows=[point('2026-10-23'),point('2026-10-30'),point('2026-11-02')],latest_date='2026-11-02'))
    now[0]='2026-11-03T16:30:00+08:00'
    store.observe(fin.KEY,dict(rows=[point('2026-10-23'),point('2026-10-30',999e8),point('2026-11-02')],latest_date='2026-11-02'))
    view=fin.day_view(store,'2026-11-03','midday')
    assert view['revision']==first and not view['backfilled']
    assert all(p['date'] is None or p['date']<='2026-11-03' for p in view['weeks'])
    backfill=fin.day_view(store,'2026-10-30','close')
    assert backfill['revision']==first and backfill['backfilled']
    assert max(p['date'] for p in backfill['weeks'] if p['date'])=='2026-10-30'
    noon_backfill=fin.day_view(store,'2026-11-02','midday')
    assert noon_backfill['latest_date']=='2026-10-30'
    assert noon_backfill['weeks'][-1]['balance'] is None
    with TestClient(create_app(store,collect=False)) as client:
        got=client.get('/api/market',params={'date':'2026-10-30'}).json()['financing']
        assert got['backfilled'] and got['weeks'][-1]['balance']==180e8


def test_financing_daily_scheduler_skips_holidays_and_handles_disclosure_delay():
    cal=['2026-11-02','2026-11-03','2026-11-04']
    at=lambda clock:datetime.fromisoformat('2026-11-03T'+clock).replace(tzinfo=TZ)
    assert fin.due_session(at('09:14'),cal) is None
    assert fin.due_session(at('09:15'),cal)==('2026-11-03','09:15')
    assert fin.due_session(at('12:00'),cal)==('2026-11-03','12:00')
    assert fin.due_session(at('16:30'),cal)==('2026-11-03','16:30')
    assert fin.due_session(at('12:00'),['2026-11-04']) is None


def test_normal_friday_t_plus_one_feed_and_weekend_are_not_stale(tmp_path):
    store=Store(tmp_path/'db')
    store.observe('calendar',['2026-10-23','2026-10-26','2026-10-29','2026-10-30','2026-11-02'])
    store.observe(fin.KEY,dict(rows=[point('2026-10-23'),point('2026-10-29')],latest_date='2026-10-29'))
    for day in ('2026-10-30','2026-10-31'):
        view=fin.day_view(store,day)
        assert view['status']=='available' and view['latest_date']=='2026-10-29'
        assert view['weeks'][-1]['balance'] is None
