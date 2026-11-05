"""市场综合强度：参考开盘啦官方披露的五个维度（涨跌家数、市场量能、涨停表现、大幅回撤、权重表现）
自研的近似算法。开盘啦官方公式未公开，本模块的权重与映射均为本项目自定，数值与开盘啦不可直接比较。
纪律：任一维度输入缺失时该维度留空（None），剩余维度按权重重新归一，绝不编造。"""
import math

WEIGHTS = {'breadth': 0.25, 'volume': 0.20, 'limit': 0.25, 'drawdown': 0.15, 'index': 0.15}
SUB_NAMES = {'breadth': '涨跌广度', 'volume': '市场量能', 'limit': '涨停表现', 'drawdown': '大幅回撤', 'index': '权重表现'}


def _tanh(x, scale):
    return 50 + 50 * math.tanh(x / scale)


def _clamp(v):
    return max(0.0, min(100.0, v))


def _median(values):
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _compute(store, date):
    """返回 {'score','subs','inputs',...}；数据不足时返回 None。"""
    market = store.snapshot('market', date, 'close')
    if not market:
        return None
    stats = market['payload'].get('stats') or {}
    pools = store.snapshot('pools', date, 'close')
    indices = store.snapshot('indices', date, 'close')
    stocks = store.snapshot('stocks', date, 'close')
    subs, inputs = {}, {}

    # 涨跌广度：涨跌幅家数差占比，映射到 0-100
    up, down, flat = stats.get('up'), stats.get('down'), stats.get('flat')
    if None not in (up, down, flat) and (up + down + flat):
        ratio = (up - down) / (up + down + flat)
        subs['breadth'] = _clamp(_tanh(ratio, 0.35))
        inputs['breadth'] = {'up': up, 'down': down, 'flat': flat}

    # 市场量能：当日成交额相对过去至多 20 个交易日的中位数
    amount = stats.get('amount')
    history = [h for h in store.history('market', date) if h['date'] < date][-20:]
    past_amounts = [(h.get('stats') or {}).get('amount') for h in history]
    past_amounts = [a for a in past_amounts if a]
    if amount and past_amounts:
        rel = amount / _median(past_amounts)
        subs['volume'] = _clamp(_tanh(rel - 1, 0.25))
        inputs['volume'] = {'amount': round(amount), 'median20': round(_median(past_amounts)), 'ratio': round(rel, 3)}

    # 涨停表现：涨停家数（饱和 100 家）+ 连板高度（饱和 8 板）+ 封板率
    if pools:
        p = pools['payload'].get('stats') or {}
        limit_up, broken_ratio, max_boards = p.get('limit_up'), p.get('broken_ratio'), p.get('max_boards')
        if limit_up is not None:
            score = min(limit_up / 100, 1) * 60
            if max_boards:
                score += min(max_boards / 8, 1) * 25
            if broken_ratio is not None:
                score += (1 - min(broken_ratio, 100) / 100) * 15
            subs['limit'] = _clamp(score)
            inputs['limit'] = {'limit_up': limit_up, 'broken_ratio': broken_ratio, 'max_boards': max_boards}

    # 大幅回撤（亏钱效应）：收盘价距当日最高价回撤 ≥5% 的个股占比，占比越高分越低
    if stocks:
        rows = stocks['payload'].get('rows') or []
        valid = [r for r in rows if r.get('high') and r.get('price') and r['high'] > 0
                 and 'ST' not in (r.get('name') or '').upper()]
        if valid:
            deep = sum(1 for r in valid if (r['high'] - r['price']) / r['high'] * 100 >= 5)
            ratio = deep / len(valid)
            subs['drawdown'] = _clamp(100 * (1 - ratio / 0.10))
            inputs['drawdown'] = {'deep': deep, 'total': len(valid), 'ratio': round(ratio, 4)}

    # 权重表现：上证指数涨跌幅
    if indices:
        row = next((r for r in (indices['payload'].get('rows') or []) if r.get('code') in ('sh000001', '000001')), None)
        if row and row.get('change') is not None:
            subs['index'] = _clamp(_tanh(row['change'], 1.2))
            inputs['index'] = {'change': row['change']}

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
    fresh = _compute(store, date)
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
    for date in dates:  # 懒回补：只算没存过的
        with store.connect() as db:
            _ensure_table(db)
            exists = db.execute('SELECT 1 FROM sentiment_daily WHERE trade_date=?', (date,)).fetchone()
        if not exists:
            daily(store, date)
    with store.connect() as db:
        _ensure_table(db)
        rows = [dict(r) for r in db.execute(
            'SELECT trade_date,score FROM sentiment_daily WHERE trade_date IN (%s) ORDER BY trade_date'
            % ','.join('?' * len(dates)), dates)] if dates else []
    return [{'date': r['trade_date'], 'score': r['score']} for r in rows]
