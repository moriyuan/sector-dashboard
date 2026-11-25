from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import Store
from .api import Dashboard


def test_test_instance_can_run_dashboard_with_general_collector_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv('SECTOR_SHORT_TERM', '1')
    # Exercise the real startup, routes and shutdown without contacting upstream feeds.
    monkeypatch.setattr(Dashboard, 'track', lambda self: self.stop.wait())
    from .terminal import RefreshEngine
    monkeypatch.setattr(RefreshEngine, 'schedule', lambda self: self.stop.wait())
    app = create_app(Store(tmp_path / 'market.sqlite3'), collect=False)
    with TestClient(app) as client:
        page = client.get('/api/short-term/')
        assert page.status_code == 200 and '短线观察台' in page.text
        assert '__REFRESH_TOKEN__' not in page.text
        assert client.get('/api/short-term/live').json()['data']['noData']
        assert client.get('/api/status').json()['collecting'] is False
        assert client.get('/api/short-term/status').json()['automatic']['interval_seconds'] == 60
        assert client.post('/api/short-term/cores', json={'action':'remove','codes':['600001']}).status_code == 403
        assert (tmp_path / 'short-term' / 'refresh-history.sqlite3').is_file()
