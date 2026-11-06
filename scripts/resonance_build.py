"""Build immutable daily evidence and replays. Membership and eligibility come from the previous close."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import warnings

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.quant_price import daily_data
from backend.resonance import BENCHMARKS,CLOCK,VERSION,analyze,parse_day


def read_capture(path,day):
    with gzip.open(path,'rt') as f:obj=json.load(f)
    matches=[r for r in obj['days'] if r['date']==day.replace('-','')]
    if len(matches)!=1 or obj['symbol']!=path.name.split('.')[0]:raise ValueError('symbol/date not found')
    px,amt,ref=parse_day(matches[0])
    return px,amt,ref,obj['name'],obj['captured']


def dump(obj,path):
    path.write_text(json.dumps(obj,ensure_ascii=False,allow_nan=False,separators=(',',':')))


def write_replay(tmp,tag,index,ref,captured,day,prices,amounts,codes,names,scanned):
    events,tracks=analyze(index,prices,amounts,codes,names,scanned)
    dump(events,tmp/(tag+'.json'))
    keys=sorted(tracks)
    np.savez_compressed(tmp/(tag+'-tracks.npz'),keys=np.asarray(keys,dtype='U12'),
                        paths=np.stack([tracks[k] for k in keys]) if keys else np.empty((0,242)),index=index,ref=ref,symbol=BENCHMARKS[tag][1],captured=captured)
    print(day,tag,'turns',len(events),'board hits',sum(len(e['boards']) for e in events),flush=True)


def build(root,quant,raw_folder,day,universe):
    days=root/'days';days.mkdir(exist_ok=True,parents=True);dest=days/day
    if dest.exists():return
    if universe['day']!=day or not universe['prior']<day:raise ValueError('wrong universe date')
    prior=universe['prior'];src=quant/'inputs'/f'{prior}.npz'
    stocks,meta=daily_data(src)
    stocks=stocks[(stocks['st']==0)&(stocks['status']==1)&(stocks['amount']>0)&(stocks['close']>0)]
    codes=[f'{n:06d}' for n in stocks['code']];names=codes.copy();n=len(codes)
    prices=np.full((n,242),np.nan);amounts=prices.copy();refs=np.full(n,np.nan)
    indices={};captures_by_tag={};captures=[];errors=0
    for tag,(_,symbol) in BENCHMARKS.items():
        px,_,ref,_,captured=read_capture(raw_folder/(symbol+'.json.gz'),day)
        if not np.isfinite(px[-1]) or np.isfinite(px).sum()<239:raise ValueError('index session missing closing minute')
        indices[tag]=(px,ref);captures_by_tag[tag]=captured;captures.append(captured)
    for i,code in enumerate(codes):
        symbol=('sh' if code.startswith(('60','68')) else 'sz')+code
        try:
            px,amt,ref,name,captured=read_capture(raw_folder/(symbol+'.json.gz'),day)
            prices[i]=np.where(amt>0,px,np.nan);amounts[i]=amt;refs[i]=ref;names[i]=name or code
            captures.append(captured)
        except (KeyError,ValueError,OSError,TypeError):errors+=1
    valid=int(np.isfinite(prices).any(axis=1).sum())
    if valid<n*.85:raise ValueError(f'minute retrieval coverage {valid}/{n} too low')
    groups=universe['boards']
    if not any(g['kind']=='行业' for g in groups):
        raise ValueError('missing verified industry universe')
    allowed=set(codes)
    scanned=[g for g in groups if len(allowed.intersection(g['members']))>=5]
    summary=dict(day=day,prior=prior,version=VERSION,universe=n,observed=valid,failed=errors,
        industry=sum(g['kind']=='行业' for g in scanned),concept=sum(g['kind']=='同花顺概念' for g in scanned),
        captures=[min(captures),max(captures)],snapshots=universe['snapshots'],quant_sha256=hashlib.sha256(src.read_bytes()).hexdigest())
    tmp=Path(tempfile.mkdtemp(prefix='.stage-',dir=days))
    try:
        for tag,(index,ref) in indices.items():
            write_replay(tmp,tag,index,ref,captures_by_tag[tag],day,prices,amounts,codes,names,scanned)
        np.savez_compressed(tmp/'minutes.npz',codes=np.array(codes),names=np.array(names),prices=prices,amounts=amounts,refs=refs)
        summary['minute_sha256']=hashlib.sha256((tmp/'minutes.npz').read_bytes()).hexdigest()
        dump(universe,tmp/'universe.json');dump(summary,tmp/'summary.json')
        os.rename(tmp,dest)
    finally:
        if tmp.exists():shutil.rmtree(tmp)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--quant',type=Path,required=True)
    p.add_argument('--capture',required=True);p.add_argument('--dates',nargs='+',required=True);a=p.parse_args()
    for day in a.dates:
        build(a.root,a.quant,a.root/'raw'/a.capture,day,json.loads((a.root/'universe'/f'{day}.json').read_text()))
