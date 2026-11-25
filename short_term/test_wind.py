import sys
from pathlib import Path
import json
import sqlite3
import pytest
from .wind_core import WindStore, WindService, aliases
from .wind_source import WindSource

DAY='2026-11-23'
def packet(codes,kind='wind',day=DAY):
    rows=[dict(code=c,name='测试'+c,rank=i+1) for i,c in enumerate(codes)]
    return dict(day=day,rows=rows,total=len(rows),complete=True,verified=True)

def test_union_exclusions_restore_restart(tmp_path):
    path=tmp_path/'core.db';store=WindStore(path)
    store.accept('wind',packet(['002148','002685']))
    store.accept('ten',packet(['002148','002685','301391','002771','301288']))
    store.accept('twenty',packet(['002148','002086','600715','002685','600530']))
    rows=store.listing();assert len(rows)==8
    assert len(next(r for r in rows if r['code']=='002148')['sources'])==3
    store.mutate('remove',['002148'])
    store.accept('wind',packet(['002148','002685']))
    store=WindStore(path)
    assert len(store.listing())==7 and store.listing(True)[0]['code']=='002148'
    store.mutate('restore',['002148'])
    assert len(store.listing())==8
    assert not next(r for r in store.listing() if r['code']=='002148').get('manual')
    store.mutate('add',['002148'])
    for kind,codes in [('wind',['002685']),('ten',['002685','301391','002771','301288','600519']),('twenty',['002086','600715','002685','600530','600519'])]:store.accept(kind,packet(codes))
    assert next(r for r in store.listing() if r['code']=='002148')['manual']

def test_partial_and_old_source_retained(tmp_path):
    s=WindStore(tmp_path/'c.db');old=packet(['002148']);s.accept('wind',old)
    for bad in [dict(old,rows=[],total=0),dict(old,complete=False),dict(old,day='2026-11-22'),dict(old,rows=old['rows']*2,total=2)]:
        with pytest.raises(ValueError):s.accept('wind',bad)
    assert s.get('source:wind')['rows']==old['rows']
    with pytest.raises(ValueError):s.accept('ten',packet(['002148']))
    assert len(s.listing())==1

def test_snapshot_dedup_and_search(tmp_path):
    s=WindStore(tmp_path/'c.db');p=packet(['002148']);p['rows'][0]['name']='北纬科技'
    for _ in range(3):s.accept('wind',p)
    with s.db() as db:assert db.execute('SELECT count(*) FROM snapshots').fetchone()[0]==1
    for q in ['BWKJ','beiweikeji','北纬','0021']:assert s.search(q)[0]['code']=='002148'
    assert 'cq' in aliases('重庆')[2:]

def test_manual_migration_no_automatic(tmp_path):
    legacy=tmp_path/'legacy.db'
    with sqlite3.connect(legacy) as db:
        db.executescript('CREATE TABLE identities(body TEXT);CREATE TABLE versions(id INTEGER,security TEXT,at TEXT,body TEXT);')
        for i,(c,m,e) in enumerate([('002148',True,False),('002685',False,True),('301391',False,False)]):
            r=dict(code=c,name=c,manual=m,excluded=e)
            db.execute('INSERT INTO identities VALUES(?)',(json.dumps(r),))
            db.execute('INSERT INTO versions VALUES(?,?,?,?)',(i,c,'2026-11-23',json.dumps(r)))
    s=WindStore(tmp_path/'c.db',legacy)
    assert [r['code'] for r in s.listing()]==['002148']
    assert [r['code'] for r in s.listing(True)]==['002685']
    assert not s.get('legacy')
    s.mutate('remove',['002148']);s=WindStore(tmp_path/'c.db',legacy)
    assert not s.listing()

def test_wind_real_fixture_and_broken_pages():
    data=json.loads(Path(__file__).with_name('wind-fixture.json').read_text(encoding='utf-8'))
    s=WindSource(timeout=1)
    try:
        s._app=lambda *a,**kw:data
        result=s.wind(DAY);assert result['total']==21
        assert next(r for r in result['rows'] if r['code']=='002148')['price']==11.07
        with pytest.raises(ValueError):s.wind('2026-11-24')
        for patch in [dict(Stocks=[]),dict(Count=22),dict(list=data['list']+data['list'][:1]),dict(Day=[])]:
            s._app=lambda *a,**kw:{**data,**patch}
            with pytest.raises(ValueError):s.wind(DAY)
    finally:s.close()

def test_failed_one_source_does_not_clear_others(tmp_path):
    store=WindStore(tmp_path/'c.db');store.accept('wind',packet(['002148']))
    class Transport:
        def set_deadline(self,v):pass
    class Broken:
        transport=Transport()
        def wind(self,expected):raise ValueError('offline')
        def close(self):pass
    service=WindService(store,lambda:Broken())
    try:
        service._refresh('wind',DAY)
        assert store.get('status:wind')['state']=='failed'
        assert store.listing()[0]['code']=='002148'
    finally:service.close()
