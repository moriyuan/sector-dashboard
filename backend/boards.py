"""最高板走势：每日涨停池中连板数最高的层级及对应股票名单。
近两周取东财涨停池快照；更早日期池数据缺失，按日 K 近似连板回补
（与情绪打分/综合强度同一套 _valid_hs + 涨停阈值 + _streak_span 交易日口径，
跨周末/假期延续），结果按 revision 缓存在 boards_daily，源快照更新后自动重算。
rows 上以 source 标注口径：pool=涨停池，kline=日K近似回补。"""
import json

from .analytics import hs_stock
from .emotion_score import _streak_span, _streaks, _valid_hs
from .store import now_iso

TIERS = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS boards_daily (
    trade_date TEXT PRIMARY KEY,
    boards INTEGER NOT NULL,
    names TEXT NOT NULL,
    source TEXT NOT NULL,
    revision INTEGER NOT NULL,
    computed_at TEXT NOT NULL
)
"""


def _ensure_table(db):
    db.execute(_SCHEMA)


def _pool_top(store, day):
    """池口径：返回 (boards, names, revision)；无池快照或池空返回 None。"""
    snap = store.snapshot('pools', day, 'close')
    if not snap:
        return None
    sealed = (snap['payload'].get('pools') or {}).get('up') or []
    by_boards = {}
    for r in sealed:
        if not hs_stock(r) or 'ST' in (r.get('name') or '').upper():
            continue
        by_boards.setdefault(int(r.get('boards') or 1), []).append(r.get('name') or r['code'])
    if not by_boards:
        return None
    boards = max(by_boards)
    return boards, sorted(by_boards[boards]), snap['id']


def _kline_tops(store, days):
    """日 K 回补：{day: (boards, names, revision) | None}。

    连板口径与 emotion_score/sentiment 完全一致（_streak_span + _streaks，
    只含真实交易日，跨周末/假期延续）。
    """
    out = {}
    if not days:
        return out
    streaks = _streaks(store, _streak_span(store, days[0], days[-1]))
    for day in days:
        cur = streaks.get(day) or {}
        snap = store.snapshot('stocks', day, 'close')
        if not cur or snap is None:
            out[day] = None
            continue
        valid = _valid_hs(snap['payload'].get('rows') or [])
        top = max(cur.values())
        names = sorted((valid[c].get('name') or c)
                       for c, s in cur.items() if s == top and c in valid)
        out[day] = (top, names, snap['id']) if names else None
    return out


def history(store, limit=60):
    """按日期升序返回 [{'date','tiers':[{'boards','names'}],'source'}]，仅最高一层。

    有池快照的日期取池口径；无池日期按日 K 近似回补；两者皆无则跳过。
    """
    days = sorted({d['trade_date'] for d in store.dates() if d['mode'] == 'close'})
    if not days:
        return []
    scope = days[-limit:]
    with store.connect() as db:
        _ensure_table(db)
        cached = {r['trade_date']: dict(r) for r in db.execute(
            'SELECT * FROM boards_daily WHERE trade_date IN (%s)'
            % ','.join('?' * len(scope)), scope).fetchall()}
        # 只取 revision（不解析 payload），避免逐日 snapshot() 的全量 JSON 开销
        pool_revs = dict(db.execute(
            "SELECT trade_date, MAX(id) FROM snapshots"
            " WHERE kind='pools' AND mode='close' GROUP BY trade_date").fetchall())
        stock_revs = dict(db.execute(
            "SELECT trade_date, MAX(id) FROM snapshots"
            " WHERE kind='stocks' AND mode='close' GROUP BY trade_date").fetchall())
    stale_pool, stale_kline = [], []
    for day in scope:
        if day in pool_revs:
            src, rev = 'pool', pool_revs[day]
        elif day in stock_revs:
            src, rev = 'kline', stock_revs[day]
        else:
            continue  # 两种口径都无数据，跳过
        row = cached.get(day)
        if row and row['source'] == src and row['revision'] >= rev:
            continue
        (stale_pool if src == 'pool' else stale_kline).append(day)
    results = {}
    for day in stale_pool:
        top = _pool_top(store, day)
        if top:
            results[day] = top
    if stale_kline:
        results.update(_kline_tops(store, stale_kline))
    with store.connect() as db:
        _ensure_table(db)
        for day, top in results.items():
            if top is None:
                continue
            boards, names, rev = top
            db.execute(
                'INSERT INTO boards_daily(trade_date,boards,names,source,revision,computed_at)'
                ' VALUES(?,?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET'
                ' boards=excluded.boards,names=excluded.names,source=excluded.source,'
                ' revision=excluded.revision,computed_at=excluded.computed_at',
                (day, boards, json.dumps(names, ensure_ascii=False),
                 'pool' if day in stale_pool else 'kline', rev, now_iso()))
        rows = [dict(r) for r in db.execute(
            'SELECT * FROM boards_daily WHERE trade_date IN (%s) ORDER BY trade_date'
            % ','.join('?' * len(scope)), scope).fetchall()]
    return [{'date': r['trade_date'], 'source': r['source'],
             'tiers': [{'boards': r['boards'],
                        'names': json.loads(r['names'])}]} for r in rows]
