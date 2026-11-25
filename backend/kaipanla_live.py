"""Five-minute intraday snapshots; API reads never trigger upstream requests."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from .sources.base import SourceError
from .sources.kaipanla import KaipanlaSource, code_value
from .store import TZ

KIND = 'kaipanla_live'
INTERVAL = 300


def iso(now):
    return now.isoformat(timespec='seconds')


def scheduled_slot(now, calendar):
    now = now.astimezone(TZ)
    if now.date().isoformat() not in calendar:
        return None
    minute = now.hour * 60 + now.minute
    if not (570 <= minute <= 690 or 780 <= minute <= 900):
        return None
    return now.date().isoformat() + '/' + str(minute // 5)


def quote_age(stamp, now):
    """Ignore the lunch/after-close clock when assessing the last trading quote."""
    reference = now
    if '11:30' <= now.strftime('%H:%M') < '13:00':
        reference = now.replace(hour=11, minute=30, second=0, microsecond=0)
    elif now.hour >= 15:
        reference = now.replace(hour=15, minute=0, second=0, microsecond=0)
    return (reference - datetime.fromisoformat(stamp)).total_seconds()


class LiveCollector:
    def __init__(self, store, source=None, *, workers=3, quote_limit=8, trend_limit=3,
                 clock=None, budget=90):
        if not 1 <= workers <= 4 or not 0 <= quote_limit <= 20 or not 0 <= trend_limit <= 5:
            raise ValueError('Invalid collection limits')
        self.store, self.source = store, source or KaipanlaSource()
        self.quote_limit, self.trend_limit, self.budget = quote_limit, trend_limit, budget
        self.clock = clock or (lambda: datetime.now(TZ))
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='kpl-feed')
        self._lock = threading.Lock()

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.source.close()

    def _measure(self, name, fetch, deadline):
        started = time.monotonic()
        result = {'requested_at': iso(self.clock()), 'ok': False, 'data': None,
                  'source_time': None, 'date_validation': 'source_date',
                  'freshness': 'date_only'}
        transport = getattr(self.source, 'transport', None)
        if transport:
            transport.set_deadline(deadline)
        try:
            if started >= deadline:
                raise SourceError('本轮采集时间预算已耗尽')
            data = fetch()
            result['data'] = data
            if isinstance(data, dict):
                result['date_validation'] = data.get('date_validation', 'source_date')
                result['source_time'] = data.get('source_time')
                if result['date_validation'] == 'observation_only_no_source_date':
                    result['freshness'] = 'unverified'
                if result['source_time']:
                    age = quote_age(result['source_time'], self.clock())
                    result['source_age_seconds'] = round(age, 1)
                    if age > 180 or age < -10:
                        result['freshness'] = 'stale'
                        raise SourceError('盘口行情时间过旧或超前')
                    result['freshness'] = 'timestamp_verified'
            result['ok'] = True
        except (SourceError, ValueError, TypeError, KeyError, IndexError) as exc:
            result['error'] = str(exc)
        finally:
            if transport:
                transport.set_deadline(None)
        result['received_at'] = iso(self.clock())
        result['seconds'] = round(time.monotonic() - started, 3)
        return name, result

    def _batch(self, jobs, deadline):
        futures = [self.pool.submit(self._measure, name, fetch, deadline) for name, fetch in jobs]
        return dict(future.result() for future in futures)

    def collect(self, stock_codes=()):
        codes = list(dict.fromkeys(code_value(code) for code in stock_codes))
        if len(codes) > 20:
            raise ValueError('最多跟踪 20 只指定个股')
        if not self._lock.acquire(blocking=False):
            raise SourceError('同一采集器已有一轮任务运行中')
        try:
            return self._collect(codes)
        finally:
            self._lock.release()

    def _collect(self, codes):
        started = time.monotonic()
        deadline = started + self.budget
        day = self.clock().date().isoformat()
        previous = self.store.snapshot(KIND, day, 'live')
        old = previous['payload'].get('feeds', {}) if previous else {}
        source = self.source
        feeds = self._batch([
            ('emotion', lambda: source.fetch_emotion(day)),
            ('boards', lambda: source.fetch_strength_board(day, include_raw=True)),
            ('market_mood', lambda: source.fetch_market_mood(day)),
            ('rise_fall', lambda: source.fetch_rise_fall(day)),
            ('break_rate', lambda: source.fetch_break_rate(day)),
            ('drawdowns', lambda: source.fetch_drawdowns(day)),
        ], deadline)
        emotion = feeds['emotion']
        highest = emotion['data']['highest'] if emotion['ok'] else None
        if highest is None:
            prior = old.get('emotion', {}).get('data') or {}
            highest = max(6, prior.get('highest', 0))
        # Emotion failure must not prevent the lower tiers from being collected.
        pools = self._batch([(f'limit_pool_{level}', lambda level=level: source.fetch_limit_pool(day, level))
                             for level in range(1, highest + 1)], deadline)
        feeds.update(pools)
        stocks = [r for feed in pools.values() if feed['ok'] for r in feed['data']]
        unique = {r['code'] for r in stocks}
        expected = emotion['data']['limit_up'] if emotion['ok'] else None
        ladder_complete = (emotion['ok'] and all(feed['ok'] for feed in pools.values())
                           and len(unique) == len(stocks) == expected
                           and max((r['boards'] for r in stocks), default=0) == highest)
        issues = []
        if not ladder_complete:
            issues.append('梯队未通过完整性核验：情绪高度、各级分页及涨停总数需一致')
        rise = feeds['rise_fall']
        if rise['ok'] and emotion['ok'] and rise['data']['limit_up'] != expected:
            ladder_complete = False
            issues.append('涨跌停统计与情绪统计不一致，可能处于异步更新中')
        # Explicit watchlist first; remaining slots prioritize high tiers and earlier limit times.
        core = {r['code']: r for r in (previous['payload'].get('core_watch', []) if previous else [])}
        core.update({r['code']: r for r in stocks})
        for row in sorted(core.values(), key=lambda r: (-r['boards'], r['limit_timestamp'], r['code'])):
            if len(codes) >= self.quote_limit:
                break
            if row['code'] not in codes:
                codes.append(row['code'])
        jobs = [(f'quote_{code}', lambda code=code: source.fetch_quote(code, day)) for code in codes]
        boards = feeds['boards']['data'] if feeds['boards']['ok'] else []
        jobs += [(f'trend_{r["code"]}', lambda code=r['code']: source.fetch_sector_trend(code, day))
                 for r in boards[:self.trend_limit]]
        feeds.update(self._batch(jobs, deadline))
        received = self.clock()
        if received.date().isoformat() != day:
            raise SourceError('采集跨越午夜，拒绝写入另一交易日')
        all_ok = all(feed['ok'] for feed in feeds.values())
        for name, feed in feeds.items():
            feed['source_date'] = day if feed['date_validation'] == 'source_date' and feed['ok'] else None
            feed['cached'] = False
            feed['last_success_at'] = feed['received_at'] if feed['ok'] else None
            if not feed['ok'] and old.get(name, {}).get('last_success_at'):
                prior = old[name]
                # Preserve original timestamps; never relabel a cached result as a success.
                feed.update(data=prior['data'], source_time=prior.get('source_time'), cached=True,
                            freshness='stale', last_success_at=prior['last_success_at'],
                            source_date=prior.get('source_date'),
                            date_validation=prior.get('date_validation', 'source_date'))
        payload = {'trade_date': day, 'observed_at': iso(received),
                   'elapsed_seconds': round(time.monotonic() - started, 3),
                   'all_requested_feeds_ok': all_ok, 'ladder_complete': ladder_complete,
                   'ladder_expected_count': expected, 'ladder_observed_count': len(unique),
                   'ladder_height_verified': emotion['ok'], 'issues': issues, 'feeds': feeds,
                   'quote_codes': codes, 'interval_seconds': INTERVAL,
                   'core_watch': [{k: core[code][k] for k in ('code', 'boards', 'limit_timestamp')}
                                  for code in codes if code in core],
                   'limit_time_basis': '供应商涨停时间字段，首次/最近封板语义尚未核定',
                   'scope': '开盘啦公开接口口径；五分钟快照不代表完整逐笔事件流'}
        revision = self.store.save(KIND, day, 'live', payload)
        self.store.event(KIND, all_ok and ladder_complete,
                         f'{day} {sum(f["ok"] for f in feeds.values())}/{len(feeds)} 模块成功；'
                         f'梯队 {len(unique)}/{expected}；耗时 {payload["elapsed_seconds"]} 秒')
        return {**payload, 'revision': revision}


def claim_slot(store, slot, now):
    """One collector per database, including multiple web workers/restarts."""
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS kpl_schedule (name TEXT PRIMARY KEY, slot TEXT, lease_until REAL)')
        # Take the SQLite write lock before reading to make concurrent worker claims atomic.
        db.execute('INSERT OR IGNORE INTO kpl_schedule VALUES (?, ?, ?)', (KIND, '', 0))
        row = db.execute('SELECT slot,lease_until FROM kpl_schedule WHERE name=?', (KIND,)).fetchone()
        if row['slot'] == slot or row['lease_until'] > now.timestamp():
            return False
        db.execute('UPDATE kpl_schedule SET slot=?,lease_until=? WHERE name=?',
                   (slot, now.timestamp() + 150, KIND))
    return True


def run_scheduler(store, stop):
    collector = LiveCollector(store)
    collector.source.transport.stop = stop
    try:
        while not stop.is_set():
            try:
                now = datetime.now(TZ)
                slot = scheduled_slot(now, store.observed('calendar') or [])
                if slot and claim_slot(store, slot, now):
                    watchlist = [c.strip() for c in os.getenv('SECTOR_KPL_WATCHLIST', '').split(',') if c.strip()]
                    collector.collect(watchlist)
            except Exception as exc:
                store.event(KIND, False, f'盘中采集失败 ({type(exc).__name__}): {exc}')
            stop.wait(2)
    finally:
        collector.close()


def view(store, day, now=None):
    now = now or datetime.now(TZ)
    snap = store.snapshot(KIND, day, 'live')
    if not snap:
        return {'trade_date': day, 'status': 'missing', 'feeds': {}, 'revision': None}
    payload = snap['payload']
    age = (now - datetime.fromisoformat(payload['observed_at'])).total_seconds()
    current = day == now.date().isoformat()
    phase = 'trading' if scheduled_slot(now, store.observed('calendar') or []) else 'closed'
    stale = current and phase == 'trading' and age > INTERVAL + 90
    for feed in payload['feeds'].values():
        feed['stale'] = stale or not feed['ok']
        if feed.get('source_time') and current:
            feed['source_age_seconds'] = round(quote_age(feed['source_time'], now), 1)
            feed['stale'] |= feed['source_age_seconds'] > 180 or feed['source_age_seconds'] < -10
    degraded = not payload['all_requested_feeds_ok'] or not payload['ladder_complete']
    degraded |= any(feed['stale'] for feed in payload['feeds'].values())
    return {**payload, 'revision': snap['id'], 'phase': phase, 'snapshot_age_seconds': round(age, 1),
            'status': 'archived' if not current else 'stale' if stale else 'degraded' if degraded else 'available'}


def router(store):
    api = APIRouter(prefix='/api/kaipanla', tags=['开盘啦盘中'])

    def checked_day(day):
        if day is None:
            return datetime.now(TZ).date().isoformat()
        try:
            if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, '日期格式应为 YYYY-MM-DD') from None
        return day

    @api.get('/live')
    def live(date: str | None = None):
        return view(store, checked_day(date))

    @api.get('/history')
    def history(date: str | None = None, limit: int = Query(60, ge=1, le=120)):
        day = checked_day(date)
        with store.connect() as db:
            rows = db.execute('SELECT id,payload FROM snapshots WHERE kind=? AND trade_date=? AND mode=? '
                              'ORDER BY id DESC LIMIT ?', (KIND, day, 'live', limit)).fetchall()
        # Compact history for evolution charts; full raw latest data is served by /live.
        result = []
        for row in reversed(rows):
            data = json.loads(row['payload'])
            result.append({'revision': row['id'], 'observed_at': data['observed_at'],
                           'ladder_complete': data['ladder_complete'],
                           'feeds': {k: v for k, v in data['feeds'].items()
                                     if k in ('emotion', 'rise_fall', 'boards', 'market_mood')}})
        return {'trade_date': day, 'snapshots': result}

    return api
