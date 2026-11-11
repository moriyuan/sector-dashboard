import hashlib,json,sqlite3
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.seat_backtest import router


def fixture(tmp_path):
    meta=dict(run_id='frozen-v1',profiles=['甲','乙'],months=['2026-01'])
    stat=dict(profile='甲',month='2026-01',n=12,days=7,events=12,win_rate=.5,ci_low=.3,ci_high=.7,mean_return=.01,states={},rank=1)
    small=dict(stat,profile='乙',n=2,days=2)
    small.pop('rank')
    dbfile=tmp_path/'study.sqlite3'
    with sqlite3.connect(dbfile) as db:
        db.executescript('CREATE TABLE meta(payload TEXT);CREATE TABLE stats(kind,scope,profile,period,horizon,payload);CREATE TABLE trades(profile,period,horizon,month,signal_date,code,state,payload);')
        db.execute('INSERT INTO meta VALUES (?)',(json.dumps(meta),))
        for kind,scope in [('ranking','recent_12_full_months'),('monthly','2026-01')]:
            for s in [stat,small]:db.execute('INSERT INTO stats VALUES (?,?,?,?,?,?)',(kind,scope,s['profile'],'single',5,json.dumps(s)))
        for i in range(12):
            row=dict(code=f'{i:06d}',state='entry_skipped' if i==0 else 'closed',signal_date='2026-01-05',net_return=None if i==0 else .01)
            db.execute('INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)',('甲','single',5,'2026-01','2026-01-05',row['code'],row['state'],json.dumps(row)))
    (tmp_path/'monthly.csv').write_text('月份,收益\n2026-01,1%\n')
    app=FastAPI();app.include_router(router(tmp_path))
    return TestClient(app),dbfile


def test_query_filters_ranking_thresholds_and_pagination(tmp_path):
    c,file=fixture(tmp_path);digest=hashlib.sha256(file.read_bytes()).hexdigest()
    data=c.get('/api/seat-backtest',params={'horizon':5}).json()
    assert len(data['rows'])==2 and data['min_trades']==30
    for h in (1,3,5,10):
        for path in ('', '/monthly', '/trades'):
            assert c.get('/api/seat-backtest'+path,params={'horizon':h,'profile':'甲'}).status_code==200
    month=c.get('/api/seat-backtest',params={'scope':'2026-01'}).json()
    assert month['min_trades']==10 and month['rows'][0]['rank']==1
    assert not month['rows'][1]['rank_eligible'] and 'rank' not in month['rows'][1]
    assert len(c.get('/api/seat-backtest/monthly',params={'profile':'甲'}).json()['rows'])==1
    assert c.get('/api/seat-backtest/monthly',params={'profile':'甲','period':'three'}).json()['rows']==[]
    r=c.get('/api/seat-backtest/trades',params={'profile':'甲','page':2}).json()
    assert r['total']==12 and len(r['rows'])==2
    r=c.get('/api/seat-backtest/trades',params={'profile':'甲','outcome':'entry_skipped'}).json()
    assert r['total']==1 and r['rows'][0]['net_return'] is None
    assert c.get('/api/seat-backtest/trades',params={'profile':'甲','page':100}).json()['rows']==[]
    assert hashlib.sha256(file.read_bytes()).hexdigest()==digest


def test_read_only_queries_and_download_allowlist(tmp_path):
    c,_=fixture(tmp_path)
    for path in ['/api/seat-backtest','/api/seat-backtest/trades','/api/seat-backtest/download/monthly.csv']:
        assert c.post(path).status_code==405
    for params in [{'horizon':2},{'period':'bad'},{'scope':"' OR 1=1 --"}]:assert c.get('/api/seat-backtest',params=params).status_code==422
    for params in [{'profile':'未知'},{'profile':'甲','month':'2020-01'},{'profile':'甲','page_size':100000},{'profile':'甲','page':0}]:assert c.get('/api/seat-backtest/trades',params=params).status_code==422
    assert c.get('/api/seat-backtest/download/monthly.csv').status_code==200
    assert c.get('/api/seat-backtest/download/study.sqlite3').status_code==404
    assert c.get('/api/seat-backtest/download/%2e%2e%2fapp.py').status_code==404


def test_missing_release_is_explicit_not_empty(tmp_path):
    app=FastAPI();app.include_router(router(tmp_path))
    with TestClient(app) as c:
        assert c.get('/api/seat-backtest').status_code==503
        assert c.get('/api/seat-backtest/download/monthly.csv').status_code==503
