from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.provider import DataError
from backend.rankings import build_rankings
from backend.schedule import phase
from backend.service import Collector
from backend.store import Store, TZ


DAY = '2026-11-03'
STAMP = DAY + 'T11:30:00+08:00'


def stock(**kw):
    return dict(dict(code='600001', name='测试', price=11, change=10, amount=100,
                     upper_limit=11, lower_limit=9, source_time=STAMP), **kw)


@pytest.mark.parametrize('clock,expected', [('11:59', 'lunch'), ('12:00', 'archive_midday'),
                                         ('12:59', 'archive_midday'), ('13:00', 'trading')])
def test_midday_window(clock, expected):
    current = datetime.fromisoformat(DAY + 'T' + clock).replace(tzinfo=TZ)
    assert phase(current, [DAY, '2026-11-04']) == expected
    assert phase(current, ['2026-11-04']) == 'holiday'


def test_midday_market_and_rankings_never_reuse_close_pool(tmp_path):
    db = Store(tmp_path / 'db')
    class Provider:
        def stocks(self):
            return [stock(), stock(code='600002', price=10, change=0, source_time=DAY + 'T10:00:00+08:00')]
    col = Collector(db, Provider())
    col.session_mode = 'midday'
    col.session_day = DAY
    assert col.market() == (DAY, 'midday')
    db.save('pools', DAY, 'close', {'pools': {'up': [{'code': '600001', 'boards': 5}]}})
    build_rankings(db, DAY, 'midday')
    snap = db.snapshot('market', DAY, 'midday')
    assert snap['payload']['stats']['eligible'] == 1
    assert db.snapshot('market', DAY, 'close') is None
    row = db.snapshot('gainers', DAY, 'midday')['payload']['rows'][0]
    assert row['boards'] is None
    assert row['limit_status'] == 'sealed_height_unknown'
    for stamp in ('2026-11-02T15:00:00+08:00', DAY+'T15:00:00+08:00'):
        col.provider.stocks = lambda: [stock(source_time=stamp)]
        with pytest.raises(DataError):
            col.market()


def test_api_midday_is_exact_mode_and_does_not_leak_close(tmp_path):
    db = Store(tmp_path / 'db')
    midday = db.save('market', DAY, 'midday', {'stats': {'up': 1}}, STAMP)
    db.save('market', DAY, 'close', {'stats': {'up': 99}}, DAY+'T15:00:00+08:00')
    db.save('industry', DAY, 'midday', {'rows': [dict(code='BK0001', name='行业')]}, STAMP)
    db.observe('bars:BK0001', [dict(date='2026-11-02', close=100), dict(date=DAY, close=999)])
    db.save('gainers', DAY, 'midday', {'rows': [dict(stock(), is_st=False, industry=[], concepts=[])]}, STAMP)
    with TestClient(create_app(db, collect=False)) as c:
        dates = c.get('/api/dates').json()['snapshots']
        assert [(d['trade_date'], d['mode']) for d in dates] == [(DAY, 'close'), (DAY, 'midday')]
        got = c.get('/api/market', params={'date': DAY, 'mode': 'midday'}).json()
        assert got['stats']['up'] == 1 and got['meta']['revision'] == midday
        assert got['emotion'] is None and got['meta']['provisional'] is True
        assert c.get('/api/market', params={'date': DAY}).json()['stats']['up'] == 99
        assert c.get('/api/gainers', params={'date': DAY, 'mode': 'midday'}).json()['total'] == 1
        assert c.get('/api/gainers', params={'date': DAY}).json()['total'] == 0
        detail = c.get('/api/sectors/BK0001', params={'date': DAY, 'mode': 'midday'}).json()
        assert all(b['date'] < DAY for b in detail['bars'])
        assert c.get('/api/sectors', params={'date': '2026-11-02', 'mode': 'midday'}).json()['rows'] == []
        assert c.get('/api/status').json()['midday_times'] == ['12:00']


def test_midday_checkpoint_survives_restart_and_skips_disclosures(tmp_path, monkeypatch):
    import backend.service as service
    current = [datetime(2026, 11, 3, 12, tzinfo=TZ)]
    class Clock:
        @staticmethod
        def now(tz):
            return current[0]
    monkeypatch.setattr(service, 'datetime', Clock)
    monkeypatch.setattr('backend.store.now_iso', lambda: current[0].isoformat(timespec='seconds'))
    class Provider:
        concept_source = 'ths'
        def calendar(self):return [DAY, '2026-11-04']
        def begin_session(self, day, mode='close'):self.session = (day, mode)
        def stocks(self):return [stock()]
        def indices(self):return [dict(code='000001', price=100, source_time=STAMP)]
        def boards(self, kind):
            # Even while providers are fetching the first board, stock prices
            # must be readable without claiming any new board membership.
            early = db.snapshot('gainers', DAY, 'midday')
            assert early and early['payload']['rows'][0]['price'] == 11
            assert early['payload']['input_refs']['concept'] is None
            return [dict(code='BK0001' if kind == 'industry' else 'THS300001', name=kind, price=100, change=1, amount=10, source_time=STAMP)]
        def supports_flow(self, kind):return False
        def pool(self, *args):raise AssertionError('午间不得采集盘后池')
    db = Store(tmp_path / 'db')
    provider = Provider()
    col = Collector(db, provider)
    assert col.cycle() is True
    assert provider.session == (DAY, 'midday')
    assert db.snapshot('gainers', DAY, 'midday')['payload']['input_refs']['concept'] == db.snapshot('concept_ths', DAY, 'midday')['id']
    assert not db.snapshot('pools', DAY, 'midday')
    restarted = Collector(db, Provider())
    assert restarted.cycle(initial=True) is False
    assert restarted.session_day == DAY and restarted.session_mode == 'midday'
    assert restarted.provider.session == (DAY, 'midday')
    current[0] = datetime(2026, 11, 3, 13, tzinfo=TZ)
    assert col.cycle() is False
