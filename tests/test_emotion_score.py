"""冰点分/高潮分：分档口径、倒挂修正、池/兜底切换、pool/kline 双模式、端点。"""
import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.emotion_score import history
from backend.leaders import list_pools
from backend.store import Store

D0, D1, D2, D3 = '2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05'


def stk(code, change, touch=None, name='测试股'):
    """prev_close=100；touch=盘中最高涨幅（默认=max(change,0)）。"""
    prev = 100.0
    price = round(prev * (1 + change / 100), 2)
    high = round(prev * (1 + (touch if touch is not None else max(change, 0)) / 100), 2)
    return dict(code=code, name=name, change=change, price=price, high=high, amount=1e6)


def save_day(store, day, up, down, rows):
    store.save('market', day, 'close', {'stats': {'up': up, 'down': down, 'flat': 0}}, f'{day}T15:00:00+08:00')
    store.save('stocks', day, 'close', {'rows': rows}, f'{day}T15:00:00+08:00')


def subs(row, side):
    return {d['key']: d for d in json.loads(row['subs'])[side]}


def full_rows(store):
    """history() 返回精简行；完整行（含 subs）从库表读。"""
    history(store, limit=10)
    with store.connect() as db:
        return {r['trade_date']: dict(r) for r in db.execute("SELECT * FROM emotion_scores").fetchall()}


def seed_kline(store):
    """D2 为考核日：纯 K 线模式（无 pools 快照）。"""
    save_day(store, D0, 2000, 2000, [stk('600101', 10, 10), stk('600102', 10, 10), stk('600900', 3)])
    save_day(store, D1, 2000, 2000, [stk('600101', 10, 10), stk('600102', 10, 10), stk('600900', -2)])
    rows = [stk(f'6000{i:02d}', -10, -5) for i in range(10, 22)]          # 12 只跌停
    rows += [stk(f'6010{i:02d}', -7, 5) for i in range(10, 25)]           # 15 只大面(11.4%)
    rows += [stk('600101', -6, -3), stk('600102', -10, -5)]               # 昨日二连板龙头今日大跌
    rows += [stk('600900', 1), stk('830001', -30, -5, '北交票'), stk('600005', -10, -5, 'ST垃圾')]
    save_day(store, D2, 300, 3100, rows)


def test_ice_dims_kline(tmp_path):
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    rows = full_rows(store)
    row = rows[D2]
    assert row['source'] == 'kline'
    ice = subs(row, 'ice')
    assert ice['breadth']['score'] == 0.5                        # 3000~3499 → 0.5
    assert ice['limit_down']['score'] == 3                       # 12 跌停→1，跌停>涨停 +2
    assert 'seal_rate' not in ice                                # 样本(0 涨停+0 炸板)<10 → 置空
    assert ice['leaders']['score'] == 2                          # 均跌-8 → 1，任一跌停 → 2 取大
    assert ice['leaders']['note'] == 'kline_streak'              # 池空 → 昨日连板兜底
    assert ice['drawdown']['score'] == 1                         # 15 只大面(>10)
    assert row['ice'] == 6.5
    clx = subs(row, 'climax')
    assert clx['breadth']['score'] == 0 and clx['limit_up']['score'] == 0
    assert clx['max_boards']['input'] == 0                       # D2 无涨停 → 连板重建为 0
    assert row['climax'] == 0
    # ST/北交所不得计入：跌停恰为 13（12 只跌停股 + 龙头 600102；ST 与北交所被过滤）
    assert ice['limit_down']['input'] == 13
    assert ice['drawdown']['input'] == 15


def test_climax_dims_pool_mode(tmp_path):
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    # 情绪核心池两只（近端评分用池内股票）
    list_pools(store)  # 建表
    with store.connect() as db:
        db.executemany("INSERT INTO leader_stocks(pool, code, name, added_at) VALUES('emotion',?,?,?)",
                       [('600201', '池股甲', '2026-11-05T09:00:00+08:00'),
                        ('600202', '池股乙', '2026-11-05T09:00:00+08:00')])
        db.commit()
    save_day(store, D3, 4600, 200, [stk('600201', 6, 6), stk('600202', 9, 9)])
    store.save('pools', D3, 'close', {'pools': {'up': [], 'broken': [], 'down': []},
                                      'stats': {'limit_up': 80, 'limit_down': 5, 'broken': 5,
                                                'broken_ratio': 5.9, 'max_boards': 9}},
               f'{D3}T15:00:00+08:00')
    rows = full_rows(store)
    row = rows[D3]
    assert row['source'] == 'pool'
    clx = subs(row, 'climax')
    assert clx['breadth']['score'] == 3                          # floor(600/300)+1
    assert clx['limit_up']['score'] == 2                         # floor(15/15)+1
    assert clx['seal_rate']['score'] == 1                        # 80/85=94.1 > 90
    assert clx['leaders']['score'] == 1                          # 池内均涨 7.5 → >5
    assert clx['leaders']['note'] == 'emotion_pool'
    assert clx['max_boards']['score'] == 2                       # 9 板 → 9-7
    assert row['climax'] == 9
    ice = subs(row, 'ice')
    assert ice['limit_down']['score'] == 0                       # 5 跌停 <10
    assert ice['seal_rate']['score'] == 0                        # 94.1 ≥ 70
    assert ice['leaders']['score'] == 0
    assert row['ice'] == 0


def test_fallback_streak_leaders(tmp_path):
    """池空时：昨日 K 线连板股（streak>=2）作为龙头。"""
    store = Store(tmp_path / 't.db')
    save_day(store, D0, 2000, 2000, [stk('600301', 10, 10)])
    save_day(store, D1, 2000, 2000, [stk('600301', 10, 10)])
    save_day(store, D2, 4200, 500, [stk('600301', 6, 6)])
    rows = full_rows(store)
    clx = subs(rows[D2], 'climax')
    assert clx['leaders']['note'] == 'kline_streak'
    assert clx['leaders']['score'] == 1                          # 均涨 6 > 5
    # D0 无昨日连板 → 龙头维度缺失
    assert 'leaders' not in subs(rows[D0], 'climax')


def test_emotion_score_endpoint_and_cache(tmp_path):
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    with TestClient(create_app(store, collect=False)) as c:
        data = c.get('/api/emotion-score').json()
        dates = [r['date'] for r in data['rows']]
        assert dates == sorted(dates) and dates[-1] == D2
        r = data['rows'][-1]
        assert r['ice'] == 6.5 and r['climax'] == 0 and r['source'] == 'kline'
        again = c.get('/api/emotion-score').json()               # 缓存一致
        assert again == data
        # 市场快照更新（revision 变化）→ 重算
        store.save('market', D2, 'close', {'stats': {'up': 300, 'down': 4100, 'flat': 0}},
                   f'{D2}T15:30:00+08:00')
        fresh = c.get('/api/emotion-score').json()
        assert fresh['rows'][-1]['ice'] == 8                     # 广度2 + 跌停3 + 龙头2 + 大面1（重算生效）


def test_emotion_score_empty(tmp_path):
    store = Store(tmp_path / 't.db')
    assert history(store, limit=10) == []
    with TestClient(create_app(store, collect=False)) as c:
        assert c.get('/api/emotion-score').json() == {'rows': []}
