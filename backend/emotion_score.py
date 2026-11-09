"""冰点分 / 高潮分 双打分体系。

冰点分越大市场情绪越差，高潮分越大市场越极致火热。
各维度口径（用户拍板版）：

冰点分（5 维）
  1. 下跌广度：下跌家数 <3000→0；3000~3499→0.5；
     ≥3500→floor((n-3500)/500)+1，封顶 5
  2. 跌停个数：n<10→0；否则 floor((n-10)/10)+1 封顶 4；
     若跌停 > 涨停，加成 +2，合计封顶 6
  3. 封板率：样本(涨停+炸板) <10→置空；≥70%→0；55~70%→1；<55%→2
  4. 龙头表现：情绪核心池非空→池内股票当日涨跌幅；池空→昨日 K 线连板股。
     平均跌幅 ≤-5%→1；任一跌停→2；取大
  5. 大面个数：(最高价-收盘价)/最高价 ≥10% 的股票数 >10→1；>20→2

高潮分（5 维）
  1. 上涨广度：上涨家数 <4000→0；≥4000→floor((n-4000)/300)+1，封顶 4
  2. 涨停个数：<65→0；≥65→floor((n-65)/15)+1，封顶 4
  3. 龙头表现：同一批龙头，平均涨幅 >5%→1；>8%→2
  4. 最高板：<8 板→0；≥8 板→boards-7，封顶 4
  5. 封板率：>90%→1；样本 <10→置空

取整一律向下取整再封顶；缺维度置空不计入总分。
近两周（有 pools 快照）用池数据精确计；更早日期用日 K 近似回补，
涨停/跌停按板块阈值近似（主板 9.8%、双创 19.8%、北交所 29.8%、ST 4.8%），
炸板=盘中触及涨停价但未封住，最高板=逐股重建连板数；source 标注 pool / kline。
"""
import json
import math
from datetime import datetime, timedelta

from .analytics import hs_stock
from .leaders import list_pools
from .store import now_iso

SCHEMA = """CREATE TABLE IF NOT EXISTS emotion_scores(
    trade_date TEXT PRIMARY KEY,
    ice REAL,
    climax REAL,
    subs TEXT,
    source TEXT,
    revision INTEGER,
    computed_at TEXT
)"""

STREAK_DAYS = 40  # 连板回补时向前扫的天数（连板极少超过 20 板，40 天足够）


def _threshold(row):
    """涨停幅度近似阈值（百分数）。"""
    code = str(row.get('code') or '')
    name = str(row.get('name') or '').upper()
    if 'ST' in name:
        return 4.8
    if code.startswith(('300', '301', '688', '689')):
        return 19.8
    if code.startswith(('8', '4', '920')):
        return 29.8
    return 9.8


def _is_st(row):
    return 'ST' in str(row.get('name') or '').upper()


def _valid_hs(rows):
    """沪深、非 ST、行情有效的股票行。"""
    seen = {}
    for r in rows:
        if hs_stock(r) and not _is_st(r) and r.get('change') is not None and r.get('price'):
            seen[r['code']] = r
    return seen


def _sealed(row):
    return row['change'] >= _threshold(row)


def _limit_down(row):
    return row['change'] <= -_threshold(row)


def _broken(row):
    """炸板近似：盘中最高触及涨停价但收盘未封。prev_close 由收盘涨跌幅反推。"""
    thr = _threshold(row)
    high, close, change = row.get('high'), row.get('price'), row.get('change')
    if not high or not close or change is None or change >= thr:
        return False
    prev = close / (1 + change / 100) if change > -100 else None
    if not prev:
        return False
    return high >= prev * (1 + thr / 100) * 0.999


def _drawdown(row):
    high, close = row.get('high'), row.get('price')
    if not high or not close or high <= 0:
        return None
    return (high - close) / high * 100


def _kline_stats(rows):
    """日 K 近似统计：涨停/跌停/炸板/大面。"""
    ups = _valid_hs(rows)
    sealed = [r for r in ups.values() if _sealed(r)]
    limit_dn = [r for r in ups.values() if _limit_down(r)]
    broken = [r for r in ups.values() if _broken(r)]
    drawdowns = [r for r in ups.values() if (_drawdown(r) or 0) >= 10]
    return {'limit_up': len(sealed), 'limit_down': len(limit_dn),
            'broken': len(broken), 'drawdown': len(drawdowns),
            'sealed_codes': {r['code'] for r in sealed}}


def _streaks(store, dates):
    """逐日扫描 stocks 快照，重建每只股票到当天为止的连板数。

    返回 {date: {code: streak}}（仅含当日涨停的票）。
    """
    result, cur = {}, {}
    for d in sorted(dates):
        snap = store.snapshot('stocks', d, 'close')
        rows = (snap['payload'].get('rows') if snap else None) or []
        sealed = {c for c, r in _valid_hs(rows).items() if _sealed(r)}
        cur = {c: cur.get(c, 0) + 1 for c in sealed}
        result[d] = dict(cur)
    return result


def _leaders_change(store, date, prev_streak):
    """龙头当日涨跌幅列表 + 数据来源说明。

    情绪核心池非空→池内股票；池空→昨日连板股（K 线重建 streak>=2）。
    返回 (changes: list[(change, limit_down)], note, count)。
    """
    pool = list_pools(store)['emotion']
    snap = store.snapshot('stocks', date, 'close')
    rows = (snap['payload'].get('rows') if snap else None) or []
    today = _valid_hs(rows)
    if pool:
        codes = [r['code'] for r in pool]
        note = 'emotion_pool'
    else:
        codes = [c for c, s in (prev_streak or {}).items() if s >= 2]
        note = 'kline_streak'
    out = []
    for c in codes:
        r = today.get(c)
        if r is not None:
            out.append((r['change'], _limit_down(r)))
    return out, note


def _breadth(store, date):
    snap = store.snapshot('market', date, 'close')
    if not snap:
        return None
    payload = snap['payload']
    stats = payload.get('stats') or {}
    return stats.get('up'), stats.get('down')


def _pool_stats(store, date):
    """pools 快照的精确统计（近两周）。无快照返回 None。"""
    snap = store.snapshot('pools', date, 'close')
    if not snap:
        return None
    stats = snap['payload'].get('stats') or {}
    if stats.get('limit_up') is None:
        return None
    return {'limit_up': stats.get('limit_up') or 0,
            'limit_down': stats.get('limit_down') or 0,
            'broken': stats.get('broken') or 0,
            'max_boards': stats.get('max_boards') or 0}


def _ice_dims(breadth, boards, leaders, leaders_note):
    dims = []
    if breadth:
        up_n, down_n = breadth
        n = down_n or 0
        if n < 3000:
            s = 0
        elif n < 3500:
            s = 0.5
        else:
            s = min(math.floor((n - 3500) / 500) + 1, 5)
        dims.append({'key': 'breadth', 'name': '下跌广度', 'score': s, 'input': n})
    if boards:
        lu, ld, broken = boards['limit_up'], boards['limit_down'], boards['broken']
        s = 0 if ld < 10 else min(math.floor((ld - 10) / 10) + 1, 4)
        bonus = 2 if ld > lu else 0
        dims.append({'key': 'limit_down', 'name': '跌停个数', 'score': min(s + bonus, 6),
                     'input': ld, 'bonus': bonus})
        sample = lu + broken
        if sample >= 10:
            rate = lu / sample * 100
            s = 0 if rate >= 70 else (1 if rate >= 55 else 2)
            dims.append({'key': 'seal_rate', 'name': '封板率(冰)', 'score': s, 'input': round(rate, 1)})
    if leaders is not None:
        changes = leaders
        if changes:
            avg = sum(c for c, _ in changes) / len(changes)
            s1 = 1 if avg <= -5 else 0
            s2 = 2 if any(dn for _, dn in changes) else 0
            dims.append({'key': 'leaders', 'name': '龙头表现(冰)', 'score': max(s1, s2),
                         'input': round(avg, 2), 'count': len(changes), 'note': leaders_note})
    if boards:
        dd = boards['drawdown']
        dims.append({'key': 'drawdown', 'name': '大面个数', 'score': 2 if dd > 20 else (1 if dd > 10 else 0),
                     'input': dd})
    return dims


def _climax_dims(breadth, boards, leaders, leaders_note, max_boards):
    dims = []
    if breadth:
        up_n, _ = breadth
        n = up_n or 0
        s = 0 if n < 4000 else min(math.floor((n - 4000) / 300) + 1, 4)
        dims.append({'key': 'breadth', 'name': '上涨广度', 'score': s, 'input': n})
    if boards:
        lu, broken = boards['limit_up'], boards['broken']
        s = 0 if lu < 65 else min(math.floor((lu - 65) / 15) + 1, 4)
        dims.append({'key': 'limit_up', 'name': '涨停个数', 'score': s, 'input': lu})
        sample = lu + broken
        if sample >= 10:
            rate = lu / sample * 100
            dims.append({'key': 'seal_rate', 'name': '封板率(潮)', 'score': 1 if rate > 90 else 0,
                         'input': round(rate, 1)})
    if leaders is not None:
        changes = leaders
        if changes:
            avg = sum(c for c, _ in changes) / len(changes)
            s = 2 if avg > 8 else (1 if avg > 5 else 0)
            dims.append({'key': 'leaders', 'name': '龙头表现(潮)', 'score': s,
                         'input': round(avg, 2), 'count': len(changes), 'note': leaders_note})
    if max_boards is not None:
        s = 0 if max_boards < 8 else min(max_boards - 7, 4)
        dims.append({'key': 'max_boards', 'name': '最高板', 'score': s, 'input': max_boards})
    return dims


def _sum(dims):
    if not dims:
        return None
    return round(sum(d['score'] for d in dims), 1)


def compute(store, date, prev_streak=None, today_streak=None):
    """计算某一天的冰点分/高潮分，返回落库行 dict；数据不足返回 None。"""
    breadth = _breadth(store, date)
    snap = store.snapshot('stocks', date, 'close')
    if not breadth and not snap:
        return None
    rows = (snap['payload'].get('rows') if snap else None) or []
    kstats = _kline_stats(rows) if rows else None
    pstats = _pool_stats(store, date)
    source = 'pool' if pstats else 'kline'

    boards = None
    max_boards = None
    if pstats:
        boards = {'limit_up': pstats['limit_up'], 'limit_down': pstats['limit_down'],
                  'broken': pstats['broken'], 'drawdown': kstats['drawdown'] if kstats else None}
        max_boards = pstats['max_boards']
    elif kstats:
        boards = kstats
        if today_streak is not None:
            max_boards = max(today_streak.values(), default=0)
    if boards and boards.get('drawdown') is None:
        boards = dict(boards, drawdown=kstats['drawdown'] if kstats else 0)

    leaders, leaders_note = _leaders_change(store, date, prev_streak)

    ice_dims = _ice_dims(breadth, boards, leaders, leaders_note)
    climax_dims = _climax_dims(breadth, boards, leaders, leaders_note, max_boards)
    revision = 0
    msnap = store.snapshot('market', date, 'close')
    if msnap:
        revision = msnap['id']
    return {'trade_date': date,
            'ice': _sum(ice_dims),
            'climax': _sum(climax_dims),
            'subs': json.dumps({'ice': ice_dims, 'climax': climax_dims}, ensure_ascii=False),
            'source': source,
            'revision': revision}


def _ensure_schema(db):
    db.executescript(SCHEMA)
    db.commit()


def _upsert(store, row):
    with store.connect() as db:
        _ensure_schema(db)
        db.execute(
            "INSERT INTO emotion_scores(trade_date, ice, climax, subs, source, revision, computed_at)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(trade_date) DO UPDATE SET ice=excluded.ice, climax=excluded.climax,"
            " subs=excluded.subs, source=excluded.source, revision=excluded.revision,"
            " computed_at=excluded.computed_at",
            (row['trade_date'], row['ice'], row['climax'], row['subs'], row['source'],
             row['revision'], now_iso()))
        db.commit()


def daily(store, date):
    """单日读缓存或重算。市场快照有更新（revision 变化）时重算。"""
    with store.connect() as db:
        _ensure_schema(db)
        cached = db.execute("SELECT * FROM emotion_scores WHERE trade_date=?", (date,)).fetchone()
    msnap = store.snapshot('market', date, 'close')
    revision = msnap['id'] if msnap else 0
    if cached and cached['revision'] == revision:
        return dict(cached)
    d = datetime.strptime(date, '%Y-%m-%d').date()
    span = [(d - timedelta(days=i)).isoformat() for i in range(STREAK_DAYS, -1, -1)]
    streaks = _streaks(store, span)
    row = compute(store, date, prev_streak=streaks.get(span[-2]), today_streak=streaks.get(date))
    if not row:
        return None
    _upsert(store, row)
    return row


def history(store, limit=250):
    """返回最近 limit 天的双分序列（升序）；缺日期的懒回补落库。"""
    dates = sorted(d['trade_date'] for d in store.dates() if d['mode'] == 'close')
    dates = dates[-limit:] if limit else dates
    if not dates:
        return []
    with store.connect() as db:
        _ensure_schema(db)
        cached = {r['trade_date']: dict(r) for r in db.execute(
            f"SELECT * FROM emotion_scores WHERE trade_date IN ({','.join('?' * len(dates))})",
            dates).fetchall()}
    d0 = datetime.strptime(dates[0], '%Y-%m-%d').date()
    span = [(d0 - timedelta(days=i)).isoformat() for i in range(STREAK_DAYS, 0, -1)] + list(dates)
    streaks = _streaks(store, span)
    rows = []
    for i, date in enumerate(dates):
        msnap = store.snapshot('market', date, 'close')
        revision = msnap['id'] if msnap else 0
        hit = cached.get(date)
        if hit and hit['revision'] == revision:
            rows.append(hit)
            continue
        prev = streaks.get(dates[i - 1]) if i else streaks.get(span[STREAK_DAYS - 1])
        row = compute(store, date, prev_streak=prev, today_streak=streaks.get(date))
        if row:
            _upsert(store, row)
            rows.append(row)
    return [{'date': r['trade_date'], 'ice': r['ice'], 'climax': r['climax'],
             'source': r['source']} for r in rows]
