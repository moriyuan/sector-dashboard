from .tracking_pool import tracking_codes
"""Supplier membership + explicit manual decisions. No stock selection algorithm."""
import copy
import json
import re
import sqlite3
import threading
import time
import math
from pathlib import Path
from contextlib import contextmanager,closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import lru_cache
from pypinyin import lazy_pinyin, Style
from backend.store import TZ
from backend.sources.kaipanla import KaipanlaSource
from .full_pool_quotes import BasicQuoteSource
from .wind_source import WindSource
from backend.sources.base import SourceError
import hashlib


def clock():return datetime.now(TZ).isoformat(timespec='milliseconds')
def encoded(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False)
def identity(code):
    if not isinstance(code,str) or not re.fullmatch(r'(?:60|68|00|30|43|83|87|88|92)\d{4}',code):
        raise ValueError('需要有效六位A股代码')
    return ('SH' if code[:2] in ('60','68') else 'SZ' if code[:2] in ('00','30') else 'BJ')+':'+code


@lru_cache(maxsize=15000)
def aliases(name):
    # Phrase-aware conversion; common securities polyphones get additional searchable aliases.
    values=[name.lower(),''.join(lazy_pinyin(name)).lower(),''.join(lazy_pinyin(name,style=Style.FIRST_LETTER)).lower()]
    for word,sound,initial in [('重庆','chongqing','cq'),('重工','zhonggong','zg'),('长江','changjiang','cj'),('长城','changcheng','cc')]:
        if word in name:
            rest=name.replace(word,'',1)
            values.extend([sound+''.join(lazy_pinyin(rest)),initial+''.join(lazy_pinyin(rest,style=Style.FIRST_LETTER))])
    return values


class WindStore:
    def __init__(self,path,legacy=None):
        self.path=Path(path)
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS identities(code TEXT PRIMARY KEY,body TEXT);
              CREATE TABLE IF NOT EXISTS manual(code TEXT PRIMARY KEY,added INTEGER,excluded INTEGER,at TEXT,note TEXT);
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,body TEXT);
              CREATE TABLE IF NOT EXISTS operations(id INTEGER PRIMARY KEY,at TEXT,action TEXT,code TEXT);
              CREATE TABLE IF NOT EXISTS detail(code TEXT PRIMARY KEY,at TEXT,body TEXT);
            ''')
        if legacy and Path(legacy).exists() and self.get('migrated') is None:self.migrate(legacy)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()

    def get(self,key):
        with self.db() as db:r=db.execute('SELECT body FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else None

    def put(self,key,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,encoded(value)))

    def migrate(self,path):
        with closing(sqlite3.connect('file:'+Path(path).as_posix()+'?mode=ro',uri=True)) as old:
            directory=[json.loads(r[0]) for r in old.execute('SELECT body FROM identities')]
            versions=[json.loads(r[0]) for r in old.execute('SELECT body FROM (SELECT body,ROW_NUMBER() OVER(PARTITION BY security ORDER BY at DESC,id DESC) n FROM versions) WHERE n=1')]
        # One transaction: retries cannot partially migrate or lose manual exclusions.
        with self.db() as db:
            for r in directory:db.execute('INSERT OR REPLACE INTO identities VALUES(?,?)',(r['code'],encoded(r)))
            for r in versions:
                if r.get('manual') or r.get('excluded'):
                    db.execute('INSERT OR IGNORE INTO manual VALUES(?,?,?,?,?)',(r['code'],int(bool(r.get('manual'))),int(bool(r.get('excluded'))),r.get('manual_at') or r.get('first_seen') or clock(),r.get('manual_note','')))
            for k,v in [('migrated',dict(at=clock(),identities=len(directory)))]:
                db.execute('INSERT INTO settings VALUES(?,?)',(k,encoded(v)))

    def identify(self,rows):
        with self.db() as db:
            for r in rows:
                key=identity(r['code']);name=r.get('name')
                if not isinstance(name,str) or not name.strip() or len(name)>60:raise ValueError('证券名称未核实')
                db.execute('INSERT OR REPLACE INTO identities VALUES(?,?)',(r['code'],encoded(dict(code=r['code'],name=name,market=key[:2],verified_at=clock()))))

    def search(self,query):
        query=query.strip().lower()
        if not 1<=len(query)<=60:return []
        with self.db() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM identities')]
        rows=[r for r in rows if query in r['code'] or any(query in a for a in aliases(r['name']))]
        return sorted(rows,key=lambda r:(query not in (r['code'],r['name'].lower()),not r['code'].startswith(query),r['code']))[:30]

    def accept(self, kind, packet):
        if kind not in ('wind','ten','twenty') or packet.get('complete') is not True or packet.get('verified') is not True:
            raise ValueError('来源完整性尚未核验')
        day=packet['day'];datetime.strptime(day,'%Y-%m-%d')
        if day>clock()[:10]:raise ValueError('来源交易日超前')
        rows=packet['rows']
        if not isinstance(rows,list) or len(rows)!=packet.get('total') or not rows:
            raise ValueError('来源数量异常')
        if kind!='wind' and len(rows)!=5:raise ValueError('区间榜必须完整返回前五')
        codes=[identity(r['code']) for r in rows]
        if len(set(codes))!=len(codes):raise ValueError('来源证券重复')
        self.identify(rows)
        stamp=clock();value=dict(packet,received_at=stamp)
        with self.db() as db:
            old=db.execute('SELECT body FROM settings WHERE key=?',('source:'+kind,)).fetchone()
            if old and json.loads(old[0])['day']>day:raise ValueError('来源日期倒退')
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('source:'+kind,encoded(value)))
            digest=hashlib.sha256(encoded(packet).encode()).hexdigest()
            db.execute('CREATE TABLE IF NOT EXISTS snapshots(kind TEXT,day TEXT,digest TEXT,at TEXT,body TEXT,PRIMARY KEY(kind,day,digest))')
            db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?)',(kind,day,digest,stamp,encoded(packet)))
            db.execute("DELETE FROM snapshots WHERE day<date(?,'-180 day')",(day,))
            for r in rows:
                db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',('first:'+r['code'],encoded(stamp)))

    def listing(self,removed=False):
        bycode={}
        for kind,label in [('wind','风向标'),('ten','10日前五'),('twenty','20日前五')]:
            packet=self.get('source:'+kind)
            if not packet:continue
            for row in packet['rows']:
                r=bycode.setdefault(row['code'],dict(code=row['code'],name=row['name'],manual=False,sources=[],first_seen=self.get('first:'+row['code'])))
                r['sources'].append(dict(kind=kind,label=label,day=packet['day'],rank=row.get('rank'),at=packet['received_at']))
                for key in ('return_10d','return_20d'):
                    if key in row:r[key]=row[key]
                if kind=='wind':r['pool_quote']=dict(row)
                r['theme']=r.get('theme') or row.get('theme','')
        with self.db() as db:
            directory={c:json.loads(b) for c,b in db.execute('SELECT code,body FROM identities')}
            for code,added,excluded,at,note in db.execute('SELECT * FROM manual'):
                if code not in bycode and (added or excluded):
                    if code not in directory:continue
                    bycode[code]=dict(directory[code],sources=[],first_seen=at)
                if code in bycode:
                    bycode[code].update(manual=bool(added),excluded=bool(excluded),note=note,manual_at=at)
                    if added:bycode[code]['sources'].append(dict(kind='manual',label='人工添加',at=at))
        return [dict(r,market=identity(r['code'])[:2]) for r in bycode.values() if bool(r.get('excluded'))==removed]

    def mutate(self,action,codes,note='',category=''):
        if action not in ('add','remove','restore') or not isinstance(codes,list) or not 1<=len(codes)<=100:
            raise ValueError('无效操作')
        codes=list(dict.fromkeys(codes))
        if not isinstance(note,str) or len(note)>300:raise ValueError('备注过长')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for code in codes:
                identity(code)
                if not db.execute('SELECT 1 FROM identities WHERE code=?',(code,)).fetchone():raise ValueError('证券身份未核验，请先搜索选择')
            for code in codes:
                old=db.execute('SELECT added,excluded,at,note FROM manual WHERE code=?',(code,)).fetchone()
                added=1 if action=='add' else old[0] if old else 0
                db.execute('INSERT OR REPLACE INTO manual VALUES(?,?,?,?,?)',(code,added,int(action=='remove'),old[2] if old else clock(),note or (old[3] if old else '')))
                db.execute('INSERT INTO operations(at,action,code) VALUES(?,?,?)',(clock(),action,code))
        return dict(changed=codes)

    def detail(self,code,value=None):
        identity(code)
        with self.db() as db:
            if value is not None:db.execute('INSERT OR REPLACE INTO detail VALUES(?,?,?)',(code,clock(),encoded(value)))
            row=db.execute('SELECT at,body FROM detail WHERE code=?',(code,)).fetchone()
        return dict(json.loads(row[1]),received_at=row[0]) if row else dict(state='missing',message='近期日线尚未请求')


def parse_daily(p,code):
    if p.get('StockID')!=code:raise ValueError('日线证券不符')
    x,y=p.get('x'),p.get('y')
    if not isinstance(x,list) or not x or not isinstance(y,list) or len(x)!=len(y):raise ValueError('日线结构缺失')
    bars=[]
    for i,(day,values) in enumerate(zip(x,y)):
        day=datetime.strptime(day,'%Y%m%d').date().isoformat()
        if day>clock()[:10] or len(values)<4:raise ValueError('日线日期/价格结构异常')
        nums=[float(v) for v in values[:4]]
        if not all(math.isfinite(v) and v>0 for v in nums):raise ValueError('日线价格无效')
        if nums[2]<max(nums[0],nums[1],nums[3]) or nums[3]>min(nums[0],nums[1],nums[2]):raise ValueError('日线高低价不符')
        bar=dict(day=day,open=nums[0],close=nums[1],high=nums[2],low=nums[3])
        for field,key in [('amount','bal'),('volume','vol'),('turnover','turnover')]:
            vals=p.get(key,[])
            val=vals[i] if isinstance(vals,list) and i<len(vals) else None
            bar[field]=float(val) if isinstance(val,(float,int)) and math.isfinite(val) and val>=0 else None
        bars.append(bar)
    if [b['day'] for b in bars]!=sorted({b['day'] for b in bars}):raise ValueError('日线日期重复或乱序')
    return dict(state='ready',bars=bars[-60:],source='开盘啦 GetKLineDay_W14',adjustment='供应商原始口径，复权尚未核验',
                return_10d=None,return_20d=None,message='日线可读；跨日复权口径未核验，10/20日涨幅暂不计算')


class WindService:
    def __init__(self,store,source_factory=None):
        self.store=store;self.stop=threading.Event();self.lock=threading.Lock()
        self.worker=ThreadPoolExecutor(max_workers=2,thread_name_prefix='core-details')
        self.sources=ThreadPoolExecutor(max_workers=3,thread_name_prefix='core-membership')
        self.source_factory=source_factory or (lambda:WindSource(timeout=4))
        self.pending={};self.jobs={};self.last_key={};self.gate=None
        self.search_slots=threading.BoundedSemaphore(2)

    def tick(self,view,force=False):
        day=view['date'];now=clock()
        if day>now[:10]:return
        # Closed and pre-open snapshots stay explicitly dated; no fabricated new session.
        active=day==now[:10] and '09:15'<=now[11:16]<='15:05'
        slot=now[:16] if active else now[:15]+str(int(now[15])//5)
        with self.lock:
            for kind in ('wind','ten','twenty'):
                key=(day,slot)
                if self.jobs.get(kind) and not self.jobs[kind].done():continue
                if not force and self.last_key.get(kind)==key:continue
                self.last_key[kind]=key
                self.jobs[kind]=self.sources.submit(self._refresh,kind,day)

    def _refresh(self,kind,day):
        source=self.source_factory();source.transport.retries=0
        started=time.monotonic()
        if self.gate:source.transport._throttle=self.gate._throttle
        try:
            source.transport.set_deadline(started+20)
            if kind=='wind':packet=source.wind(expected=day)
            else:
                window=10 if kind=='ten' else 20
                directory=kind=='twenty' and not self.store.get('directory:'+day)
                data=source.fetch_interval_leaders(day,window,directory=directory)
                if directory:
                    self.store.identify(data['directory'])
                    self.store.put('directory:'+day,dict(day=day,at=clock(),count=len(data['directory'])))
                packet=dict(day=day,rows=data['rows'],total=5,source='开盘啦区间榜',complete=True,verified=True,
                    ranking={k:v for k,v in data.items() if k not in ('raw','directory')})
            self.store.accept(kind,packet)
            self.store.put('status:'+kind,dict(state='ready',day=day,checked_at=clock(),elapsed_ms=round((time.monotonic()-started)*1000)))
        except Exception as exc:
            self.store.put('status:'+kind,dict(state='failed',day=day,checked_at=clock(),message='本轮未更新，保留上次成功名单（'+type(exc).__name__+'）'))
        finally:source.close()

    def ranking(self,day):
        packet=self.store.get('source:ten')
        if not packet or packet['day']!=day:raise SourceError('10日前五仍在独立后台更新')
        return dict(packet['ranking'],observed_at=packet['received_at'])

    def search(self,q,day):
        rows=self.store.search(q)
        if not rows and re.fullmatch(r'\d{6}',q):
            identity(q)
            if not self.search_slots.acquire(blocking=False):raise ValueError('搜索繁忙，请稍后重试')
            s=BasicQuoteSource(timeout=3);s.transport.retries=0
            try:
                s.transport.set_deadline(time.monotonic()+4);s.allowed_days={clock()[:10],day}
                quote=s.basic_quote(q,day);self.store.identify([quote]);rows=self.store.search(q)
            finally:s.close();self.search_slots.release()
        members={r['code'] for r in self.store.listing()};excluded={r['code'] for r in self.store.listing(True)}
        return [dict(r,in_pool=r['code'] in members,excluded=r['code'] in excluded) for r in rows]

    def request_detail(self,code):
        identity(code)
        if not any(r['code']==code for r in self.store.listing()):raise ValueError('请先加入近期核心')
        with self.lock:
            old=self.store.detail(code)
            if old.get('received_at','')[:10]==clock()[:10] and old.get('state')=='ready':return old
            if code not in self.pending or self.pending[code].done():
                if len([f for f in self.pending.values() if not f.done()])<100:
                    self.pending[code]=self.worker.submit(self._detail,code)
            return dict(old,state='loading',message='后台补齐近期日线')

    def _detail(self,code):
        s=KaipanlaSource(timeout=4);s.transport.retries=0
        try:
            s.transport.set_deadline(time.monotonic()+5)
            p=s._post('https://apphis.longhuvip.com/w1/api/index.php',dict(c='StockLineData',a='GetKLineDay_W14',StockID=code,Type='d',Index=0,st=61,Is_FS=1))
            result=parse_daily(p,code)
        except Exception:
            previous=self.store.detail(code)
            result=dict(previous,state='failed',message='日线获取失败，旧数据保留，可重新展开重试')
        finally:s.close()
        self.store.detail(code,result)

    def payload(self,view,quotes):
        at=clock();known=quotes.last_known(at);attempts=quotes.attempts()
        def decorate(row):
            q=known.get(row['code']);fallback=row.get('pool_quote')
            if fallback and fallback.get('quote_day') and (not q or fallback['quote_day']>q.get('quote_day','')):q=fallback
            origin=q.get('source_time') if q else None
            state='缺失'
            if q:
                state='来源时点未提供' if not origin else '最近可用'
                if q.get('quote_day')==at[:10] and origin and '09:15'<=at[11:16]<='15:00':
                    age=(datetime.fromisoformat(at)-datetime.fromisoformat(origin)).total_seconds()
                    state='当日报价' if 0<=age<=120 else '过期报价'
            return dict(row,quote=q,quote_status=state,quote_attempt=attempts.get(row['code']),detail=self.store.detail(row['code']))
        packets={k:self.store.get('source:'+k) for k in ('wind','ten','twenty')}
        rows=self.store.listing();removed=self.store.listing(True)
        active_tracking=tracking_codes(view,self.store)
        source_codes={r['code'] for p in packets.values() if p for r in p['rows']}
        return dict(rows=[decorate(r) for r in rows],removed=[decorate(r) for r in removed],
            tracking_quotes={c:q for c,q in known.items() if c in active_tracking},
            sources={k:dict(packet={x:v for x,v in p.items() if x not in ('rows','ranking')} if p else None,
                status=self.store.get('status:'+k)) for k,p in packets.items()},
            counts=dict(wind=len(packets['wind']['rows']) if packets['wind'] else 0,
                ten=len(packets['ten']['rows']) if packets['ten'] else 0,twenty=len(packets['twenty']['rows']) if packets['twenty'] else 0,
                automatic_union=len(source_codes),manual=sum(bool(r.get('manual')) for r in rows+removed),excluded=len(removed),shown=len(rows)),
            ten_day=dict(packets['ten']['ranking'],observed_at=packets['ten']['received_at']) if packets['ten'] and packets['ten']['day']==view['date'] else None,
            directory=self.store.get('directory:'+view['date']) or self.store.get('migrated'),
            quote_refresh=quotes.status(),market_day=view['date'],market_time=view['capturedAt'],
            basis='801225风向标 ∪ 10交易日前五 ∪ 20交易日前五 ∪ 人工添加 − 人工排除；区间榜含ST，复权沿用供应商口径')

    def close(self):
        self.stop.set();self.sources.shutdown(wait=True);self.worker.shutdown(wait=True)
