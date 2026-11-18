"""Collected seat disclosures, without simulated trades or return statistics."""
from contextlib import closing
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from .lhb import period as disclosure_period
from .lhb_seats import PROFILES, annotate, NOTE

ROOT = Path(__file__).resolve().parents[1] / 'research/seat-stocks-v1/participants.sqlite3'
FIELDS = ('code', 'name', 'date', 'period', 'profile', 'seat', 'seat_code', 'reason',
          'buy', 'sell', 'net', 'source', 'url', 'evidence', 'reviewed_at', 'mapping_version')


def identity(row):
    fields = ('code', 'date', 'period', 'profile', 'seat', 'seat_code', 'reason', 'buy', 'sell', 'net')
    return hashlib.sha256(json.dumps({k: row.get(k) for k in fields}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def clean_event(row):
    value = {key: row.get(key) for key in FIELDS}
    if not re.fullmatch(r'\d{6}', str(value['code'])) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value['date'])):
        raise ValueError('invalid security or disclosure date')
    value['id'] = identity(value)
    return value


def raw_events(rows):
    profiles = {p['name']: p for p in PROFILES}
    for row in rows:
        p = profiles[row['profile']]
        seat, code = row['OPERATEDEPT_NAME'], str(row['OPERATEDEPT_CODE'])
        if (p['seat_code'] and code != p['seat_code']) or (not p['seat_code'] and seat not in p['names']):
            raise ValueError('unverified seat association')
        yield clean_event(dict(code=row['SECURITY_CODE'], name=row['SECURITY_NAME_ABBR'],
            date=row['TRADE_DATE'][:10], period=disclosure_period(row['EXPLANATION']),
            profile=p['name'], seat=seat, seat_code=code, reason=row['EXPLANATION'],
            buy=row.get('ACT_BUY'), sell=row.get('ACT_SELL'), net=row.get('NET_AMT'),
            source='东方财富 · 营业部交易明细', url='https://data.eastmoney.com/stock/tradedetail.html',
            evidence=p['sources'], reviewed_at=p['reviewed_at'], mapping_version=p['version']))


def current_events(store):
    with store.connect() as db:
        records = db.execute("SELECT trade_date,payload FROM snapshots WHERE kind='lhb' AND mode='close' ORDER BY id").fetchall()
    for record in records:
        for row in annotate(json.loads(record['payload']))['rows']:
            for side in ('buy', 'sell'):
                for seat in row.get(side, []):
                    match = seat.get('association')
                    if not match:
                        continue
                    yield clean_event(dict(code=row['code'], name=row['name'], date=record['trade_date'],
                        period=row['period'], profile=match['name'], seat=seat['name'], seat_code=seat.get('seat_code'),
                        reason=row['reason'], buy=seat.get('buy'), sell=seat.get('sell'), net=seat.get('net'),
                        source='东方财富 · 每日龙虎榜', url='https://data.eastmoney.com/stock/tradedetail.html',
                        evidence=match['sources'], reviewed_at=match['reviewed_at'], mapping_version=match['version']))


def archive(path, events):
    """Append observed disclosures; safe to repeat, with an explicit field allowlist."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        for row in events:
            row = clean_event(row)
            db.execute('INSERT OR IGNORE INTO events VALUES(?,?)', (row['id'], json.dumps(row, ensure_ascii=False, allow_nan=False)))
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('seat archive integrity failure')


@lru_cache(maxsize=1)
def read_archive(path, stamp, size):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM events')]


def router(store, path=ROOT):
    api = APIRouter(prefix='/api/seat-stocks')
    path = Path(path)

    def read(profile, period, q):
        events = []
        if path.is_file():
            stat = path.stat(); events.extend(read_archive(str(path), stat.st_mtime_ns, stat.st_size))
        events.extend(current_events(store))
        events = list({e['id']: e for e in events}.values())
        profiles = sorted({e['profile'] for e in events})
        # Keep collected bonds in the archive, but the stock page lists stocks only.
        stocks = [e for e in events if not e['code'].startswith('1')]
        meta = dict(archive_available=path.is_file(), profiles=profiles, note=NOTE,
                    stock_count=len({e['code'] for e in stocks}), event_count=len(stocks),
                    first_date=min((e['date'] for e in stocks), default=None),
                    last_date=max((e['date'] for e in stocks), default=None),
                    excluded_nonstocks=len(events)-len(stocks), retrospective_mapping=True)
        rows = [e for e in stocks if (not profile or e['profile']==profile)
                and (period=='all' or e['period']==period)
                and (not q or q.casefold() in (e['code']+' '+e['name']).casefold())]
        rows.sort(key=lambda e: (e['date'], e['code'], e['profile']), reverse=True)
        return meta, rows

    @api.get('')
    def listing(profile:str=Query('',max_length=60), period:Literal['all','single','three','other']='all',
                q:str=Query('',max_length=80), page:int=Query(1,ge=1), page_size:int=Query(20,ge=1,le=100)):
        meta, events = read(profile, period, q.strip())
        grouped = {}
        for e in events:
            row = grouped.setdefault(e['code'], dict(code=e['code'], name=e['name'], profiles=set(), seats=set(),
                first_date=e['date'], last_date=e['date'], disclosures=0))
            row['profiles'].add(e['profile']); row['seats'].add(e['seat'])
            row['first_date'] = min(row['first_date'], e['date']); row['disclosures'] += 1
        rows = [{**r, 'profiles':sorted(r['profiles']), 'seats':sorted(r['seats'])} for r in grouped.values()]
        return dict(meta=meta, rows=rows[(page-1)*page_size:page*page_size], total=len(rows), page=page)

    @api.get('/{code}')
    def detail(code:str, profile:str=Query('',max_length=60), period:Literal['all','single','three','other']='all',
               page:int=Query(1,ge=1), page_size:int=Query(20,ge=1,le=100)):
        if not re.fullmatch(r'\d{6}', code): raise HTTPException(422,'证券代码应为六位数字')
        meta, rows = read(profile, period, code)
        rows = [r for r in rows if r['code']==code]
        if not rows: raise HTTPException(404,'尚无该股票的关联席位存档')
        return dict(meta=meta, rows=rows[(page-1)*page_size:page*page_size], total=len(rows), page=page)
    return api
