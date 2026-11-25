"""Independent KPL original-text feed. No rules, scores or model dependency."""
import json
import sqlite3
import threading
import time
from pathlib import Path
from contextlib import closing
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from backend.store import TZ
from backend.sources.kaipanla import KaipanlaSource, source_date
from backend.sources.base import SourceError


def stamp():
    return datetime.now(TZ).isoformat(timespec='milliseconds')


def parse_broadcast(payload, day):
    source_date(payload.get('date'), day)
    raw = payload.get('List')
    if not isinstance(raw, list):
        raise SourceError('直播列表结构异常')
    rows, seen = [], set()
    for r in raw:
        ident = str(r['ID'])
        published = datetime.fromtimestamp(int(r['Time']), TZ)
        text = r.get('Comment')
        if (not ident.isdigit() or ident in seen or published.date().isoformat() != day
                or published.timestamp() > time.time()+60):
            raise SourceError('直播日期或编号异常')
        if not isinstance(text, str) or not text.strip() or len(text)>20000:
            raise SourceError('直播正文异常')
        seen.add(ident)
        rows.append(dict(id=ident, published_at=published.isoformat(), text=text,
            plate=str(r.get('PlateName') or ''), stocks=[dict(code=str(x[0]),name=str(x[1]))
            for x in r.get('Stock', []) if isinstance(x,list) and len(x)>=2]))
    return sorted(rows,key=lambda r:(r['published_at'],int(r['id'])))


class BroadcastStore:
    def __init__(self,path):
        self.path=path
        with self.db() as db,db:
            db.executescript('''PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS messages(day TEXT,id TEXT,published TEXT,received TEXT,updated TEXT,version INTEGER,body TEXT,PRIMARY KEY(day,id));
              CREATE TABLE IF NOT EXISTS revisions(day TEXT,id TEXT,version INTEGER,at TEXT,body TEXT,PRIMARY KEY(day,id,version));
              CREATE TABLE IF NOT EXISTS checks(day TEXT PRIMARY KEY,body TEXT);
              CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
            ''')

    def import_history(self,path):
        """Copy only stored original broadcasts. Never load former analysis code/reports."""
        path=Path(path)
        if not path.exists():return
        with self.db() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='original-history-import'").fetchone():return
        with closing(sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True)) as old:
            if not old.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='commentary'").fetchone():return
            saved=old.execute('SELECT day,id,published_at,received_at,body FROM commentary').fetchall()
        with self.db() as db,db:
            for day,ident,published,received,raw in saved:
                row=json.loads(raw)
                if row.get('id')!=ident or not ident.isdigit() or published[:10]!=day or not isinstance(row.get('text'),str):continue
                if not row['text'].strip() or len(row['text'])>20000:continue
                if db.execute('SELECT 1 FROM messages WHERE day=? AND id=?',(day,ident)).fetchone():continue
                body=json.dumps(row,ensure_ascii=False,sort_keys=True)
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',(day,ident,published,received,received,1,body))
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(day,ident,1,received,body))
            db.execute("INSERT INTO metadata VALUES('original-history-import',?)",(stamp(),))

    def db(self):
        return closing(sqlite3.connect(self.path,timeout=5))

    def save(self,day,rows,at):
        added=changed=0
        with self.db() as db,db:
            for row in rows:
                body=json.dumps(row,ensure_ascii=False,sort_keys=True)
                old=db.execute('SELECT received,version,body FROM messages WHERE day=? AND id=?',(day,row['id'])).fetchone()
                if old and old[2]==body:continue
                version=old[1]+1 if old else 1
                added+=int(not old);changed+=int(bool(old))
                db.execute('INSERT OR REPLACE INTO messages VALUES(?,?,?,?,?,?,?)',
                    (day,row['id'],row['published_at'],old[0] if old else at,at,version,body))
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(day,row['id'],version,at,body))
        return added,changed

    def check(self,day,state):
        with self.db() as db,db:
            db.execute('INSERT OR REPLACE INTO checks VALUES(?,?)',(day,json.dumps(state,ensure_ascii=False)))

    def payload(self,day):
        with self.db() as db:
            rows=db.execute('SELECT body,received,updated,version FROM messages WHERE day=? ORDER BY published,CAST(id AS INTEGER)',(day,)).fetchall()
            check=db.execute('SELECT body FROM checks WHERE day=?',(day,)).fetchone()
        return dict(day=day,rows=[dict(json.loads(b),received_at=r,updated_at=u,version=v) for b,r,u,v in rows],
                    status=json.loads(check[0]) if check else dict(state='idle',message='等待首次直播查询'))


class BroadcastService:
    def __init__(self,path,source=None):
        self.store=BroadcastStore(path)
        self.store.import_history(Path(path).parent/'analysis-history.sqlite3')
        self.source=source or KaipanlaSource(timeout=4)
        self.source.transport.retries=0
        self.stop=threading.Event();self.lock=threading.Lock()
        self.runner=ThreadPoolExecutor(max_workers=1,thread_name_prefix='kpl-live-text')
        self.future=None;self.last_attempt=-1e9;self.revision=0
        self.instance=str(time.time_ns());self.last_viewed=-1e9

    def touch(self):
        self.last_viewed=time.monotonic()

    def request(self):
        with self.lock:
            if self.stop.is_set() or (self.future and not self.future.done()) or time.monotonic()-self.last_attempt<10:
                return False
            self.last_attempt=time.monotonic()
            self.future=self.runner.submit(self.fetch,datetime.now(TZ).date().isoformat())
            return True

    def fetch(self,day):
        started=time.monotonic();old=self.store.payload(day)['status']
        state=dict(old,state='running',checked_at=stamp(),message='查询中，保留原文')
        self.store.check(day,state);self.revision+=1
        rows=[];seen=set();complete=False;warning=None
        try:
            self.source.transport.set_deadline(time.monotonic()+8)
            # Bound catch-up; never silently claim a complete day when a page repeats or budget ends.
            for offset in range(0,500,100):
                payload=self.source._app('ConceptionPoint','ZhiBoContent',index=offset,st=100,Date=day)
                page=parse_broadcast(payload,day)
                ids={r['id'] for r in page}
                if ids & seen:
                    warning='分页重复，历史覆盖待核实';break
                seen|=ids;rows.extend(page)
                if len(page)<100:complete=True;break
            added,edited=self.store.save(day,rows,stamp())
            state.update(state='ready' if complete else 'partial',last_success_at=stamp(),
                message=warning or ('本轮新增 '+str(added)+' 条' if added else '本轮暂无新直播') if complete or warning else '历史超出本轮补取上限',
                added=added,edited=edited,history_complete=complete,elapsed_ms=round((time.monotonic()-started)*1000))
        except Exception:
            # Valid earlier pages still contain useful new messages; mark the coverage gap.
            if rows:self.store.save(day,rows,stamp())
            state.update(state='failed',history_complete=False,message='直播查询未完成，保留已获取原文',elapsed_ms=round((time.monotonic()-started)*1000))
        finally:
            self.source.transport.set_deadline(None)
            state['checked_at']=stamp();self.store.check(day,state);self.revision+=1
        return self.store.payload(day)

    def payload(self,day=None):
        result=self.store.payload(day or datetime.now(TZ).date().isoformat())
        result.update(revision=self.revision,instance=self.instance,interval_seconds=60)
        return result

    def schedule(self):
        self.request()
        while not self.stop.wait(1):
            # A connected page keeps the shared feed alive, including lunch and after close.
            if time.monotonic()-self.last_viewed<90 and time.monotonic()-self.last_attempt>=60:self.request()

    def close(self):
        self.stop.set();self.runner.shutdown(wait=True);self.source.close()
