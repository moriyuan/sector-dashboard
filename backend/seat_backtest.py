"""Read-only access to a frozen, versioned public-seat event study."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import FileResponse

ROOT=Path(__file__).resolve().parents[1]/'research/seat-follow-v1'
WINDOWS={'recent_12_full_months':('2025-11','2026-10'),'all':('2024-11','2026-11'),
         'early':('2024-11','2026-04'),'holdout':('2026-05','2026-10')}
Period=Literal['single','three','other']
DOWNLOADS={'monthly.csv':'月度回测.csv','ranking.csv':'席位排名.csv','report.md':'回测报告.md','protocol.json':'protocol.json'}


def router(root=ROOT):
    root=Path(root)
    api=APIRouter(prefix='/api/seat-backtest')

    @contextmanager
    def connect():
        file=root/'study.sqlite3'
        if not file.is_file():raise HTTPException(503,'回测版本尚未安装，未生成空白或替代结果')
        db=sqlite3.connect(file.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
        try:yield db
        finally:db.close()

    def metadata(db):
        return json.loads(db.execute('SELECT payload FROM meta').fetchone()[0])

    def horizon_check(horizon):
        if horizon not in (1,3,5,10):raise HTTPException(422,'持有期仅支持1、3、5、10个交易日')

    def profile_check(meta,profile):
        if profile not in meta['profiles']:raise HTTPException(422,'未知关联席位')

    @api.get('')
    def ranking(scope:str='recent_12_full_months',period:Period='single',horizon:int=5):
        horizon_check(horizon)
        with connect() as db:
            meta=metadata(db)
            if scope not in WINDOWS and scope not in meta['months']:raise HTTPException(422,'未知样本窗口或月份')
            monthly=scope not in WINDOWS
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM stats WHERE kind=? AND scope=? AND period=? AND horizon=?',('monthly' if monthly else 'ranking',scope,period,horizon))]
            if monthly:
                # Monthly exploratory ranks use the explicit threshold from the report.
                selected=[]
                for r in rows:
                    r['rank_eligible']=(r['n']>=10 and r['days']>=5 and sum(r['states'].get(k,0) for k in ('missing_path','unresolved_exit'))<=.05*max(1,r['events']))
                    if r['rank_eligible']:selected.append(r)
                selected.sort(key=lambda r:(-r['ci_low'],-r['win_rate'],-r['n']))
                for rank,r in enumerate(selected,1):r['rank']=rank
            rows.sort(key=lambda r:(r.get('rank',999),r['profile']))
            return dict(meta=meta,rows=rows,scope=scope,period=period,horizon=horizon,
                        min_trades=10 if monthly else 30,min_days=5 if monthly else 15,
                        interval='Wilson近似区间，仅用于当月回看' if monthly else '按入场日期聚类自助抽样95%区间')

    @api.get('/monthly')
    def monthly(profile:str,period:Period='single',horizon:int=5):
        horizon_check(horizon)
        with connect() as db:
            meta=metadata(db);profile_check(meta,profile)
            rows=[json.loads(r[0]) for r in db.execute("SELECT payload FROM stats WHERE kind='monthly' AND profile=? AND period=? AND horizon=? ORDER BY scope",(profile,period,horizon))]
            sensitivity=db.execute("SELECT payload FROM stats WHERE kind='nonoverlap' AND profile=? AND period=? AND horizon=?",(profile,period,horizon)).fetchone()
            return dict(run_id=meta['run_id'],rows=rows,nonoverlap=json.loads(sensitivity[0]) if sensitivity else None)

    @api.get('/trades')
    def trades(profile:str,period:Period='single',horizon:int=5,month:str='',
               outcome:Literal['all','closed','entry_skipped','missing_path','right_censored','unresolved_exit']='all',
               page:int=Query(1,ge=1,le=100000),page_size:int=Query(10,ge=1,le=50)):
        horizon_check(horizon)
        with connect() as db:
            meta=metadata(db);profile_check(meta,profile)
            if month and month not in meta['months']:raise HTTPException(422,'未知入场月份')
            clauses=['profile=?','period=?','horizon=?'];args=[profile,period,horizon]
            if month:clauses.append('month=?');args.append(month)
            if outcome!='all':clauses.append('state=?');args.append(outcome)
            where=' AND '.join(clauses)
            total=db.execute('SELECT COUNT(*) FROM trades WHERE '+where,args).fetchone()[0]
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM trades WHERE '+where+' ORDER BY signal_date DESC,code LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])]
            return dict(run_id=meta['run_id'],rows=rows,total=total,page=page,page_size=page_size)

    @api.get('/download/{name}')
    def download(name:str):
        if name not in DOWNLOADS:raise HTTPException(404,'报告文件不存在')
        path=root/name
        if not path.is_file():raise HTTPException(503,'回测报告尚未安装')
        return FileResponse(path,filename=DOWNLOADS[name],media_type='text/csv; charset=utf-8' if name.endswith('.csv') else 'application/octet-stream')
    return api
