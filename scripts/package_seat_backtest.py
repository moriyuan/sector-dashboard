"""Package a verified frozen study for read-only web queries; no market DB writes."""
import argparse
from collections import defaultdict
import hashlib,json,shutil,sqlite3
from pathlib import Path

WINDOWS={'recent_12_full_months':('2025-11','2026-10'),'all':('2024-11','2026-11'),'early':('2024-11','2026-04'),'holdout':('2026-05','2026-10')}
DOWNLOADS={'monthly.csv':'月度回测.csv','ranking.csv':'席位排名.csv','report.md':'回测报告.md','protocol.json':'protocol.json'}

def package(source,target):
    target.mkdir(parents=True,exist_ok=True)
    dest=target/'study.sqlite3'
    if dest.exists():raise ValueError('Refuse to replace a frozen study')
    read=lambda name:json.loads((source/(name+'.json')).read_text())
    protocol=read('protocol');audit=read('audit');events=read('events');trades=read('trades')
    ranks=read('ranking');months=read('monthly');sens=read('nonoverlap')
    check=read('crosscheck')
    by=defaultdict(list)
    for e in events:by[(e['profile'],e['period'])].append(e)
    for r in ranks:
        start,end=WINDOWS[r['window']]
        samples=[e for e in by[(r['profile'],r['period'])] if start<=e.get('month','')<=end]
        eligible=[e for e in samples if e['entry_state']=='eligible']
        r.update(signals=len(samples),entry_skipped=len(samples)-len(eligible),active_months=len({e['month'] for e in eligible}),
                 last_entry=max((e['entry_date'] for e in eligible),default=None))
    for r in months:
        samples=[e for e in by[(r['profile'],r['period'])] if e.get('month')==r['month']]
        r.update(signals=len(samples),entry_skipped=sum(e['entry_state']!='eligible' for e in samples))
    with sqlite3.connect(dest) as db:
        db.executescript('''CREATE TABLE meta(payload TEXT NOT NULL);
          CREATE TABLE stats(kind TEXT,scope TEXT,profile TEXT,period TEXT,horizon INTEGER,payload TEXT);
          CREATE INDEX stats_lookup ON stats(kind,scope,period,horizon,profile);
          CREATE TABLE trades(profile TEXT,period TEXT,horizon INTEGER,month TEXT,signal_date TEXT,code TEXT,state TEXT,payload TEXT);
          CREATE INDEX trades_lookup ON trades(profile,period,horizon,month,signal_date);''')
        serial=lambda r:json.dumps(r,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        meta=dict(run_id=protocol['version']+'-20261108',frozen=True,signal_start=protocol['signal_start'],data_end=protocol['data_end'],
                  profiles=sorted({r['profile'] for r in ranks}),months=sorted({r['month'] for r in months}),audit=audit,protocol=protocol,
                  validation={k:check[k] for k in ('daily_count','trade_count','daily_max_error_pp','trade_max_error')},
                  source_hashes={n:hashlib.sha256((source/(n+'.json')).read_bytes()).hexdigest() for n in ('protocol','history','ranking','monthly','trades','events')})
        db.execute('INSERT INTO meta VALUES (?)',(serial(meta),))
        for kind,rows,key in [('ranking',ranks,'window'),('monthly',months,'month'),('nonoverlap',sens,None)]:
            db.executemany('INSERT INTO stats VALUES (?,?,?,?,?,?)',[(kind,r[key] if key else 'recent_12_full_months',r['profile'],r['period'],r['horizon'],serial(r)) for r in rows])
        # Include rejected entry opportunities in the same drill-down, without invented returns.
        for e in events:
            if e['entry_state']!='eligible':
                for horizon in (1,3,5,10):trades.append(dict(e,horizon=horizon,state='entry_skipped'))
        db.executemany('INSERT INTO trades VALUES (?,?,?,?,?,?,?,?)',[
            (r['profile'],r['period'],r['horizon'],r.get('month',''),r['signal_date'],r['code'],r['state'],serial(r)) for r in trades])
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    for name,original in DOWNLOADS.items():shutil.copyfile(source/original,target/name)
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()}
    (target/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Packaged',len(ranks),'rankings,',len(months),'monthly cells,',len(trades),'outcomes; bytes',sum(p.stat().st_size for p in target.iterdir()))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--target',type=Path,required=True)
    a=p.parse_args();package(a.source,a.target)
