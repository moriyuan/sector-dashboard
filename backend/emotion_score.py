"""冰点分 / 高潮分 双打分体系（2026-11-10 数据驱动全维度重校准，口径用户拍板）。

冰点分越大市场情绪越差，高潮分越大市场越极致火热。
打分方式：各维度在锚点间分段线性映射、按 0.5 取整（两端钳制）；
缺维度置空不计入总分。锚点经 126 个交易日真实数据分位数校准（2026-11-10）。

冰点分（8 维，满分 24）
  1. 下跌广度：<3000→0；3000~3599→0.5；≥3600 起线性 3600/4000/4400/4800/5000 → 1~5
  2. 跌停个数：<15→0；15/25/40/60 → 1~4 线性；若跌停 > 涨停 +2，合计封顶 6
  3. 封板率：样本(涨停+炸板) <10→置空；66%→0，58%→1，50%→2，区间线性
  4. 龙头表现：情绪核心池非空→池内股票；池空→昨日 K 线连板股；样本 ≥3 只才记分。
     平均跌幅 -5%→1、-8%→2 线性；任一跌停→2；取大
  5. 大面个数：(最高价-收盘价)/最高价 ≥10% 的股票数，10/20/40 → 1~3 线性
  6. 跌停放大：今日跌停 ÷ 近 5 日跌停均值（样本 ≥3 天且均值 >0），1.8/3 倍 → 1~2 线性
  7. 昨涨停今表现：昨日涨停股今日均涨幅（样本 ≥5 只），<0%→1、-1.5%→1.5、-2.5%→2 线性
  8. 昨连板今表现：昨日连板股（streak≥2）今日均涨幅（样本 ≥3 只），<0%→1、-1.5%→1.5、-3%→2 线性

高潮分（10 维，满分 29）
  1. 上涨广度：<3800→0；3800/4200/4600/5000 → 1~4 线性
  2. 涨停个数：<70→0；70/85/100/115 → 1~4 线性；
     加成：涨停股成交额 ÷ 近 5 日均值（样本 ≥3 天且均值 >0），2/3 倍 → +1/+2 两档，
     叠加在个数分上，本维合计最高 6（用户拍板 2026-11-09）
  3. 封板率：样本 <10→置空；80%→1，85%→2，区间线性
  4. 龙头表现：同一批龙头，样本 ≥3 只才记分；均涨 3%→1、6%→2 线性
  5. 最高板：6/7/8/9+ 板 → 1/2/3/4；当日出现绕异动高度股
     （10 日涨幅 ≥100% 或 30 日涨幅 ≥200%）额外 +1，本维合计最高 5
  6. 高位股群：当日 3 板及以上家数（K 线连板重建），5 家→1、7 家→2 线性
  7. 连板晋级率：昨日连板股（streak≥2，样本 ≥2 只）今日仍涨停比例，45%/60% → 1~2 线性
  8. 涨停放大：今日涨停 ÷ 近 5 日涨停均值（样本 ≥3 天且均值 >0），1.3/1.7 倍 → 1~2 线性
  9. 昨涨停今表现：昨日涨停股今日均涨幅（样本 ≥5 只），>2%→1、>3%→1.5、>4%→2 线性
 10. 昨连板今表现：昨日连板股（streak≥2）今日均涨幅（样本 ≥3 只），>3%→1、>4%→1.5、>5%→2 线性

近两周（有 pools 快照）用池数据精确计；更早日期用日 K 近似回补，
涨停/跌停按板块阈值近似（主板 9.8%、双创 19.8%、北交所 29.8%、ST 4.8%），
炸板=盘中触及涨停价但未封住，最高板与连板=逐股重建；source 标注 pool / kline。
新股上市初期、复牌首日等无涨跌幅限制的异动（|涨跌幅| > 1.5×板块阈值）
不计入涨跌停、连板与龙头统计。
涨停股成交额统一用日 K 近似涨停口径加总（池数据期同口径），保证跨期比值可比。
"""
import json
import math

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


def _no_limit(row):
    """当日无涨跌幅限制（新股上市初期、复牌首日等）：涨跌幅超出制度阈值 1.5 倍。

    这类异动不是真涨停/跌停，计入会污染涨跌停统计、连板重建与龙头均值。
    """
    change = row.get('change')
    if change is None:
        return False
    return abs(change) > _threshold(row) * 1.5


def _valid_hs(rows):
    """沪深、非 ST、行情有效、当日有涨跌幅限制的股票行。"""
    seen = {}
    for r in rows:
        if (hs_stock(r) and not _is_st(r) and not _no_limit(r)
                and r.get('change') is not None and r.get('price')):
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
    """日 K 近似统计：涨停/跌停/炸板/大面 + 涨停股成交额。"""
    ups = _valid_hs(rows)
    sealed = [r for r in ups.values() if _sealed(r)]
    limit_dn = [r for r in ups.values() if _limit_down(r)]
    broken = [r for r in ups.values() if _broken(r)]
    drawdowns = [r for r in ups.values() if (_drawdown(r) or 0) >= 10]
    lu_amount = sum(r.get('amount') or 0 for r in sealed)
    return {'limit_up': len(sealed), 'limit_down': len(limit_dn),
            'broken': len(broken), 'drawdown': len(drawdowns),
            'sealed_codes': {r['code'] for r in sealed},
            'lu_amount': lu_amount}


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


def _streak_span(store, start, end=None):
    """连板统计用的交易日序列（升序）：start 之前 30 个 stocks 收盘交易日起，
    至 end（默认 start）止。只含真实交易日——连板跨周末/假期延续。
    （30 个交易日 ≈ 42 个自然日，覆盖 STREAK_DAYS 的回看语义。）"""
    with store.connect() as db:
        tdays = [r[0] for r in db.execute(
            "SELECT trade_date FROM snapshots WHERE kind='stocks' AND mode='close'"
            ' AND trade_date<=? GROUP BY trade_date ORDER BY trade_date',
            (end or start,)).fetchall()]
    lead = [d for d in tdays if d < start][-30:]
    return lead + [d for d in tdays if d >= start]


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


def _yday_perf(store, date, rows, prev_streak):
    """昨日涨停/连板股的今日均涨幅（赚钱效应/亏钱效应）。

    昨日涨停名单按昨日 stocks 快照的日 K 近似口径（已过滤无涨跌幅限制异动），
    昨日连板名单取 prev_streak 中 streak≥2 的股票；今日涨跌幅取今日有效报价。
    返回 {'lu': (avg, n), 'streak': (avg, n)}，缺数据或样本为空对应项为 None。
    """
    if not rows:
        return None
    with store.connect() as db:
        r = db.execute(
            "SELECT MAX(trade_date) d FROM snapshots "
            "WHERE kind='stocks' AND mode='close' AND trade_date<?", (date,)).fetchone()
    if not r or not r['d']:
        return None
    prev_snap = store.snapshot('stocks', r['d'], 'close')
    prev_rows = (prev_snap['payload'].get('rows') if prev_snap else None) or []
    if not prev_rows:
        return None
    today = _valid_hs(rows)
    out = {}
    sealed = _kline_stats(prev_rows)['sealed_codes']
    lu_changes = [today[c]['change'] for c in sealed if c in today]
    out['lu'] = (round(sum(lu_changes) / len(lu_changes), 2), len(lu_changes)) if lu_changes else None
    st_codes = [c for c, s in (prev_streak or {}).items() if s >= 2]
    st_changes = [today[c]['change'] for c in st_codes if c in today]
    out['streak'] = (round(sum(st_changes) / len(st_changes), 2), len(st_changes)) if st_changes else None
    return out


def _abnormal_height(store, date, rows):
    """绕异动高度股家数：当日存在 10 日涨幅 ≥100% 或 30 日涨幅 ≥200% 的个股。

    监管严、连板高度难拓开时，资金常用「断板走趋势」绕开异动监管线打高度，
    这类票是高度的替代信号。基准日为前第 10 / 第 30 个收盘日（stocks 快照）。
    数据不足（前序快照 <10 天）返回 0。
    """
    if not rows:
        return 0
    with store.connect() as db:
        prev_days = [r['trade_date'] for r in db.execute(
            "SELECT DISTINCT trade_date FROM snapshots "
            "WHERE kind='stocks' AND mode='close' AND trade_date<? "
            "ORDER BY trade_date DESC LIMIT 30", (date,)).fetchall()]
    if len(prev_days) < 10:
        return 0
    # 今日侧排除无涨跌幅限制的异动行（新股/复牌首日暴涨不是绕异动高度）
    today_px = {r['code']: r['price'] for r in rows if r.get('price') and not _no_limit(r)}

    def _px_map(day):
        snap = store.snapshot('stocks', day, 'close')
        prows = (snap['payload'].get('rows') if snap else None) or []
        return {r['code']: r['price'] for r in prows if r.get('price')}

    d10 = _px_map(prev_days[9])
    hit = {c for c, p in today_px.items() if d10.get(c) and p / d10[c] - 1 >= 1.0}
    if len(prev_days) >= 30:
        d30 = _px_map(prev_days[29])
        hit |= {c for c, p in today_px.items() if d30.get(c) and p / d30[c] - 1 >= 2.0}
    return len(hit)


def _ice_dims(breadth, boards, leaders, leaders_note, prev5_ld=None, yday=None):
    dims = []
    if breadth:
        up_n, down_n = breadth
        n = down_n or 0
        if n < 3000:
            s = 0
        elif n < 3600:
            s = 0.5
        else:
            s = _lerp(n, [(3600, 1), (4000, 2), (4400, 3), (4800, 4), (5000, 5)])
        dims.append({'key': 'breadth', 'name': '下跌广度', 'score': s, 'input': n})
    if boards:
        lu, ld, broken = boards['limit_up'], boards['limit_down'], boards['broken']
        s = 0 if ld < 15 else _lerp(ld, [(15, 1), (25, 2), (40, 3), (60, 4)])
        bonus = 2 if ld > lu else 0
        dims.append({'key': 'limit_down', 'name': '跌停个数', 'score': min(s + bonus, 6),
                     'input': ld, 'bonus': bonus})
        sample = lu + broken
        if sample >= 10:
            rate = lu / sample * 100
            dims.append({'key': 'seal_rate', 'name': '封板率(冰)',
                         'score': _lerp(rate, [(50, 2), (58, 1), (66, 0)]),
                         'input': round(rate, 1)})
        surge = _surge(ld, prev5_ld)
        if surge:
            ratio, base = surge
            s = 0 if ratio <= 1.8 else _lerp(ratio, [(1.8, 1), (3, 2)])
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
        s = 0 if dd <= 10 else _lerp(dd, [(10, 1), (20, 2), (40, 3)])
        dims.append({'key': 'drawdown', 'name': '大面个数', 'score': s, 'input': dd})
    if yday:
        if yday.get('lu'):
            avg, n = yday['lu']
            if n >= 5:
                s = 0 if avg >= 0 else _lerp(-avg, [(0, 1), (1.5, 1.5), (2.5, 2)])
                dims.append({'key': 'lu_yday', 'name': '昨涨停今表现', 'score': s,
                             'input': avg, 'count': n})
        if yday.get('streak'):
            avg, n = yday['streak']
            if n >= 3:
                s = 0 if avg >= 0 else _lerp(-avg, [(0, 1), (1.5, 1.5), (3, 2)])
                dims.append({'key': 'st_yday', 'name': '昨连板今表现', 'score': s,
                             'input': avg, 'count': n})
    return dims


def _climax_dims(breadth, boards, leaders, leaders_note, max_boards,
                 promote=None, prev5_lu=None, prev5_lu_amount=None, yday=None,
                 abnormal=None, hi3=None):
    dims = []
    if breadth:
        up_n, _ = breadth
        n = up_n or 0
        s = 0 if n < 3800 else _lerp(n, [(3800, 1), (4200, 2), (4600, 3), (5000, 4)])
        dims.append({'key': 'breadth', 'name': '上涨广度', 'score': s, 'input': n})
    if boards:
        lu, broken = boards['limit_up'], boards['broken']
        s = 0 if lu < 70 else _lerp(lu, [(70, 1), (85, 2), (100, 3), (115, 4)])
        dim = {'key': 'limit_up', 'name': '涨停个数', 'score': s, 'input': lu}
        # 成交额加成：今日涨停股成交额 ÷ 近 5 日均值，2/3 倍 → +1/+2 两档，
        # 叠加在个数分上，本维合计最高 6（用户拍板 2026-11-09）
        lu_amount = boards.get('lu_amount')
        if lu_amount is not None:
            dim['lu_amount'] = round(lu_amount / 1e8, 2)  # 亿元
        bonus = 0
        surge = _surge(lu_amount or 0, prev5_lu_amount) if lu_amount else None
        if surge:
            ratio, base = surge
            dim['amount_ratio'] = round(ratio, 2)
            dim['amount_base'] = round(base / 1e8, 2)     # 近5日均值，亿元
            bonus = 0 if ratio < 2 else _lerp(ratio, [(2, 1), (3, 2)])
            if bonus:
                dim['bonus'] = bonus
        dim['score'] = min(s + bonus, 6)
        dims.append(dim)
        sample = lu + broken
        if sample >= 10:
            rate = lu / sample * 100
            dims.append({'key': 'seal_rate', 'name': '封板率(潮)',
                         'score': 0 if rate <= 80 else _lerp(rate, [(80, 1), (85, 2)]),
                         'input': round(rate, 1)})
        surge = _surge(lu, prev5_lu)
        if surge:
            ratio, base = surge
            s = 0 if ratio <= 1.3 else _lerp(ratio, [(1.3, 1), (1.7, 2)])
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
        # 6 板起步，6 / 7 / 8 / 9+ 四档（整数保持阶梯）；
        # 绕异动高度（10 日 ≥100% 或 30 日 ≥200% 个股出现）额外 +1，合计封顶 5
        s = 0 if max_boards < 6 else min(max_boards - 5, 4)
        dim = {'key': 'max_boards', 'name': '最高板', 'input': max_boards}
        bonus = 1 if abnormal else 0
        if abnormal:
            dim['abnormal'] = abnormal
        if bonus:
            dim['bonus'] = bonus
        dim['score'] = min(s + bonus, 5)
        dims.append(dim)
    if hi3 is not None:
        # 高位股群：当日 3 板及以上家数，5 家→1、7 家→2 线性
        dims.append({'key': 'hi3', 'name': '高位股群',
                     'score': 0 if hi3 < 5 else _lerp(hi3, [(5, 1), (7, 2)]),
                     'input': hi3})
    if promote:
        rate, count = promote
        dims.append({'key': 'promote', 'name': '连板晋级率',
                     'score': 0 if rate <= 45 else _lerp(rate, [(45, 1), (60, 2)]),
                     'input': round(rate, 1), 'count': count})
    if yday:
        if yday.get('lu'):
            avg, n = yday['lu']
            if n >= 5:
                s = 0 if avg <= 2 else _lerp(avg, [(2, 1), (3, 1.5), (4, 2)])
                dims.append({'key': 'lu_yday', 'name': '昨涨停今表现', 'score': s,
                             'input': avg, 'count': n})
        if yday.get('streak'):
            avg, n = yday['streak']
            if n >= 3:
                s = 0 if avg <= 3 else _lerp(avg, [(3, 1), (4, 1.5), (5, 2)])
                dims.append({'key': 'st_yday', 'name': '昨连板今表现', 'score': s,
                             'input': avg, 'count': n})
    return dims


def _sum(dims):
    if not dims:
        return None
    return round(sum(d['score'] for d in dims), 1)


def compute(store, date, prev_streak=None, today_streak=None, prev5=None):
    """计算某一天的冰点分/高潮分，返回落库行 dict；数据不足返回 None。

    prev5: 近 5 个收盘日的 [(limit_up, limit_down, lu_amount|None), ...]，
    用于涨停/跌停放大维度与涨停成交额加成。
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
                  'broken': pstats['broken'], 'drawdown': kstats['drawdown'] if kstats else None,
                  'lu_amount': kstats['lu_amount'] if kstats else None}
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

    prev5_lu = [t[0] for t in (prev5 or [])]
    prev5_ld = [t[1] for t in (prev5 or [])]
    prev5_amt = [t[2] for t in (prev5 or []) if t[2] is not None]
    yday = _yday_perf(store, date, rows, prev_streak)
    abnormal = _abnormal_height(store, date, rows)
    hi3 = None
    if today_streak is not None:
        hi3 = sum(1 for s in today_streak.values() if s >= 3)
    ice_dims = _ice_dims(breadth, boards, leaders, leaders_note, prev5_ld=prev5_ld,
                         yday=yday)
    climax_dims = _climax_dims(breadth, boards, leaders, leaders_note, max_boards,
                               promote=promote, prev5_lu=prev5_lu,
                               prev5_lu_amount=prev5_amt, yday=yday,
                               abnormal=abnormal, hi3=hi3)
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
    """从缓存行的 subs JSON 提取 (涨停数, 跌停数, 涨停成交额元|None)；解析失败返回 None。"""
    try:
        subs = json.loads(row['subs'])
    except (TypeError, ValueError):
        return None
    lu = ld = amt = None
    for d in subs.get('climax') or []:
        if d.get('key') == 'limit_up':
            lu = d.get('input')
            if d.get('lu_amount') is not None:
                amt = d['lu_amount'] * 1e8  # subs 里存亿元，还原为元
    for d in subs.get('ice') or []:
        if d.get('key') == 'limit_down':
            ld = d.get('input')
    if lu is None or ld is None:
        return None
    return lu, ld, amt


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
    span = _streak_span(store, date)
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
    row = compute(store, date, prev_streak=streaks.get(span[-2]) if len(span) >= 2 else None,
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
    span = _streak_span(store, dates[0], dates[-1])
    streaks = _streaks(store, span)
    lead = [d for d in span if d < dates[0]]  # 窗口前的种子交易日
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
        prev = streaks.get(dates[i - 1]) if i else (streaks.get(lead[-1]) if lead else None)
        row = compute(store, date, prev_streak=prev, today_streak=streaks.get(date),
                      prev5=counts[-5:])
        if row:
            _upsert(store, row)
            rows.append(row)
            v = _lu_ld_of(row)
            if v is not None:
                counts.append(v)
    out = []
    for r in rows:
        item = {'date': r['trade_date'], 'ice': r['ice'], 'climax': r['climax'],
                'source': r['source']}
        try:
            item['subs'] = json.loads(r['subs']) if r.get('subs') else None
        except (ValueError, TypeError):
            item['subs'] = None
        out.append(item)
    return out
