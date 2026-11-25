import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.kaipanla_live import LiveCollector, claim_slot, router, scheduled_slot, view
from backend.sources.base import SourceError
from backend.sources.kaipanla import KaipanlaSource, source_date, streak
from backend.sources.kpl_transport import KplTransport
from backend.store import Store, TZ

DAY = '2026-11-19'
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=TZ)


def pool_row(code='600001', level=1, minute=31):
    row = [None] * 23
    row[0:2] = [code, '测试股']
    row[4] = int(NOW.replace(hour=9, minute=minute).timestamp())
    row[5], row[12], row[15] = '芯片', '芯片、设备', level
    return row


@pytest.mark.parametrize('value', [None, '2026-11-18', ['2026-11-18', DAY], [DAY, '2026-11-18']])
def test_rejects_missing_old_or_ambiguous_date(value):
    with pytest.raises(SourceError):
        source_date(value, DAY)


def test_no_misclassification_of_n_days_m_boards():
    assert streak('3天2板') is None
    assert streak('3天3板') == 3
    assert streak('首板') == 1


def test_replay_ignores_misleading_day_field():
    src = KaipanlaSource()
    src._post = Mock(return_value={'date': '2026-11-18', 'Day': [DAY]})
    with pytest.raises(SourceError, match='来源日期'):
        src.fetch_resumption(DAY)
    assert src._post.call_count == 2


def test_replay_rejects_truncated_stock_universe():
    src = KaipanlaSource()
    with pytest.raises(SourceError, match='覆盖不足'):
        src._parse_resumption({'nums': {'ZT': 2, 'DT': 1, 'SZJS': 1, 'XDJS': 2}, 'list': []}, DAY)


def test_pages_follow_total_even_if_server_caps_page_size():
    src = KaipanlaSource()
    records = [[f'{i:06d}'] for i in range(123)]
    calls = []
    def fetch(index):
        calls.append(index)
        return {'list': records[index:index + 30], 'Count': len(records)}
    assert len(src._pages(fetch, lambda p: p['list'], total_field='Count')) == 123
    assert calls == [0, 30, 60, 90, 120]


def test_history_page_count_is_not_total():
    src = KaipanlaSource()
    records = [[f'{i:06d}'] for i in range(123)]
    def fetch(index):
        page = records[index:index + 60]
        return {'list': page, 'Count': len(page)}
    assert len(src._pages(fetch, lambda p: p['list'], total_field='Count', page_count=True)) == 123


@pytest.mark.parametrize('kind', ['repeated', 'drift', 'truncated'])
def test_bad_pagination_never_returns_partial_success(kind):
    src = KaipanlaSource()
    def fetch(index):
        if index:
            return {'Count': 2 if kind != 'drift' else 3,
                    'list': [] if kind == 'truncated' else [['600001']]}
        return {'Count': 2, 'list': [['600001']]}
    with pytest.raises(SourceError):
        src._pages(fetch, lambda p: p['list'], total_field='Count')


def test_pool_multiple_pages_and_time_sort():
    src = KaipanlaSource()
    src.page_size = 2
    rows = [pool_row('600001', minute=35), pool_row('600002', minute=31), pool_row('600003', minute=33)]
    src._post = lambda url, p: {'info': [rows[p['Index']:p['Index'] + 2], DAY]}
    assert [r['code'] for r in src.fetch_limit_pool(DAY, 1)] == ['600002', '600003', '600001']


def test_pool_date_checked_on_every_page():
    src = KaipanlaSource()
    src.page_size = 1
    src._post = lambda url, p: {'info': [[pool_row(str(600001 + p['Index']))], DAY if p['Index'] == 0 else '2026-11-18']}
    with pytest.raises(SourceError, match='来源日期'):
        src.fetch_limit_pool(DAY, 1)


def test_bad_height_is_source_error():
    src = KaipanlaSource()
    src._post = lambda *args: {'info': [{'Day': DAY, 'lbgd': None, 'ztjs': 10, 'strong': 50}]}
    with pytest.raises(SourceError):
        src.fetch_emotion(DAY)


class FakeSource:
    def __init__(self):
        self.failed = set()
        self.quote_time = NOW.isoformat()
        self.total = 1
        self.calls = []

    def close(self):
        pass

    def guard(self, name):
        self.calls.append(name)
        if name in self.failed:
            raise SourceError('模拟接口失败')

    def fetch_emotion(self, day):
        self.guard('emotion')
        return {'highest': 1, 'limit_up': self.total}

    def fetch_strength_board(self, day, **kwargs):
        self.guard('boards')
        return [{'code': '801001', 'strength': 100}]

    def fetch_market_mood(self, day):
        return {'date_validation': 'observation_only_no_source_date', 'values': {}}

    def fetch_rise_fall(self, day):
        return {'limit_up': self.total}

    def fetch_break_rate(self, day):
        return {}

    def fetch_drawdowns(self, day):
        return {'rows': []}

    def fetch_limit_pool(self, day, level):
        self.guard(f'pool{level}')
        return [{'code': '600001', 'boards': 1, 'limit_timestamp': 1}] if level == 1 else []

    def fetch_quote(self, code, day):
        return {'code': code, 'source_time': self.quote_time, 'price': 10}

    def fetch_sector_trend(self, code, day):
        return {'code': code}


@pytest.fixture
def collector(tmp_path):
    obj = LiveCollector(Store(tmp_path / 'data.db'), FakeSource(), clock=lambda: NOW)
    yield obj
    obj.close()


def test_emotion_failure_keeps_other_feeds_and_lower_tiers(collector):
    collector.source.failed = {'emotion'}
    result = collector.collect()
    assert result['feeds']['boards']['ok']
    assert result['feeds']['limit_pool_1']['ok']
    assert 'pool6' in collector.source.calls
    assert not result['ladder_complete'] and not result['ladder_height_verified']


def test_failed_refresh_preserves_original_timestamp_and_marks_cache(collector):
    first = collector.collect()
    collector.clock = lambda: NOW + timedelta(minutes=5)
    collector.source.failed = {'boards'}
    second = collector.collect()
    feed = second['feeds']['boards']
    assert not feed['ok'] and feed['cached']
    assert feed['last_success_at'] == first['feeds']['boards']['last_success_at']
    assert feed['received_at'] != feed['last_success_at']
    assert feed['data'] == first['feeds']['boards']['data']


def test_stale_quote_does_not_pass_just_because_date_matches(collector):
    collector.source.quote_time = NOW.replace(hour=9, minute=30).isoformat()
    result = collector.collect()
    assert not result['feeds']['quote_600001']['ok']
    assert result['feeds']['quote_600001']['freshness'] == 'stale'


def test_count_mismatch_cannot_claim_full_ladder(collector):
    collector.source.total = 2
    result = collector.collect()
    assert result['all_requested_feeds_ok']
    assert not result['ladder_complete']
    assert result['ladder_observed_count'] == 1


def test_previous_core_is_still_quoted_after_leaving_limit_pool(collector):
    first = collector.collect()
    assert first['core_watch'][0]['code'] == '600001'
    collector.source.fetch_emotion = lambda day: {'highest': 0, 'limit_up': 0}
    collector.source.total = 0
    second = collector.collect()
    assert second['ladder_complete'] and second['ladder_observed_count'] == 0
    assert second['feeds']['quote_600001']['ok']


def test_cache_never_crosses_trading_day(collector):
    collector.collect()
    collector.clock = lambda: NOW + timedelta(days=1)
    collector.source.failed = {'boards'}
    second = collector.collect()
    assert second['trade_date'] == '2026-11-20'
    assert not second['feeds']['boards']['cached']
    assert second['feeds']['boards']['data'] is None


def test_crossing_midnight_does_not_archive_under_wrong_date(collector):
    def boards(day, **kwargs):
        collector.clock = lambda: NOW + timedelta(days=1)
        return []
    collector.source.fetch_strength_board = boards
    with pytest.raises(SourceError, match='跨越午夜'):
        collector.collect()
    assert collector.store.snapshot('kaipanla_live', DAY, 'live') is None


def test_failed_history_replay_falls_back_to_valid_dated_route():
    src = KaipanlaSource()
    empty = {'date': DAY, 'nums': {'ZT': 0, 'DT': 0, 'SZJS': 0, 'XDJS': 0}, 'list': []}
    src._post = Mock(side_effect=[SourceError('历史入口不可用'), empty])
    assert src.fetch_resumption(DAY)['complete']


def test_non_list_empty_replay_is_not_valid_zero_stock_day():
    src = KaipanlaSource()
    with pytest.raises(SourceError, match='结构异常'):
        src._parse_resumption({'nums': {'ZT': 0, 'DT': 0, 'SZJS': 0, 'XDJS': 0}, 'list': {}}, DAY)


def test_expired_snapshot_and_history_api_never_fetch_upstream(collector):
    collector.store.observe('calendar', [DAY])
    collector.collect()
    calls = list(collector.source.calls)
    result = view(collector.store, DAY, NOW + timedelta(minutes=7))
    assert result['status'] == 'stale'
    assert result['feeds']['boards']['stale']
    app = FastAPI()
    app.include_router(router(collector.store))
    with TestClient(app) as client:
        assert client.get('/api/kaipanla/live', params={'date': DAY}).status_code == 200
        assert len(client.get('/api/kaipanla/history', params={'date': DAY}).json()['snapshots']) == 1
        assert client.get('/api/kaipanla/live?date=bad').status_code == 422
    assert collector.source.calls == calls


def test_schedule_calendar_lunch_close_and_restart_dedup(tmp_path):
    store = Store(tmp_path / 'schedule.db')
    assert scheduled_slot(NOW, []) is None
    assert scheduled_slot(NOW.replace(hour=12), [DAY]) is None
    assert scheduled_slot(NOW.replace(hour=15, minute=1), [DAY]) is None
    assert scheduled_slot(NOW.replace(hour=11, minute=30), [DAY])
    slot = scheduled_slot(NOW, [DAY])
    assert claim_slot(store, slot, NOW)
    assert not claim_slot(Store(store.path), slot, NOW + timedelta(seconds=200))
    later = NOW + timedelta(minutes=5)
    assert claim_slot(Store(store.path), scheduled_slot(later, [DAY]), later)


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self.headers = status, headers or {}
        self.content = json.dumps(data or {'errcode': 0}).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, size):
        yield self.content


def transport_with(monkeypatch, responses):
    transport = KplTransport(interval=0)
    session = Mock()
    session.post.side_effect = responses
    monkeypatch.setattr(transport, '_session', lambda: session)
    monkeypatch.setattr(transport, '_sleep', lambda seconds: None)
    return transport, session


URL = 'https://apphwhq.longhuvip.com/w1/api/index.php'


def test_network_retry_and_no_business_retry(monkeypatch):
    transport, session = transport_with(monkeypatch, [requests.Timeout(), Response()])
    assert transport.post(URL, {'a': 'Test'}, {})['errcode'] == 0
    assert session.post.call_count == 2
    assert session.post.call_args.kwargs['allow_redirects'] is False
    transport, session = transport_with(monkeypatch, [Response(data={'errcode': 1020})])
    with pytest.raises(SourceError):
        transport.post(URL, {'a': 'Test'}, {})
    assert session.post.call_count == 1


def test_retry_after_cooldown_blocks_following_calls(monkeypatch):
    transport, session = transport_with(monkeypatch, [Response(429, headers={'Retry-After': '120'})])
    for _ in range(2):
        with pytest.raises(SourceError):
            transport.post(URL, {'a': 'Test'}, {})
    assert session.post.call_count == 1


def test_nonfinite_raw_value_is_rejected_before_it_can_poison_snapshot(monkeypatch):
    response = Response()
    response.content = b'{"errcode":0,"raw":[NaN]}'
    transport, session = transport_with(monkeypatch, [response])
    with pytest.raises(SourceError, match='JSON'):
        transport.post(URL, {'a': 'Test'}, {})
    assert session.post.call_count == 1


def test_tls_failure_and_redirect_never_bypassed(monkeypatch):
    for response in [requests.exceptions.SSLError(), Response(302)]:
        transport, session = transport_with(monkeypatch, [response])
        with pytest.raises(SourceError):
            transport.post(URL, {'a': 'Test'}, {})
        assert session.post.call_count == 1


def test_transport_rejects_other_hosts_and_exhausted_budget(monkeypatch):
    transport, session = transport_with(monkeypatch, [])
    with pytest.raises(SourceError):
        transport.post('https://example.com/w1/api/index.php', {}, {})
    transport.set_deadline(0)
    with pytest.raises(SourceError):
        transport.post(URL, {}, {})
    session.post.assert_not_called()


def test_file_lock_is_exclusive_and_released_on_close(tmp_path):
    from backend.file_lock import flock, LOCK_EX, LOCK_NB
    path = tmp_path / 'test.lock'
    with path.open('a') as first, path.open('a') as second:
        flock(first, LOCK_EX | LOCK_NB)
        with pytest.raises(BlockingIOError):
            flock(second, LOCK_EX | LOCK_NB)
    with path.open('a') as third:
        flock(third, LOCK_EX | LOCK_NB)
