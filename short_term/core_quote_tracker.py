"""Shared quote cache and bounded worker resources."""
import json
import math
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime

from backend.sources.kaipanla import KaipanlaSource
from backend.store import TZ
from .retention import cutoff_day


class CoreQuoteTracker:
    def __init__(self, path, source_factory=None):
        self.path = path
        self.source_factory = source_factory or (lambda:KaipanlaSource(timeout=3))
        self.lock = threading.Lock()
        self.running = False
        self.last_bucket = None
        self.cursor = 0
        self.runner = ThreadPoolExecutor(max_workers=1, thread_name_prefix='core-quote-round')
        self.workers = ThreadPoolExecutor(max_workers=3, thread_name_prefix='core-quote')
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS core_quotes (
                  day TEXT, code TEXT, received_at TEXT, source_time TEXT, body TEXT,
                  PRIMARY KEY(day,code,received_at));
                CREATE INDEX IF NOT EXISTS core_quotes_time ON core_quotes(day,received_at);
            ''')

    def available(self, day, cutoff):
        if day < cutoff_day():
            return {}
        with closing(sqlite3.connect(self.path, timeout=3)) as db:
            rows = db.execute('SELECT code,received_at,source_time,body FROM core_quotes '
                              'WHERE day=? AND julianday(received_at)<=julianday(?) AND julianday(source_time)<=julianday(?) ORDER BY received_at',
                              (day,cutoff,cutoff)).fetchall()
        values = {}
        for code, received, origin, body in rows:
            age = (datetime.fromisoformat(cutoff)-datetime.fromisoformat(origin)).total_seconds()
            if 0 <= age <= 120:
                values[code] = {**json.loads(body),'received_at':received,'age_seconds':round(age,1)}
        return values

    def last_known(self, cutoff):
        """Display-only last price; it never qualifies as fresh analysis evidence."""
        with closing(sqlite3.connect(self.path, timeout=3)) as db:
            rows=db.execute('''SELECT code,received_at,source_time,body FROM (
                SELECT code,received_at,source_time,body,ROW_NUMBER() OVER
                (PARTITION BY code ORDER BY received_at DESC) n FROM core_quotes
                WHERE day>=? AND received_at<=? AND source_time<=?) WHERE n=1''',(cutoff_day(),cutoff,cutoff)).fetchall()
        return {code:{**json.loads(body),'received_at':received} for code,received,origin,body in rows}

    def close(self):
        self.runner.shutdown(wait=True)
        self.workers.shutdown(wait=True)
