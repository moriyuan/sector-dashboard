"""Independent full-pool display cache; inherited available() remains strict evidence."""
import copy
import json
import math
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime,date

from backend.store import TZ
from backend.sources.kaipanla import KaipanlaSource,source_date,code_value,number
from backend.sources.kpl_transport import KplTransport
from backend.sources.base import SourceError
from .core_quote_tracker import CoreQuoteTracker
from .tracking_pool import front_codes,tracking_codes
from .retention import cutoff_day


def clock():
    return datetime.now(TZ)


def phase(at):
    t=at.strftime('%H:%M')
    if at.weekday()>=5:return '休市'
    if t<'09:15':return '盘前'
    if t<'09:30':return '集合竞价'
    if t<'11:31':return '盘中'
    if t<'13:00':return '午休'
    if t<'15:01':return '盘中'
    return '收盘后'


class PriorityTransport(KplTransport):
    def __init__(self,gate,main_busy):
        super().__init__(timeout=3,interval=.25,retries=0)
        self.gate,self.main_busy=gate,main_busy

    def _throttle(self,host):
        while self.main_busy():
            self._sleep(.1)
        # Use the main transport's reservation lock/map; its sessions remain separate.
        if self.gate:
            self.gate._throttle(host)
        else:
            super()._throttle(host)
        self._remaining()


class BasicQuoteSource(KaipanlaSource):
    def verified_day(self,today,minimum):
        if getattr(self,'_verified_today',None)==today and minimum<=today:
            return today
        data=self._post('https://apphis.longhuvip.com/w1/api/index.php',
            {'c':'HisHomeDingPan','a':'ChangeStatistics','Index':0,'st':5})
        days=[r['Day'] for r in data['info']]
        if not days or days!=sorted(set(days),reverse=True):
            raise ValueError('交易日历缺失或顺序不符')
        for day in days:date.fromisoformat(day)
        self.allowed_days=set(days)
        latest=days[0]
        if latest<today:
            try:
                # Real-time emotion validates its own Day; history can lag during auction.
                self.fetch_emotion(today)
                latest=today;self.allowed_days.add(today)
            except SourceError:
                pass
        if not minimum<=latest<=today:
            raise ValueError('最新交易日尚未核验，保留原报价')
        if latest==today:self._verified_today=today
        return latest

    def basic_quote(self,code,day):
        p=self._app('StockL2Data','GetStockPanKou',StockID=code_value(code))
        raw_day=str(p['day'])
        actual=datetime.strptime(raw_day,'%Y%m%d').date().isoformat() if len(raw_day)==8 else date.fromisoformat(raw_day).isoformat()
        if actual not in getattr(self,'allowed_days',{day}):
            raise ValueError('报价交易日未经核验')
        day=actual
        if p.get('code')!=code:raise ValueError('返回证券代码不符')
        real=p['real'];stamp=None
        amount=None
        try:
            if real.get('total_turnover') is not None:
                candidate=number(real['total_turnover'])
                if candidate>=0:amount=candidate
        except (SourceError,ValueError,TypeError):
            pass  # An optional amount failure must not discard a valid basic quote.
        if real.get('time') not in (None,'',0,'0'):
            stamp=datetime.strptime(day+str(int(real['time'])).zfill(9),'%Y-%m-%d%H%M%S%f').replace(tzinfo=TZ).isoformat()
        return dict(code=code,name=str(p['name']),price=number(real['last_px']),
                    change=number(real['px_change_rate']),source_time=stamp,quote_day=day,
                    amount=amount,
                    time_basis='供应商盘口更新时间；不等同交易所成交时间',
                    quote_phase='集合竞价' if stamp and '09:15'<=stamp[11:16]<'09:30' else '盘口报价')


class FullPoolQuotes(CoreQuoteTracker):
    full_pool = True
    def __init__(self,path,registry,gate=None,main_busy=lambda:False,source=None):
        super().__init__(path)
        self.registry=registry;self.main_busy=main_busy
        self.source=source or BasicQuoteSource(timeout=3,transport=PriorityTransport(gate,main_busy))
        self.front=set();self.preferred=None
        self.stop=threading.Event();self.last_key=None;self.last_codes=set();self.current_future=None
        self.progress={'state':'idle','total':0,'completed':0,'updated':0,'reused':0,'failed':0,'requests':0}
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS quote_attempts(code TEXT PRIMARY KEY,at TEXT,day TEXT,state TEXT,error TEXT)')

    def schedule(self,*args):
        # Analysis reads the cache; it must not launch duplicate six-stock sampling.
        return

    def status(self):
        with self.lock:return copy.deepcopy(self.progress)

    def attempts(self):
        with closing(sqlite3.connect(self.path)) as db:
            return {c:dict(at=a,day=d,state=s,error=e) for c,a,d,s,e in db.execute('SELECT * FROM quote_attempts')}

    def prefer(self,code,view):
        if code not in tracking_codes(view,self.registry):raise ValueError('不在当前追踪池')
        with self.lock:self.preferred=code
        return {'code':code,'state':'queued','message':'下轮后台报价优先；当前显示缓存'}

    def tick(self,view,current=None):
        current=current or clock()
        with self.lock:self.front=front_codes(view)
        codes=tracking_codes(view,self.registry)
        period=phase(current)
        slot=current.isoformat()[:16] if period in ('盘中','集合竞价') else current.date().isoformat()+'/'+period
        key=(slot,view['date'])
        with self.lock:
            if self.running or self.stop.is_set() or self.main_busy():return False
            if key==self.last_key and codes<=self.last_codes:
                # Outside sessions, retry failures only every five minutes.
                if not (self.progress.get('failed') and time.monotonic()-self.last_attempt>=300):return False
            selected=codes if key!=self.last_key else codes-self.last_codes
            if not selected:selected=codes
            if not selected:return False
            self.last_codes=codes;self.last_key=key;self.last_attempt=time.monotonic();self.running=True
            self.progress=dict(state='running',total=len(selected),completed=0,updated=0,reused=0,failed=0,requests=0,
                               phase=period,started_at=current.isoformat(),day=None)
            self.current_future=self.runner.submit(self._full_round,view,selected,current)
            return True

    def _full_round(self,view,codes,current):
        started=time.monotonic();deadline=started+45
        try:
            self.source.transport.set_deadline(min(deadline,time.monotonic()+7))
            day=self.source.verified_day(current.date().isoformat(),view['date'])
            with self.lock:self.progress['day']=day
            # Reuse only independently time-stamped quotes within the same minute.
            known=self.last_known(current.isoformat())
            futures=[]
            ordered=sorted(codes,key=lambda c:(c!=self.preferred,known.get(c,{}).get('received_at',''),c))
            for code in ordered:
                q=known.get(code);origin=q.get('source_time') if q else None
                age=(current-datetime.fromisoformat(origin)).total_seconds() if origin else None
                if q and q.get('quote_day',origin[:10] if origin else None)==day and age is not None and 0<=age<45:
                    with self.lock:self.progress['reused']+=1;self.progress['completed']+=1
                else:
                    futures.append(self.workers.submit(self._one,code,day,deadline))
            for future in futures:future.result()
        except Exception as exc:
            with self.lock:
                self.progress['error']=str(exc)[:160]
                self.progress['failed']=self.progress['total']-self.progress['updated']-self.progress['reused']
            for code in codes:self._attempt(code,view['date'],'failed','交易日核验或本轮任务未完成：'+str(exc)[:120])
        finally:
            self.source.transport.set_deadline(None)
            with self.lock:
                self.running=False
                self.progress.update(state='partial' if self.progress['failed'] else 'complete',
                    elapsed_ms=round((time.monotonic()-started)*1000),finished_at=clock().isoformat())

    def _attempt(self,code,day,state,error=''):
        if 'ProxyError' in error or 'ConnectionError' in error:
            error='开盘啦连接暂时失败'
        elif 'Timeout' in error:
            error='开盘啦请求超时'
        with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
            db.execute('INSERT OR REPLACE INTO quote_attempts VALUES(?,?,?,?,?)',
                       (code,clock().isoformat(),day,state,error[:180]))

    def _one(self,code,day,deadline):
        try:
            if self.stop.is_set() or code not in ({r['code'] for r in self.registry.listing()} | self.front):
                return
            if time.monotonic()>=deadline:raise ValueError('本轮45秒预算用尽，下轮继续')
            # Wait for the market refresh without consuming the per-request timeout.
            while self.main_busy():
                if self.stop.wait(.1) or time.monotonic()>=deadline:raise ValueError('主行情优先，报价延后')
            self.source.transport.set_deadline(min(deadline,time.monotonic()+4))
            with self.lock:self.progress['requests']+=1
            quote=self.source.basic_quote(code,day);received=clock().isoformat(timespec='milliseconds')
            actual=quote.get('quote_day')
            if not actual or not cutoff_day(clock())<=actual<=clock().date().isoformat():
                raise ValueError('报价超出30日保留窗口')
            if quote['code']!=code or actual not in getattr(self.source,'allowed_days',{day}):
                raise ValueError('报价身份或日期不符')
            if not all(isinstance(quote[k],(int,float)) and math.isfinite(quote[k]) for k in ('price','change')) or quote['price']<=0:
                raise ValueError('接口未提供有效价格，保留旧报价')
            origin=quote.get('source_time')
            if origin and (origin[:10]!=actual or (datetime.fromisoformat(origin)-datetime.fromisoformat(received)).total_seconds()>2):
                raise ValueError('报价来源日期不符或时点超前')
            with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
                db.execute('INSERT OR IGNORE INTO core_quotes VALUES(?,?,?,?,?)',
                    (actual,code,received,origin,json.dumps(quote,ensure_ascii=False,allow_nan=False)))
            self._attempt(code,actual,'success')
            with self.lock:self.progress['updated']+=1
        except Exception as exc:
            self._attempt(code,day,'failed',str(exc))
            with self.lock:self.progress['failed']+=1
        finally:
            self.source.transport.set_deadline(None)
            with self.lock:self.progress['completed']+=1

    def last_known(self,cutoff):
        with closing(sqlite3.connect(self.path,timeout=5)) as db:
            rows=db.execute('''SELECT code,received_at,body FROM (
              SELECT code,received_at,body,ROW_NUMBER() OVER(PARTITION BY code ORDER BY day DESC,
              COALESCE(source_time,received_at) DESC,received_at DESC) n FROM core_quotes
              WHERE day>=? AND julianday(received_at)<=julianday(?) AND (source_time IS NULL OR julianday(source_time)<=julianday(?))) WHERE n=1''',(cutoff_day(clock()),cutoff,cutoff)).fetchall()
        return {c:{**json.loads(b),'received_at':a} for c,a,b in rows}

    def close(self):
        self.stop.set()
        super().close()
        self.source.close()
