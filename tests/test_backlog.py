import sqlite3

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import Store


def client(tmp_path):
    return TestClient(create_app(Store(tmp_path / 'test.db'), collect=False))


def test_backlog_add_list_done_flow(tmp_path):
    with client(tmp_path) as c:
        assert c.get('/api/backlog').json() == {'rows': [], 'open': 0, 'done': 0}
        first = c.post('/api/backlog', json={
            'title': '  加入板块拥挤度指标  ', 'detail': '换手率与波动率合成拥挤度，进强弱观察列表',
        }).json()
        assert first['title'] == '加入板块拥挤度指标'
        assert first['detail'] == '换手率与波动率合成拥挤度，进强弱观察列表'
        assert first['done'] == 0 and first['done_at'] is None and first['created_at']
        second = c.post('/api/backlog', json={'title': '龙虎榜席位画像'}).json()
        assert second['detail'] == ''  # 描述可省略
        board = c.get('/api/backlog').json()
        assert board['open'] == 2 and board['done'] == 0
        assert [r['id'] for r in board['rows']] == [second['id'], first['id']]  # 未完成按新增倒序
        done = c.post(f"/api/backlog/{first['id']}/done").json()
        assert done['done'] == 1 and done['done_at']
        again = c.post(f"/api/backlog/{first['id']}/done").json()
        assert again['done_at'] == done['done_at']  # 重复点击幂等，不刷新完成时间
        board = c.get('/api/backlog').json()
        assert board['open'] == 1 and board['done'] == 1
        assert board['rows'][-1]['id'] == first['id']  # 已完成排在未完成之后


def test_backlog_validation_and_missing(tmp_path):
    with client(tmp_path) as c:
        assert c.post('/api/backlog', json={'title': ''}).status_code == 422
        assert c.post('/api/backlog', json={'title': '   '}).status_code == 422
        assert c.post('/api/backlog', json={'title': 'x' * 41}).status_code == 422
        assert c.post('/api/backlog', json={'title': 'ok', 'detail': 'x' * 301}).status_code == 422
        assert c.post('/api/backlog', json={}).status_code == 422
        assert c.post('/api/backlog/999/done').status_code == 404


def test_backlog_persists_across_app_instances(tmp_path):
    db_path = tmp_path / 'test.db'
    with TestClient(create_app(Store(db_path), collect=False)) as c:
        created = c.post('/api/backlog', json={'title': '新闻联动回看', 'detail': '按事件回看板块联动'}).json()
    with TestClient(create_app(Store(db_path), collect=False)) as c:
        board = c.get('/api/backlog').json()
        assert board['open'] == 1 and board['rows'][0]['id'] == created['id']
        assert board['rows'][0]['detail'] == '按事件回看板块联动'


def test_backlog_edit_and_delete(tmp_path):
    with client(tmp_path) as c:
        item = c.post('/api/backlog', json={'title': '原主题', 'detail': '原描述'}).json()
        edited = c.patch(f"/api/backlog/{item['id']}", json={'detail': '改后的描述'}).json()
        assert edited['title'] == '原主题' and edited['detail'] == '改后的描述'  # 只改描述不动主题
        edited = c.patch(f"/api/backlog/{item['id']}", json={'title': '  新主题  '}).json()
        assert edited['title'] == '新主题' and edited['detail'] == '改后的描述'
        assert c.patch(f"/api/backlog/{item['id']}", json={}).status_code == 422  # 空修改
        assert c.patch(f"/api/backlog/{item['id']}", json={'title': ''}).status_code == 422
        assert c.patch(f"/api/backlog/{item['id']}", json={'title': 'x' * 41}).status_code == 422
        assert c.patch(f"/api/backlog/{item['id']}", json={'detail': 'x' * 301}).status_code == 422
        assert c.patch('/api/backlog/999', json={'title': 'x'}).status_code == 404
        done = c.post(f"/api/backlog/{item['id']}/done").json()
        edited = c.patch(f"/api/backlog/{item['id']}", json={'detail': '完成后也能改'}).json()
        assert edited['done'] == 1 and edited['detail'] == '完成后也能改'  # 已完成仍可编辑
        assert c.delete(f"/api/backlog/{item['id']}").json() == {'deleted': item['id']}
        assert c.get('/api/backlog').json() == {'rows': [], 'open': 0, 'done': 0}
        assert c.delete(f"/api/backlog/{item['id']}").status_code == 404


def test_backlog_migrates_table_without_detail(tmp_path):
    db_path = tmp_path / 'test.db'
    raw = sqlite3.connect(db_path)
    raw.execute('''CREATE TABLE backlog_items(
        id INTEGER PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0, done_at TEXT)''')
    raw.execute("INSERT INTO backlog_items(title,created_at) VALUES('旧事项','2026-01-01T00:00:00')")
    raw.commit()
    raw.close()
    with TestClient(create_app(Store(db_path), collect=False)) as c:
        row = c.get('/api/backlog').json()['rows'][0]
        assert row['title'] == '旧事项' and row['detail'] == ''  # 旧数据自动补空描述
        added = c.post('/api/backlog', json={'title': '新事项', 'detail': '带描述'}).json()
        assert added['detail'] == '带描述'
