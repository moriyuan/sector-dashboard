import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .store import Store, TZ, RULE_VERSION
from .service import Collector, metadata
from .schedule import phase
from .taxonomy import sector_kind, vendor_name

ROOT = Path(__file__).resolve().parents[1]


def create_app(store=None, collect=True):
    store = store or Store(os.getenv('SECTOR_DB',str(ROOT/'runtime/market.sqlite3')))
    collector = Collector(store)

    @asynccontextmanager
    async def lifespan(app):
        if collect:
            collector.start()
        yield
        collector.shutdown()

    app = FastAPI(title='板块研究台',lifespan=lifespan)
    app.state.store = store
    app.state.collector = collector
    from .news_api import router as news_router
    app.include_router(news_router(store))
    from .lhb_api import router as lhb_router
    app.include_router(lhb_router(store))

    from .etf_tracker import router as etf_router
    app.include_router(etf_router(store))

    from .backlog import router as backlog_router
    app.include_router(backlog_router(store))

    def date_value(value):
        if value:
            try:
                return datetime.strptime(value,'%Y-%m-%d').date().isoformat()
            except ValueError:
                raise HTTPException(422,'日期格式应为 YYYY-MM-DD')
        dates = [d for d in store.dates() if d['mode']=='close']
        return dates[0]['trade_date'] if dates else datetime.now(TZ).date().isoformat()

    @app.get('/api/dates')
    def dates():
        calendar = store.observed('calendar') or []
        observations = [d for d in store.dates() if d['mode']=='close']
        bars_dates = set()
        for kind in ('industry','concept'):
            for row in store.observed('catalog:'+sector_kind(kind)) or []:
                bars_dates.update(b['date'] for b in store.observed('bars:'+row['code']) or [])
        return {'snapshots':observations,'history_dates':sorted(bars_dates,reverse=True)[0:250],'calendar':calendar[-300:],'rule_version':RULE_VERSION}

    @app.get('/api/status')
    def status():
        now = datetime.now(TZ)
        events = store.events()
        current_phase = phase(now,store.observed('calendar') or [])
        for e in events:
            # A dated close archive does not become stale every two quote cycles.
            e['stale'] = False
        return {'phase':current_phase,'server_time':now.isoformat(),'collecting':bool(collector.thread and collector.thread.is_alive()),'events':events,
                'mode':'post_close','archive_times':['15:30','16:30'],'retry_interval':300,'rule_version':RULE_VERSION,'public_sources_only':True}

    @app.get('/api/market')
    def market(date: str|None=None, mode:Literal['close']='close'):
        date = date_value(date)
        snap = store.snapshot('market',date,mode)
        pools = store.snapshot('pools',date,mode)
        indices = store.snapshot('indices',date,mode)
        from .sentiment import daily
        return {'meta':metadata(snap,date,mode),'stats':snap['payload']['stats'] if snap else None,'indices':indices['payload']['rows'] if indices else [],
                'indices_meta':metadata(indices,date,mode),
                'emotion':pools['payload']['stats'] if pools else None,'emotion_meta':metadata(pools,date,mode),
                'sentiment':daily(store,date)}

    @app.get('/api/sentiment')
    def sentiment_curve(limit:int=Query(60,ge=1,le=500)):
        from .sentiment import history
        return {'rows':history(store,limit),'note':'自研近似算法（参考开盘啦五维度），非开盘啦官方数值'}

    @app.get('/api/boards-history')
    def boards_history(limit:int=Query(60,ge=1,le=250)):
        from .boards import history
        return {'rows':history(store,limit),'note':'每日涨停池最高连板及股票名单'}

    @app.get('/api/sectors')
    def sectors(kind:Literal['industry','concept']='industry',date:str|None=None,mode:Literal['close']='close',revision:int|None=None,
                concept_source:Literal['ths','eastmoney']='ths'):
        date = date_value(date)
        key=sector_kind(kind,concept_source)
        snap = store.snapshot(key,date,mode,revision)
        if snap:
            return {'meta':metadata(snap,date,mode),**snap['payload'],'taxonomy':concept_source if kind=='concept' else 'eastmoney'}
        if revision is not None:
            raise HTTPException(404,'该修订不属于所选日期、模式和分类')
        # Index-only history does not fabricate historical membership or sentiment.
        rows, histories = [], {}
        if mode=='close':
            for item in store.observed('catalog:'+key) or []:
                bars = store.observed('bars:'+item['code']) or []
                bar = next((b for b in bars if b['date']==date),None)
                if bar and date < datetime.now(TZ).date().isoformat():
                    rows.append({'code':item['code'],'name':item['name'],'price':bar['close'],'change':bar['change'],'amount':bar['amount'],'up_ratio':None,'coverage':None,'flow':None,'amount_share':None})
                    histories[item['code']] = bars
        from .analytics import analyze
        return {'meta':{**metadata(None,date,mode),'status':'index_only' if rows else 'missing','source':vendor_name(kind,concept_source)},
                'rows':analyze(rows,histories,[],date,mode,store.observed('calendar') or [],
                               universe_codes=[r['code'] for r in store.observed('catalog:'+key) or []]),
                'taxonomy':concept_source if kind=='concept' else 'eastmoney',
                'scope':'仅'+vendor_name(kind,concept_source)+'指数历史；目录为当前目录，历史成分、资金与情绪未存档'}

    @app.get('/api/gainers')
    def gainers(date:str|None=None,revision:int|None=None,limit:int=Query(100,ge=1,le=500),
                include_st:bool=False,sector:str|None=None,q:str=''):
        day=date_value(date)
        snap=store.snapshot('gainers',day,'close',revision)
        if revision is not None and not snap:raise HTTPException(404,'该修订不属于所选日期的个股榜')
        rows=snap['payload']['rows'] if snap else []
        rows=[r for r in rows if (include_st or not r['is_st']) and (not q or q in r['name'] or q in r['code'])
              and (not sector or any(b['code']==sector for b in r['industry']+r['concepts']))]
        return {'meta':metadata(snap,day,'close'),'rows':rows[:limit],'total':len(rows),
                'concept_source':snap['payload'].get('concept_source','eastmoney') if snap else 'ths',
                'membership_counts':snap['payload'].get('membership_counts') if snap else None,
                'input_refs':snap['payload'].get('input_refs') if snap else None}

    @app.get('/api/sectors/{code}')
    def detail(code:str,kind:Literal['industry','concept']='industry',date:str|None=None,mode:Literal['close']='close',revision:int|None=None,
               concept_source:Literal['ths','eastmoney']='ths'):
        if not re.fullmatch(r'(?:BK\d{4,8}|THS\d{6})',code):
            raise HTTPException(422,'板块代码无效')
        date = date_value(date)
        listing = sectors(kind,date,mode,revision,concept_source)
        row = next((r for r in listing['rows'] if r['code']==code),None)
        if not row:
            raise HTTPException(404,'该板块在所选日期没有记录')
        cutoff = listing['meta']['collected_at']
        refs = listing.get('input_refs')
        bars = store.observed('bars:'+code,cutoff) or []
        members = store.observed('members:'+code+':'+date,cutoff) if cutoff else None
        stocks = store.snapshot('stocks',date,mode)
        # Freeze component snapshots to the board revision's collection time.
        if stocks and cutoff:
            with store.connect() as db:
                rev = db.execute('SELECT id FROM snapshots WHERE kind=? AND trade_date=? AND mode=? AND collected_at<=? ORDER BY id DESC LIMIT 1',('stocks',date,mode,cutoff)).fetchone()
            stocks = store.snapshot('stocks',date,mode,rev['id']) if rev else None
        elif not cutoff:
            stocks = None
        if refs is not None:
            bar_record = store.observation_record(revision=refs['bars'].get(code)) if refs['bars'].get(code) else None
            member_record = store.observation_record(revision=refs['members'].get(code)) if refs['members'].get(code) else None
            bars = bar_record['payload'] if bar_record else []
            members = member_record['payload'] if member_record else None
            stocks = store.snapshot('stocks',date,mode,refs['stocks']) if refs.get('stocks') else None
        member_meta_id=(refs or {}).get('members_meta',{}).get(code)
        member_meta=store.observation_record(revision=member_meta_id) if member_meta_id else None
        if row.get('membership_verified') is False or (member_meta and not member_meta['payload'].get('verified')):
            members=None
        member_rows = [r for r in stocks['payload']['rows'] if r['code'] in set(members or [])] if stocks else []
        pools = store.snapshot('pools',date,mode)
        if pools and cutoff:
            with store.connect() as db:
                p_rev=db.execute('SELECT id FROM snapshots WHERE kind=? AND trade_date=? AND mode=? AND collected_at<=? ORDER BY id DESC LIMIT 1',('pools',date,mode,cutoff)).fetchone()
            pools=store.snapshot('pools',date,mode,p_rev['id']) if p_rev else None
        elif not cutoff:
            pools=None
        if refs is not None:
            pools=store.snapshot('pools',date,mode,refs['pools']) if refs.get('pools') else None
        limit_rows = pools['payload']['pools'].get('up') if pools else None
        boards = {r['code']:r.get('boards') for r in limit_rows or []}
        for r in member_rows:
            r['boards'] = boards.get(r['code'])
        flow_history=[]
        for day in store.history('flow_'+sector_kind(kind,concept_source),date,listing['meta']['revision']):
            flow_row=day['rows'].get(code)
            if flow_row:
                flow_history.append({'date':day['date'],**flow_row})
        if cutoff:
            flow_history=[f for f in flow_history if f['date']<date]
            if row.get('flow'):
                flow_history.append({'date':date,**row['flow']})
        else:
            flow_history=[]
        return {'meta':listing['meta'],'components':listing.get('components'),'membership_meta':member_meta['payload'] if member_meta else None,'sector':row,'bars':[b for b in bars if b['date']<=date][-250:],
                'members':member_rows,'members_available':members is not None,'flow_history':flow_history[-60:],
                'limit_up':[r for r in limit_rows or [] if r['code'] in set(members or [])] if members is not None and limit_rows is not None else None}

    dist = ROOT/'frontend/dist'
    if dist.exists():
        app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
        @app.get('/')
        def index():
            return FileResponse(dist/'index.html', headers={'Cache-Control':'no-cache'})
    return app


app = create_app(collect=os.getenv('SECTOR_DISABLE_COLLECTOR')!='1')
