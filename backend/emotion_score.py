"""冰点分 / 高潮分 双打分体系（2026-11-09 平滑版，口径用户拍板）。

冰点分越大市场情绪越差，高潮分越大市场越极致火热。
打分方式：各维度在锚点间分段线性映射、按 0.5 取整（两端钳制）；
缺维度置空不计入总分。

冰点分（6 维，满分 19）
  1. 下跌广度：<3000→0；3000~3499→0.5；≥3500 起线性 3500/4000/4500/5000/5500 → 1~5
  2. 跌停个数：<10→0；10/20/30/40 → 1~4 线性；若跌停 > 涨停 +2，合计封顶 6
  3. 封板率：样本(涨停+炸板) <10→置空；70%→0，55%→2，区间线性
  4. 龙头表现：情绪核心池非空→池内股票；池空→昨日 K 线连板股；样本 ≥3 只才记分。
     平均跌幅 -5%→1、-8%→2 线性；任一跌停→2；取大
  5. 大面个数：(最高价-收盘价)/最高价 ≥10% 的股票数，10/20 → 1~2 线性
  6. 跌停放大：今日跌停 ÷ 近 5 日跌停均值（样本 ≥3 天且均值 >0），1.5/2 倍 → 1~2 线性

高潮分（7 维，满分 20）
  1. 上涨广度：<4000→0；4000/4334/4667/5000 → 1~4 线性
  2. 涨停个数：<65→0；65/84/102/120 → 1~4 线性
  3. 封板率：样本 <10→置空；82%→1，86%→2，区间线性
  4. 龙头表现：同一批龙头，样本 ≥3 只才记分；均涨 3%→1、6%→2 线性
  5. 最高板：6/7/8/9+ 板 → 1/2/3/4
  6. 连板晋级率：昨日连板股（streak≥2，样本 ≥2 只）今日仍涨停比例，40%/60% → 1~2 线性
  7. 涨停放大：今日涨停 ÷ 近 5 日涨停均值（样本 ≥3 天且均值 >0），1.5/2 倍 → 1~2 线性

近两周（有 pools 快照）用池数据精确计；更早日期用日 K 近似回补，
涨停/跌停按板块阈值近似（主板 9.8%、双创 19.8%、北交所 29.8%、ST 4.8%），
炸板=盘中触及涨停价但未封住，最高板与连板=逐股重建；source 标注 pool / kline。
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


def _lerp(value, anchors):
    """分段线性映射：anchors=[(x,分),...] 按 x 升序，两端钳制，结果按 0.5 取整。"""
    if value <= anchors[0][0]:
        s = anchors[0][1]
    else:
        s = anchors[-1][1]
        for (xa, sa), (xb, sb) in zip(anchors, anchors[1:]):
            if value <= xb:
                s = sa + (sb - sa) * (value - xa) / (xb - xa)
                break
    return round(s * 2) / 2


def _surge(today, prev5):
    """今日值 ÷ 近 5 日均值 → (ratio, base)；样本 <3 天或均值为 0 返回 None。"""
    if not prev5 or len(prev5) < 3:
        return None
    base = sum(prev5) / len(prev5)
    if base <= 0:
        return None
    return today / base, base


def _ice_dims(breadth, boards, leaders, leaders_note, prev5_ld=None):
    dims = []
    if breadth:
        up_n, down_n = breadth
        n = down_n or 0
        if n < 3000:
            s = 0
        elif n < 3500:
            s = 0.5
        else:
            s = _lerp(n, [(3500, 1), (4000, 2), (4500, 3), (5000, 4), (5500, 5)])
        dims.append({'key': 'breadth', 'name': '下跌广度', 'score': s, 'input': n})
    if boards:
        lu, ld, broken = boards['limit_up'], boards['limit_down'], boards['broken']
        s = 0 if ld < 10 else _lerp(ld, [(10, 1), (20, 2), (30, 3), (40, 4)])
        bonus = 2 if ld > lu else 0
        dims.append({'key': 'limit_down', 'name': '跌停个数', 'score': min(s + bonus, 6),
                     'input': ld, 'bonus': bonus})
        sample = lu + broken
        if sample >= 10:
            rate = lu / sample * 100
            dims.append({'key': 'seal_rate', 'name': '封板率(冰)',
                         'score': _lerp(rate, [(55, 2), (62.5, 1), (70, 0)]),
                         'input': round(rate, 1)})
        surge = _surge(ld, prev5_ld)
        if surge:
            ratio, base = surge
            s = 0 if ratio <= 1.5 else _lerp(ratio, [(1.5, 1), (2, 2)])
            dims.append({'key': 'ld_surge', 'name': '跌停放大', 'score': s,
                         'input': round(ratio, 2), 'base': round(base, 1)})
    if leaders is not None:
        changes = leaders
        if len(changes) >= 3:  # 样本太小不记分，防单股噪声
            avg = sum(c for c, _ in changes) / len(changes)
            s1 = 0 if avg > -5 else _lerp(-avg, [(5, 1), (8, 2)])
            s2 = 2 if any(dn for _, dn in changes) else 0
            dims.append({'key': 'leaders', 'name': '龙头表现(冰)', 'score': max(s1, s2),
                         'input': round(avg, 2), 'count': len(changes), 'note': leaders_note})
    if boards:
        dd = boards['drawdown'] or 0
        s = 0 if dd <= 10 else _lerp(dd, [(10, 1), (20, 2)])
        dims.append({'key': 'drawdown', 'name': '大面个数', 'score': s, 'input': dd})
    return dims


def _climax_dims(breadth, boards, leaders, leaders_note, max_boards,
                 promote=None, prev5_lu=None):
    dims = []
    if breadth:
        up_n, _ = breadth
        n = up_n or 0
        # 用户拍板档位 4000/4334/4667/5000 → 1~4 线性，<4000→0，>5000 封顶 4
        s = 0 if n < 4000 else _lerp(n, [(4000, 1), (4334, 2), (4667, 3), (5000, 4)])
        dims.append({'key': 'breadth', 'name': '上涨广度', 'score': s, 'input': n})
    if boards:
        lu, broken = boards['limit_up'], boards['broken']
        # 65 起步，84 / 102 / 120 → 1~4 线性，超过 120 封顶 4
        s = 0 if lu < 65 else _lerp(lu, [(65, 1), (84, 2), (102, 3), (120, 4)])
        dims.append({'key': 'limit_up', 'name': '涨停个数', 'score': s, 'input': lu})
        sample = lu + broken
        if sample >= 10:
            rate = lu / sample * 100
            dims.append({'key': 'seal_rate', 'name': '封板率(潮)',
                         'score': 0 if rate <= 82 else _lerp(rate, [(82, 1), (86, 2)]),
                         'input': round(rate, 1)})
        surge = _surge(lu, prev5_lu)
        if surge:
            ratio, base = surge
            s = 0 if ratio <= 1.5 else _lerp(ratio, [(1.5, 1), (2, 2)])
            dims.append({'key': 'lu_surge', 'name': '涨停放大', 'score': s,
                         'input': round(ratio, 2), 'base': round(base, 1)})
    if leaders is not None:
        changes = leaders
        if len(changes) >= 3:  # 样本太小不记分，防单股噪声
            avg = sum(c for c, _ in changes) / len(changes)
            s = 0 if avg <= 3 else _lerp(avg, [(3, 1), (6, 2)])
            dims.append({'key': 'leaders', 'name': '龙头表现(潮)', 'score': s,
                         'input': round(avg, 2), 'count': len(changes), 'note': leaders_note})
    if max_boards is not None:
        # 6 板起步，6 / 7 / 8 / 9+ 四档（整数保持阶梯）
        s = 0 if max_boards < 6 else min(max_boards - 5, 4)
        dims.append({'key': 'max_boards', 'name': '最高板', 'score': s, 'input': max_boards})
    if promote:
        rate, count = promote
        dims.append({'key': 'promote', 'name': '连板晋级率',
                     'score': 0 if rate <= 40 else _lerp(rate, [(40, 1), (60, 2)]),
                     'input': round(rate, 1), 'count': count})
    return dims


def _sum(dims):
    if not dims:
        return None
    return round(sum(d['score'] for d in dims), 1)


def compute(store, date, prev_streak=None, today_streak=None, prev5=None):
    """计算某一天的冰点分/高潮分，返回落库行 dict；数据不足返回 None。

    prev5: 近 5 个收盘日的 [(limit_up, limit_down), ...]，用于涨停/跌停放大维度。
    """
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

    promote = None
    if kstats is not None and prev_streak:
        # 连板晋级率：昨日连板股（streak>=2，样本 >=2 只）今日仍在涨停的比例
        cands = [c for c, s in prev_streak.items() if s >= 2]
        if len(cands) >= 2:
            hit = sum(1 for c in cands if c in kstats['sealed_codes'])
            promote = (hit / len(cands) * 100, len(cands))

    prev5_lu = [lu for lu, _ in (prev5 or [])]
    prev5_ld = [ld for _, ld in (prev5 or [])]
    ice_dims = _ice_dims(breadth, boards, leaders, leaders_note, prev5_ld=prev5_ld)
    climax_dims = _climax_dims(breadth, boards, leaders, leaders_note, max_boards,
                               promote=promote, prev5_lu=prev5_lu)
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


def _lu_ld_of(row):
    """从缓存行的 subs JSON 提取 (涨停数, 跌停数)；解析失败返回 None。"""
    try:
        subs = json.loads(row['subs'])
    except (TypeError, ValueError):
        return None
    lu = ld = None
    for d in subs.get('climax') or []:
        if d.get('key') == 'limit_up':
            lu = d.get('input')
    for d in subs.get('ice') or []:
        if d.get('key') == 'limit_down':
            ld = d.get('input')
    if lu is None or ld is None:
        return None
    return lu, ld


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
    # 近 5 个收盘日的 (涨停, 跌停)，供涨停/跌停放大维度
    close_dates = sorted(x['trade_date'] for x in store.dates()
                         if x['mode'] == 'close' and x['trade_date'] < date)[-5:]
    prev5 = []
    if close_dates:
        with store.connect() as db:
            _ensure_schema(db)
            prev_rows = {r['trade_date']: dict(r) for r in db.execute(
                f"SELECT * FROM emotion_scores WHERE trade_date IN ({','.join('?' * len(close_dates))})",
                close_dates).fetchall()}
        prev5 = [v for v in (_lu_ld_of(prev_rows.get(x)) for x in close_dates)
                 if v is not None]
    row = compute(store, date, prev_streak=streaks.get(span[-2]),
                  today_streak=streaks.get(date), prev5=prev5)
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
    # 窗口前 5 个收盘日的缓存行，作为首日涨停/跌停放大维度的 prev5 种子
    seed_dates = sorted(x['trade_date'] for x in store.dates()
                        if x['mode'] == 'close' and x['trade_date'] < dates[0])[-5:]
    if seed_dates:
        with store.connect() as db:
            for r in db.execute(
                    f"SELECT * FROM emotion_scores WHERE trade_date IN ({','.join('?' * len(seed_dates))})",
                    seed_dates).fetchall():
                cached.setdefault(r['trade_date'], dict(r))
    counts = [v for v in (_lu_ld_of(cached.get(x)) for x in seed_dates) if v is not None]
    rows = []
    for i, date in enumerate(dates):
        msnap = store.snapshot('market', date, 'close')
        revision = msnap['id'] if msnap else 0
        hit = cached.get(date)
        if hit and hit['revision'] == revision:
            rows.append(hit)
            v = _lu_ld_of(hit)
            if v is not None:
                counts.append(v)
            continue
        prev = streaks.get(dates[i - 1]) if i else streaks.get(span[STREAK_DAYS - 1])
        row = compute(store, date, prev_streak=prev, today_streak=streaks.get(date),
                      prev5=counts[-5:])
        if row:
            _upsert(store, row)
            rows.append(row)
            v = _lu_ld_of(row)
            if v is not None:
                counts.append(v)
    return [{'date': r['trade_date'], 'ice': r['ice'], 'climax': r['climax'],
             'source': r['source']} for r in rows]
