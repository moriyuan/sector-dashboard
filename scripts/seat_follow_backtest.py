"""Offline, retrospective public-seat event study; never invokes the trading platform."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import csv
import hashlib
import json
from pathlib import Path
import sys
import math

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.lhb import period

HORIZONS=(1,3,5,10)
COMMISSION=.0003
TRANSFER=.00001
STAMP=.0005
SLIPPAGE=.0005
MAX_DELAY=20


def finite(v):
    return isinstance(v,(int,float,np.number)) and np.isfinite(v)


def valid(row):
    return row is not None and all(finite(row[k]) and row[k]>0 for k in ('open','high','low','close','preclose'))


def limit_price(code,row,day,up):
    rate='.20' if code.startswith(('30','68')) else ('.05' if row['st']==1 and day<'2026-07-06' else '.10')
    multiplier=Decimal(1)+(Decimal(rate) if up else -Decimal(rate))
    return float((Decimal(str(float(row['preclose'])))*multiplier).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))


def entry_check(code,row,day,age):
    if row is None or row['status'] not in (0,1):return 'missing_entry'
    if row['status']==0:return 'suspended'
    if not valid(row) or not finite(row['volume']) or row['volume']<=0:return 'missing_entry'
    if row['st'] not in (0,1):return 'unknown_st'
    if row['st']==1:return 'st'
    if age<=5:return 'ipo_first_five'
    upper=limit_price(code,row,day,True)
    if row['open']>upper+.005:return 'unknown_limit_regime'
    if row['open']>=upper-.005:
        return 'one_price_up' if row['high']-row['low']<.005 else 'open_limit_up'
    if row['high']-row['low']<.005:return 'one_price_other'
    return 'eligible'


def simulate(code,bars,dates,entry,horizon):
    planned=entry+horizon
    if planned>=len(dates):return dict(state='right_censored')
    gross=bars[entry]['close']/bars[entry]['open']
    for i in range(entry+1,min(planned+MAX_DELAY+1,len(dates))):
        row=bars[i]
        if row is None or row['status'] not in (0,1):return dict(state='missing_path')
        if row['status']==0:
            # No trade, no price return. An exit order remains pending.
            continue
        if not valid(row) or row['st'] not in (0,1):return dict(state='missing_path')
        gross*=row['close']/row['preclose']
        if i<planned:continue
        if row['volume']<=0 or row['close']<=limit_price(code,row,dates[i],False)+.005:continue
        cost=(1-SLIPPAGE)*(1-COMMISSION-TRANSFER-STAMP)/((1+SLIPPAGE)*(1+COMMISSION+TRANSFER))
        return dict(state='closed',planned_exit=dates[planned],exit_date=dates[i],delay=i-planned,
                    gross_return=float(gross-1),net_return=float(gross*cost-1))
    return dict(state='unresolved_exit',planned_exit=dates[planned])


def make_signals(raw):
    grouped=defaultdict(list);audit=Counter()
    for r in raw:
        code=r['SECURITY_CODE']
        if not (len(code)==6 and code.startswith(('00','30','60','68'))):
            audit['non_a_share']+=1;continue
        p=period(r['EXPLANATION'])
        grouped[(r['profile'],r['TRADE_DATE'][:10],code,p)].append(r)
    result=[]
    for (profile,day,code,p),records in sorted(grouped.items()):
        amounts={r['NET_AMT'] for r in records}
        if len(amounts)!=1:
            audit['conflicting_amount']+=1;continue
        net=next(iter(amounts))
        if not finite(net):audit['unknown_net']+=1;continue
        if net<=0:audit['nonpositive_net']+=1;continue
        result.append(dict(profile=profile,signal_date=day,code=code,name=records[0]['SECURITY_NAME_ABBR'],
                           period=p,net=float(net),disclosures=len(records)))
    audit['raw_rows']=len(raw);audit['grouped_events']=len(grouped);audit['positive_events']=len(result)
    return result,dict(audit)


def metrics(trades,bootstrap=False):
    closed=[r for r in trades if r['state']=='closed']
    n=len(closed);states=Counter(r['state'] for r in trades)
    result=dict(events=len(trades),n=n,days=len({r['entry_date'] for r in closed}),states=dict(states),
                win_rate=None,mean_return=None,median_return=None,ci_low=None,ci_high=None,
                mean_excess=None,delayed=sum(r.get('delay',0)>0 for r in closed))
    if not n:return result
    ret=np.array([r['net_return'] for r in closed]);win=ret>0
    p=float(win.mean());z=1.96;den=1+z*z/n;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    center=(p+z*z/(2*n))/den
    result.update(win_rate=p,mean_return=float(ret.mean()),median_return=float(np.median(ret)),
                  ci_low=center-half,ci_high=center+half,p10_return=float(np.quantile(ret,.1)),
                  mean_excess=float(np.mean([r['excess_return'] for r in closed if r.get('excess_return') is not None])) if any(r.get('excess_return') is not None for r in closed) else None)
    if bootstrap and result['days']>=10:
        by=defaultdict(lambda:[0,0])
        for r in closed:
            by[r['entry_date']][0]+=r['net_return']>0;by[r['entry_date']][1]+=1
        vals=np.array(list(by.values()),dtype=float);rng=np.random.default_rng(20261108)
        sampled=vals[rng.integers(0,len(vals),(2000,len(vals)))].sum(axis=1)
        low,high=np.quantile(sampled[:,0]/sampled[:,1],[.025,.975])
        result.update(ci_low=float(low),ci_high=float(high))
    unresolved=sum(states.get(k,0) for k in ('unresolved_exit','missing_path'))
    result['win_lower_if_unresolved_loss']=float(win.sum()/(n+unresolved))
    return result


def write_csv(path,rows):
    if not rows:return
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,default=Path('runtime/quant-price-v2/inputs'))
    args=parser.parse_args();root=args.root
    protocol=json.loads((root/'protocol.json').read_text())
    raw=json.loads((root/'history.json').read_text());signals,audit=make_signals(raw)
    codes=sorted({r['code'] for r in signals});col={code:i for i,code in enumerate(codes)}
    files=sorted(p for p in args.inputs.glob('*.npz') if p.stem<=protocol['data_end'])
    dates=[p.stem for p in files];dayidx={d:i for i,d in enumerate(dates)}
    dtype=np.dtype([(k,'f8') for k in ('open','high','low','close','preclose','volume','amount','status','st')])
    data=np.empty((len(files),len(codes)),dtype=dtype)
    for k in dtype.names:data[k].fill(np.nan)
    bench=np.full(len(files),np.nan);hashes=[]
    wanted=np.array([int(c) for c in codes])
    for i,file in enumerate(files):
        with np.load(file,allow_pickle=False) as inp:
            rows=inp['stocks'];meta=json.loads(str(inp['meta']))
        assert meta['day']==file.stem
        _,a,b=np.intersect1d(wanted,rows['code'],return_indices=True)
        for k in dtype.names:data[k][i,a]=rows[k][b]
        bench[i]=meta['benchmark_return_pct']/100
        hashes.append(dict(file=str(file),sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    present=np.isfinite(data['status']);age=np.cumsum(present,axis=0)
    # Benchmark opening prices are required; daily close-only excess cannot substitute.
    import pandas as pd
    benchmark=pd.read_parquet('runtime/quant-history/benchmark.parquet').set_index('date')
    assert all(d in benchmark.index for d in dates)
    print('loaded daily panels',len(dates),len(codes),flush=True)
    events=[];trades=[]
    for signal in signals:
        d=signal['signal_date'];si=dayidx.get(d);j=col[signal['code']]
        if si is None:events.append(dict(signal,entry_state='missing_signal_day'));continue
        if si+1>=len(dates):events.append(dict(signal,entry_state='right_censored'));continue
        ei=si+1;entry=dates[ei];bars=data[:,j]
        state=entry_check(signal['code'],bars[ei],entry,age[ei,j])
        if not present[si,j]:state='missing_signal_quote'
        elif bars[si]['st']!=0:state='signal_st_or_unknown'
        event=dict(signal,entry_date=entry,month=entry[:7],entry_state=state)
        events.append(event)
        if state!='eligible':continue
        for h in HORIZONS:
            outcome=simulate(signal['code'],bars,dates,ei,h)
            trade=dict(event,horizon=h,**outcome)
            if outcome['state']=='closed':
                end=dayidx[outcome['exit_date']]
                bg=float(benchmark.loc[entry,'close'])/float(benchmark.loc[entry,'open'])*float(np.prod(1+bench[ei+1:end+1]))-1
                trade.update(benchmark_return=bg,excess_return=outcome['net_return']-bg)
            trades.append(trade)
    groups=defaultdict(list);monthly=defaultdict(list)
    windows={'all':('2024-11','2026-11'),'recent_12_full_months':('2025-11','2026-10'),
             'early':('2024-11','2026-04'),'holdout':('2026-05','2026-10')}
    # Keep zero-sample profile/month cells visible rather than dropping failed entries.
    for profile in sorted({r['profile'] for r in raw}):
        for p in ('single','three','other'):
            for h in HORIZONS:
                for month in sorted({r['month'] for r in events if 'month' in r}):
                    monthly[(profile,p,h,month)]
                for window in windows:
                    groups[(profile,p,h,window)]
    for t in trades:
        monthly[(t['profile'],t['period'],t['horizon'],t['month'])].append(t)
        for window,(start,end) in windows.items():
            if start<=t['month']<=end:groups[(t['profile'],t['period'],t['horizon'],window)].append(t)
    ranks=[]
    for (profile,p,h,window),ts in groups.items():
        m=metrics(ts,bootstrap=True);m.update(profile=profile,period=p,horizon=h,window=window)
        m['rank_eligible']=m['n']>=30 and m['days']>=15 and m['states'].get('missing_path',0)+m['states'].get('unresolved_exit',0)<=.05*max(1,m['events'])
        ranks.append(m)
    for window in windows:
        for p in ('single','three','other'):
            for h in HORIZONS:
                selected=[r for r in ranks if r['window']==window and r['period']==p and r['horizon']==h and r['rank_eligible']]
                selected.sort(key=lambda r:(-r['ci_low'],-r['win_rate'],-r['n']))
                for rank,r in enumerate(selected,1):r['rank']=rank
    monthrows=[]
    for (profile,p,h,month),ts in sorted(monthly.items()):
        monthrows.append(dict(profile=profile,period=p,horizon=h,month=month,**metrics(ts)))
    # One position per profile/stock/period/horizon: suppress repeated signals until actual exit.
    nonoverlap=[];until={}
    for t in sorted(trades,key=lambda r:(r['entry_date'],r['code'],r['profile'],r['period'],r['horizon'])):
        key=(t['profile'],t['code'],t['period'],t['horizon'])
        if t['entry_date']<=until.get(key,''):continue
        nonoverlap.append(t)
        until[key]=t.get('exit_date',dates[-1])
    sensitivity=[]
    for r in ranks:
        if r['window']!='recent_12_full_months':continue
        start,end=windows[r['window']]
        ts=[t for t in nonoverlap if (t['profile'],t['period'],t['horizon'])==(r['profile'],r['period'],r['horizon']) and start<=t['month']<=end]
        sensitivity.append(dict(profile=r['profile'],period=r['period'],horizon=r['horizon'],**metrics(ts)))
    audit.update(entry_states=dict(Counter(r['entry_state'] for r in events)),periods=dict(Counter(r['period'] for r in signals)),
                 trade_states=dict(Counter(r['state'] for r in trades)),profiles=len({r['profile'] for r in raw}),quote_end=dates[-1])
    for name,rows in [('events',events),('trades',trades),('ranking',ranks),('monthly',monthrows),('nonoverlap',sensitivity)]:
        (root/(name+'.json')).write_text(json.dumps(rows,ensure_ascii=False,allow_nan=False))
        write_csv(root/(name+'.csv'),rows)
    (root/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    (root/'input-hashes.json').write_text(json.dumps(hashes,indent=2))
    provenance=[Path(__file__),root/'protocol.json',root/'history.json',root/'fetch-audit.json',
                Path('backend/lhb_seats.py'),Path('backend/lhb_seat_profiles.json'),
                Path('runtime/quant-history/benchmark.parquet')]
    (root/'run-manifest.json').write_text(json.dumps([
        dict(file=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in provenance],indent=2))
    print(json.dumps(audit,ensure_ascii=False,indent=2))
    for r in sorted((r for r in ranks if r['window']=='recent_12_full_months' and r['period']=='single' and r['horizon']==5),key=lambda r:r.get('rank',999)):
        print(r['profile'],'rank',r.get('rank'),'n',r['n'],'win',r['win_rate'],'mean',r['mean_return'],'excess',r['mean_excess'])


if __name__=='__main__':main()
