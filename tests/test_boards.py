"""最高板走势：层级切分、ST/非沪深过滤、缺池日期跳过、端点。"""
import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.boards import history
from backend.store import Store

DAYS = ['2026-11-03', '2026-11-04', '2026-11-05']


def seed(store):
    for i, day in enumerate(DAYS):
        store.save('market', day, 'close', {'stats': {'up': 1, 'down': 1, 'flat': 1}}, f'{day}T15:00:00+08:00')
    # 11-03：最高 6 板两只并列，次高 5 板，第三 3 板 + 一只 ST 高板应被过滤 + 一只北交所应被过滤
    store.save('pools', '2026-11-03', 'close', {'pools': {'up': [
        dict(code='600001', name='甲股', boards=6), dict(code='000002', name='乙股份', boards=6),
        dict(code='300003', name='丙科技', boards=5),
        dict(code='600004', name='丁集团', boards=3),
        dict(code='600005', name='ST戴帽', boards=7), dict(code='830006', name='北交票', boards=8),
    ], 'broken': [], 'down': []}}, '2026-11-03T15:00:00+08:00')
    # 11-05：只有一档
    store.save('pools', '2026-11-05', 'close', {'pools': {'up': [
        dict(code='600007', name='庚股份', boards=2)]}}, '2026-11-05T15:00:00+08:00')
    # 11-04 无池快照，应跳过


def test_boards_history_tiers_and_filters(tmp_path):
    store = Store(tmp_path / 't.db')
    seed(store)
    rows = history(store)
    assert [r['date'] for r in rows] == ['2026-11-03', '2026-11-05']  # 无池且无 stocks 快照的日期跳过
    assert all(r['source'] == 'pool' for r in rows)
    tiers = rows[0]['tiers']
    assert [t['boards'] for t in tiers] == [6]  # 仅最高一层
    assert tiers[0]['names'] == ['乙股份', '甲股']  # 并列多只都列出
    # ST 7 板与北交所 8 板不得进入
    all_names = [n for t in tiers for n in t['names']]
    assert 'ST戴帽' not in all_names and '北交票' not in all_names
    assert [t['boards'] for t in rows[1]['tiers']] == [2]


def test_boards_history_endpoint(tmp_path):
    store = Store(tmp_path / 't.db')
    seed(store)
    with TestClient(create_app(store, collect=False)) as c:
        data = c.get('/api/boards-history').json()
        assert len(data['rows']) == 2 and data['rows'][0]['date'] == '2026-11-03'
        assert c.get('/api/boards-history?limit=1').json()['rows'][0]['date'] == '2026-11-05'


def test_boards_history_empty(tmp_path):
    store = Store(tmp_path / 't.db')
    store.save('market', '2026-11-05', 'close', {'stats': {}}, '2026-11-05T15:00:00+08:00')
    assert history(store) == []


KDAYS = ['2026-10-26', '2026-10-27', '2026-10-28']  # 连续三个自然日，供连板累进


def _srow(code, name, change):
    return {'code': code, 'name': name, 'change': change, 'price': 10.0,
            'high': 10.0, 'amount': 1e8}


def seed_kline(store):
    for day in KDAYS:
        store.save('market', day, 'close', {'stats': {}})
    # 甲股份主板 9.8 阈值三连板；乙股份前两天未板、第三天首板
    store.save('stocks', KDAYS[0], 'close', {'rows': [_srow('600001', '甲股份', 10.0),
                                                      _srow('600002', '乙股份', 3.0)]})
    store.save('stocks', KDAYS[1], 'close', {'rows': [_srow('600001', '甲股份', 10.0),
                                                      _srow('600002', '乙股份', -2.0)]})
    store.save('stocks', KDAYS[2], 'close', {'rows': [_srow('600001', '甲股份', 10.1),
                                                      _srow('600002', '乙股份', 9.9)]})


def test_boards_kline_backfill(tmp_path):
    """无池日期按日 K 近似回补连板高度，source 标注 kline。"""
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    rows = history(store)
    assert [r['date'] for r in rows] == KDAYS
    assert all(r['source'] == 'kline' for r in rows)
    assert [r['tiers'][0]['boards'] for r in rows] == [1, 2, 3]
    assert rows[-1]['tiers'][0]['names'] == ['甲股份']
    assert rows[0]['tiers'][0]['names'] == ['甲股份']  # 乙股份首日未涨停


def test_boards_kline_streaks_bridge_weekend(tmp_path):
    """连板跨周末延续：周五涨停 + 周一涨停 = 二连板（区分自然日清零旧口径）。"""
    store = Store(tmp_path / 't.db')
    for day in ['2026-11-06', '2026-11-09']:  # 周五、周一
        store.save('market', day, 'close', {'stats': {}})
    store.save('stocks', '2026-11-06', 'close', {'rows': [_srow('600001', '甲股份', 10.0)]})
    store.save('stocks', '2026-11-09', 'close', {'rows': [_srow('600001', '甲股份', 10.0)]})
    rows = history(store)
    assert rows[-1]['date'] == '2026-11-09' and rows[-1]['tiers'][0]['boards'] == 2


def test_boards_cache_and_recompute_on_new_revision(tmp_path):
    """结果落 boards_daily 缓存；stocks 新修订触发重算，池口径优先于日 K。"""
    store = Store(tmp_path / 't.db')
    seed_kline(store)
    first = history(store)
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM boards_daily').fetchone()[0] == 3
    assert history(store) == first  # 第二次全读缓存
    # 第三天新快照：甲股份断板，乙股份首板成最高板
    store.save('stocks', KDAYS[2], 'close', {'rows': [_srow('600001', '甲股份', 2.0),
                                                      _srow('600002', '乙股份', 10.0)]})
    rows = history(store)
    assert rows[-1]['tiers'][0]['boards'] == 1
    assert rows[-1]['tiers'][0]['names'] == ['乙股份']
    # 有池快照的日期不受 stocks 影响，仍取池口径
    store.save('pools', KDAYS[2], 'close', {'pools': {'up': [
        dict(code='600009', name='池股', boards=5)], 'broken': [], 'down': []}})
    rows = history(store)
    assert rows[-1]['source'] == 'pool' and rows[-1]['tiers'][0]['boards'] == 5
    assert rows[-1]['tiers'][0]['names'] == ['池股']
