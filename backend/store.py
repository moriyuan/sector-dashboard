"""Append-only observations; every API read is date- and phase-scoped."""
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RULE_VERSION = 'sector-v1.0'
TZ = ZoneInfo('Asia/Shanghai')


def now_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS snapshots (
              id INTEGER PRIMARY KEY, kind TEXT, trade_date TEXT, mode TEXT,
              source_time TEXT, collected_at TEXT, rule_version TEXT, sha256 TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS snapshot_lookup ON snapshots(kind,trade_date,mode,id);
            CREATE TABLE IF NOT EXISTS observations (
              id INTEGER PRIMARY KEY, key TEXT, observed_at TEXT, sha256 TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS observation_lookup ON observations(key,id);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY, job TEXT, time TEXT, ok INTEGER, message TEXT);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def save(self, kind, trade_date, mode, payload, source_time=None):
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self.connect() as db:
            cursor = db.execute('INSERT INTO snapshots(kind,trade_date,mode,source_time,collected_at,rule_version,sha256,payload) VALUES(?,?,?,?,?,?,?,?)',
                                (kind, trade_date, mode, source_time, now_iso(), RULE_VERSION, hashlib.sha256(text.encode()).hexdigest(), text))
            return cursor.lastrowid

    def snapshot(self, kind, date, mode, revision=None):
        sql = 'SELECT * FROM snapshots WHERE kind=? AND trade_date=? AND mode=?'
        args = [kind, date, mode]
        if revision is not None:
            sql += ' AND id=?'
            args.append(revision)
        with self.connect() as db:
            row = db.execute(sql + ' ORDER BY id DESC LIMIT 1', args).fetchone()
        if not row:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        return result

    def dates(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT trade_date,mode,MAX(id) revision FROM snapshots WHERE kind IN ('industry','concept','indices','market') GROUP BY trade_date,mode ORDER BY trade_date DESC, mode DESC")]

    def observe(self, key, payload):
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.connect() as db:
            old = db.execute('SELECT id,sha256 FROM observations WHERE key=? ORDER BY id DESC LIMIT 1',(key,)).fetchone()
            if old and old['sha256']==digest:
                return old['id']
            cursor=db.execute('INSERT INTO observations(key,observed_at,sha256,payload) VALUES(?,?,?,?)', (key, now_iso(), digest, text))
            return cursor.lastrowid

    def observation_record(self, key=None, revision=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM observations WHERE id=?',(revision,)).fetchone() if revision is not None else db.execute('SELECT * FROM observations WHERE key=? ORDER BY id DESC LIMIT 1',(key,)).fetchone()
        return {**dict(row),'payload':json.loads(row['payload'])} if row else None

    def observed(self, key, before=None):
        with self.connect() as db:
            sql = 'SELECT * FROM observations WHERE key=?'
            args = [key]
            if before:
                sql += ' AND observed_at<=?'
                args.append(before)
            row = db.execute(sql + ' ORDER BY id DESC LIMIT 1', args).fetchone()
        return json.loads(row['payload']) if row else None

    def event(self, job, ok, message):
        with self.connect() as db:
            db.execute('INSERT INTO events(job,time,ok,message) VALUES(?,?,?,?)', (job, now_iso(), int(ok), message[:800]))

    def events(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM events WHERE id IN (SELECT MAX(id) FROM events GROUP BY job) ORDER BY id DESC')]

    def history(self, kind, date, max_revision=None):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM snapshots WHERE kind=? AND mode='close' AND trade_date<=? AND id IN (SELECT MAX(id) FROM snapshots WHERE kind=? AND mode='close' AND id<=? GROUP BY trade_date) ORDER BY trade_date", (kind, date, kind, max_revision if max_revision is not None else 2**63-1)).fetchall()
        return [{'date': r['trade_date'], **json.loads(r['payload'])} for r in rows]
