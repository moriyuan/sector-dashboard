"""Normalize input files and materialize a separate version of daily factor estimates."""
import argparse
from bisect import bisect_right
from datetime import date
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.quant_price import DTYPE, VERSION, atomic_json, calculate


def digest(path):
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


@lru_cache(maxsize=4)
def industry_map(path):
    frame=pd.read_parquet(path)
    good=frame.loc[frame.industry.fillna('').str.match(r'^[A-Z]\d{2}')]
    if good.code.duplicated().any() or set(good.industryClassification)!={'证监会行业分类'}:
        raise ValueError(f'invalid industry evidence {path.name}')
    labels=dict(zip(good.industry.str[:3],good.industry))
    if good.industry.nunique()!=len(labels):raise ValueError('industry code/name conflict')
    return good.set_index('code').industry.str[:3],labels,digest(path)


def numeric(series):
    return pd.to_numeric(series,errors='coerce').astype(float).replace([np.inf,-np.inf],np.nan)


def normalize(raw, valuation, industry, day, benchmark, path, industry_day):
    frame=pd.read_parquet(raw)
    if set(frame.date)!={day}:raise ValueError(f'wrong date {raw}')
    frame=frame[frame.code.str.fullmatch(r'(?:sh\.(?:60|68)|sz\.(?:00|30))\d{4}')].sort_values('code')
    if len(frame)<4000 or frame.code.duplicated().any():raise ValueError(f'incomplete stocks {day}')
    records=np.empty(len(frame),dtype=DTYPE)
    records['code']=frame.code.str[3:].astype(int)
    records['status']=numeric(frame.tradestatus).fillna(-1)
    records['st']=numeric(frame.isST).fillna(-1)
    for name in ('open','high','low','close','preclose','volume','amount','turn','ret'):
        records[name]=numeric(frame['pctChg' if name=='ret' else name])
    ok=(records['status']==1)&(records['preclose']>0)&(records['volume']>0)
    diff=np.abs((records['close'][ok]/records['preclose'][ok]-1)*100-records['ret'][ok])
    if len(diff)<3000 or np.nanquantile(diff,.99)>.02:raise ValueError(f'return basis conflict {day}')
    for name in ('total_cap','float_cap','pb'):records[name]=np.nan
    records['industry']=''
    meta=dict(day=day,benchmark_return_pct=float(benchmark),industry_names={},industry_date=None,
              raw_bar_sha256=digest(raw),count=len(frame))
    if valuation:
        v=pd.read_parquet(valuation).set_index('SECURITY_CODE')
        if v.index.has_duplicates or not v.TRADE_DATE.str.startswith(day).all():raise ValueError('valuation scope')
        v=v.reindex(frame.code.str[3:].to_numpy())
        prices=numeric(v.CLOSE_PRICE).to_numpy()
        same=np.isclose(prices,records['close'],atol=.00501,rtol=0)
        meta['valuation_matched']=int((same&ok).sum())
        if meta['valuation_matched']<ok.sum()*.9:raise ValueError(f'valuation coverage {day}')
        for name,col in [('total_cap','TOTAL_MARKET_CAP'),('float_cap','NOTLIMITED_MARKETCAP_A'),('pb','PB_MRQ')]:
            records[name]=np.where(same,numeric(v[col]),np.nan)
        meta['valuation_sha256']=digest(valuation)
    if industry:
        mapping,labels,ind_hash=industry_map(industry)
        records['industry']=frame.code.map(mapping).fillna('')
        meta.update(industry_names=labels,industry_date=industry_day,industry_sha256=ind_hash)
    records.sort(order='code')
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp')
    with tmp.open('wb') as out:
        np.savez_compressed(out,stocks=records,meta=np.array(json.dumps(meta,ensure_ascii=False,allow_nan=False)))
    os.replace(tmp,path)
    return meta


def build(source,output,start,end,replace=False,only_inputs=False):
    bench=pd.read_parquet(source/'benchmark.parquet').set_index('date')
    if bench.index.has_duplicates or not bench.index.is_monotonic_increasing:raise ValueError('benchmark chronology')
    if not np.allclose((numeric(bench.close)/numeric(bench.preclose)-1)*100,numeric(bench.pctChg),atol=.015):
        raise ValueError('benchmark return mismatch')
    industry={}
    for file in sorted((source/'industry').glob('*.parquet')):
        if file.stem>end:continue
        update=pd.read_parquet(file,columns=['updateDate']).updateDate.unique()
        if len(update)!=1 or update[0]>file.stem:raise ValueError('industry vintage is invalid')
        industry.setdefault(update[0],file)
    vintages=sorted(industry)
    days=[d for d in bench.index if d<=end]
    first=next(i for i,d in enumerate(days) if d>=start)
    for day in days[max(0,first-252):]:
        path=output/'inputs'/f'{day}.npz'
        if path.exists() and not replace:continue
        raw=source/'bars'/f'{day}.parquet'
        value=source/'values'/f'{day}.parquet'
        pos=bisect_right(vintages,day)-1
        vintage=vintages[pos] if pos>=0 else None
        if day>=days[max(0,first-1)]:
            if not value.exists():raise ValueError(f'missing valuation {day}')
            if vintage is None or (date.fromisoformat(day)-date.fromisoformat(vintage)).days>15:
                raise ValueError(f'missing historical industry {day}')
        normalize(raw,value if value.exists() else None,industry.get(vintage),day,bench.loc[day,'pctChg'],path,vintage)
    if not only_inputs:calculate(output,days,start,end,replace)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--replace',action='store_true');p.add_argument('--only-inputs',action='store_true')
    args=p.parse_args()
    build(args.root,args.output,args.start,args.end,args.replace,args.only_inputs)
