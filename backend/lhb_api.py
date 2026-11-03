from datetime import date, datetime
from fastapi import APIRouter, HTTPException
from . import lhb
from .gainer_news import gainers_snapshot, rank_news
from .move_news import related_moves
from .news import NewsStore
from .store import TZ
import re


def router(store):
    api=APIRouter(prefix='/api/lhb')
    news=NewsStore(store)

    def inputs(day,asof):
        now=datetime.now(TZ)
        if asof and asof.tzinfo is None:
            raise HTTPException(422,'截止时间需包含时区')
        return (day or now.date()).isoformat(),min(asof,now) if asof else None

    @api.get('')
    def listing(date: date|None=None, asof: datetime|None=None):
        day,cutoff=inputs(date,asof)
        result=lhb.view(store,day,cutoff)
        result['rows']=[{k:v for k,v in row.items() if k not in ('buy','sell')} for row in result['rows']]
        now=cutoff or datetime.now(TZ)
        market=gainers_snapshot(store,day,now) if result['rows'] else (None,None,[])
        result['gainers_revision']=market[0]
        result['related_by_code']=related_moves(news,day,now,market,result['rows'],{r['code'] for r in result['rows']},preview=True)
        return result

    @api.get('/{code}')
    def stock(code: str,date: date|None=None,asof: datetime|None=None):
        if not re.fullmatch(r'[036]\d{5}',code):
            raise HTTPException(422,'请输入沪深 A 股代码')
        day,cutoff=inputs(date,asof)
        result=lhb.view(store,day,cutoff)
        now=cutoff or datetime.now(TZ)
        market=gainers_snapshot(store,day,now)
        result['related']=related_moves(news,day,now,market,result['rows'],{code})[code]
        result['rows']=[r for r in result['rows'] if r['code']==code]
        label=result['rows'][0]['name'] if result['rows'] else None
        matches=rank_news(news,day,now,code=code,fallback_name=label,market=market)
        result.update(stock=matches['rows'][0],news_asof=matches['asof'],gainers_revision=matches['revision'])
        return result
    return api
