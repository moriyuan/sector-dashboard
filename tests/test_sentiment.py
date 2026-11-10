from fastapi.testclient import TestClient

from backend.app import create_app
from backend.sentiment import _compute, daily, history
from backend.store import Store

DAY = '2026-11-03'


def seeded_store(tmp_path):
    store = Store(tmp_path / 'test.db')
    # 前 5 个交易日成交额，供量能中位数
    for i, day in enumerate(['2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31']):
        store.save('market', day, 'close', {'stats': {'up': 2000, 'down': 2000, 'flat': 100, 'amount': 1.0e12}})
    store.save('market', DAY, 'close', {'stats': {'up': 3500, 'down': 1000, 'flat': 100, 'amount': 1.4e12}})
    store.save('pools', DAY, 'close', {'stats': {'limit_up': 71, 'limit_down': 0, 'broken': 8,
                                                 'broken_ratio': 10.1, 'max_boards': 8}})
    store.save('indices', DAY, 'close', {'rows': [{'code': 'sh000001', 'change': 0.8}]})
    rows = [{'code': f'{i:06d}', 'name': f'股{i}', 'high': 10.0, 'price': 9.9 if i % 10 else 8.9}
            for i in range(100)]  # 10% 的个股回撤超 10%（大面）
    store.save('stocks', DAY, 'close', {'rows': rows})
    return store


def test_sentiment_composite_and_subs(tmp_path):
    store = seeded_store(tmp_path)
    s = _compute(store, DAY)
    assert s is not None and 0 <= s['score'] <= 100
    keys = [x['key'] for x in s['subs']]
    assert keys == ['breadth', 'volume', 'limit', 'drawdown', 'index']
    assert s['missing'] == []
    assert s['score'] > 60  # 强市：普涨 + 放量 + 71 涨停
    by_key = {x['key']: x for x in s['subs']}
    assert by_key['volume']['inputs']['ratio'] == 1.4
    assert by_key['volume']['inputs']['direction'] == 1          # 涨家多于跌家 → 放量加分
    assert by_key['drawdown']['inputs']['deep'] == 10
    assert by_key['limit']['inputs']['max_boards'] == 8
    assert by_key['limit']['inputs']['source'] == 'pool'
    assert by_key['limit']['inputs']['seal_rate'] == 89.9        # 71/(71+8)


def test_sentiment_missing_dims_renormalize(tmp_path):
    store = Store(tmp_path / 'test.db')
    store.save('market', DAY, 'close', {'stats': {'up': 1000, 'down': 3000, 'flat': 100, 'amount': None}})
    s = _compute(store, DAY)  # 只有广度可算
    assert s is not None and [x['key'] for x in s['subs']] == ['breadth']
    assert set(s['missing']) == {'市场量能', '涨停表现', '大幅回撤', '权重表现'}
    assert s['score'] < 40  # 普跌 → 低分
    assert _compute(store, '2026-01-01') is None  # 无快照 → 整体留空


def test_sentiment_daily_persists_and_recomputes_on_new_revision(tmp_path):
    store = seeded_store(tmp_path)
    first = daily(store, DAY)
    assert first['score'] > 60
    cached = daily(store, DAY)
    assert cached.get('cached') is True and cached['score'] == first['score']  # 第二次读库
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM sentiment_daily').fetchone()[0] == 1  # 只算当天
    history(store)  # 回补全部历史
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM sentiment_daily').fetchone()[0] == 6  # 每天一行
    # 底层快照出现新修订 → 重算并更新
    store.save('market', DAY, 'close', {'stats': {'up': 500, 'down': 4000, 'flat': 100, 'amount': 1.4e12}})
    recomputed = daily(store, DAY)
    assert not recomputed.get('cached') and recomputed['score'] < first['score']


def test_sentiment_history_endpoint(tmp_path):
    with TestClient(create_app(seeded_store(tmp_path), collect=False)) as c:
        body = c.get('/api/sentiment').json()
        rows = body['rows']
        assert len(rows) == 6  # 懒回补了全部 6 天
        assert [r['date'] for r in rows] == sorted(r['date'] for r in rows)  # 升序
        assert rows[-1]['date'] == DAY and rows[-1]['score'] > 60
        again = c.get('/api/sentiment?limit=3').json()['rows']
        assert len(again) == 3 and again[-1]['date'] == DAY


def test_market_api_carries_sentiment(tmp_path):
    with TestClient(create_app(seeded_store(tmp_path), collect=False)) as c:
        body = c.get(f'/api/market?date={DAY}').json()
        assert body['sentiment']['score'] > 0
        assert body['sentiment']['note'].startswith('自研')
        empty = c.get('/api/market?date=2026-01-01').json()
        assert empty['sentiment'] is None


def stk(code, change, price=100.0, amount=1e6):
    """日 K 行：change=涨跌幅%，price=收盘价。"""
    p = round(price * (1 + change / 100), 2)
    return dict(code=code, name='测试股', change=change, price=p, high=max(p, price), amount=amount)


def test_sentiment_volume_direction(tmp_path):
    """量能带方向：放量下跌减分，缩量下跌不加分。"""
    store = Store(tmp_path / 't.db')
    for day in ['2026-10-29', '2026-10-30']:
        store.save('market', day, 'close', {'stats': {'up': 2000, 'down': 2000, 'flat': 100, 'amount': 1.0e12}})
    # 放量下跌
    store.save('market', DAY, 'close', {'stats': {'up': 1000, 'down': 3500, 'flat': 100, 'amount': 1.4e12}})
    s = _compute(store, DAY)
    vol = {x['key']: x for x in s['subs']}['volume']
    assert vol['inputs']['direction'] == -1 and vol['score'] < 10     # 放量 40% 但普跌 → 低分
    # 缩量下跌：比值 0.8、方向 -1 → signed = +0.2 → 中偏上（抛压不大），但必须低于同幅缩量上涨
    store2 = Store(tmp_path / 'b.db')
    for day in ['2026-10-29', '2026-10-30']:
        store2.save('market', day, 'close', {'stats': {'up': 2000, 'down': 2000, 'flat': 100, 'amount': 1.0e12}})
    store2.save('market', DAY, 'close', {'stats': {'up': 1000, 'down': 3500, 'flat': 100, 'amount': 0.8e12}})
    store2.save('market', '2026-11-04', 'close', {'stats': {'up': 3500, 'down': 1000, 'flat': 100, 'amount': 0.8e12}})
    down_vol = {x['key']: x for x in _compute(store2, DAY)['subs']}['volume']['score']
    up_vol = {x['key']: x for x in _compute(store2, '2026-11-04')['subs']}['volume']
    assert up_vol['score'] < 50 < down_vol                            # 缩量上涨弱、缩量下跌相对抗跌
    assert abs(up_vol['score'] - (100 - down_vol)) < 0.05             # 镜像对称


def test_sentiment_limit_kline_backfill(tmp_path):
    """无 pools 快照的历史日：涨停表现维度用日 K 近似回补（与情绪分同口径）。"""
    store = Store(tmp_path / 't.db')
    sealed = [stk(f'6001{i:02d}', 10) for i in range(70)]             # 70 只连板
    for i in range(3):
        day = f'2026-11-0{2 + i}'
        store.save('market', day, 'close', {'stats': {'up': 2000, 'down': 2000, 'flat': 100, 'amount': 1e12}})
        store.save('stocks', day, 'close', {'rows': sealed})
    s = _compute(store, '2026-11-04', prev_streak={r['code']: 2 for r in sealed},
                 today_streak={r['code']: 3 for r in sealed})
    lim = {x['key']: x for x in s['subs']}['limit']
    assert lim['inputs']['source'] == 'kline'
    assert lim['inputs']['limit_up'] == 70
    assert lim['inputs']['max_boards'] == 3 and lim['inputs']['hi3'] == 70
    assert lim['inputs']['seal_rate'] == 100.0
    assert lim['score'] > 60                                          # 个数/高度/封板都不错


def test_sentiment_limit_yday_feedback(tmp_path):
    """赚钱效应：昨日涨停/连板今日大涨 → 涨停表现维度得分显著高于今日惨跌的情形。"""
    def build(tmp, day3_change):
        store = Store(tmp)
        ups = [stk(f'6002{i:02d}', 10) for i in range(8)]             # 8 只涨停
        for day in ['2026-10-29', '2026-10-30', '2026-11-02']:
            store.save('market', day, 'close', {'stats': {'up': 2000, 'down': 2000, 'flat': 100, 'amount': 1e12}})
            store.save('stocks', day, 'close', {'rows': ups})
        rows = [stk(f'6002{i:02d}', day3_change) for i in range(8)]   # 昨日涨停股今日表现
        store.save('market', DAY, 'close', {'stats': {'up': 2000, 'down': 2000, 'flat': 100, 'amount': 1e12}})
        store.save('stocks', DAY, 'close', {'rows': rows})
        return store

    from backend.emotion_score import _streaks
    store = build(tmp_path / 'a.db', 4)
    days = ['2026-10-29', '2026-10-30', '2026-11-02', DAY]
    streaks = _streaks(store, days)
    s = _compute(store, DAY, prev_streak=streaks['2026-11-02'], today_streak=streaks[DAY])
    lim = {x['key']: x for x in s['subs']}['limit']
    assert lim['inputs']['lu_yday'] == 4 and lim['inputs']['st_yday'] == 4

    store2 = build(tmp_path / 'b.db', -4)
    streaks2 = _streaks(store2, days)
    s2 = _compute(store2, DAY, prev_streak=streaks2['2026-11-02'], today_streak=streaks2[DAY])
    lim2 = {x['key']: x for x in s2['subs']}['limit']
    assert lim2['inputs']['lu_yday'] == -4
    assert lim['score'] - lim2['score'] > 15                          # 赚钱效应拉开差距（当日无涨停，维度分由赚钱效应主导）
