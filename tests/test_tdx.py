import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.sources.base import SourceError
from backend.sources.tdx import TdxSource


def bar(stamp, **fields):
    date = datetime.fromisoformat(stamp)
    values = {'year': date.year, 'month': date.month, 'day': date.day,
              'hour': date.hour, 'minute': date.minute, 'open': 10, 'high': 11, 'low': 9,
              'close': 10.5, 'vol': 100, 'amount': 10500, **fields}
    return SimpleNamespace(**values)


def install_client(monkeypatch, pages):
    calls = []

    class Client:
        def __init__(self, host, port, **kwargs):
            self.host = host
            self.port = port

        def connect(self):
            calls.append(('connect', self.host, self.port))
            if self.host not in pages:
                raise TimeoutError('timeout')

        def close(self):
            calls.append(('disconnect', self.host))

        def get_bars(self, market, code, category, start, count):
            calls.append(('bars', category, market, code, start, count))
            return pages[self.host]

    monkeypatch.setitem(sys.modules, 'xmtdx', SimpleNamespace(TdxClient=Client, TdxError=RuntimeError,
                        KlineCategory=SimpleNamespace(DAY_ALT=9, MIN_1_ALT=8), Market=int))
    monkeypatch.setattr(TdxSource, 'min_interval', 0)
    return calls


def test_index_minutes_failover_and_timezone(monkeypatch):
    later = bar('2026-11-17 09:32', up_count=10, down_count=12)
    calls = install_client(monkeypatch, {'second': [later, bar('2026-11-17 09:31')]})
    result = TdxSource('first:7709,second:80').fetch_bars('sh000001', period='1m', count=2, offset=240)
    assert result['endpoint'] == 'second:80'
    assert [row['timestamp'] for row in result['rows']] == ['2026-11-17T09:31+08:00', '2026-11-17T09:32+08:00']
    assert result['rows'][1]['up_count'] == 10
    assert calls == [('connect', 'first', 7709), ('disconnect', 'first'), ('connect', 'second', 80),
                     ('bars', 8, 1, '000001', 240, 2), ('disconnect', 'second')]


def test_bad_daily_data_is_not_returned(monkeypatch):
    calls = install_client(monkeypatch, {'bad': [bar('2026-11-17 15:00', close=float('nan'))]})
    with pytest.raises(SourceError, match='量价异常'):
        TdxSource('bad:7709').fetch_bars('sz000001')
    assert ('bars', 9, 0, '000001', 0, 20) in calls
