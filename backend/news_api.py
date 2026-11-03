"""Read-only news routes; all collection/model work lives in the worker."""
from datetime import date, datetime
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from .gainer_news import gainers_snapshot, rank_news
from .move_news import related_moves
from .store import TZ
from .news import NewsStore, normalize_time


def router(store):
    api = APIRouter(prefix='/api/news')
    news = NewsStore(store)

    @api.get('/sources')
    def sources():
        result = news.sources()
        with store.connect() as db:
            result['gaps'] = [dict(r) for r in db.execute('SELECT * FROM news_gaps ORDER BY id DESC LIMIT 20')]
        return result

    @api.get('')
    def listing(date: date | None = None, region: Literal['domestic','overseas'] | None = None,
                source: str | None = Query(None,max_length=30), theme: str | None = Query(None,max_length=30),
                stock: str | None = Query(None,pattern=r'^\d{6}$'), relation: Literal['direct','business','supply_chain'] | None = None,
                q: str = Query('',max_length=100), page: int = Query(1,ge=1), page_size: int = Query(24,ge=1,le=100),
                level: Literal['focus','brief'] | None = None, asof: datetime | None = None, window: Literal['24h','all'] = '24h'):
        return news.list_events(date=date.isoformat() if date else None,region=region,source=source,theme=theme,stock=stock,relation=relation,q=q,level=level,page=page,page_size=page_size,asof=normalize_time(asof) if asof else None,window=window)

    @api.get('/gainers')
    def gainer_news(date: date | None = None):
        now = datetime.now(TZ)
        day=(date or now.date()).isoformat()
        market=gainers_snapshot(store,day,now)
        result=rank_news(news,day,now,market=market)
        profiles=related_moves(news,day,now,market,[],{r['code'] for r in result['rows']})
        for row in result['rows']:row['attribution']=profiles[row['code']]
        return result

    @api.get('/{event_id}')
    def detail(event_id: str, asof: datetime | None = None):
        if len(event_id)!=24 or any(c not in '0123456789abcdef' for c in event_id):
            raise HTTPException(422,'事件编号无效')
        result = news.detail(event_id,asof=normalize_time(asof) if asof else None)
        if not result:raise HTTPException(404,'所选时点没有该事件')
        return result
    return api
