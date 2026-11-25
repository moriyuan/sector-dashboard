import copy
import hashlib
import json
import math
import secrets
import sqlite3
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from backend.sources.kaipanla import KaipanlaSource, code_value
from backend.sources.base import SourceError
from backend.store import TZ
from .ten_day_source import PreviewSource
from .retention import cutoff_day
REFRESH_SECONDS = 60


def now():
    return datetime.now(TZ)


def stamp():
    return now().isoformat(timespec='milliseconds')


def encode(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True).encode()


def slim(data):
    return {k: v for k, v in data.items() if k != 'raw'}


def active_time(current):
    clock = current.strftime('%H:%M')
    return current.weekday() < 5 and ('09:30' <= clock <= '11:30' or '13:00' <= clock <= '15:00')


class SnapshotArchive:
    def __init__(self, path):
        self.path = Path(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS blobs (
                    sha TEXT PRIMARY KEY, codec TEXT NOT NULL, raw_bytes INTEGER NOT NULL, body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS frames (
                    id INTEGER PRIMARY KEY, day TEXT NOT NULL, trigger TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                    view_hash TEXT NOT NULL REFERENCES blobs(sha), manifest TEXT NOT NULL, summary TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS frames_day ON frames(day,id);
            ''')

    @staticmethod
    def _blob(db, data):
        raw = encode(data)
        digest = hashlib.sha256(raw).hexdigest()
        db.execute('INSERT OR IGNORE INTO blobs VALUES(?,?,?,?)',
                   (digest, 'zlib-json-v1', len(raw), zlib.compress(raw, 6)))
        return digest

    def save(self, day, trigger, started_at, feeds, view, metrics):
        if not cutoff_day(now()) <= day <= now().date().isoformat():
            raise ValueError('快照日期超出30日保留窗口')
        # One atomic transaction per completed round. Partial UI updates stay in memory.
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            manifest = {}
            for name, feed in feeds.items():
                metadata = {k: v for k, v in feed.items() if k != 'data'}
                metadata['body_hash'] = self._blob(db, feed['data']) if feed.get('data') is not None else None
                manifest[name] = metadata
            view_hash = self._blob(db, view)
            summary = {'emotion': view.get('emotion'), 'riseFall': view.get('riseFall'),
                       'ladderComplete': view.get('ladderComplete'), 'metrics': metrics}
            cursor = db.execute('INSERT INTO frames(day,trigger,started_at,finished_at,view_hash,manifest,summary) '
                'VALUES(?,?,?,?,?,?,?)', (day, trigger, started_at, stamp(), view_hash,
                                         encode(manifest).decode(), encode(summary).decode()))
            return cursor.lastrowid

    def restore(self, day):
        if not cutoff_day(now()) <= day <= now().date().isoformat():
            return None, {}
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            rows = db.execute('SELECT view_hash,manifest FROM frames WHERE day=? ORDER BY id DESC', (day,))
            def body(digest):
                blob = db.execute('SELECT codec,body FROM blobs WHERE sha=?', (digest,)).fetchone()
                if not blob or blob[0] != 'zlib-json-v1':
                    raise ValueError('Unsupported snapshot codec')
                raw = zlib.decompress(blob[1])
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError('Snapshot hash mismatch')
                return json.loads(raw)
            for row in rows:
                try:
                    feeds = json.loads(row[1])
                    for feed in feeds.values():
                        feed['data'] = body(feed['body_hash']) if feed['body_hash'] else None
                    view = body(row[0])
                    if not isinstance(view, dict) or view.get('date') != day:
                        continue
                    return view, feeds
                except (ValueError, TypeError, KeyError, AttributeError, zlib.error):
                    continue
            return None, {}

    def latest_day(self, cutoff):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            return db.execute('SELECT MAX(day) FROM frames WHERE day<=? AND day>=?', (cutoff, cutoff_day(now()))).fetchone()[0]

    def stats(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            count, original, compressed = db.execute('SELECT COUNT(*),COALESCE(SUM(raw_bytes),0),'
                                                     'COALESCE(SUM(length(body)),0) FROM blobs').fetchone()
            frames = db.execute('SELECT COUNT(*) FROM frames').fetchone()[0]
        return {'frames': frames, 'unique_bodies': count, 'raw_bytes': original, 'compressed_bytes': compressed,
                'database_bytes': self.path.stat().st_size,
                'wal_bytes': Path(str(self.path) + '-wal').stat().st_size if Path(str(self.path) + '-wal').exists() else 0,
                'retention': '30_calendar_days', 'codec': 'zlib-json-v1'}


class RefreshEngine:
    def __init__(self, archive, seed, source=None):
        self.archive = archive
        self.source = source or PreviewSource(timeout=4)
        if hasattr(self.source, 'transport'):
            self.source.transport.retries = 1
        self.lock = threading.RLock()
        self.workers = ThreadPoolExecutor(max_workers=3, thread_name_prefix='preview-feed')
        self.runner = ThreadPoolExecutor(max_workers=1, thread_name_prefix='preview-refresh')
        today = now().date().isoformat()
        restored, feeds = None, {}
        candidate = archive.latest_day(today)
        while candidate:
            try:
                restored, recovered_feeds = archive.restore(candidate)
                if restored:
                    if candidate == today:
                        feeds = recovered_feeds
                    break
            except (ValueError, TypeError, KeyError, zlib.error):
                pass
            candidate = archive.latest_day((date.fromisoformat(candidate)-timedelta(days=1)).isoformat())
        valid_seed = cutoff_day(now()) <= seed.get('date', '') <= today
        self.latest = restored or (copy.deepcopy(seed) if valid_seed else {
            'date': today, 'capturedAt': '', 'source': '30日窗口内无有效行情，待获取',
            'noData': True, 'market': {}, 'emotion': {}, 'riseFall': {}, 'boards': [],
            'stocks': [], 'drawdowns': [], 'trends': {}, 'sectionStatus': {}, 'ladderComplete': False})
        self.feeds = feeds
        self.revision = 1
        self.job = {'state': 'idle', 'completed': 0, 'total': 0, 'errors': []}
        self.last_started = -float('inf')
        self.selected = None
        self.verified_day = None if self.latest.get('noData') else self.latest.get('date')
        self.stop = threading.Event()
        self.last_slot = None
        self.day_probe = None
        self.last_probe = (None, -float('inf'))

    def close(self):
        self.stop.set()
        if hasattr(self.source, 'transport'):
            self.source.transport.stop = self.stop
        self.runner.shutdown(wait=True)
        self.workers.shutdown(wait=True)
        self.source.close()

    def state(self):
        with self.lock:
            current = now()
            day = current.date().isoformat()
            result = {'revision': self.revision, 'job': copy.deepcopy(self.job), 'server_day': day,
                      'cooldown_seconds': max(0, round(10 - (time.monotonic() - self.last_started), 1)),
                      'automatic': {'interval_seconds': REFRESH_SECONDS, 'active': active_time(current)
                          and self.verified_day == day,
                          'pause_reason': '非交易时段' if not active_time(current) else '待核验交易日' if self.verified_day != day else None,
                          'scope': '交易时段每1分钟更新；新交易日自动核验，午休和收盘后暂停'}}
            if self.job['state'] == 'running':
                result['job']['elapsed_ms'] = round((time.monotonic() - self.last_started) * 1000)
            return result

    def data(self):
        with self.lock:
            return {'revision': self.revision, 'data': copy.deepcopy(self.latest)}

    def request(self, selected=None, trigger='manual'):
        if selected is not None:
            code_value(selected)
        with self.lock:
            if self.job['state'] == 'running':
                return {**self.state(), 'joined_existing_job': True}
            if time.monotonic() - self.last_started < 10:
                return {**self.state(), 'reused_recent_result': True}
            self.selected = selected
            self.last_started = time.monotonic()
            self.job = {'id': secrets.token_hex(6), 'state': 'running', 'phase': '获取概况与板块',
                        'started_at': stamp(), 'completed': 0, 'total': 5, 'errors': [],
                        'first_data_ms': None, 'core_ms': None, 'elapsed_ms': 0}
            self.runner.submit(self._run, trigger)
            return self.state()

    def _measure(self, name, fn, deadline):
        transport = getattr(self.source, 'transport', None)
        if transport:
            transport.set_deadline(deadline)
        start = time.monotonic()
        try:
            if start >= deadline:
                raise SourceError('已达到本轮 20 秒时间预算')
            data = fn()
            if name.startswith('quote_'):
                source_time = datetime.fromisoformat(data['source_time'])
                current = now()
                if (source_time-current).total_seconds() > 10:
                    raise SourceError('盘口时间超前')
                reference = current
                if '11:30' <= current.strftime('%H:%M') < '13:00':
                    reference = current.replace(hour=11, minute=30, second=0, microsecond=0)
                elif current.hour >= 15:
                    reference = current.replace(hour=15, minute=0, second=0, microsecond=0)
                # A legitimate post-close publication is not a future quote.
                if (reference-source_time).total_seconds() > 180:
                    raise SourceError('盘口价格时间过旧')
            received = data.get('observed_at') if name=='tenDay' and isinstance(data,dict) else None
            return name, {'ok': True, 'data': data, 'received_at': received or stamp(),
                          'seconds': round(time.monotonic()-start, 3), 'cached': False}
        except Exception as exc:
            return name, {'ok': False, 'data': None, 'received_at': stamp(),
                          'error': str(exc)[:240], 'seconds': round(time.monotonic()-start, 3), 'cached': False}
        finally:
            if transport:
                transport.set_deadline(None)

    def _view(self, feeds, previous, day, pool_levels, pool_validated):
        view = copy.deepcopy(previous) if previous.get('date') == day else {
            'date': day, 'market': {}, 'emotion': {}, 'riseFall': {}, 'boards': [], 'stocks': [],
            'drawdowns': [], 'trends': {}, 'source': '开盘啦公开接口'}
        mapping = {'emotion': 'emotion', 'market': 'market', 'riseFall': 'riseFall',
                   'boards': 'boards', 'drawdowns': 'drawdowns', 'tenDay': 'tenDay'}
        for name, key in mapping.items():
            feed = feeds.get(name, {})
            if not feed.get('ok'):
                continue
            data = feed['data']
            if name == 'market':
                data = data['values']
            elif name == 'drawdowns':
                data = data['rows']
            elif name == 'boards':
                data = [slim(r) for r in data]
            elif name == 'tenDay':
                data = copy.deepcopy(slim(data))
            else:
                data = slim(data)
            view[key] = data
        old_quotes = {r['code']: r.get('quote') or {} for r in view.get('stocks', [])}
        old_ten_quotes = {r['code']: r.get('quote') or {} for r in previous.get('tenDay', {}).get('rows', [])}
        if view.get('tenDay'):
            for row in view['tenDay']['rows']:
                feed = feeds.get('quote_'+row['code'], {})
                if feed.get('ok'):
                    q = feed['data']
                    row['quote'] = {**slim(q), 'high': q['raw']['real'].get('high_px'),
                        'low': q['raw']['real'].get('low_px'), 'turnover': q['raw']['real'].get('turnover_ratio')}
                elif row['code'] in old_ten_quotes and previous.get('date') == day:
                    row['quote'] = old_ten_quotes[row['code']]
            ten_feed = feeds.get('tenDay', {})
            if ten_feed.get('ok'):
                view['tenDay']['observed_at'] = ten_feed['received_at']
        if pool_validated:
            stocks = []
            for level in pool_levels:
                for row in feeds[f'pool_{level}']['data']:
                    raw = row['raw']
                    price, change, amount = [float(raw[i]) for i in (21, 22, 11)]
                    if not all(math.isfinite(v) for v in (price, change, amount)) or price <= 0 or amount < 0:
                        raise SourceError('涨停池价格字段异常')
                    previous_quote = old_quotes.get(row['code'], {})
                    q = {'price': price, 'change': change, 'amount': amount, 'source_time': None,
                         'price_basis': 'current_limit_pool', 'observed_at': feeds[f'pool_{level}']['received_at'],
                         'reason': previous_quote.get('reason'), 'high': previous_quote.get('high'),
                         'low': previous_quote.get('low'), 'turnover': previous_quote.get('turnover'),
                         'detail_source_time': previous_quote.get('detail_source_time') or previous_quote.get('source_time')}
                    stocks.append({**{k:row[k] for k in ('code','name','boards','limit_time','limit_timestamp','theme','tags')},
                                   'quote': q})
            view['stocks'] = sorted(stocks, key=lambda r: (r['limit_timestamp'], r['code']))
        for row in view.get('stocks', []):
            feed = feeds.get('quote_'+row['code'], {})
            if feed.get('ok'):
                q = feed['data']
                # Prices remain consistent with the full ladder snapshot; details have their own timestamp.
                row['quote'].update(reason=q.get('reason'), high=q['raw']['real'].get('high_px'),
                    low=q['raw']['real'].get('low_px'), turnover=q['raw']['real'].get('turnover_ratio'),
                    detail_source_time=q['source_time'])
        trends = {name[6:]: f['data']['raw'] for name, f in feeds.items() if name.startswith('trend_') and f.get('ok')}
        view.setdefault('trends', {}).update(trends)
        view['sectionStatus'] = {name: {k:v for k,v in f.items() if k != 'data'} for name,f in feeds.items()}
        view['ladderComplete'] = bool(pool_validated)
        view['capturedAt'] = stamp()
        view['mode'] = 'progressive_refresh'
        if all(isinstance(view.get(section, {}).get(key), (int, float)) for section, key in
               (('market', 'SZJS'), ('emotion', 'highest'), ('riseFall', 'break_rate'))):
            view.pop('noData', None)
        else:
            view['noData'] = True
        return view

    def _run(self, trigger):
        start = self.last_started
        deadline = start + 20
        day = now().date().isoformat()
        with self.lock:
            previous = copy.deepcopy(self.latest)
            old_feeds = copy.deepcopy(self.feeds) if previous.get('date') == day else {}
        feeds, futures, levels = {}, {}, None
        extras_started, pool_validated = False, False
        def submit(name, fn):
            futures[self.workers.submit(self._measure, name, fn, deadline)] = name
        source = self.source
        try:
            for name, fn in [('emotion', lambda: source.fetch_emotion(day)),
                             ('market', lambda: source.fetch_market_mood(day)),
                             ('riseFall', lambda: source.fetch_rise_fall(day)),
                             ('boards', lambda: source.fetch_strength_board(day, include_raw=True)),
                             ('drawdowns', lambda: source.fetch_drawdowns(day))]:
                submit(name, fn)
            while futures:
                finished, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    name = futures.pop(future)
                    _, result = future.result()
                    if not result['ok'] and old_feeds.get(name, {}).get('data') is not None:
                        result.update(data=old_feeds[name]['data'], cached=True,
                                      last_success_at=old_feeds[name].get('last_success_at'))
                    elif result['ok']:
                        result['last_success_at'] = result['received_at']
                    feeds[name] = result
                    with self.lock:
                        self.job['completed'] += 1
                        if not result['ok']:
                            self.job['errors'].append(f'{name}: {result["error"]}')
                        elif self.job['first_data_ms'] is None:
                            self.job['first_data_ms'] = round((time.monotonic()-start)*1000)
                    if name == 'emotion':
                        highest = result['data']['highest'] if result['ok'] else max(6, previous.get('emotion', {}).get('highest', 0))
                        levels = list(range(1, highest+1))
                        for level in levels:
                            submit(f'pool_{level}', lambda level=level: source.fetch_limit_pool(day, level))
                        with self.lock:
                            self.job['total'] += len(levels)
                            self.job['phase'] = '更新完整梯队'
                    if name == 'tenDay' and result['ok']:
                        known = set(feeds) | set(futures.values())
                        for row in result['data']['rows']:
                            code = row['code']
                            if 'quote_'+code not in known:
                                submit('quote_'+code, lambda code=code: source.fetch_quote(code, day))
                                with self.lock:
                                    self.job['total'] += 1
                pool_done = levels is not None and all(f'pool_{n}' in feeds for n in levels)
                if pool_done and not extras_started:
                    extras_started = True
                    emotion = feeds['emotion']
                    rows = [r for n in levels for r in (feeds[f'pool_{n}']['data'] or []) if feeds[f'pool_{n}']['ok']]
                    pool_validated = (emotion['ok'] and all(feeds[f'pool_{n}']['ok'] for n in levels)
                        and len(rows) == len({r['code'] for r in rows}) == emotion['data']['limit_up']
                        and max((r['boards'] for r in rows), default=0) == emotion['data']['highest'])
                    if not pool_validated:
                        with self.lock:
                            self.job['errors'].append('梯队完整性未通过，保留旧名单和原始时间')
                    # Complete current prices already come from the bulk pool. Only core details need individual requests.
                    codes = [r['code'] for r in sorted(rows, key=lambda r: (-r['boards'],r['limit_timestamp']))[:4]]
                    if self.selected and self.selected in {r['code'] for r in rows} and self.selected not in codes:
                        codes.append(self.selected)
                    for code in codes:
                        submit('quote_'+code, lambda code=code: source.fetch_quote(code, day))
                    if hasattr(source, 'fetch_ten_day_leaders'):
                        submit('tenDay', lambda: source.fetch_ten_day_leaders(day))
                        with self.lock:
                            self.job['total'] += 1
                    with self.lock:
                        self.job['total'] += len(codes)
                        self.job['phase'] = '补充核心盘口与分时'
                if 'boards' in feeds and feeds['boards']['ok'] and not any(n.startswith('trend_') for n in feeds) and not any(n.startswith('trend_') for n in futures.values()):
                    # Queue trends after prices, keeping the ladder on the critical path.
                    if extras_started:
                        for row in feeds['boards']['data'][:3]:
                            submit('trend_'+row['code'], lambda code=row['code']: source.fetch_sector_trend(code, day))
                        with self.lock:
                            self.job['total'] += min(3, len(feeds['boards']['data']))
                if now().date().isoformat() != day:
                    raise SourceError('采集跨日，本轮不发布')
                if pool_validated and feeds.get('riseFall', {}).get('ok'):
                    if feeds['riseFall']['data']['limit_up'] != feeds['emotion']['data']['limit_up']:
                        pool_validated = False
                        with self.lock:
                            if '跨接口涨停总数不一致' not in self.job['errors']:
                                self.job['errors'].append('跨接口涨停总数不一致')
                current_view = self._view(feeds, previous, day, levels or [], pool_validated)
                core_ready = all(feeds.get(n, {}).get('ok') for n in ('emotion','market','riseFall','boards')) and pool_validated
                if core_ready:
                    with self.lock:
                        if self.job['core_ms'] is None:
                            self.job['core_ms'] = round((time.monotonic()-start)*1000)
                with self.lock:
                    self.latest = current_view
                    self.revision += 1
            elapsed = round((time.monotonic()-start)*1000)
            with self.lock:
                metrics = {k:self.job[k] for k in ('first_data_ms','core_ms')}
                metrics['total_ms'] = elapsed
                view = copy.deepcopy(self.latest)
                view['refreshMetrics'] = metrics
            revision = self.archive.save(day, trigger, self.job['started_at'], feeds, view, metrics)
            with self.lock:
                self.feeds = feeds
                self.latest = view
                self.latest['archiveRevision'] = revision
                self.revision += 1
                self.job.update(state='degraded' if self.job['errors'] else 'complete',
                                phase='更新完成', elapsed_ms=elapsed, metrics=metrics, archive_revision=revision)
                if pool_validated:
                    self.verified_day = day
        except Exception as exc:
            # Unfinished worker requests retain their deadline. Do not allow the next job to overlap them.
            if futures:
                wait(futures)
            with self.lock:
                self.job.update(state='failed', phase='更新失败，保留已显示数据',
                                elapsed_ms=round((time.monotonic()-start)*1000))
                self.job['errors'].append(str(exc)[:240])

    def _verify_day(self, day):
        transport = getattr(self.source, 'transport', None)
        try:
            if transport:
                transport.set_deadline(time.monotonic()+4)
            # This adapter verifies the response date. Holidays/stale sessions fail closed.
            self.source.fetch_emotion(day)
            with self.lock:
                if now().date().isoformat() == day:
                    self.verified_day = day
        except Exception:
            pass
        finally:
            if transport:
                transport.set_deadline(None)

    def schedule_tick(self, current=None):
        current = current or now()
        if not active_time(current):
            return
        day = current.date().isoformat()
        with self.lock:
            if self.verified_day != day:
                last_day, attempted = self.last_probe
                if self.job['state'] != 'running' and (self.day_probe is None or self.day_probe.done()) and (last_day != day or time.monotonic()-attempted >= 300):
                    self.last_probe = (day, time.monotonic())
                    self.day_probe = self.runner.submit(self._verify_day, day)
                return
            slot = day + '/' + str((current.hour*3600+current.minute*60)//REFRESH_SECONDS)
            if slot == self.last_slot:
                return
            self.request(trigger='scheduled')
            # One attempt per minute, including joined jobs and recent manual results.
            self.last_slot = slot

    def schedule(self):
        while not self.stop.wait(1):
            self.schedule_tick()


def build_page(seed):
    template = (ROOT/'terminal-template.html').read_text(encoding='utf-8')
    reading_start = template.index('      <section class="kt-reading">')
    reading_end = template.index('      <section class="kt-panel kt-trend-panel">', reading_start)
    template = template[:reading_start] + (ROOT/'broadcast-card.html').read_text(encoding='utf-8') + '\n' + template[reading_end:]
    reading_start = template.index('function renderReading(){')
    reading_end = template.index('function renderTrend(){', reading_start)
    template = template[:reading_start] + 'function renderReading(){}' + '\n' + template[reading_end:]
    template = template.replace('</style>', (ROOT/'broadcast-card.css').read_text(encoding='utf-8')+'\n</style>', 1)
    template = template.replace('<h3>日内核心前排</h3><span>高度 / 时间优先</span></header>',
        '<h3>日内核心前排</h3><span data-core-scope>合并观察池</span></header>'
        '<div class="rc-tabs"><button data-core-tab="today" aria-pressed="true">今日前排</button>'
        '<button data-core-tab="recent" aria-pressed="false">近期前排核心 <b data-recent-count>—</b></button></div>'
        '<div data-recent-pane hidden></div>')
    template = template.replace('<tr><th>个股</th><th>身位</th>', '<tr data-core-head><th>个股</th><th>身位</th>')
    template = template.replace('<div class="kt-table-scroll"><table class="kt-table"><thead><tr data-core-head>',
        '<div class="kt-table-scroll" tabindex="0" role="region" aria-label="日内核心前排，可滚动查看全部个股"><table class="kt-table"><thead><tr data-core-head>')
    template = template.replace('<div class="kt-tape" data-tape>',
        '<div class="kt-tape" data-tape tabindex="0" role="region" aria-label="今日涨停时间流，可滚动查看全部个股">')
    template = template.replace('按时间查看先后顺序', '列表内滚动查看全部')
    core_start = template.index('function renderCore(){')
    core_end = template.index('function renderLadder(){', core_start)
    template = template[:core_start] + template[core_end:]
    template = template.replace('function renderLadder(){',
        (ROOT/'ten-day-core.js').read_text(encoding='utf-8')+'\nfunction renderLadder(){')
    detail_start = template.index('function renderDetail(){')
    detail_end = template.index('function renderLoss(){', detail_start)
    template = template[:detail_start] + (ROOT/'tracking-card.js').read_text(encoding='utf-8') + '\n' + template[detail_end:]
    template = template.replace('<script>\n(()=>{', '<script>'+ (ROOT/'tracking-model.js').read_text(encoding='utf-8') + '</script>\n<script>\n(()=>{', 1)
    template = template.replace('</style>', (ROOT/'tracking-card.css').read_text(encoding='utf-8')+'\n</style>', 1)
    template = template.replace('if(first)state.stock=first.code;', '')
    template = template.replace("state.stock=button.dataset.stock;renderCore();renderLadder();renderTape();renderDetail();remember();", "trackSelect(button.dataset.stock);")
    template = template.replace('const D=JSON.parse', 'let D=JSON.parse')
    template = template.replace('const sorted=D.stocks', 'let sorted=D.stocks')
    template = template.replace('const counts={};', 'let counts={};')
    template = template.replace('const carriers=Object.entries', 'let carriers=Object.entries')
    template = template.replace('盘口来源时间 ', '盘口补充时间 ')
    template = template.replace("q?.source_time?.slice(11,19)", "(q?.detail_source_time||q?.source_time)?.slice(11,19)")
    template = template.replace(' · 涨停原因来自供应商', ' · 价格/成交额来自本轮涨停池；补充盘口单独计时')
    template = template.replace('当前页面为单次真实快照，不自动更新。', '交易时段每1分钟自动更新，也可手动后台刷新。')
    template = template.replace('本次预览不自动刷新', '交易时段每1分钟更新')
    template = template.replace('所有价格和涨幅均来自真实盘口', '价格、涨幅、成交额来自本轮涨停池，核心盘口与原因另行补充')
    template = template.replace('metrics();renderLoss();render();', '''
root.addEventListener('kpl-recent-ranking',event=>{
  const ranking=event.detail;
  if(ranking&&ranking.end===D.date){
    D.tenDay=ranking;D.sectionStatus=D.sectionStatus||{};
    D.sectionStatus.tenDay={ok:true,received_at:ranking.observed_at};renderCore();renderDetail();
  }
});
root.addEventListener('kpl-refresh-data',event=>{
  D=event.detail;
  sorted=D.stocks.slice().sort((a,b)=>a.limit_timestamp-b.limit_timestamp||a.code.localeCompare(b.code));
  counts={};D.stocks.forEach(r=>counts[r.theme]=(counts[r.theme]||0)+1);
  carriers=Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  // REQ-036: selection belongs to the complete tracking queue, not only the limit pool.
  if(!D.trends[state.trend])state.trend=Object.keys(D.trends)[0];
  $('[data-asof]').textContent=D.date+' '+D.capturedAt.slice(11,19);
  $('[data-session]').textContent=(D.capturedAt.slice(11,16)>='15:00'?'已收盘':'交易中')+' · 北京时间';
  $('[data-quality]').textContent=(D.ladderComplete?'涨停名单完整核验':'梯队本轮尚未完整核验')+' · '+D.boards.length+' 个板块';
  $('[data-footer]').textContent=D.source+' · 本轮观察 '+D.capturedAt.slice(11,19)+' · 交易时段每1分钟更新';
  const times=Object.entries(D.sectionStatus||{}).map(([n,s])=>n+' '+(s.ok?'成功':'未更新')+' '+(s.last_success_at||s.received_at||'').slice(11,19)).join('；');
  $('[data-source-note]').textContent='涨停股价格、涨幅和成交额取自本轮涨停池；10日榜新增个股取自独立盘口，盘口信息单独标明来源时间。'+times;
  metrics();renderLoss();render();
});
metrics();renderLoss();render();''')
    # Load layout after all card styles so a single grid owns shared boundaries.
    template = template.replace('</style>', (ROOT/'terminal-layout.css').read_text(encoding='utf-8')+'\n</style>', 1)
    template = template.replace('__TODAY_DATA__', encode(seed).decode().replace('<','\\u003c'))
    # The standalone service is allowed to use same-origin APIs; the conversation fragment remains network-free.
    template = template.replace('<button data-notes>', '<span class="kt-refresh-status" data-refresh-status role="status">准备就绪</span>'
        '<button data-refresh>手动实时更新</button><button data-notes>')
    bridge = (ROOT/'refresh-bridge.js').read_text(encoding='utf-8')
    recent_css = (ROOT/'recent-core.css').read_text(encoding='utf-8')
    recent_js = (ROOT/'wind-ui.js').read_text(encoding='utf-8')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>短线观察台</title><style>body{margin:0;padding:12px;background:#0a0e10}'
            '#kpl-today [data-refresh]{background:#274d43;border-color:#4f7e70;--kt-ink:#ddf7ed;min-width:112px}'
            '#kpl-today .kt-refresh-status{--kt-ink:#bed3ca;max-width:230px;white-space:normal}'
            '#kpl-today .kt-core-panel .kt-table-scroll{max-height:580px}'
            '#kpl-today .kt-tape{max-height:clamp(320px,calc(100vh - 340px),1050px)}'
            '#kpl-today .kt-core-panel .kt-table-scroll,#kpl-today .kt-tape{overflow-y:auto;'
            'scrollbar-width:auto;scrollbar-color:#789295 #10181c;scrollbar-gutter:stable;overscroll-behavior-y:contain}'
            '#kpl-today .kt-core-panel .kt-table-scroll::-webkit-scrollbar,#kpl-today .kt-tape::-webkit-scrollbar{width:12px}'
            '#kpl-today .kt-core-panel .kt-table-scroll::-webkit-scrollbar-track,#kpl-today .kt-tape::-webkit-scrollbar-track{background:#10181c}'
            '#kpl-today .kt-core-panel .kt-table-scroll::-webkit-scrollbar-thumb,#kpl-today .kt-tape::-webkit-scrollbar-thumb{background:#789295;border:2px solid #10181c;border-radius:8px;min-height:36px}'
            '#kpl-today .kt-core-panel .kt-table-scroll:focus-visible,#kpl-today .kt-tape:focus-visible{outline:2px solid #8cd9c8;outline-offset:-2px}'
            '#kpl-today .kt-core-panel .kt-table button .kt-core-labels{--kt-ink:#edc17b;white-space:normal}'
            '#kpl-today .kt-core-panel .kt-table{font-size:14px}'
            '#kpl-today .kt-core-panel .kt-table th{font-size:12px}'
            '#kpl-today .kt-core-panel .kt-table button span{font-size:12px;max-width:125px;overflow:hidden;text-overflow:ellipsis}'
            '#kpl-today .kt-core-panel .kt-panel-note{font-size:12px;line-height:1.7}'
            '#kpl-today .kt-core-panel strong{font-weight:600}'
            '@media(max-width:650px){body{padding:0}}'+recent_css+'</style></head><body>' + template
            + '<script>' + (ROOT/'broadcast-card.js').read_text(encoding='utf-8') + '</script><script>' + bridge + '</script><script>'+recent_js+'</script></body></html>').encode()


