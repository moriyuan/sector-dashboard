"""核心龙头股票池：情绪核心 / 趋势核心两个池子，支持增删查。

情绪核心池用于冰点分/高潮分的龙头维度评分（见 emotion_score.py）；
趋势核心池当前仅做记录，预留后续趋势维度评分。
"""
import re

from fastapi import APIRouter, Body, HTTPException

from .store import now_iso

POOLS = ('emotion', 'trend')
POOL_NAMES = {'emotion': '情绪核心', 'trend': '趋势核心'}

_SCHEMA = """CREATE TABLE IF NOT EXISTS leader_stocks(
    pool TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY(pool, code)
)"""


def _ensure_schema(db):
    db.executescript(_SCHEMA)
    db.commit()


def list_pools(store):
    """返回 {pool: [rows...]}，emotion_score 与路由共用。"""
    with store.connect() as db:
        _ensure_schema(db)
        rows = db.execute(
            "SELECT pool, code, name, added_at FROM leader_stocks ORDER BY added_at"
        ).fetchall()
    result = {p: [] for p in POOLS}
    for r in rows:
        if r['pool'] in result:
            result[r['pool']].append({'code': r['code'], 'name': r['name'], 'added_at': r['added_at']})
    return result


def pool_perf(store):
    """池内个股表现：最近一个收盘涨幅 + 近 10 日 / 20 日涨幅。

    最近收盘日随快照走——15:00 归档后即为「今日收盘」，之前为上一交易日。
    近 N 日 = 最新收盘价 / N 个交易日前收盘价 - 1；历史不足留空。
    """
    dates = sorted(d['trade_date'] for d in store.dates() if d['mode'] == 'close')
    pools = list_pools(store)
    if not dates:
        return {'date': None, 'pools': pools}

    def close_map(day):
        snap = store.snapshot('stocks', day, 'close')
        return {str(r.get('code')): r for r in (snap['payload'].get('rows') if snap else []) or []}

    latest_day = dates[-1]
    latest = close_map(latest_day)
    ref = {}
    for n in (10, 20):
        ref[n] = close_map(dates[-(n + 1)]) if len(dates) > n else None

    def perf_of(code):
        row = latest.get(code)
        out = {'d1': row.get('change') if row else None, 'd10': None, 'd20': None}
        price = row.get('price') if row else None
        for n in (10, 20):
            past = ref[n]
            if past is not None and price:
                old = past.get(code, {}).get('price')
                if old:
                    out[f'd{n}'] = round((price / old - 1) * 100, 2)
        return out

    result = {p: [{**r, **perf_of(r['code'])} for r in pools[p]] for p in POOLS}
    return {'date': latest_day, 'pools': result}


def router(store):
    api = APIRouter()

    @api.get('/api/leaders/perf')
    def get_leaders_perf():
        return pool_perf(store)

    @api.get('/api/leaders')
    def get_leaders():
        pools = list_pools(store)
        return {'pools': [{'pool': p, 'name': POOL_NAMES[p], 'rows': pools[p]} for p in POOLS]}

    @api.post('/api/leaders')
    def add_leader(pool: str = Body(..., embed=True), code: str = Body(..., embed=True),
                   name: str = Body(..., embed=True)):
        pool = (pool or '').strip()
        code = (code or '').strip()
        name = (name or '').strip()
        if pool not in POOLS:
            raise HTTPException(422, f'pool 只能是 {"/".join(POOLS)}')
        if not re.fullmatch(r'\d{6}', code):
            raise HTTPException(422, '代码应为 6 位数字')
        if not name:
            raise HTTPException(422, '名称不能为空')
        with store.connect() as db:
            _ensure_schema(db)
            exists = db.execute(
                "SELECT 1 FROM leader_stocks WHERE pool=? AND code=?", (pool, code)).fetchone()
            if exists:
                raise HTTPException(422, '该股票已在池中')
            db.execute(
                "INSERT INTO leader_stocks(pool, code, name, added_at) VALUES(?,?,?,?)",
                (pool, code, name, now_iso()))
            db.commit()
        return {'ok': True}

    @api.delete('/api/leaders/{pool}/{code}')
    def remove_leader(pool: str, code: str):
        with store.connect() as db:
            _ensure_schema(db)
            cur = db.execute(
                "DELETE FROM leader_stocks WHERE pool=? AND code=?", (pool, code))
            db.commit()
        if not cur.rowcount:
            raise HTTPException(404, '池中没有这只股票')
        return {'ok': True}

    return api
