import json
import sys
import time
from pathlib import Path
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.store import TZ
from .live_broadcast import BroadcastStore,BroadcastService,parse_broadcast

DAY=datetime.now(TZ).date().isoformat()
AT=datetime.now(TZ).isoformat()

def raw(i,text='开盘啦原文',day=DAY):
    return dict(ID=str(i),Time=int(min(datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=TZ).timestamp(), datetime.fromisoformat(AT).timestamp())),Comment=text,Stock=[['002242','九阳股份']])

def parsed(i,text='原文'):
    return parse_broadcast(dict(date=DAY,List=[raw(i,text)]),DAY)[0]

class Transport:
    retries=0
    def set_deadline(self,v):pass

class Source:
    def __init__(self,rows,fail=False,repeat=False):
        self.transport=Transport();self.rows=rows;self.calls=[];self.fail=fail;self.repeat=repeat
    def _app(self,c,a,**kw):
        self.calls.append(kw)
        if self.fail:raise TimeoutError('sensitive raw provider detail must not appear')
        offset=0 if self.repeat else kw['index']
        return dict(date=DAY,List=self.rows[offset:offset+kw['st']])
    def close(self):pass


def test_store_dedup_revisions_and_first_receipt(tmp_path):
    s=BroadcastStore(tmp_path/'feed.db');r=parsed(1)
    assert s.save(DAY,[r],AT)==(1,0)
    assert s.save(DAY,[r],AT)==(0,0)
    assert s.save(DAY,[dict(r,text='修订')],AT)==(0,1)
    row=s.payload(DAY)['rows'][0]
    assert row['version']==2 and row['received_at']==AT
    with s.db() as db:assert db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0]==2
    assert BroadcastStore(tmp_path/'feed.db').payload(DAY)['rows']==s.payload(DAY)['rows']


def test_original_history_migration_idempotent(tmp_path):
    import sqlite3
    old=tmp_path/'old.db'
    with sqlite3.connect(old) as db:
        db.execute('CREATE TABLE commentary(day,id,published_at,received_at,body)')
        r=parsed(1)
        db.execute('INSERT INTO commentary VALUES(?,?,?,?,?)',(DAY,'1',r['published_at'],AT,json.dumps(r)))
    store=BroadcastStore(tmp_path/'new.db');store.import_history(old)
    assert len(store.payload(DAY)['rows'])==1
    store.save(DAY,[dict(r,text='后来的修订')],AT);store.import_history(old)
    assert store.payload(DAY)['rows'][0]['text']=='后来的修订'


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(date='1999-01-01'),
    lambda p:p.update(List=None),
    lambda p:p['List'].append(p['List'][0]),
    lambda p:p['List'][0].update(Comment=''),
    lambda p:p['List'][0].update(Time=int(time.time()+3600)),
])
def test_invalid_payload_rejected(mutation):
    p=dict(date=DAY,List=[raw(1)]);mutation(p)
    with pytest.raises(Exception):parse_broadcast(p,DAY)


def test_all_pages_and_old_messages_retained(tmp_path):
    source=Source([raw(i) for i in range(205,0,-1)])
    s=BroadcastService(tmp_path/'feed.db',source)
    try:
        p=s.fetch(DAY)
        assert len(p['rows'])==205 and p['status']['history_complete']
        assert [c['index'] for c in source.calls]==[0,100,200]
        source.rows=[raw(206)]
        assert len(s.fetch(DAY)['rows'])==206
        assert s.fetch(DAY)['status']['added']==0
    finally:s.close()


def test_repeated_page_partial_and_no_unbounded_requests(tmp_path):
    source=Source([raw(i) for i in range(100)],repeat=True);s=BroadcastService(tmp_path/'feed.db',source)
    try:
        p=s.fetch(DAY)
        assert len(p['rows'])==100 and p['status']['state']=='partial'
        assert not p['status']['history_complete'] and len(source.calls)==2
    finally:s.close()


def test_failure_preserves_text_and_sanitizes_error(tmp_path):
    source=Source([raw(1)]);s=BroadcastService(tmp_path/'feed.db',source)
    try:
        before=s.fetch(DAY);source.fail=True;after=s.fetch(DAY)
        assert after['rows']==before['rows']
        assert after['status']['last_success_at']==before['status']['last_success_at']
        assert after['status']['state']=='failed'
        assert 'sensitive' not in json.dumps(after)
    finally:s.close()


def test_single_shared_background_request(tmp_path):
    source=Source([raw(1)]);s=BroadcastService(tmp_path/'feed.db',source)
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            accepted=list(pool.map(lambda _:s.request(),range(20)))
        s.future.result(timeout=3)
        assert sum(accepted)==1 and len(source.calls)==1
    finally:s.close()


def test_page_contains_no_analysis_or_model_entry():
    from . import terminal as m
    seed=json.loads(Path(__file__).with_name('test-seed.json').read_text(encoding='utf-8'))
    page=m.build_page(seed).decode()
    for forbidden in ('data-analysis-body','/api/analysis','后台模型研判','ModelReasoner','TapeIntelligence'):
        assert forbidden not in page
    assert '开盘啦 · 盘中直播' in page and './broadcast/events' in page
