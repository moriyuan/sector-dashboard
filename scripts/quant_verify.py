"""Verify all materialized dates, rolling provenance and an independent last-date regression."""
import argparse
from bisect import bisect_right
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.quant_price import daily_data, FACTORS


def validate(root,source,start,end):
    calendar=pd.read_parquet(source/'benchmark.parquet').date.tolist()
    days=[d for d in calendar if start<=d<=end]
    files={p.stem:p for p in (root/'inputs').glob('*.npz')}
    hashes={d:hashlib.sha256(p.read_bytes()).hexdigest() for d,p in files.items()}
    # Every trading week must have an actual supplier query, including holiday-shortened weeks.
    weeks={}
    for d in days:
        dt=date.fromisoformat(d)
        weeks[dt-timedelta(days=dt.weekday())]=d
    files_ind=sorted((source/'industry').glob('*.parquet'))
    queries={f.stem for f in files_ind}
    assert set(weeks.values())<=queries
    vintages=sorted({str(v) for f in files_ind for v in pd.read_parquet(f,columns=['updateDate']).updateDate.unique()})
    results=[]
    stocks=set();bars=0;values=0
    for d in days:
        v=json.loads((root/'results'/f'{d}.json').read_text())
        idx=calendar.index(d)
        slab=calendar[idx-252:idx+1]
        assert len(slab)==253 and v['previous']==slab[-2] and v['window_start']==slab[0]
        assert v['day']==d and v['industry_date']==vintages[bisect_right(vintages,slab[-2])-1]
        assert v['input_digest']==hashlib.sha256(''.join(hashes[x] for x in slab).encode()).hexdigest()
        assert [f['name'] for f in v['factors'] if f['group']=='风格']==list(FACTORS)
        assert v['coverage']>=.6 and np.isfinite([f['value'] for f in v['factors']]).all()
        s,m=daily_data(files[d]);stocks.update(s['code'].tolist());bars+=len(s)
        values+=int(((s['float_cap']>0)&(s['total_cap']>0)).sum())
        results.append(v)
    last=results[-1]; idx=calendar.index(end)
    ys=[];ts=[];bench=[]
    for d in calendar[idx-252:idx]:
        s,m=daily_data(files[d]);frame=pd.DataFrame(s).set_index('code')
        good=frame.status.eq(1)&frame.amount.gt(0)&frame.close.gt(0)&frame.ret.gt(-100)
        ys.append(frame.ret.where(good));ts.append(frame.turn.where(good&frame.turn.gt(0)));bench.append(m['benchmark_return_pct'])
    y=pd.DataFrame(ys).reset_index(drop=True);t=pd.DataFrame(ts).reset_index(drop=True);b=pd.Series(bench,index=y.index)
    # Independent centered-moment implementation, including stock-specific missing observations.
    cov=y.apply(lambda col:col.cov(b))
    variance=y.apply(lambda col:b[col.notna()].var())
    beta=(cov/variance).where(y.count()>=202)
    err=y.sub(y.mean(),axis=1)-pd.DataFrame(np.outer(b,beta),index=y.index,columns=y.columns)
    vol=(err.var(ddof=1)*(y.count()-1)/(y.count()-2))**.5
    mom=((1+y.iloc[-147:-21]/100).prod(min_count=126)-1)*100
    liquidity=sum(np.log(t.iloc[-h:].mean().where(t.iloc[-h:].count()>=np.ceil(.8*h))) for h in (20,60,252))/3
    prior=frame.loc[good & frame.st.eq(0)&frame.float_cap.gt(0)&frame.pb.gt(0)&frame.turn.gt(0)&frame.industry.ne('')].copy()
    current=pd.DataFrame(daily_data(files[end])[0]).set_index('code')
    target=current.ret.where(current.status.eq(1)&current.amount.gt(0)&current.close.gt(0)&current.ret.gt(-100))
    x=pd.DataFrame(dict(size=np.log(prior.float_cap),value=1/prior.pb,turn=np.log(prior.turn),rev=-prior.ret,
                        beta=beta,mom=mom,vol=vol,liquid=liquidity)).reindex(prior.index)
    ids=x.dropna().index.intersection(target.dropna().index)
    ids=prior.loc[ids].groupby('industry').filter(lambda g:len(g)>=5).index
    x=x.loc[ids];cap=prior.float_cap.loc[ids]
    x=x.clip(x.quantile(.01),x.quantile(.99),axis=1)
    x=(x-np.average(x,weights=cap,axis=0))/x.std(ddof=0)
    groups=pd.get_dummies(prior.industry.loc[ids],drop_first=True,dtype=float)
    design=np.column_stack((np.ones(len(ids)),groups.to_numpy(),x.to_numpy()))
    w=(cap/cap.max())**.25
    coeff=np.linalg.lstsq(design*w.to_numpy()[:,None],target.loc[ids]*w,rcond=None)[0][-len(FACTORS):]
    saved=np.array([f['value'] for f in last['factors'] if f['group']=='风格'])
    np.testing.assert_allclose(coeff,saved,atol=1e-8)
    assert len(ids)==last['n']
    return dict(start=start,end=end,days=len(days),securities=len(stocks),bars=bars,rows_with_both_caps=values,
                min_coverage=min(v['coverage'] for v in results),latest_n=last['n'],latest_universe=last['universe'],
                industry_weeks=len(weeks),latest_industry_date=last['industry_date'],latest_r2=last['r2'],independent_max_error_pp=float(np.max(np.abs(coeff-saved))))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ('root','source','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    a=p.parse_args();result=validate(a.root,a.source,a.start,a.end)
    a.output.write_text(json.dumps(result,indent=2));print(result)
