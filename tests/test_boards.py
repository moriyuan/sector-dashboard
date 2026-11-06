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
    assert [r['date'] for r in rows] == ['2026-11-03', '2026-11-05']  # 无池日期跳过
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
