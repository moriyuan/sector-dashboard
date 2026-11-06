"""Point-in-time price/volume regressions. Returns and residual SD are in percentage points."""
import hashlib
import json
import os
from collections import deque
from pathlib import Path

import numpy as np

from .barra import STYLES, fit

VERSION = 'cn-price-volume-v2'
FACTORS = (*STYLES, 'Beta', '中期动量', '残差波动率', '多周期流动性')
WINDOW = 252
# Names also define the immutable, compressed daily input contract.
DTYPE = np.dtype([('code','<i4'),('status','i1'),('st','i1'),('industry','U5')] +
    [(k,'<f8') for k in ('open','high','low','close','preclose','volume','amount','turn','ret','total_cap','float_cap','pb')])


def atomic_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')))
    os.replace(tmp,path)


def risk_exposures(y, market, turnover):
    """All rows end at T-1. Pairwise-valid intercept OLS; omitted quotes stay missing."""
    if y.shape!=turnover.shape or len(y)!=WINDOW or market.shape!=(WINDOW,):
        raise ValueError('expected 252 aligned past sessions')
    valid=np.isfinite(y)&np.isfinite(market[:,None])
    n=valid.sum(axis=0)
    with np.errstate(invalid='ignore',divide='ignore'):
        a=np.where(valid,y,0)
        b=np.where(valid,market[:,None],0)
        sy=a.sum(axis=0); sx=b.sum(axis=0)
        yy=np.einsum('ij,ij->j',a,a)-sy**2/n
        xx=np.einsum('ij,ij->j',b,b)-sx**2/n
        xy=np.einsum('ij,ij->j',a,b)-sx*sy/n
        beta=np.where((n>=202)&(xx>1e-10),xy/xx,np.nan)
        resid=np.sqrt(np.maximum(yy-beta*xy,0)/(n-2))
        medium=y[-147:-21]
        # Missing returns inside the compounded period could hide a corporate action.
        momentum=np.expm1(np.log1p(medium/100).sum(axis=0))*100
        terms=[]
        for h in (20,60,252):
            t=turnover[-h:]
            count=np.isfinite(t).sum(axis=0)
            mean=np.nansum(t,axis=0)/count
            terms.append(np.where((count>=np.ceil(.8*h))&(mean>0),np.log(mean),np.nan))
    return np.column_stack((beta,momentum,resid,np.mean(terms,axis=0)))


def daily_data(path):
    with np.load(path,allow_pickle=False) as inp:
        data=inp['stocks'];metadata=json.loads(str(inp['meta']))
    if data.dtype!=DTYPE or metadata['day']!=path.stem or np.any(np.diff(data['code'])<=0):
        raise ValueError(f'invalid input partition {path.stem}')
    return data,metadata


def traded(s):
    return (s['status']==1)&(s['amount']>0)&(s['close']>0)&np.isfinite(s['ret'])&(s['ret']>-100)


def estimate_day(previous,today,industry_names,exposures):
    eligible=traded(previous)&(previous['st']==0)
    cur=today[traded(today)]
    _,left,right=np.intersect1d(previous['code'],cur['code'],return_indices=True,assume_unique=True)
    left_filter=(eligible & (previous['float_cap']>0) & (previous['pb']>0) &
                 (previous['turn']>0) & (previous['industry']!='') & np.isfinite(exposures).all(axis=1))
    left,right=left[left_filter[left]],right[left_filter[left]]
    s=previous[left]
    x=np.column_stack((np.log(s['float_cap']),1/s['pb'],np.log(s['turn']),-s['ret'],exposures[left]))
    rows=list(zip([industry_names[i] for i in s['industry']],s['float_cap'],cur['ret'][right],x))
    result=fit(rows,int(eligible.sum()),FACTORS)
    result['coverage_counts']=dict(eligible=int(eligible.sum()),history=int((eligible&np.isfinite(exposures).all(axis=1)).sum()),
        industry=int((eligible&(previous['industry']!='')).sum()),valuations=int((eligible&(previous['float_cap']>0)&(previous['pb']>0)).sum()))
    return result


def calculate(root, calendar, start, end, replace=False):
    root=Path(root);dates=[d for d in calendar if d<=end]
    first=next((i for i,d in enumerate(dates) if d>=start and (replace or not (root/'results'/f'{d}.json').exists())),None)
    if first is None:return
    history=deque(maxlen=WINDOW)
    previous=None
    prev_meta=None
    for i in range(max(0,first-WINDOW),len(dates)):
        day=dates[i];path=root/'inputs'/f'{day}.npz'
        if not path.exists():
            history.clear()
            if i>=first:raise ValueError(f'missing trading session {day}')
            continue
        stock,meta=daily_data(path); last=root/'results'/f'{day}.json'
        if i>=first and len(history)==WINDOW and (replace or not last.exists()):
            codes=previous['code']
            y=np.full((WINDOW,len(codes)),np.nan);turn=np.full_like(y,np.nan)
            for j,(day_codes,returns,turnovers,_,_) in enumerate(history):
                _,a,b=np.intersect1d(codes,day_codes,assume_unique=True,return_indices=True)
                y[j,a]=returns[b]
                turn[j,a]=turnovers[b]
            market=np.array([entry[3] for entry in history])
            result=estimate_day(previous,stock,prev_meta['industry_names'],risk_exposures(y,market,turn))
            result.update(day=day,previous=dates[i-1],version=VERSION,benchmark='沪深300价格指数',
                industry_date=prev_meta['industry_date'],window_start=dates[i-WINDOW],
                input_digest=hashlib.sha256((''.join(entry[-1] for entry in history)+hashlib.sha256(path.read_bytes()).hexdigest()).encode()).hexdigest())
            atomic_json(last,result)
        live=traded(stock)
        history.append((stock['code'][live],stock['ret'][live],np.where(stock['turn'][live]>0,stock['turn'][live],np.nan),
                        meta['benchmark_return_pct'],hashlib.sha256(path.read_bytes()).hexdigest()))
        previous,prev_meta=stock,meta
