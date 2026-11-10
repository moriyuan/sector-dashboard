"""市场综合强度：参考开盘啦官方披露的五个维度（涨跌家数、市场量能、涨停表现、大幅回撤、权重表现）
自研的近似算法。开盘啦官方公式未公开，本模块的权重与映射均为本项目自定，数值与开盘啦不可直接比较。

2026-11-10 重构（口径用户拍板，锚点经 127 个交易日真实数据分位数校准）：
- 涨停表现：pools 快照缺失日用日 K 近似回补（与冰点/高潮分同口径），127 天全程可比；
  维度内拆为 个数45% + 高度20%（最高板 6 成 + 高位股群 4 成 + 绕异动加成）+ 封板率15% + 赚钱效应20%
  （昨涨停/昨连板今表现，复用 emotion_score._yday_perf）。
- 市场量能带方向：放量上涨加分、放量下跌减分（方向取涨跌家数对比）。
- 大幅回撤与情绪分统一为「大面」口径：距当日最高价回撤 ≥10% 的家数，越多分越低。
- 权重表现：全部指数涨跌幅的中位数（不再只看上证）。
纪律：任一维度输入缺失时该维度留空（None），剩余维度按权重重新归一，绝不编造。"""
import math
from datetime import datetime, timedelta

from .emotion_score import (STREAK_DAYS, _abnormal_height, _kline_stats,
                            _streaks, _yday_perf)

WEIGHTS = {'breadth': 0.25, 'volume': 0.20, 'limit': 0.25, 'drawdown': 0.15, 'index': 0.15}
SUB_NAMES = {'breadth': '涨跌广度', 'volume': '市场量能', 'limit': '涨停表现', 'drawdown': '大幅回撤', 'index': '权重表现'}

# 数据驱动锚点（127 天真实分布，2026-11-10 校准）
LU_CNT_POINTS = [(0, 0), (40, 25), (68, 50), (90, 68), (115, 85), (150, 100)]  # 涨停家数：中位 68 / p90 114 / max 151
MB_POINTS = [(0, 0), (3, 10), (5, 35), (6, 50), (7, 65), (8, 80), (9, 100)]     # 最高板：中位 5 / p90 6
HI3_POINTS = [(0, 0), (3, 40), (5, 60), (7, 85), (10, 100)]                # 3 板及以上家数：中位 4 / p90 7
SEAL_POINTS = [(50, 0), (67, 50), (80, 85), (90, 100)]                     # 封板率%：中位 67
LU_YDAY_POINTS = [(-1, 10), (0, 35), (1.5, 55), (3, 75), (4.5, 100)]       # 昨涨停今表现%：中位 1.3 / p90 3.7
ST_YDAY_POINTS = [(-1, 10), (1, 40), (2.5, 55), (4, 75), (5.5, 100)]       # 昨连板今表现%：中位 2.3 / p90 5.6
DD_POINTS = [(0, 100), (5, 95), (18, 80), (40, 60), (110, 35), (250, 15), (500, 0)]  # 大面家数：中位 18 / p90 107 / max 811


def _tanh(x, scale):
    return 50 + 50 * math.tanh(x / scale)


def _clamp(v):
    return max(0.0, min(100.0, v))


def _piece(value, points):
    """分段线性映射到 0-100（不取整）：points=[(x, 分), ...] 按 x 升序，两端钳制。"""
    if value <= points[0][0]:
        return float(points[0][1])
    for (xa, sa), (xb, sb) in zip(points, points[1:]):
        if value <= xb:
            return sa + (sb - sa) * (value - xa) / (xb - xa)
    return float(points[-1][1])


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _limit_dim(store, date, pools, rows, today_streak, prev_streak):
    """涨停表现维度（0-100）：个数 45% + 高度 20% + 封板率 15% + 赚钱效应 20%（缺项归一）。

    pools 快照（近两周）给出精确涨停数/炸板/最高板；更早日期用日 K 近似回补。
    """
    if pools:
        p = pools['payload'].get('stats') or {}
        lu, broken, max_boards = p.get('limit_up'), p.get('broken'), p.get('max_boards')
        if lu is None:
            return None, None
        source = 'pool'
    elif rows:
        ks = _kline_stats(rows)
        lu, broken = ks['limit_up'], ks['broken']
        max_boards = max((today_streak or {}).values(), default=0)
        source = 'kline'
    else:
        return None, None

    inputs = {'source': source, 'limit_up': lu}
    parts = [(_piece(lu, LU_CNT_POINTS), 45)]

    sample = lu + (broken or 0)
    if sample >= 10:
        rate = lu / sample * 100
        parts.append((_piece(rate, SEAL_POINTS), 15))
        inputs['seal_rate'] = round(rate, 1)

    # 高度：最高板 6 成 + 高位股群 4 成，出现绕异动高度股（10 日 ≥100% / 30 日 ≥200%）+10
    height = _piece(max_boards or 0, MB_POINTS)
    inputs['max_boards'] = max_boards or 0
    if today_streak is not None:
        hi3 = sum(1 for s in today_streak.values() if s >= 3)
        inputs['hi3'] = hi3
        height = height * 0.6 + _piece(hi3, HI3_POINTS) * 0.4
        abnormal = _abnormal_height(store, date, rows) if rows else 0
        if abnormal:
            inputs['abnormal'] = abnormal
            height += 10
    parts.append((_clamp(height), 20))

    # 赚钱效应：昨涨停/昨连板今表现（复用情绪分口径），可得项取均值
    yd_scores = []
    yday = _yday_perf(store, date, rows, prev_streak) if rows else None
    if yday and yday.get('lu'):
        avg, n = yday['lu']
        if n >= 5:
            yd_scores.append(_piece(avg, LU_YDAY_POINTS))
            inputs['lu_yday'] = avg
    if yday and yday.get('streak'):
        avg, n = yday['streak']
        if n >= 3:
            yd_scores.append(_piece(avg, ST_YDAY_POINTS))
            inputs['st_yday'] = avg
    if yd_scores:
        parts.append((sum(yd_scores) / len(yd_scores), 20))

    total_w = sum(w for _, w in parts)
    return sum(s * w for s, w in parts) / total_w, inputs


def _compute(store, date, prev_streak=None, today_streak=None):
    """返回 {'score','subs','inputs',...}；数据不足时返回 None。"""
    market = store.snapshot('market', date, 'close')
    if not market:
        return None
    stats = market['payload'].get('stats') or {}
    pools = store.snapshot('pools', date, 'close')
    indices = store.snapshot('indices', date, 'close')
    stocks = store.snapshot('stocks', date, 'close')
    rows = (stocks['payload'].get('rows') if stocks else None) or []
    subs, inputs = {}, {}

    # 涨跌广度：涨跌幅家数差占比，映射到 0-100
    up, down, flat = stats.get('up'), stats.get('down'), stats.get('flat')
    if None not in (up, down, flat) and (up + down + flat):
        ratio = (up - down) / (up + down + flat)
        subs['breadth'] = _clamp(_tanh(ratio, 0.35))
        inputs['breadth'] = {'up': up, 'down': down, 'flat': flat}

    # 市场量能：当日成交额相对过去至多 20 个交易日的中位数，带方向（涨跌家数定号）——
    # 放量上涨加分、放量下跌减分（127 天里有 5 天放量 >10% 且跌家多于涨家，旧口径误加分）
    amount = stats.get('amount')
    history = [h for h in store.history('market', date) if h['date'] < date][-20:]
    past_amounts = [(h.get('stats') or {}).get('amount') for h in history]
    past_amounts = [a for a in past_amounts if a]
    if amount and past_amounts:
        rel = amount / _median(past_amounts)
        direction = 1
        if None not in (up, down) and (up + down):
            direction = 1 if up >= down else -1
        subs['volume'] = _clamp(_tanh((rel - 1) * direction, 0.25))
        inputs['volume'] = {'amount': round(amount), 'median20': round(_median(past_amounts)),
                            'ratio': round(rel, 3), 'direction': direction}

    # 涨停表现：个数 + 高度（最高板/高位股群/绕异动）+ 封板率 + 赚钱效应
    limit_score, limit_inputs = _limit_dim(store, date, pools, rows, today_streak, prev_streak)
    if limit_score is not None:
        subs['limit'] = _clamp(limit_score)
        inputs['limit'] = limit_inputs

    # 大幅回撤（亏钱效应）：与情绪分统一为「大面」口径——距当日最高价回撤 ≥10% 的家数，越多分越低
    if stocks:
        valid = [r for r in rows if r.get('high') and r.get('price') and r['high'] > 0
                 and 'ST' not in (r.get('name') or '').upper()]
        if valid:
            deep = sum(1 for r in valid if (r['high'] - r['price']) / r['high'] * 100 >= 10)
            subs['drawdown'] = _clamp(_piece(deep, DD_POINTS))
            inputs['drawdown'] = {'deep': deep, 'total': len(valid)}

    # 权重表现：全部指数当日涨跌幅的中位数（不再只看上证，避免双创行情失真）
    if indices:
        changes = [r['change'] for r in (indices['payload'].get('rows') or [])
                   if r.get('change') is not None]
        if changes:
            med = _median(changes)
            subs['index'] = _clamp(_tanh(med, 1.2))
            inputs['index'] = {'change': round(med, 2), 'n': len(changes)}

    available = {k: v for k, v in subs.items() if v is not None}
    if not available:
        return None
    total_weight = sum(WEIGHTS[k] for k in available)
    score = round(sum(available[k] * WEIGHTS[k] for k in available) / total_weight, 1)
    return {'score': score, 'date': date,
            'subs': [{'key': k, 'name': SUB_NAMES[k], 'score': round(available[k], 1),
                      'weight': WEIGHTS[k], 'inputs': inputs.get(k)} for k in WEIGHTS if k in available],
            'missing': [SUB_NAMES[k] for k in WEIGHTS if k not in available],
            'note': '自研近似算法（参考开盘啦五维度），非开盘啦官方数值'}


_SCHEMA = '''CREATE TABLE IF NOT EXISTS sentiment_daily(
    trade_date TEXT PRIMARY KEY, score REAL NOT NULL, revision INTEGER NOT NULL,
    subs TEXT NOT NULL, computed_at TEXT NOT NULL)'''


def _ensure_table(db):
    db.execute(_SCHEMA)


def _streak_pair(store, date):
    """当日与前一日的连板表（供涨停表现维度：最高板/高位股群/赚钱效应）。"""
    d = datetime.strptime(date, '%Y-%m-%d').date()
    span = [(d - timedelta(days=i)).isoformat() for i in range(STREAK_DAYS, -1, -1)]
    streaks = _streaks(store, span)
    return streaks.get(span[-2]), streaks.get(date)


def daily(store, date):
    """当日综合强度：已存且基于最新市场快照则直接读库，否则重算并落库。"""
    import json as _json
    from .store import now_iso
    market = store.snapshot('market', date, 'close')
    if not market:
        return None
    with store.connect() as db:
        _ensure_table(db)
        row = db.execute('SELECT * FROM sentiment_daily WHERE trade_date=?', (date,)).fetchone()
        if row and row['revision'] >= market['id']:
            payload = _json.loads(row['subs'])
            return {'score': row['score'], 'date': date, 'subs': payload['subs'],
                    'missing': payload['missing'],
                    'note': '自研近似算法（参考开盘啦五维度），非开盘啦官方数值', 'cached': True}
    prev_streak, today_streak = _streak_pair(store, date)
    fresh = _compute(store, date, prev_streak=prev_streak, today_streak=today_streak)
    if fresh is None:
        return None
    payload = {'subs': fresh['subs'], 'missing': fresh['missing']}
    with store.connect() as db:
        _ensure_table(db)
        db.execute('INSERT INTO sentiment_daily(trade_date,score,revision,subs,computed_at) VALUES(?,?,?,?,?) '
                   'ON CONFLICT(trade_date) DO UPDATE SET score=excluded.score,revision=excluded.revision,'
                   'subs=excluded.subs,computed_at=excluded.computed_at',
                   (date, fresh['score'], market['id'], _json.dumps(payload, ensure_ascii=False), now_iso()))
    return fresh


def history(store, limit=60):
    """综合强度历史曲线：按日期升序。先回补缺失日期的计算，再读库。"""
    import json as _json
    dates = [d['trade_date'] for d in store.dates() if d['mode'] == 'close']
    dates = sorted(set(dates))[-limit:]
    if not dates:
        return []
    with store.connect() as db:
        _ensure_table(db)
        cached = {r['trade_date'] for r in db.execute(
            'SELECT trade_date FROM sentiment_daily WHERE trade_date IN (%s)'
            % ','.join('?' * len(dates)), dates).fetchall()}
    missing = [d for d in dates if d not in cached]
    if missing:
        # 一次性预扫描连板（懒回补整段共用），避免逐日重复扫描
        d0 = datetime.strptime(dates[0], '%Y-%m-%d').date()
        span = [(d0 - timedelta(days=i)).isoformat() for i in range(STREAK_DAYS, 0, -1)] + list(dates)
        streaks = _streaks(store, span)
        for i, date in enumerate(dates):
            if date in cached:
                continue
            prev = streaks.get(dates[i - 1]) if i else streaks.get(span[STREAK_DAYS - 1])
            fresh = _compute(store, date, prev_streak=prev, today_streak=streaks.get(date))
            if fresh is None:
                continue
            market = store.snapshot('market', date, 'close')
            payload = {'subs': fresh['subs'], 'missing': fresh['missing']}
            from .store import now_iso
            with store.connect() as db:
                _ensure_table(db)
                db.execute('INSERT INTO sentiment_daily(trade_date,score,revision,subs,computed_at)'
                           ' VALUES(?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET'
                           ' score=excluded.score,revision=excluded.revision,'
                           ' subs=excluded.subs,computed_at=excluded.computed_at',
                           (date, fresh['score'], market['id'],
                            _json.dumps(payload, ensure_ascii=False), now_iso()))
    with store.connect() as db:
        _ensure_table(db)
        rows = [dict(r) for r in db.execute(
            'SELECT trade_date,score FROM sentiment_daily WHERE trade_date IN (%s) ORDER BY trade_date'
            % ','.join('?' * len(dates)), dates)] if dates else []
    return [{'date': r['trade_date'], 'score': r['score']} for r in rows]
