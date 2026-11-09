"""核心龙头股票池：双池增删查、参数校验。"""
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import Store


def client(tmp_path):
    store = Store(tmp_path / 't.db')
    return TestClient(create_app(store, collect=False))


def test_leaders_crud(tmp_path):
    with client(tmp_path) as c:
        data = c.get('/api/leaders').json()
        assert [p['pool'] for p in data['pools']] == ['emotion', 'trend']
        assert all(p['rows'] == [] for p in data['pools'])

        assert c.post('/api/leaders', json={'pool': 'emotion', 'code': '600001', 'name': '甲股'}).json()['ok']
        assert c.post('/api/leaders', json={'pool': 'emotion', 'code': '300002', 'name': '乙科技'}).json()['ok']
        assert c.post('/api/leaders', json={'pool': 'trend', 'code': '600001', 'name': '甲股'}).json()['ok']  # 同票可入另一池

        data = c.get('/api/leaders').json()
        emo, trend = data['pools']
        assert [r['code'] for r in emo['rows']] == ['600001', '300002']
        assert [r['code'] for r in trend['rows']] == ['600001']
        assert emo['name'] == '情绪核心' and trend['name'] == '趋势核心'

        assert c.delete('/api/leaders/emotion/600001').json()['ok']
        data = c.get('/api/leaders').json()
        assert [r['code'] for r in data['pools'][0]['rows']] == ['300002']
        assert [r['code'] for r in data['pools'][1]['rows']] == ['600001']  # 另一池不受影响


def test_leaders_validation(tmp_path):
    with client(tmp_path) as c:
        assert c.post('/api/leaders', json={'pool': 'foo', 'code': '600001', 'name': '甲股'}).status_code == 422
        assert c.post('/api/leaders', json={'pool': 'emotion', 'code': '60001', 'name': '甲股'}).status_code == 422
        assert c.post('/api/leaders', json={'pool': 'emotion', 'code': 'abcdef', 'name': '甲股'}).status_code == 422
        assert c.post('/api/leaders', json={'pool': 'emotion', 'code': '600001', 'name': '  '}).status_code == 422
        c.post('/api/leaders', json={'pool': 'emotion', 'code': '600001', 'name': '甲股'})
        assert c.post('/api/leaders', json={'pool': 'emotion', 'code': '600001', 'name': '甲股'}).status_code == 422  # 重复
        assert c.delete('/api/leaders/emotion/600999').status_code == 404


def test_leaders_perf(tmp_path):
    from datetime import date, timedelta
    store = Store(tmp_path / 't.db')
    base = date(2026, 10, 10)
    for i in range(21):  # 21 个交易日，股价 100→120 每天 +1
        d = (base + timedelta(days=i)).isoformat()
        store.save('market', d, 'close', {'stats': {'up': 1, 'down': 1, 'flat': 0}}, f'{d}T15:00:00+08:00')
        price = float(100 + i)
        prev = float(99 + i)
        store.save('stocks', d, 'close', {'rows': [
            dict(code='600001', name='甲股', price=price, change=round((price / prev - 1) * 100, 2),
                 high=price, amount=1e6)]}, f'{d}T15:00:00+08:00')
    with TestClient(create_app(store, collect=False)) as c:
        c.post('/api/leaders', json={'pool': 'emotion', 'code': '600001', 'name': '甲股'})
        data = c.get('/api/leaders/perf').json()
        assert data['date'] == '2026-10-30'
        row = data['pools']['emotion'][0]
        assert row['code'] == '600001'
        assert row['d1'] == round((120 / 119 - 1) * 100, 2)   # 最近收盘涨幅
        assert row['d10'] == round((120 / 110 - 1) * 100, 2)  # 近 10 日
        assert row['d20'] == 20.0                             # 近 20 日
        assert data['pools']['trend'] == []                   # 空池返回空


def test_leaders_perf_short_history(tmp_path):
    store = Store(tmp_path / 't.db')
    for i, d in enumerate(['2026-11-04', '2026-11-05']):
        store.save('market', d, 'close', {'stats': {'up': 1, 'down': 1, 'flat': 0}}, f'{d}T15:00:00+08:00')
        store.save('stocks', d, 'close', {'rows': [
            dict(code='600001', name='甲股', price=100.0 + i, change=1.0, high=101.0, amount=1e6)]},
            f'{d}T15:00:00+08:00')
    with TestClient(create_app(store, collect=False)) as c:
        c.post('/api/leaders', json={'pool': 'emotion', 'code': '600001', 'name': '甲股'})
        row = c.get('/api/leaders/perf').json()['pools']['emotion'][0]
        assert row['d1'] == 1.0
        assert row['d10'] is None and row['d20'] is None      # 历史不足留空
    # 完全无快照
    empty = Store(tmp_path / 'e.db')
    with TestClient(create_app(empty, collect=False)) as c:
        assert c.get('/api/leaders/perf').json()['date'] is None
