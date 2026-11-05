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
    rows = [{'code': f'{i:06d}', 'name': f'股{i}', 'high': 10.0, 'price': 9.9 if i % 10 else 9.3}
            for i in range(100)]  # 10% 的个股回撤超 5%
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
    assert by_key['drawdown']['inputs']['deep'] == 10
    assert by_key['limit']['inputs']['max_boards'] == 8


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
