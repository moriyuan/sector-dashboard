"""Causal minute-bar rules for an upward index turn and stock diffusion."""
from datetime import date
from functools import lru_cache
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
import numpy as np

VERSION='index-resonance-v1'
BENCHMARKS={'sh000001':('上证指数','sh000001'),'sz399006':('创业板指','sz399006'),'sh000680':('科创综指','sh000680')}
CLOCK=[f'{m//60:02d}:{m%60:02d}' for a,b in [(570,690),(780,900)] for m in range(a,b+1)]
MINUTE={s.replace(':',''):i for i,s in enumerate(CLOCK)}
SESSIONS=[(0,121),(121,242)]


def parse_day(record):
    """Ignore extended-session records. Missing prices and discontinuous amount deltas remain missing."""
    prices=np.full(242,np.nan);amounts=prices.copy()
    reference=float(record['prec'])
    if not np.isfinite(reference) or reference<=0:raise ValueError('invalid previous close')
    seen=set()
    for line in record['data']:
        fields=line.split()
        if len(fields)<4 or fields[0] not in MINUTE:continue
        pos=MINUTE[fields[0]]
        if pos in seen:raise ValueError('duplicate minute')
        seen.add(pos)
        price,amount=float(fields[1]),float(fields[3])
        if np.isfinite(price) and price>0:prices[pos]=price
        if np.isfinite(amount) and amount>=0:amounts[pos]=amount
    return prices,amounts,reference


def turns(px):
    events=[]
    for a,b in SESSIONS:
        next_min=a+8
        for t in range(a+8,b):
            if t<next_min:continue
            window=px[max(a,t-12):t+1]
            if not np.isfinite(window).all():continue
            low=max(a,t-12)+int(np.where(window==window.min())[0][-1])
            if not a+2<=low<=t-2:continue
            before=px[max(a,low-10):low]
            if not np.isfinite(before).all():continue
            if (px[low]/before.max()-1)*100>-.18:continue
            if (px[t]/px[low]-1)*100<.12 or px[t]<=px[t-3:t].max():continue
            events.append(dict(pivot=low,confirm=t));next_min=t+20
    return events


def pullback(px,pivot,confirm,end):
    """Return first qualifying pullback and a subsequent reclaim, with their observation times."""
    peak=confirm;start=None
    for i in range(confirm+1,end+1):
        if not np.isfinite(px[i]):return dict(status='分钟缺口',peak=peak,pull=start,again=None,stop=i)
        if px[i]<px[pivot]:return dict(status='跌破转折低点',peak=peak,pull=start,again=None,stop=i)
        if start is None:
            if px[i]>=px[peak]:peak=i
            elif (1-px[i]/px[peak])*100>=.08 and px[peak]-px[i]>=.3*(px[peak]-px[pivot]):
                start=i
        elif px[i]>px[peak]:
            return dict(status='已再次突破',peak=peak,pull=start,again=i)
    return dict(status='回踩待再攻' if start is not None else '未出现合格回踩',peak=peak,pull=start,again=None)


def held(price,launch,level,pb,end):
    if pb['pull'] is None or launch>pb['peak']:return '未验证'
    stop=pb['again'] if pb['again'] is not None else end
    w=price[pb['peak']:stop+1]
    if np.any(w<level):return '跌破突破位'
    if not np.isfinite(w).all():return '分钟缺口'
    return '守住' if pb['again'] is not None else '待再攻确认'


def lag_return(prices,n):
    ret=np.full_like(prices,np.nan)
    for a,b in SESSIONS:
        ret[...,a+n:b]=(prices[...,a+n:b]/prices[...,a:b-n]-1)*100
    return ret


def high5(prices):
    out=np.full_like(prices,np.nan)
    for a,b in SESSIONS:
        past=np.lib.stride_tricks.sliding_window_view(prices[...,a:b-1],5,axis=-1)
        out[...,a+5:b]=past.max(axis=-1)
    return out


def series_sector(px):
    """Chain same-stock adjacent-minute returns, using the start-of-day roster as denominator."""
    adjacent=lag_return(px,1)
    path=np.full(242,np.nan)
    ok=np.isfinite(adjacent)
    count=ok.sum(axis=0);g=np.divide(np.nansum(adjacent,axis=0),count,out=np.full(242,np.nan),where=count>=max(5,.7*len(px)))
    # Within a session, a missing minute breaks the chain; rebasing a new segment avoids a synthetic jump.
    for a,b in SESSIONS:
        path[a]=100 if np.isfinite(px[:,a]).sum()>=max(5,.7*len(px)) else np.nan
        for j in range(a+1,b):
            if np.isfinite(g[j]):path[j]=(path[j-1] if np.isfinite(path[j-1]) else 100)*(1+g[j]/100)
    return path


def stock_hits(px,amounts,sector_r,idx_r):
    ret=lag_return(px,3);level=high5(px)
    traded=np.zeros(px.shape,dtype=bool)
    for a,b in SESSIONS:
        traded[:,a+3:b]=(amounts[:,a+3:b]-amounts[:,a:b-3])>0
    return (px>level)&(ret>=.45)&(ret-sector_r>=.15)&(ret-idx_r>=.10)&traded,level,ret


def candidates(px,hits,levels,ret,codes,names,board_start,pivot,confirm,pb,end):
    session=0 if pivot<121 else 121
    first=max(session,board_start-5,pivot-10)
    rows=[]
    for i in np.flatnonzero(hits[:,first:end+1].any(axis=1)):
        launch=first+int(np.flatnonzero(hits[i,first:end+1])[0])
        rows.append(dict(code=codes[i],name=names[i],at=launch,level=round(float(levels[i,launch]),4),
            gain=round(float(ret[i,launch]),3),hold=held(px[i],launch,levels[i,launch],pb,end)))
    rows.sort(key=lambda r:(r['at'],r['code']))
    # If no observed stock started by the sector signal, the leader is unknown.
    if not rows:return rows
    leader_known=rows[0]['at']<=board_start
    pos=0
    while pos<len(rows):
        last=pos+1
        while last<len(rows) and rows[last]['at']==rows[pos]['at']:last+=1
        for r in rows[pos:last]:
            r.update(rank_from=pos+1,rank_to=last,leader_known=leader_known)
        pos=last
    return rows


def analyze(index,prices,amounts,codes,names,groups):
    idx_r=lag_return(index,3)
    events=[]
    indices={code:i for i,code in enumerate(codes)}
    for event in turns(index):
        pivot,confirm=event['pivot'],event['confirm']
        end=min(120 if pivot<121 else 241,confirm+30)
        pb=pullback(index,pivot,confirm,end)
        end=pb['again'] if pb['again'] is not None else pb.get('stop',end)
        event.update(end=end,pullback=pb,boards=[])
        events.append(event)
    tracks={}
    for group in groups:
        ids=[indices[c] for c in group['members'] if c in indices]
        if len(ids)<5:continue
        # prices includes all eligible members, including all-NaN rows when retrieval failed.
        px=prices[ids];path=series_sector(px);sector_r=lag_return(path,3)
        r=lag_return(px,3);ok=np.isfinite(r);count=ok.sum(axis=0)
        breadth=np.divide((r>0).sum(axis=0),count,out=np.zeros(242),where=count>0)
        trigger=(path>high5(path))&(sector_r>=.2)&(sector_r-idx_r>=.10)&(breadth>=.55)&(count>=max(5,.7*len(ids)))
        hits,levels,stock_r=stock_hits(px,amounts[ids],sector_r,idx_r)
        for event in events:
            pivot,confirm=event['pivot'],event['confirm']
            lo=max(0 if pivot<121 else 121,pivot-8)
            found=np.flatnonzero(trigger[lo:confirm+1])
            if not len(found):continue
            # A sector breakout alone does not establish a response to the benchmark bounce.
            pair=np.isfinite(px[:,pivot]) & np.isfinite(px[:,confirm])
            response_up=int((px[pair,confirm]>px[pair,pivot]).sum())
            response=(path[confirm]/path[pivot]-1)*100
            if (pair.sum()<max(5,.7*len(ids)) or not np.isfinite(path[pivot:confirm+1]).all()
                    or response<.04 or response_up<pair.sum()*.5):
                continue
            at=lo+int(found[0])
            members=candidates(px,hits,levels,stock_r,[codes[i] for i in ids],[names[i] for i in ids],
                               at,pivot,confirm,event['pullback'],event['end'])
            event['boards'].append(dict(code=group['code'],name=group['name'],kind=group['kind'],at=at,
                lead=pivot-at,response=round(float(response),3),response_up=response_up,response_n=int(pair.sum()),gain=round(float(sector_r[at]),3),excess=round(float(sector_r[at]-idx_r[at]),3),
                up=int((r[:,at]>0).sum()),valid=int(count[at]),eligible=len(ids),members=members))
            tracks[group['code']]=path
    for e in events:e['boards'].sort(key=lambda b:(b['at'],-b['excess'],b['code']))
    return events,tracks


@lru_cache(maxsize=4)
def saved_json(path,mtime):
    return json.loads(path.read_text())


@lru_cache(maxsize=1)
def price_cache(path,mtime):
    with np.load(path,allow_pickle=False) as src:
        return {str(c):i for i,c in enumerate(src['codes'])},src['prices']


def router(store):
    api=APIRouter()
    root=store.path.parent/'resonance'/'days'
    @api.get('/api/resonance')
    def replay(day:date|None=None, benchmark:Literal['sh000001','sz399006','sh000680']='sh000001',turn:int|None=None,
               board:str|None=None,stock:str|None=None):
        dates=sorted((d.name for d in root.glob('????-??-??') if (d/'summary.json').is_file()),reverse=True)
        selected=day.isoformat() if day else next(iter(dates),None)
        base=dict(day=selected,dates=dates,benchmark=benchmark,clock=CLOCK,events=[],version=VERSION)
        if selected not in dates:return dict(base,reason='该日分钟回放尚未归档')
        folder=root/selected
        p=folder/(benchmark+'.json')
        if not p.is_file() or not (folder/(benchmark+'-tracks.npz')).is_file():
            return dict(base,reason='该指数当日分钟回放尚未归档')
        events=saved_json(p,p.stat().st_mtime_ns)
        base['summary']=json.loads((folder/'summary.json').read_text())
        base['events']=[dict(id=i,pivot=e['pivot'],confirm=e['confirm'],count=len(e['boards']),
                             leading=sum(b['lead']>0 for b in e['boards'])) for i,e in enumerate(events)]
        if not events:return dict(base,reason='当日未检测到符合规则的指数向上转折')
        if turn is None:turn=next((e['id'] for e in base['events'] if e['leading']),0)
        if not 0<=turn<len(events):raise HTTPException(404,'转折不存在')
        event=events[turn]
        base['selected']=turn
        base['wave']={k:v for k,v in event.items() if k!='boards'}
        base['boards']=[{**{k:v for k,v in b.items() if k!='members'},'stocks':len(b['members']),
                         'held':sum(r['hold']=='守住' for r in b['members'])} for b in event['boards']]
        active=next((b for b in event['boards'] if b['code']==board),None) if board else next(iter(event['boards']),None)
        if board and not active:raise HTTPException(404,'该波段未匹配此板块')
        a=max(0 if event['pivot']<121 else 121,event['pivot']-12);b=event['end']+1
        def track(array):
            x=array[a:b]
            if not np.isfinite(x[0]) or x[0]<=0:return [None]*len(x)
            return [round(float((v/x[0]-1)*100),4) if np.isfinite(v) else None for v in x]
        with np.load(folder/(benchmark+'-tracks.npz'),allow_pickle=False) as src:
            chart=dict(start=a,index=track(src['index']))
            if active:
                chart['sector']=track(src['paths'][src['keys'].tolist().index(active['code'])])
        if active:
            base['board']=active['code'];base['members']=active['members']
            chosen=next((r for r in active['members'] if r['code']==stock),None) if stock else next(iter(active['members']),None)
            if stock and chosen is None:raise HTTPException(404,'该波段未匹配此股票')
            if chosen:
                pp=folder/'minutes.npz';codes,prices=price_cache(pp,pp.stat().st_mtime_ns)
                chart['stock']=track(prices[codes[chosen['code']]]);base['stock']=chosen['code']
        return dict(base,chart=chart)
    return api
