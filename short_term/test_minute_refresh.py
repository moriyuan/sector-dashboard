from datetime import datetime, timedelta

import pytest

from .test_fast_preview import engine, preview


@pytest.mark.parametrize('result',[{}, {'joined_existing_job':True}, {'reused_recent_result':True}])
def test_each_minute_has_only_one_attempt(engine,monkeypatch,result):
    start=datetime(2026,11,20,10,0,tzinfo=preview.TZ)
    engine.verified_day=start.date().isoformat()
    calls=[]
    monkeypatch.setattr(engine,'request',lambda **kwargs:calls.append(kwargs) or result)
    for seconds in range(0,180):engine.schedule_tick(start+timedelta(seconds=seconds))
    assert len(calls)==3
    assert all(x['trigger']=='scheduled' for x in calls)
    assert engine.state()['automatic']['interval_seconds']==60


def test_only_session_minutes_are_scheduled(engine,monkeypatch):
    day=datetime(2026,11,20,tzinfo=preview.TZ)
    engine.verified_day=day.date().isoformat();calls=[]
    monkeypatch.setattr(engine,'request',lambda **kwargs:calls.append(kwargs) or {})
    for minute in range(24*60):engine.schedule_tick(day+timedelta(minutes=minute))
    assert len(calls)==242
    engine.schedule_tick(datetime(2026,11,21,10,0,tzinfo=preview.TZ))
    assert len(calls)==242


def test_new_day_is_verified_automatically_before_first_round(engine,monkeypatch):
    current=datetime(2026,11,20,10,0,tzinfo=preview.TZ)
    monkeypatch.setattr(preview,'now',lambda:current)
    engine.verified_day='2026-11-19'
    calls=[]
    monkeypatch.setattr(engine,'request',lambda **kwargs:calls.append(kwargs) or {})
    engine.schedule_tick(current)
    engine.day_probe.result(timeout=2)
    assert engine.verified_day=='2026-11-20' and not calls
    engine.schedule_tick(current)
    assert len(calls)==1


def test_unverified_day_backs_off_without_fetching_full_market(engine,monkeypatch):
    current=datetime(2026,11,20,10,0,tzinfo=preview.TZ)
    monkeypatch.setattr(preview,'now',lambda:current)
    engine.verified_day='2026-11-19';probes=[];calls=[]
    def unavailable(day):
        probes.append(day)
        raise preview.SourceError('source still reports previous trading day')
    monkeypatch.setattr(engine.source,'fetch_emotion',unavailable)
    monkeypatch.setattr(engine,'request',lambda **kwargs:calls.append(kwargs) or {})
    engine.schedule_tick(current);engine.day_probe.result(timeout=2)
    for i in range(60):engine.schedule_tick(current+timedelta(seconds=i))
    assert len(probes)==1 and not calls
    assert engine.state()['automatic']['pause_reason']=='待核验交易日'
