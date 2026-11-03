import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from backend import news_sources as sources


class Response:
    status_code = 200
    headers = {}

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield b'{"ok":true}'


def test_request_referrer_matches_public_source(monkeypatch):
    monkeypatch.setattr(sources.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.1.1.1', 443))])
    seen = []
    def request(url, **kwargs):
        seen.append(kwargs['headers'])
        if urlsplit(url).hostname == 'np-weblist.eastmoney.com' and kwargs['headers'].get('Referer') != 'https://kuaixun.eastmoney.com/':
            raise requests.HTTPError('567: invalid source referrer')
        return Response()
    monkeypatch.setattr(sources.requests, 'get', request)
    assert sources.fetch(sources.SOURCES['em']['url'])[0]
    sources.fetch(sources.SOURCES['cls']['url'])
    sources.fetch(sources.SOURCES['fed']['url'])
    assert seen[1]['Referer'] == 'https://www.cls.cn/telegraph'
    assert 'Referer' not in seen[2]


def test_cls_current_public_cache_contract():
    url = urlsplit(sources.SOURCES['cls']['url'])
    assert url.path == '/api/cache' and parse_qs(url.query)['name'] == ['telegraph']
    raw = json.dumps({'errno': 0, 'data': {'roll_data': [
        {'id': 2199431, 'title': '', 'content': '<b>公司公告</b>', 'ctime': 1793556000}
    ]}}).encode()
    row = sources.parse('cls', raw)[0]
    assert row['source_id'] == '2199431' and row['title'] == '公司公告'
    assert row['published_at'] == '2026-11-01T18:00:00+00:00'
    assert row['url'] == 'https://www.cls.cn/detail/2199431'


def test_provider_error_with_rows_is_not_accepted():
    raw = json.dumps({'errno': 403, 'data': {'roll_data': [
        {'id': 1, 'title': 'stale cache', 'content': '', 'ctime': 1793556000}
    ]}}).encode()
    with pytest.raises(ValueError): sources.parse('cls', raw)
    with pytest.raises(ValueError): sources.parse('em', b'{"code":"0","data":{"fastNewsList":[]}}')


def test_collect_recovery_deduplicates_and_preserves_failure_evidence(tmp_path, monkeypatch):
    from backend.news import NewsStore
    from backend.store import Store
    from backend import news_worker as worker
    n = NewsStore(Store(tmp_path / 'news.sqlite3'))
    payload = json.dumps({'errno': 0, 'data': {'roll_data': [
        {'id': 2199431, 'title': '公司公告', 'content': '公告摘要', 'ctime': 1793556000}
    ]}}).encode()
    with n.store.connect() as db:
        db.execute('INSERT INTO news_sources VALUES(?,?)', ('cls', json.dumps({'ok': False, 'failures': 4})))
    monkeypatch.setattr(worker, 'fetch', lambda *args: (payload, {}))
    first = worker.collect(n, 'cls')
    second = worker.collect(n, 'cls')
    assert first['ok'] and second['ok'] and second['failures'] == 0 and second['error'] is None
    with n.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM news_items').fetchone()[0] == 1
    response = requests.Response(); response.status_code = 503
    def unavailable(*args): raise requests.HTTPError('untrusted error text', response=response)
    monkeypatch.setattr(worker, 'fetch', unavailable)
    failed = worker.collect(n, 'cls')
    assert not failed['ok'] and failed['last_success'] == second['last_success']
    assert failed['error'].startswith('HTTP 503') and 'untrusted' not in failed['error']


def test_proxy_dns_only_resolves_synthetic_network(monkeypatch):
    from backend import news_network
    monkeypatch.setenv('SECTOR_NEWS_PUBLIC_DNS', '1')
    monkeypatch.setattr(news_network, 'public_dns_answers', lambda _: ['93.184.216.34'])
    assert news_network.public_addresses('www.cls.cn', ['198.18.0.1']) == ['93.184.216.34']
    for values in (['127.0.0.1'], ['198.18.0.1', '10.0.0.1']):
        with pytest.raises(ValueError):
            news_network.public_addresses('www.cls.cn', values)
    monkeypatch.setattr(news_network, 'public_dns_answers', lambda _: ['93.184.216.34', '169.254.169.254'])
    with pytest.raises(ValueError):
        news_network.public_addresses('www.cls.cn', ['198.19.1.1'])
    monkeypatch.delenv('SECTOR_NEWS_PUBLIC_DNS')
    with pytest.raises(ValueError):
        news_network.public_addresses('www.cls.cn', ['198.18.0.1'])


def test_local_feed_pins_resolved_ip_and_rechecks_redirect(monkeypatch):
    from backend import news_network
    monkeypatch.setenv('SECTOR_NEWS_PUBLIC_DNS', '1')
    monkeypatch.setattr(sources.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('198.18.1.1', 443))])
    monkeypatch.setattr(news_network, 'public_dns_answers', lambda _: ['93.184.216.34'])
    calls = []
    class Pool:
        def __init__(self, ip, port, **kw):
            calls.append((ip, kw['server_hostname'], kw['assert_hostname']))
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, path, **kw):
            assert kw['headers']['Host'] == 'www.cls.cn' and not kw['redirect']
            from urllib3.response import HTTPResponse
            return HTTPResponse(status=302, headers={'Location':'https://127.0.0.1/internal'}, body=b'')
    monkeypatch.setattr(news_network.urllib3, 'HTTPSConnectionPool', Pool)
    with pytest.raises(ValueError, match='not allowed'):
        sources.fetch(sources.SOURCES['cls']['url'])
    assert calls == [('93.184.216.34','www.cls.cn','www.cls.cn')]
