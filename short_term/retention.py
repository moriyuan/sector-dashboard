"""REQ-037: bounded, cancellable maintenance for two explicit preview databases."""
import copy
import json
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager, closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
FILES = ('refresh-history.sqlite3', 'core-quotes.sqlite3')


def clock():
    return datetime.now(TZ)


def cutoff_day(current=None):
    return ((current or clock()).astimezone(TZ).date() - timedelta(days=29)).isoformat()


def allowed(current):
    return not '09:00' <= current.astimezone(TZ).strftime('%H:%M') < '15:10'


def size(path):
    total = 0
    for suffix in ('', '-wal'):
        try:
            total += Path(str(path) + suffix).stat().st_size
        except FileNotFoundError:
            pass
    return total


@contextmanager
def process_lock(path):
    """OS releases the byte lock even when a process exits unexpectedly."""
    with open(path, 'a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def validate_days(db, table):
    for (value,) in db.execute('SELECT DISTINCT day FROM ' + table):
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('业务日期非法，停止该库清理')


def prune(db, name, cutoff):
    """Caller owns transaction. Validate every retained reference before any deletion."""
    if name == FILES[1]:
        validate_days(db, 'core_quotes')
        return {'quotes': db.execute('DELETE FROM core_quotes WHERE day<?', (cutoff,)).rowcount}
    if name != FILES[0]:
        raise ValueError('不允许的清理目标')
    validate_days(db, 'frames')
    db.execute('CREATE TEMP TABLE keep_refs(sha TEXT PRIMARY KEY)')
    def keep(value):
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('快照引用格式非法')
        db.execute('INSERT OR IGNORE INTO keep_refs VALUES(?)', (value,))
    for view_hash, manifest in db.execute('SELECT view_hash,manifest FROM frames WHERE day>=?', (cutoff,)):
        keep(view_hash)
        feeds = json.loads(manifest)
        if not isinstance(feeds, dict):
            raise ValueError('快照清单格式非法')
        for feed in feeds.values():
            if not isinstance(feed, dict) or 'body_hash' not in feed:
                raise ValueError('快照清单缺少引用')
            if feed['body_hash'] is not None:
                keep(feed['body_hash'])
    if db.execute('SELECT 1 FROM keep_refs k LEFT JOIN blobs b ON b.sha=k.sha WHERE b.sha IS NULL LIMIT 1').fetchone():
        raise ValueError('保留快照引用缺失，停止清理')
    frames = db.execute('DELETE FROM frames WHERE day<?', (cutoff,)).rowcount
    blobs = db.execute('DELETE FROM blobs WHERE sha NOT IN (SELECT sha FROM keep_refs)').rowcount
    return {'frames': frames, 'blobs': blobs}


class RetentionService:
    def __init__(self, root, busy=lambda: False, now=clock, budget=10, vacuum_budget=20):
        self.root = Path(root).resolve()
        self.busy, self.now = busy, now
        self.budget, self.vacuum_budget = budget, vacuum_budget
        self.stop = threading.Event()
        self.guard = threading.RLock()
        self.running = False
        self.worker = None
        self.state_path = self.root / 'retention-state.json'
        self.state = dict(enabled_on=now().astimezone(TZ).date().isoformat(), state='pending', failures=0)
        try:
            saved = json.loads(self.state_path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError('invalid state')
            self.state.update(saved)
            if self.state['state'] == 'running':
                self.state.update(state='pending', message='上次维护中断，等待补做')
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            self.state.update(state='pending', enabled_on='1970-01-01', message='维护状态不可读，等待安全重试')
        self._persist()

    def _persist(self):
        temp = self.state_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.state_path)

    def _set(self, **values):
        with self.guard:
            self.state.update(values)
            self._persist()

    def status(self):
        with self.guard:
            result = copy.deepcopy(self.state)
        current = self.now().astimezone(TZ)
        result.update(retention_days=30, cutoff=cutoff_day(current), schedule='每日北京时间16:10；错过后非交易维护窗口补做',
                      database_wal_bytes=sum(size(self.root / name) for name in FILES))
        if not allowed(current):
            result['waiting_reason'] = '09:00—15:10不执行维护'
        elif self.busy():
            result['waiting_reason'] = '行情或报价采集中，维护延后'
        else:
            result['waiting_reason'] = result.get('message') or '等待16:10或错过任务补做'
        return result

    def due(self, current=None):
        current = (current or self.now()).astimezone(TZ)
        today = current.date().isoformat()
        slot = (current.date() - timedelta(days=current.strftime('%H:%M') < '16:10')).isoformat()
        with self.guard:
            if self.running or self.stop.is_set() or not allowed(current) or self.busy():
                return False
            if self.state.get('last_success', '') >= slot or self.state.get('last_success') == today:
                return False
            if slot < self.state['enabled_on']:
                return False
            retry = self.state.get('retry_at')
            return not retry or current >= datetime.fromisoformat(retry)

    def tick(self):
        with self.guard:
            if not self.due():
                return False
            self.running = True
            self.worker = threading.Thread(target=self._work, daemon=True, name='retention-worker')
            self.worker.start()
            return True

    def schedule(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                # State persistence failure must not stop ingestion or kill the scheduler.
                with self.guard:
                    self.state.update(state='failed', message='维护状态保存失败，稍后重试')
            self.stop.wait(30)

    def _check(self, deadline):
        if self.stop.is_set() or self.busy() or not allowed(self.now()) or time.monotonic() >= deadline:
            raise InterruptedError('采集优先、维护窗口结束或预算耗尽，延后重试')

    def _database(self, name, cutoff, previous):
        path = self.root / name
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ValueError('拒绝越界的数据库路径')
        result = {'logical': 'pending', 'physical': 'pending', 'before_bytes': size(path), 'deleted': {}}
        deadline = time.monotonic() + self.budget
        self._check(deadline)
        # rw never recreates a missing database. Windows paths are encoded by as_uri().
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=.25)) as db:
            db.execute('PRAGMA busy_timeout=250')
            def interrupted():
                try:
                    self._check(deadline)
                    return 0
                except InterruptedError:
                    return 1
            db.set_progress_handler(interrupted, 500)
            try:
                db.execute('BEGIN IMMEDIATE')
                result['deleted'] = prune(db, name, cutoff)
                self._check(deadline)
                db.commit()
                result['logical'] = 'complete'
            except Exception:
                db.set_progress_handler(None, 0)
                db.rollback()
                raise
            # A failed physical operation cannot undo an already committed logical prune.
            try:
                deadline = time.monotonic() + self.vacuum_budget
                self._check(deadline)
                if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                    raise TimeoutError('WAL仍被读取，磁盘回收延后')
                pages = db.execute('PRAGMA page_count').fetchone()[0]
                free = db.execute('PRAGMA freelist_count').fetchone()[0]
                page_size = db.execute('PRAGMA page_size').fetchone()[0]
                last = previous.get('last_vacuum')
                weekly = not last or (self.now().astimezone(TZ).date() - date.fromisoformat(last)).days >= 7
                result['last_vacuum'] = last
                if free and (weekly or free / max(pages, 1) >= .2):
                    if shutil.disk_usage(self.root).free < pages * page_size * 2 + 16 * 1024**2:
                        raise OSError('磁盘剩余空间不足，压缩延后')
                    self._check(deadline)
                    db.execute('VACUUM')
                    result['last_vacuum'] = self.now().astimezone(TZ).date().isoformat()
                    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                        raise TimeoutError('压缩已完成，WAL回收待重试')
                result['reusable_bytes'] = db.execute('PRAGMA freelist_count').fetchone()[0] * page_size
                result['physical'] = 'complete'
            except (sqlite3.Error, InterruptedError, OSError) as exc:
                result.update(physical='pending', error=type(exc).__name__ + ': ' + str(exc)[:140])
            finally:
                db.set_progress_handler(None, 0)
        result['after_bytes'] = size(path)
        result['reclaimed_bytes'] = max(0, result['before_bytes'] - result['after_bytes'])
        return result

    def _work(self):
        try:
            with process_lock(self.root / 'retention.lock'):
                # Another process may have completed while this one waited for its tick.
                saved = json.loads(self.state_path.read_text(encoding='utf-8'))
                if saved.get('last_success') == self.now().astimezone(TZ).date().isoformat():
                    with self.guard:
                        self.state.update(saved)
                    return
                self._run()
        except Exception as exc:
            self._failure(type(exc).__name__ + ': ' + str(exc)[:140])
        finally:
            with self.guard:
                self.running = False

    def _failure(self, message, **values):
        failures = self.state.get('failures', 0) + 1
        retry = self.now() + timedelta(seconds=min(3600, 300 * 2**min(failures - 1, 4)))
        self._set(state='failed', failures=failures, retry_at=retry.isoformat(), message=message, **values)

    def _run(self):
        current = self.now().astimezone(TZ)
        cutoff = cutoff_day(current)
        previous = copy.deepcopy(self.state.get('databases', {}))
        self._set(state='running', started_at=current.isoformat(), message='后台清理中')
        results = {}
        for name in FILES:
            try:
                results[name] = self._database(name, cutoff, previous.get(name, {}))
            except Exception as exc:
                results[name] = dict(logical='failed', physical='pending', deleted={},
                                     last_vacuum=previous.get(name, {}).get('last_vacuum'),
                                     error=type(exc).__name__ + ': ' + str(exc)[:140])
        complete = all(r['logical'] == 'complete' and r['physical'] == 'complete' for r in results.values())
        values = dict(databases=results, finished_at=self.now().isoformat(), cutoff_used=cutoff,
                      deleted_frames=sum(r['deleted'].get('frames', 0) for r in results.values()),
                      deleted_quotes=sum(r['deleted'].get('quotes', 0) for r in results.values()),
                      reclaimed_bytes=sum(r.get('reclaimed_bytes', 0) for r in results.values()))
        # Preserve cumulative counts across physical retries of the same daily maintenance.
        if self.state.get('cutoff_used') == cutoff and self.state.get('last_success') != current.date().isoformat():
            for key in ('deleted_frames', 'deleted_quotes', 'reclaimed_bytes'):
                values[key] += self.state.get(key, 0)
        if complete:
            self._set(state='complete', last_success=current.date().isoformat(), failures=0, retry_at=None,
                      message='清理完成；实际回收按数据库与WAL文件大小差值统计', **values)
        else:
            self._failure('部分完成，未完成部分自动退避重试' if any(r['logical'] == 'complete' for r in results.values()) else '本次未完成，自动退避重试', **values)
            if any(r['logical'] == 'complete' for r in results.values()):
                self._set(state='partial')

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=self.budget + self.vacuum_budget + 2)
