"""Freeze the existing public-pool study into a dependency-free group estimator.
Usage: python scripts/build_relay_model.py /path/to/limit_up_nextday_20261102
Raw evidence remains in the research archive; this exports aggregate statistics only.
"""
import csv,hashlib,json,math,random,sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def build(root):
    calendar=json.loads((root/'calendar.json').read_text())
    report=json.loads((root/'analysis.json').read_text())
    assert calendar[-1]=='2026-11-02' and len(calendar)==242
    pools={}; hashes={}
    for day in calendar:
        p=root/'days'/f'{day}.json'; raw=p.read_bytes(); data=json.loads(raw)
        assert data['date']==day and data['count']==len(data['rows'])
        assert len({r['code'] for r in data['rows']})==data['count'] and data['source_hashes']
        pools[day]={r['code']:r for r in data['rows']}; hashes[day]=hashlib.sha256(raw).hexdigest()
    heights=None; groups=defaultdict(list)
    for i,day in enumerate(calendar[:-1]):
        pool=pools[day]
        heights={c:None if heights is None or c in heights and heights[c] is None else 1+heights.get(c,0) for c in pool}
        for code,row in pool.items():
            if row['market_type'] not in ('HS','GEM','STAR') or not code.startswith(('0','3','6')) or 'ST' in row['name'].upper() or not heights[code]:continue
            key=('20' if code.startswith(('30','688','689')) else '10')+':'+str(min(heights[code],4))
            groups[key].append((day,int(code in pools[calendar[i+1]])))
    audit=defaultdict(list)
    with (root/'walk_forward.csv').open() as f:
        for r in csv.DictReader(f):
            if report['audit_days'][0]<=r['day']<=report['audit_days'][1]:
                audit[r['tier']+':'+str(min(int(r['height']),4))].append(r)
    estimates={};rng=random.Random(20261103);dates=calendar[1:-1]
    for key,rows in sorted(groups.items()):
        n=len(rows); hits=sum(y for _,y in rows); by=defaultdict(lambda:[0,0])
        for day,y in rows:by[day][0]+=y;by[day][1]+=1
        boots=[]
        for _ in range(500):
            selected=[]
            while len(selected)<len(dates):
                start=rng.randrange(len(dates));selected.extend(dates[(start+j)%len(dates)] for j in range(5))
            h=sum(by[d][0] for d in selected[:len(dates)]); count=sum(by[d][1] for d in selected[:len(dates)])
            boots.append((h+.5)/(count+1))
        boots.sort();v=audit[key]; vn=len(v)
        estimates[key]=dict(n=n,hits=hits,days=len({day for day,_ in rows}),probability=(hits+.5)/(n+1),interval=[boots[12],boots[487]],
          audit_n=vn,audit_actual=sum(float(r['y']) for r in v)/vn if vn else None,
          audit_mean=sum(float(r['base']) for r in v)/vn if vn else None,
          audit_brier=sum((float(r['base'])-float(r['y']))**2 for r in v)/vn if vn else None)
    for r in report['baselines']:
        g=estimates[r['tier']+':'+r['height']];assert (g['n'],g['hits'])==(r['n'],r['hits'])
    return dict(version='relay-group-v1',released_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
      train_start=report['train_first'],train_end=report['train_last'],label_end=calendar[-1],expires='2026-12-02',
      source='同花顺沪深非ST收盘涨停池',source_url='https://data.10jqka.com.cn/limit_up/',
      target='下一交易日再次进入同口径收盘涨停池',method='按涨停制度与连板高度分组，Jeffreys平滑频率',
      interval_method='500次五交易日区块重采样的95%估计区间；不覆盖模型误设及市场制度变化',
      audit_window=report['audit_days'],min_samples=100,min_days=20,min_audit=50,
      evidence_hashes=hashes,walk_forward_sha256=hashlib.sha256((root/'walk_forward.csv').read_bytes()).hexdigest(),groups=estimates)

if __name__=='__main__':
    result=build(Path(sys.argv[1])); destination=Path(__file__).resolve().parents[1]/'backend/models/relay-v1.json'
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(version=result['version'],groups={k:v['n'] for k,v in result['groups'].items()})))
