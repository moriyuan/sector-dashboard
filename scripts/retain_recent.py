"""Offline SQLite compaction for explicitly configured three-session retention.

Writers must be stopped by the maintenance wrapper. Never edits a source database.
Unknown tables fail closed; frozen research packages are outside the data roots.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from datetime import date

DAY = re.compile(r'20\d{2}-\d{2}-\d{2}')
RAW_PREFIXES=('raw:','public_raw:','browser_raw:','ths_raw:','calendar_raw','financing:raw')
RETIRED_PREFIXES=('index-pe:','financing:','etf-check:')


def retained_days(db, keep, asof):
    if not 1 <= keep <= 30: raise ValueError('invalid retention count')
    date.fromisoformat(asof)
    days=[r[0] for r in db.execute("SELECT DISTINCT trade_date FROM snapshots WHERE kind='market' AND mode='close' AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",(asof,keep))]
    if len(days)<keep: raise ValueError('not enough archived sessions; refuse deletion')
    for day in days: date.fromisoformat(day)
    return days


def trim(value, cutoff):
    if isinstance(value,list):
        return [trim(v,cutoff) for v in value if not (isinstance(v,dict) and
          isinstance(v.get('date'),str) and DAY.fullmatch(v['date']) and v['date']<cutoff)]
    if isinstance(value,dict):
        return {k:trim(v,cutoff) for k,v in value.items() if not (DAY.fullmatch(k) and k<cutoff)}
    return value


def install_guards(db, cutoff, tables):
    # Bound to the committed retention boundary, advanced on each maintenance run.
    date.fromisoformat(cutoff)
    for table,column in [('snapshots','trade_date'),('news_items','published')]:
        if table not in tables: continue
        condition=(f"julianday(NEW.published)<julianday('{cutoff}T00:00:00+08:00')"
                   if table=='news_items' else f"NEW.trade_date<'{cutoff}' OR NEW.kind='broad_etf'")
        for operation in ('INSERT','UPDATE'):
            db.execute(f'''CREATE TRIGGER retention_{table}_{operation.lower()}
                BEFORE {operation} ON {table} WHEN {condition}
                BEGIN SELECT RAISE(IGNORE); END''')
    if 'observations' in tables:
        condition=' OR '.join("NEW.key LIKE '"+p+"%'" for p in RAW_PREFIXES+RETIRED_PREFIXES)
        db.execute(f'''CREATE TRIGGER retention_raw_observations BEFORE INSERT ON observations
            WHEN {condition} BEGIN SELECT RAISE(IGNORE); END''')
    if 'news_raw' in tables:
        db.execute('''CREATE TRIGGER retention_news_raw BEFORE INSERT ON news_raw
            BEGIN SELECT RAISE(IGNORE); END''')


def build_retained(source, destination, cutoff):
    source=Path(source);destination=Path(destination);date.fromisoformat(cutoff)
    if destination.exists(): raise ValueError('destination exists; refuse overwrite')
    # Source journal is checkpointed by the wrapper after all writers have stopped.
    old=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)
    schema=old.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()
    tables={n for t,n,s in schema if t=='table'}
    news_cutoff=cutoff+'T00:00:00+08:00'
    filters={
      'snapshots':("trade_date>=? AND kind!='broad_etf'",[cutoff]),
      'events':("julianday(time)>=julianday(?) AND job NOT IN ('宽基ETF','融资余额') AND job NOT LIKE '指数估值 %'",[news_cutoff]),
      'news_items':("julianday(coalesce(nullif(published,''),seen))>=julianday(?)",[news_cutoff]),
      'news_raw':('0',[]),'news_budget':('day>=?',[cutoff]),
      'news_gaps':('julianday(end)>=julianday(?)',[news_cutoff]),
      'news_rank_scans':('trade_date>=?',[cutoff]),
      'daily_notices':('day>=?',[cutoff]),'daily_changes':('day>=?',[cutoff]),
      'daily_sources':('day>=?',[cutoff]),
      'daily_risk_documents':('published_day>=?',[cutoff]),
      'daily_risks':('published_day>=?',[cutoff]),
      'sentiment_daily':('trade_date>=?',[cutoff]),
      'emotion_scores':('trade_date>=?',[cutoff]),'boards_daily':('trade_date>=?',[cutoff]),
      'limitup_meta':('trade_date>=?',[cutoff]),'limitup_theme':('trade_date>=?',[cutoff]),
      'limitup_stock':('trade_date>=?',[cutoff]),'leader_stocks':('1',[]),
      'news_sources':('1',[]),'news_worker_state':('1',[]),
      'news_business':('1',[]),'backlog_items':('1',[]),
      'news_analysis':('event_id IN (SELECT event_id FROM main.news_items)',[]),
      'news_identity':('EXISTS(SELECT 1 FROM main.news_items i WHERE i.source=original.news_identity.source AND i.source_id=original.news_identity.source_id)',[]),
      'news_jobs':('event_id IN (SELECT event_id FROM main.news_items)',[]),
      'news_event_links':('child IN (SELECT event_id FROM main.news_items) AND parent IN (SELECT event_id FROM main.news_items)',[]),
      'news_enrichment':('EXISTS(SELECT 1 FROM main.news_items i WHERE i.source=original.news_enrichment.source AND i.source_id=original.news_enrichment.source_id)',[]),
      'daily_finance':('notice_id IN (SELECT id FROM main.daily_notices)',[]),
      'daily_analysis':('finance_id IN (SELECT id FROM main.daily_finance)',[]),
      'daily_jobs':("key IN (SELECT 'finance:'||id FROM main.daily_notices) OR EXISTS(SELECT 1 FROM main.daily_finance f WHERE key LIKE 'analysis:%:'||f.id)",[]),
    }
    unknown=tables-set(filters)-{'observations'}
    if unknown:
        old.close();raise ValueError('unclassified tables: '+','.join(sorted(unknown)))
    new=sqlite3.connect(destination)
    counts={}
    try:
        new.execute('PRAGMA journal_mode=DELETE');new.execute('PRAGMA synchronous=FULL')
        for typ,name,sql in schema:
            if typ=='table':new.execute(sql)
        new.execute('ATTACH DATABASE ? AS original',(str(source.resolve()),))
        for name,(where,args) in filters.items():
            if name not in tables:continue
            new.execute(f'INSERT INTO main."{name}" SELECT * FROM original."{name}" WHERE {where}',args)
            counts[name]=new.execute(f'SELECT count(*) FROM main."{name}"').fetchone()[0]
        # Include old reference observations explicitly cited by retained snapshots;
        # only their retained-date history survives. IDs remain stable for details.
        refs=set()
        def references(value):
            if isinstance(value,dict):
                for v in value.values():references(v)
            elif isinstance(value,list):
                for v in value:references(v)
            elif type(value) is int:refs.add(value)
        for payload, in new.execute('SELECT payload FROM snapshots'):
            references(json.loads(payload).get('input_refs',{}))
        old.row_factory=sqlite3.Row
        keys=old.execute('SELECT key,max(id) id FROM observations GROUP BY key').fetchall()
        baseline={r['id'] for r in keys if r['key']=='calendar' or r['key'].startswith(('catalog:','bars:'))}
        new_count=0
        for row in old.execute('SELECT * FROM observations'):
            row=dict(row);key=row['key']
            if key.startswith(RAW_PREFIXES+RETIRED_PREFIXES):continue
            dates=DAY.findall(key)
            if dates and max(dates)<cutoff:continue
            if row['observed_at']<cutoff and row['id'] not in baseline|refs:continue
            if key!='calendar':
                value=trim(json.loads(row['payload']),cutoff)
                if not value:continue
                row['payload']=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
                row['sha256']=hashlib.sha256(row['payload'].encode()).hexdigest()
            new.execute('INSERT INTO observations VALUES(:id,:key,:observed_at,:sha256,:payload)',row);new_count+=1
        counts['observations']=new_count
        for typ,name,sql in schema:
            if typ in ('index','view'):new.execute(sql)
            if typ=='trigger' and not name.startswith(('cleanup_','retention_')):new.execute(sql)
        install_guards(new,cutoff,tables)
        new.commit()
        if new.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('retained database failed integrity check')
        if new.execute('SELECT count(*) FROM snapshots WHERE trade_date<?',(cutoff,)).fetchone()[0]:raise ValueError('retention boundary failed')
        return dict(cutoff=cutoff,bytes_before=source.stat().st_size,bytes_after=destination.stat().st_size,counts=counts)
    except Exception:
        new.close();destination.unlink(missing_ok=True);raise
    finally:
        new.close();old.close()


def prune_files(root, cutoff):
    root=Path(root);removed=[]
    for relative in ('quant-price-v2/inputs','quant-price-v2/results','quant-price-sources/bars','quant-price-sources/values','quant-price-sources/industry','resonance/days'):
        folder=root/relative
        if not folder.is_dir():continue
        for p in folder.iterdir():
            day=p.stem if p.is_file() else p.name
            if DAY.fullmatch(day) and day<cutoff:
                removed.append(str(p));shutil.rmtree(p) if p.is_dir() else p.unlink()
    for p in (root/'backfill_progress').glob('kline_raw.jsonl'):
        p.unlink();removed.append(str(p))
    benchmark=root/'quant-price-sources/benchmark.parquet'
    if benchmark.exists():
        import pandas as pd
        df=pd.read_parquet(benchmark);df=df[df.date.astype(str)>=cutoff]
        df.to_parquet(benchmark,index=False)
    return removed


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True);parser.add_argument('--cutoff',required=True)
    args=parser.parse_args();print(json.dumps(build_retained(args.source,args.destination,args.cutoff),ensure_ascii=False))
