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


def test_leaders_note(tmp_path):
    """个股属性：添加时填写、列表返回、PUT 修改、超长校验。"""
    with client(tmp_path) as c:
        r = c.post('/api/leaders', json={'pool': 'emotion', 'code': '002242', 'name': '九阳股份',
                                         'note': '玄学抱团总龙头'})
        assert r.json()['ok']
        row = c.get('/api/leaders').json()['pools'][0]['rows'][0]
        assert row['note'] == '玄学抱团总龙头'

        assert c.put('/api/leaders/emotion/002242/note', json={'note': '短线情绪锚'}).json()['ok']
        row = c.get('/api/leaders').json()['pools'][0]['rows'][0]
        assert row['note'] == '短线情绪锚'
        assert c.put('/api/leaders/emotion/002242/note', json={'note': ''}).json()['ok']  # 清空
        assert c.get('/api/leaders').json()['pools'][0]['rows'][0]['note'] == ''

        assert c.put('/api/leaders/emotion/600999/note', json={'note': 'x'}).status_code == 404
        assert c.post('/api/leaders', json={'pool': 'emotion', 'code': '600001', 'name': '甲股',
                                            'note': '长' * 31}).status_code == 422


def test_stock_search(tmp_path):
    """模糊搜索：拼音首字母前缀、代码前缀、名称子串；排序代码优先。"""
    store = Store(tmp_path / 't.db')
    store.save('stocks', '2026-11-09', 'close', {'rows': [
        dict(code='002242', name='九阳股份', price=10.0, change=1.0, amount=1e6),
        dict(code='600001', name='甲股份', price=10.0, change=1.0, amount=1e6),
        dict(code='300999', name='九安医疗', price=10.0, change=1.0, amount=1e6),
    ]}, '2026-11-09T15:00:00+08:00')
    with TestClient(create_app(store, collect=False)) as c:
        rows = c.get('/api/stocks/search?q=jygf').json()['rows']
        assert rows and rows[0] == {'code': '002242', 'name': '九阳股份'}
        rows = c.get('/api/stocks/search?q=0022').json()['rows']
        assert rows[0]['code'] == '002242'
        rows = c.get('/api/stocks/search?q=九安').json()['rows']
        assert [r['code'] for r in rows] == ['300999']
        assert c.get('/api/stocks/search?q=').json()['rows'] == []
        assert c.get('/api/stocks/search?q=zzz').json()['rows'] == []


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
