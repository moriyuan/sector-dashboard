"""Audit timing and rank invariants for all saved waves; independently replay a real first/second/third sequence."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.resonance import BENCHMARKS


def verify(root,capture):
    clock=[f'{m//60:02d}:{m%60:02d}' for a,b in [(570,690),(780,900)] for m in range(a,b+1)]
    audited=boards=0;examples={}
    for folder in sorted((root/'days').glob('????-??-??'),reverse=True):
        day=folder.name
        meta=json.loads((folder/'summary.json').read_text())
        assert meta['prior']<day and hashlib.sha256((folder/'minutes.npz').read_bytes()).hexdigest()==meta['minute_sha256']
        uni={g['code']:g for g in json.loads((folder/'universe.json').read_text())['boards']}
        for tag,(_,symbol) in BENCHMARKS.items():
            events=json.loads((folder/(tag+'.json')).read_text())
            with np.load(folder/(tag+'-tracks.npz')) as src:index=src['index']
            with gzip.open(root/'raw'/capture/(symbol+'.json.gz'),'rt') as f:raw_index=json.load(f)
            assert raw_index['symbol']==symbol
            record=next(r for r in raw_index['days'] if r['date']==day.replace('-',''))
            ticks={line.split()[0]:float(line.split()[1]) for line in record['data']}
            np.testing.assert_allclose(index,[ticks.get(t.replace(':',''),np.nan) for t in clock],equal_nan=True)
            for e in events:
                audited+=1;lo,cf,end=e['pivot'],e['confirm'],e['end'];pb=e['pullback']
                assert lo<cf<=end and lo//121==end//121 and index[cf]/index[lo]>=1.0012
                assert index[cf]>index[cf-3:cf].max()
                if pb['again'] is not None:
                    assert cf<=pb['peak']<pb['pull']<pb['again']<=end and index[end]>index[pb['peak']]
                for b in e['boards']:
                    boards+=1;t=b['at'];rows=b['members']
                    assert t<=cf and lo-t==b['lead'] and b['valid']>=.7*b['eligible'] and b['up']>=b['valid']*.55
                    assert b['response_up']>=.5*b['response_n'] and b['response']>=.039 and len({r['code'] for r in rows})==len(rows)
                    stamps=[r['at'] for r in rows]
                    assert stamps==sorted(stamps)
                    for r in rows:
                        assert r['rank_from']==1+sum(x<r['at'] for x in stamps) and r['rank_to']==sum(x<=r['at'] for x in stamps)
                        if r['hold']=='守住':assert pb['again'] is not None and r['at']<=pb['peak']
                    if len(rows)<3 or b['lead']<=0 or not rows[0]['leader_known']:continue
                    kind='sequence' if [s['rank_to'] for s in rows[:3]]==[1,2,3] else 'held' if any(r['hold']=='守住' for r in rows[:3]) else ''
                    if not kind or kind in examples:continue
                    # Independent evidence: verify provider timestamps and each trigger directly from raw records.
                    with np.load(folder/'minutes.npz') as src:
                        eligible=set(src['codes'].tolist())
                    codes=eligible.intersection(uni[b['code']]['members'])
                    paths={}
                    for code in codes:
                        ticker=('sh' if code.startswith(('60','68')) else 'sz')+code
                        p=root/'raw'/capture/(ticker+'.json.gz')
                        if not p.exists():continue
                        with gzip.open(p,'rt') as f:raw=json.load(f)
                        rec=next((d for d in raw['days'] if d['date']==day.replace('-','')),None)
                        if not rec:continue
                        ticks={z[0]:float(z[1]) if float(z[3])>0 else np.nan for line in rec['data'] if len(z:=line.split())>=4}
                        paths[code]=[ticks.get(x.replace(':',''),np.nan) for x in clock]
                    frame=pd.DataFrame(paths)
                    move=frame.pct_change(3,fill_method=None).iloc[t];assert int((move>0).sum())==b['up'] and int(move.count())==b['valid']
                    one=frame.pct_change(fill_method=None).mean(axis=1)
                    np.testing.assert_allclose(((1+one.iloc[t-2:t+1]).prod()-1)*100,b['gain'],atol=.00051)
                    checks=[]
                    for r in rows[:3]:
                        at=r['at'];px=frame[r['code']];level=px.iloc[at-5:at].max()
                        assert abs(level-r['level'])<.0001 and px.iloc[at]>level
                        assert abs((px.iloc[at]/px.iloc[at-3]-1)*100-r['gain'])<.00051
                        if r['hold']=='守住':assert px.iloc[pb['peak']:pb['again']+1].ge(level).all()
                        checks.append(dict(code=r['code'],name=r['name'],time=clock[at],ranks=[r['rank_from'],r['rank_to']],hold=r['hold']))
                    examples[kind]=dict(day=day,benchmark=tag,sector=b['name'],sector_start=clock[t],pivot=clock[lo],confirm=clock[cf],
                                 pull=clock[pb['pull']] if pb['pull'] is not None else None,again=clock[pb['again']] if pb['again'] is not None else None,sequence=checks)
    assert set(examples)=={"sequence","held"} and audited and boards
    return dict(turns=audited,board_events=boards,samples=examples)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('capture');a=p.parse_args()
    result=verify(a.root,a.capture)
    (a.root/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result)
