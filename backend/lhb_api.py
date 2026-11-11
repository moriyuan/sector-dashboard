from datetime import date, datetime, time
import json
import math
from fastapi import APIRouter, HTTPException
from . import lhb
from .lhb_seats import annotate, stock_summary
from .gainer_news import gainers_snapshot, rank_news
from .move_news import related_moves
from .news import NewsStore
from .store import TZ
import re


def three_day_changes(store,day,cutoff,market,codes):
    boundary=min(cutoff,datetime.combine(date.fromisoformat(day),time(23,59,59),TZ)).astimezone(TZ).isoformat(timespec='seconds')
    calendar=sorted(set(store.observed('calendar',before=boundary) or []))
    days=[d for d in calendar if d<=day][-3:]
    if len(days)!=3 or days[-1]!=day:
        return {},[],[]
    quotes=[]; revisions=[]
    for d in days:
        if d==day:
            revision,_,rows=market
        else:
            with store.connect() as db:
                record=db.execute("SELECT id,payload FROM snapshots WHERE kind='gainers' AND mode='close' AND trade_date=? AND collected_at<=? ORDER BY id DESC LIMIT 1",(d,boundary)).fetchone()
            revision=record['id'] if record else None
            rows=json.loads(record['payload'])['rows'] if record else []
        revisions.append(dict(date=d,revision=revision))
        quotes.append({r['code']:r for r in rows})
    result={}
    for code in codes:
        ratios=[]
        for snapshot in quotes:
            row=snapshot.get(code,{})
            price,previous=row.get('price'),row.get('pre_close')
            if not all(isinstance(v,(float,int)) and math.isfinite(v) and v>0 for v in (price,previous)):
                break
            ratios.append(price/previous)
        if len(ratios)==3:
            result[code]=(math.prod(ratios)-1)*100
    return result,days,revisions


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
        result=annotate(lhb.view(store,day,cutoff),cutoff)
        result['rows']=[{k:v for k,v in row.items() if k not in ('buy','sell')} for row in result['rows']]
        now=cutoff or datetime.now(TZ)
        market=gainers_snapshot(store,day,now) if result['rows'] else (None,None,[])
        quotes={row['code']:row for row in market[2]}
        cumulative,window,revisions=three_day_changes(store,day,now,market,{r['code'] for r in result['rows'] if r['period']=='three'})
        for row in result['rows']:
            row['change']=quotes.get(row['code'],{}).get('change')
            row['three_day_change']=cumulative.get(row['code']) if row['period']=='three' else None
        result['seat_stocks']=stock_summary(result['rows'])
        result['three_day_window']=window
        result['three_day_revisions']=revisions
        result['gainers_revision']=market[0]
        result['quote_date']=day if market[0] is not None else None
        result['quote_source_time']=market[1]
        result['related_by_code']=related_moves(news,day,now,market,result['rows'],{r['code'] for r in result['rows']},preview=True)
        return result

    @api.get('/{code}')
    def stock(code: str,date: date|None=None,asof: datetime|None=None):
        if not re.fullmatch(r'[036]\d{5}',code):
            raise HTTPException(422,'请输入沪深 A 股代码')
        day,cutoff=inputs(date,asof)
        result=annotate(lhb.view(store,day,cutoff),cutoff)
        now=cutoff or datetime.now(TZ)
        market=gainers_snapshot(store,day,now)
        result['related']=related_moves(news,day,now,market,result['rows'],{code})[code]
        result['rows']=[r for r in result['rows'] if r['code']==code]
        label=result['rows'][0]['name'] if result['rows'] else None
        matches=rank_news(news,day,now,code=code,fallback_name=label,market=market)
        result.update(stock=matches['rows'][0],news_asof=matches['asof'],gainers_revision=matches['revision'])
        return result
    return api
