import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from . import retention as mod
from .retention import RetentionService, FILES, TZ, cutoff_day, process_lock

NOW = datetime(2026, 11, 24, 16, 11, tzinfo=TZ)


def fixture_db(root, days=45):
    with closing(sqlite3.connect(root / FILES[0])) as db, db:
        db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE blobs(sha TEXT PRIMARY KEY,codec TEXT,raw_bytes INTEGER,body BLOB);
            CREATE TABLE frames(id INTEGER PRIMARY KEY,day TEXT,view_hash TEXT,manifest TEXT);
        ''')
        def blob(value):
            h = hashlib.sha256(value).hexdigest()
            db.execute('INSERT OR IGNORE INTO blobs VALUES(?,?,?,?)', (h, 'test', len(value), value))
            return h
        common = blob(b'shared')
        for i in range(days):
            day = (NOW.date() - timedelta(days=i)).isoformat()
            h = blob(os.urandom(12000))
            db.execute('INSERT INTO frames(day,view_hash,manifest) VALUES(?,?,?)',
                       (day, h, json.dumps({'feed': {'body_hash': common}})))
        blob(b'orphan')
    with closing(sqlite3.connect(root / FILES[1])) as db, db:
        db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE core_quotes(day TEXT,code TEXT,received_at TEXT,source_time TEXT,body TEXT);
            CREATE TABLE quote_attempts(code TEXT PRIMARY KEY,state TEXT);
            INSERT INTO quote_attempts VALUES('600001','success');''')
        for i in range(days):
            day = (NOW.date() - timedelta(days=i)).isoformat()
            db.execute('INSERT INTO core_quotes VALUES(?,?,?,?,?)',
                       (day, '600001', day+'T15:00:00+08:00', None, '{}'))
    # Other files represent explicitly excluded user data.
    (root / 'wind-core.sqlite3').write_bytes(b'manual-core-unchanged')
    (root / 'broadcast-original.sqlite3').write_bytes(b'broadcast-unchanged')
    return common


def read(root, name, sql):
    with closing(sqlite3.connect(root / name)) as db:
        return db.execute(sql).fetchall()


def run(service):
    assert service.tick()
    service.worker.join(8)
    assert not service.running
    return service.status()


def test_exact_30_calendar_days_shared_blobs_reclaim_and_idempotence(tmp_path):
    common = fixture_db(tmp_path)
    service = RetentionService(tmp_path, now=lambda: NOW)
    state = run(service)
    assert state['state'] == 'complete', state
    assert state['deleted_frames'] == state['deleted_quotes'] == 15
    assert read(tmp_path, FILES[0], 'SELECT MIN(day),COUNT(*) FROM frames') == [('2026-10-26', 30)]
    assert read(tmp_path, FILES[1], 'SELECT MIN(day),COUNT(*) FROM core_quotes') == [('2026-10-26', 30)]
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM blobs') == [(31,)]
    assert read(tmp_path, FILES[0], "SELECT COUNT(*) FROM blobs WHERE sha='"+common+"'") == [(1,)]
    assert state['reclaimed_bytes'] > 0
    assert all(r['reclaimed_bytes'] == max(0, r['before_bytes']-r['after_bytes']) for r in state['databases'].values())
    assert read(tmp_path, FILES[1], 'SELECT * FROM quote_attempts') == [('600001', 'success')]
    assert (tmp_path/'wind-core.sqlite3').read_bytes() == b'manual-core-unchanged'
    assert (tmp_path/'broadcast-original.sqlite3').read_bytes() == b'broadcast-unchanged'
    assert not service.tick()
    assert not RetentionService(tmp_path, now=lambda: NOW).due()
    # Direct repeated maintenance is also logically idempotent.
    service._run()
    assert service.status()['deleted_frames'] == service.status()['deleted_quotes'] == 0


@pytest.mark.parametrize('mutation', [
    "UPDATE frames SET manifest='bad' WHERE day='2026-11-24'",
    "UPDATE frames SET manifest='{}',view_hash='missing' WHERE day='2026-11-24'",
    "UPDATE frames SET manifest='[]' WHERE day='2026-11-24'",
    "UPDATE frames SET manifest='{\"a\":{}}' WHERE day='2026-11-24'",
    "DELETE FROM blobs WHERE sha=(SELECT view_hash FROM frames WHERE day='2026-11-24')",
    "UPDATE frames SET day='2026-2-3' WHERE day='2026-11-24'",
])
def test_bad_retained_references_or_dates_rollback_one_db(tmp_path, mutation):
    fixture_db(tmp_path)
    with closing(sqlite3.connect(tmp_path/FILES[0])) as db, db:
        db.execute(mutation)
    state = run(RetentionService(tmp_path, now=lambda: NOW))
    assert state['state'] == 'partial'
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(45,)]
    assert read(tmp_path, FILES[1], 'SELECT COUNT(*) FROM core_quotes') == [(30,)]
    assert state['deleted_frames'] == 0 and state['deleted_quotes'] == 15
    assert state['retry_at'] > NOW.isoformat()


def test_empty_and_all_expired(tmp_path):
    fixture_db(tmp_path, 0)
    service = RetentionService(tmp_path, now=lambda: NOW)
    assert run(service)['state'] == 'complete'
    with closing(sqlite3.connect(tmp_path/FILES[0])) as db, db:
        db.execute("INSERT INTO frames(day,view_hash,manifest) VALUES('2020-01-01','expired','invalid old manifest')")
    service._run()
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(0,)]
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM blobs') == [(0,)]


def test_timezone_schedule_busy_catchup_and_backoff(tmp_path):
    fixture_db(tmp_path)
    current = [NOW.replace(hour=14)]
    service = RetentionService(tmp_path, now=lambda: current[0])
    assert cutoff_day(datetime(2026,11,23,16,1,tzinfo=timezone.utc)) == '2026-10-26'
    assert not service.due()
    current[0] = NOW.replace(hour=15,minute=11)
    assert not service.due()  # installed today, no invented missed run yesterday
    current[0] = NOW
    service.busy = lambda: True
    assert not service.due()
    service.busy = lambda: False
    assert service.due()
    current[0] = NOW + timedelta(days=1)
    current[0] = current[0].replace(hour=8)
    assert service.due()  # yesterday's missed job, outside exclusion
    assert run(service)['state'] == 'complete'
    current[0] = current[0].replace(hour=16,minute=11)
    assert not service.due()  # success earlier today


def test_database_lock_wait_is_short_and_other_db_completes(tmp_path):
    fixture_db(tmp_path)
    with closing(sqlite3.connect(tmp_path/FILES[0])) as db:
        db.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        state = run(RetentionService(tmp_path, now=lambda: NOW))
        assert time.monotonic()-started < 2
        assert state['state'] == 'partial'
        assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(45,)]
        db.rollback()


def test_busy_cancels_after_delete_before_commit(tmp_path, monkeypatch):
    fixture_db(tmp_path)
    busy = [False]
    original = mod.prune
    def preempt(*args):
        result = original(*args)
        busy[0] = True
        return result
    monkeypatch.setattr(mod, 'prune', preempt)
    state = run(RetentionService(tmp_path, busy=lambda: busy[0], now=lambda: NOW))
    assert state['state'] == 'failed'
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(45,)]
    assert read(tmp_path, FILES[1], 'SELECT COUNT(*) FROM core_quotes') == [(45,)]


def test_budget_rolls_back_and_retries(tmp_path):
    fixture_db(tmp_path)
    service = RetentionService(tmp_path, now=lambda: NOW, budget=0)
    state = run(service)
    assert state['state'] == 'failed'
    assert not service.due()
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(45,)]


def test_low_disk_and_vacuum_timeout_keep_logical_deletes(tmp_path, monkeypatch):
    fixture_db(tmp_path)
    monkeypatch.setattr(mod.shutil, 'disk_usage', lambda path: SimpleNamespace(free=0))
    service = RetentionService(tmp_path, now=lambda: NOW)
    state = run(service)
    assert state['state'] == 'partial'
    assert state['databases'][FILES[0]]['logical'] == 'complete'
    assert state['databases'][FILES[0]]['physical'] == 'pending'
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(30,)]
    monkeypatch.undo()
    service.vacuum_budget = 0
    service._run()
    assert service.status()['state'] == 'partial'
    assert service.status()['deleted_frames'] == 15
    service.vacuum_budget = 20
    service._run()
    assert service.status()['state'] == 'complete'
    assert service.status()['deleted_frames'] == 15
    assert read(tmp_path, FILES[0], 'PRAGMA integrity_check') == [('ok',)]


def test_process_lock_and_missing_db_not_created(tmp_path):
    fixture_db(tmp_path)
    service = RetentionService(tmp_path, now=lambda: NOW)
    with process_lock(tmp_path/'retention.lock'):
        assert run(service)['state'] == 'failed'
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(45,)]
    (tmp_path/FILES[1]).unlink()
    service._run()
    assert service.status()['state'] == 'partial'
    assert not (tmp_path/FILES[1]).exists()


def test_old_seed_does_not_resurrect_and_refresh_recovers(tmp_path, monkeypatch):
    from .test_fast_preview import preview, Fake, complete
    monkeypatch.setattr(preview, 'now', lambda: NOW)
    archive = preview.SnapshotArchive(tmp_path/'history.sqlite3')
    seed = {'date': '2020-01-01', 'stocks': [{'code': 'old'}]}
    engine = preview.RefreshEngine(archive, seed, Fake())
    try:
        assert engine.data()['data']['noData'] and not engine.data()['data']['stocks']
        assert engine.verified_day is None
        html = preview.build_page(engine.data()['data'])
        assert '30日窗口内无有效'.encode() in html
        engine.request()
        assert complete(engine)['state'] == 'complete'
        assert not engine.data()['data'].get('noData')
        with pytest.raises(ValueError):
            archive.save('2020-01-01','test','',{},seed,{})
        assert archive.restore('2020-01-01') == (None,{})
    finally:
        engine.close()


def test_restore_retained_latest_and_quote_read_guard(tmp_path, monkeypatch):
    from .test_fast_preview import preview, Fake
    from . import full_pool_quotes
    from .full_pool_quotes import FullPoolQuotes
    monkeypatch.setattr(preview, 'now', lambda: NOW)
    monkeypatch.setattr(full_pool_quotes, 'clock', lambda: NOW)
    archive = preview.SnapshotArchive(tmp_path/'history.sqlite3')
    seed = json.loads((preview.ROOT/'test-seed.json').read_text(encoding='utf-8'))
    seed['date'] = '2026-10-26'
    archive.save(seed['date'], 'test', '', {}, seed, {})
    with closing(sqlite3.connect(archive.path)) as db, db:
        db.execute("INSERT INTO frames(day,trigger,started_at,finished_at,view_hash,manifest,summary) VALUES(?,?,?,?,?,?,?)",
                   (seed['date'],'test','','','missing','{}','{}'))
    engine = preview.RefreshEngine(archive, {'date': '2020-01-01'}, Fake())
    try:
        assert engine.data()['data']['date'] == '2026-10-26'
    finally:
        engine.close()
    fixture_db(tmp_path)
    tracker = object.__new__(FullPoolQuotes)
    tracker.path = tmp_path/FILES[1]
    with closing(sqlite3.connect(tracker.path)) as db, db:
        db.execute("UPDATE core_quotes SET code=day")
    assert len(tracker.last_known(NOW.isoformat())) == 30


def test_interrupt_during_large_sql_rolls_back(tmp_path, monkeypatch):
    fixture_db(tmp_path)
    original = mod.prune
    service = RetentionService(tmp_path, now=lambda: NOW, budget=.1)
    def expensive(db, name, cutoff):
        result = original(db, name, cutoff)
        db.execute('WITH RECURSIVE many(n) AS (SELECT 0 UNION ALL SELECT n+1 FROM many WHERE n<100000000) SELECT sum(n) FROM many').fetchone()
        return result
    monkeypatch.setattr(mod, 'prune', expensive)
    state = run(service)
    assert state['state'] == 'failed'
    assert read(tmp_path, FILES[0], 'SELECT COUNT(*) FROM frames') == [(45,)]


def test_weekly_compaction_and_free_page_threshold(tmp_path):
    fixture_db(tmp_path)
    service = RetentionService(tmp_path, now=lambda: NOW)
    run(service)
    # Last compaction today, less than 20% free: pages remain reusable.
    with closing(sqlite3.connect(tmp_path/FILES[0])) as db, db:
        data = os.urandom(12000)
        h = hashlib.sha256(data).hexdigest()
        db.execute('INSERT INTO blobs VALUES(?,?,?,?)',(h,'test',len(data),data))
    recent = service._database(FILES[0], cutoff_day(NOW), {'last_vacuum': '2026-11-24'})
    assert recent['reusable_bytes'] > 0
    weekly = service._database(FILES[0], cutoff_day(NOW), {'last_vacuum': '2026-11-17'})
    assert weekly['reusable_bytes'] == 0 and weekly['last_vacuum'] == '2026-11-24'


def test_incomplete_first_round_remains_explicitly_empty(tmp_path, monkeypatch):
    from .test_fast_preview import preview, Fake
    monkeypatch.setattr(preview, 'now', lambda: NOW)
    archive = preview.SnapshotArchive(tmp_path/'history.sqlite3')
    engine = preview.RefreshEngine(archive, {'date':'2020-01-01'}, Fake())
    try:
        view = engine._view({}, {'date':'2026-11-23'}, '2026-11-24', [], False)
        assert view['noData']
        archive.save(view['date'],'test','',{},view,{})
        restored, _ = archive.restore(view['date'])
        assert restored['noData'] and restored['market'] == {}
    finally:
        engine.close()
