import json
import sqlite3
import pytest
from backend.store import Store
from backend.news import NewsStore
from scripts.retain_recent import retained_days, build_retained, prune_files


def fixture_db(tmp_path):
    path=tmp_path/'market.sqlite3';store=Store(path);NewsStore(store)
    for day in ('2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12'):
        store.save('market',day,'close',{'stats':{}})
    store.save('market','2026-11-13','midday',{'stats':{}})
    store.observe('calendar',['2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13'])
    return path,store


def test_three_archives_not_calendar_days(tmp_path):
    path,store=fixture_db(tmp_path)
    with sqlite3.connect(path) as db:
        assert retained_days(db,3,'2026-11-13')==['2026-11-12','2026-11-11','2026-11-10']
        assert retained_days(db,3,'2026-11-10')==['2026-11-10','2026-11-09','2026-11-06']


def test_compact_preserves_current_and_links_removes_old_and_cache(tmp_path):
    path,store=fixture_db(tmp_path)
    with store.connect() as d:
        for i,stamp in enumerate(('2026-11-09T23:59:59+08:00','2026-11-09T16:00:00+00:00'),1):
            d.execute('INSERT INTO news_items VALUES(?,?,?,?,?,?,?,?)',(i,str(i),'em',str(i),'h',stamp,stamp,'{}'))
            d.execute('INSERT INTO news_analysis VALUES(?,?,?,?,?,?)',(i,str(i),'h',stamp,'ok','{}'))
            d.execute('INSERT INTO news_jobs VALUES(?,0,0)',(str(i),))
        d.execute("INSERT INTO news_budget VALUES('today','2026-11-13',0.7,NULL,NULL)")
        d.execute("CREATE TABLE backlog_items(id INTEGER PRIMARY KEY,title TEXT)")
        d.execute("INSERT INTO backlog_items VALUES(1,'user note')")
    store.observe('bars:BK0001',[{'date':'2026-11-09','close':1},{'date':'2026-11-10','close':2}])
    first=store.observation_record(key='bars:BK0001')['id']
    store.observe('bars:BK0001',[{'date':'2026-11-10','close':3}])
    store.observe('raw:vendor',{'large':'cache'})
    dest=tmp_path/'out.sqlite3';build_retained(path,dest,'2026-11-10')
    with sqlite3.connect(dest) as d:
        assert [r[0] for r in d.execute('select distinct trade_date from snapshots order by trade_date')]==['2026-11-10','2026-11-11','2026-11-12','2026-11-13']
        assert d.execute('select event_id from news_items').fetchall()==[('2',)]
        assert d.execute('select event_id from news_analysis').fetchall()==[('2',)]
        assert d.execute('select event_id from news_jobs').fetchall()==[('2',)]
        assert d.execute('select reserved from news_budget').fetchone()[0]==0.7
        assert d.execute('select title from backlog_items').fetchone()[0]=='user note'
        assert json.loads(d.execute('select payload from observations where id=?',(first,)).fetchone()[0])==[{'date':'2026-11-10','close':2}]
        assert d.execute("select count(*) from observations where key like 'raw:%'").fetchone()[0]==0
        d.execute("INSERT INTO snapshots(kind,trade_date,mode,payload) VALUES('market','2026-11-06','close','{}')")
        d.execute("INSERT INTO news_items(event_id,published,payload) VALUES('old','2026-10-01','{}')")
        assert d.execute("select count(*) from snapshots where trade_date<'2026-11-10'").fetchone()[0]==0
        assert d.execute("select count(*) from news_items where event_id='old'").fetchone()[0]==0
        assert d.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_unknown_table_fails_without_replacing_original(tmp_path):
    path,store=fixture_db(tmp_path)
    with store.connect() as d:d.execute('CREATE TABLE unknown_user_data(value TEXT)')
    with pytest.raises(ValueError,match='unclassified'):
        build_retained(path,tmp_path/'out.sqlite3','2026-11-10')
    with sqlite3.connect(path) as d:assert d.execute('select count(*) from snapshots').fetchone()[0]==6


def test_dated_files_do_not_touch_frozen_research(tmp_path):
    for relative in ['quant-price-v2/inputs/2026-11-09.npz','quant-price-v2/inputs/2026-11-10.npz','resonance/days/2026-11-09/a.json','resonance/days/2026-11-12/a.json','research/seat-follow-v1/study.sqlite3']:
        p=tmp_path/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
    prune_files(tmp_path,'2026-11-10')
    assert not (tmp_path/'quant-price-v2/inputs/2026-11-09.npz').exists()
    assert (tmp_path/'quant-price-v2/inputs/2026-11-10.npz').exists()
    assert not (tmp_path/'resonance/days/2026-11-09').exists()
    assert (tmp_path/'research/seat-follow-v1/study.sqlite3').exists()


def test_insufficient_sessions_refuses_guessing(tmp_path):
    path,store=fixture_db(tmp_path)
    with sqlite3.connect(path) as d:
        with pytest.raises(ValueError,match='not enough'):
            retained_days(d,3,'2026-11-07')


def test_benchmark_parquet_retains_cutoff_and_later_rows(tmp_path):
    import pandas as pd
    path=tmp_path/'quant-price-sources/benchmark.parquet'
    path.parent.mkdir()
    frame=pd.DataFrame({'date':['2026-11-09','2026-11-10','2026-11-11'],
                        'return':[0.01,-0.02,0.03]})
    frame.to_parquet(path,index=False)
    prune_files(tmp_path,'2026-11-10')
    pd.testing.assert_frame_equal(pd.read_parquet(path),frame.iloc[1:].reset_index(drop=True))


def test_compressed_backup_is_lossless_and_replaces_only_its_target(tmp_path):
    import gzip
    import hashlib
    from scripts.retention_maintenance import compressed_backup
    path,store=fixture_db(tmp_path)
    target=tmp_path/'retained.sqlite3.gz';target.write_bytes(b'old backup')
    source_bytes=path.read_bytes()
    result=compressed_backup(path,target)
    assert result==hashlib.sha256(source_bytes).hexdigest()
    assert gzip.decompress(target.read_bytes())==source_bytes
    assert path.read_bytes()==source_bytes
    assert not target.with_suffix('.gz.tmp').exists()
