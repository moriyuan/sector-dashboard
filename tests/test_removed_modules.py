from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.service import Collector
from backend.store import Store
from scripts.retain_recent import build_retained
from scripts.remove_retired_data import clean


def test_market_keeps_quotes_without_retired_modules(tmp_path):
    store = Store(tmp_path / 'market.sqlite3')
    store.save('indices', '2026-11-13', 'close', {'rows': [{'code': '000001', 'price': 4000}]})
    with TestClient(create_app(store, collect=False)) as client:
        data = client.get('/api/market?date=2026-11-13').json()
        assert data['indices'][0]['price'] == 4000
        assert 'index_pe' not in data and 'financing' not in data
        assert client.get('/api/etf-tracker?date=2026-11-13').status_code == 404
        for path in ('/api/barra','/api/daily','/api/seat-backtest','/api/seat-backtest/download/monthly.csv'):
            assert client.get(path).status_code == 404


def test_retired_collectors_no_longer_start(tmp_path):
    collector = Collector(Store(tmp_path / 'market.sqlite3'))
    with patch('backend.service.threading.Thread') as thread:
        collector.start()
    assert {c.kwargs['name'] for c in thread.call_args_list} == {'sector-collector', 'lhb-collector'}


def test_retention_drops_only_retired_data_and_blocks_reappearance(tmp_path):
    source = tmp_path / 'market.sqlite3'
    store = Store(source)
    for kind in ('broad_etf', 'indices', 'flow_industry'):
        store.save(kind, '2026-11-13', 'close', {'rows': []})
    retired = ('index-pe:v1:000001', 'index-pe:check:000001', 'financing:series',
               'financing:raw', 'financing:check:2026-11-13', 'etf-check:2026-11-13')
    for key in (*retired, 'catalog:industry', 'quotes:2026-11-13'):
        store.observe(key, {'sample': 1})
    for job in ('宽基ETF', '融资余额', '指数估值 000001', 'board_industry'):
        store.event(job, True, 'ok')
    destination = tmp_path / 'clean.sqlite3'
    build_retained(source, destination, '2026-11-11')
    clean = Store(destination)
    assert clean.snapshot('broad_etf', '2026-11-13', 'close') is None
    for kind in ('indices', 'flow_industry'):
        assert clean.snapshot(kind, '2026-11-13', 'close')['payload'] == {'rows': []}
    assert [e['job'] for e in clean.events()] == ['board_industry']
    for key in retired:
        assert clean.observed(key) is None
        clean.observe(key, {'sample': 2})
        assert clean.observed(key) is None
    clean.save('broad_etf', '2026-11-13', 'close', {'rows': []})
    assert clean.snapshot('broad_etf', '2026-11-13', 'close') is None
    assert clean.observed('catalog:industry') == {'sample': 1}
    assert clean.observed('quotes:2026-11-13') == {'sample': 1}


def test_targeted_cleanup_preserves_older_dates_and_is_idempotent(tmp_path):
    path = tmp_path / 'archive.sqlite3'
    store = Store(path)
    store.save('indices', '2025-01-02', 'close', {'rows': [1]})
    store.save('broad_etf', '2026-11-13', 'close', {'rows': [2]})
    store.observe('stock:fundamental', {'pe': 20})
    store.observe('index-pe:v1:000001', {'pe': 15})
    report = clean(path)
    assert report['removed']['snapshots'] == 1
    assert report['removed']['observations'] == 1
    assert store.snapshot('indices', '2025-01-02', 'close')['payload'] == {'rows': [1]}
    assert store.observed('stock:fundamental') == {'pe': 20}
    assert not any(clean(path)['removed'].values())
