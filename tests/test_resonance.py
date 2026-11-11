import json
import numpy as np
import pytest
from backend.resonance import parse_day,turns,pullback,candidates,held,lag_return,series_sector


def test_minute_boundaries_and_duplicate():
    raw={'prec':'10','data':['0930 10 0 0','0932 10.1 4 80','1500 11 5 100','1530 50 5 200']}
    p,a,ref=parse_day(raw)
    assert ref==10 and np.isnan(p[1]) and p[-1]==11 and a[-1]==100
    raw['data'].append('0932 11 4 85')
    with pytest.raises(ValueError,match='duplicate'):parse_day(raw)
    p=np.r_[np.full(121,100.),np.full(121,150.)]
    assert np.isnan(lag_return(p,3)[121:124]).all()
    assert series_sector(np.tile(p,(8,1)))[121]==100


def test_turn_and_pullback_confirmation_are_observable():
    px=np.full(242,100.)
    px[4:16]=[99.9,99.8,99.7,99.6,99.55,99.5,99.55,99.65,99.8,99.95,99.82,100.04]
    events=turns(px)
    assert events[0]=={'pivot':9,'confirm':11}
    for cutoff in range(8,16):
        truncated=px.copy();truncated[cutoff+1:]=np.nan
        assert [e for e in events if e['confirm']<=cutoff]==turns(truncated)
    px[14]=99.78
    pb=pullback(px,9,11,15)
    assert (pb['peak'],pb['pull'],pb['again'])==(13,14,15)
    assert pullback(px,9,11,14)['again'] is None
    px[14]=99.4
    assert pullback(px,9,11,15)['stop']==14


def test_distinct_stock_ties_and_hold_boundaries():
    p=np.full((4,242),10.1);h=np.zeros_like(p,dtype=bool);lvl=np.full_like(p,10.);r=np.ones_like(p)
    for i,t in enumerate([10,11,11,16]):h[i,[t,t+1]]=True
    p[1,15]=9.99;p[2,15]=np.nan
    pb=dict(peak=14,pull=15,again=18)
    rows=candidates(p,h,lvl,r,['a','b','c','d'],['A','B','C','D'],10,12,14,pb,18)
    assert [(s['rank_from'],s['rank_to']) for s in rows]==[(1,1),(2,3),(2,3),(4,4)]
    assert [s['hold'] for s in rows]==['守住','跌破突破位','分钟缺口','未验证']
    assert held(p[0],10,10.,dict(peak=14,pull=15,again=None),18)=='待再攻确认'


def test_replay_benchmarks_are_isolated(tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.resonance import router
    app=FastAPI()
    app.include_router(router(SimpleNamespace(path=tmp_path/'market.sqlite3')))
    c=TestClient(app)
    folder=tmp_path/'resonance/days/2026-11-06';folder.mkdir(parents=True)
    (folder/'summary.json').write_text('{}')
    for symbol in ('sh000001','sz399006','sh000680'):
        r=c.get('/api/resonance',params={'benchmark':symbol})
        assert r.status_code==200 and r.json()['benchmark']==symbol and '尚未归档' in r.json()['reason']
    assert c.get('/api/resonance').json()['benchmark']=='sh000001'
    assert c.get('/api/resonance?benchmark=hs300').status_code==422
