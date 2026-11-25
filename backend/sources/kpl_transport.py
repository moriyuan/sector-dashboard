"""Bounded, anonymous HTTPS transport for Kaipanla read-only queries."""
import random
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from .base import SourceError


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


class KplTransport:
    hosts = {'apphis.longhuvip.com', 'apphq.longhuvip.com',
             'apphwhq.longhuvip.com', 'apphwshhq.longhuvip.com'}

    def __init__(self, timeout=8, interval=0.25, retries=2):
        self.timeout, self.interval, self.retries = timeout, interval, retries
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions, self._next, self._failures = [], {}, {}
        self._closed = False

    def _session(self):
        if not hasattr(self._local, 'session'):
            session = requests.Session()
            session.mount('https://', HTTPAdapter(pool_connections=4, pool_maxsize=1, max_retries=0))
            with self._lock:
                self._sessions.append(session)
            self._local.session = session
        return self._local.session

    def set_deadline(self, deadline):
        self._local.deadline = deadline

    def _remaining(self):
        if getattr(self, 'stop', None) is not None and self.stop.is_set():
            raise SourceError('采集正在停止')
        deadline = getattr(self._local, 'deadline', None)
        remaining = deadline - time.monotonic() if deadline is not None else self.timeout
        if remaining <= 0:
            raise SourceError('本轮采集时间预算已耗尽')
        return min(self.timeout, remaining)

    def _sleep(self, seconds):
        deadline = getattr(self._local, 'deadline', None)
        if deadline is not None and time.monotonic() + seconds >= deadline:
            raise SourceError('本轮采集时间预算不足以等待重试')
        stop = getattr(self, 'stop', None)
        if stop is not None:
            if stop.wait(seconds):
                raise SourceError('采集正在停止')
        else:
            time.sleep(seconds)

    def _throttle(self, host):
        # Reserve a slot without holding the lock during sleep; shared by worker threads.
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(host, 0))
            self._next[host] = slot + self.interval
        self._sleep(max(0, slot - now))

    def _cooldown(self, key):
        with self._lock:
            count, until = self._failures.get(key, (0, 0))
            if until > time.monotonic():
                raise SourceError(f'{key[1]} 暂停请求，等待渠道恢复')

    def _failed(self, key, delay=0):
        with self._lock:
            count, _ = self._failures.get(key, (0, 0))
            count += 1
            self._failures[key] = (count, time.monotonic() + max(delay, 60 if count >= 3 else 0))

    @staticmethod
    def _retry_after(value):
        try:
            return max(0, float(value))
        except (ValueError, TypeError):
            try:
                return max(0, parsedate_to_datetime(value).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                return 0

    def post(self, url, data, headers):
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.hostname not in self.hosts or parts.port
                or parts.username or parts.path != '/w1/api/index.php' or parts.query or parts.fragment):
            raise SourceError('开盘啦请求地址不在允许列表')
        if self._closed:
            raise SourceError('开盘啦连接已关闭')
        key = (parts.hostname, str(data.get('c', '')) + '/' + str(data.get('a', '')))
        self._cooldown(key)
        error = '请求失败'
        for attempt in range(self.retries + 1):
            self._remaining()
            self._throttle(parts.hostname)
            delay = 0
            try:
                with self._session().post(url, data=data, headers=headers,
                                          timeout=(min(4, self._remaining()), self._remaining()),
                                          allow_redirects=False, stream=True) as response:
                    status = response.status_code
                    if status == 429 or status in (500, 502, 503, 504):
                        delay = self._retry_after(response.headers.get('Retry-After'))
                        error = f'HTTP {status}'
                        if delay > 5:
                            # Long rate limits never occupy a collector worker or trigger a retry storm.
                            self._failed(key, min(delay, 3600))
                            raise SourceError(f'{key[1]} HTTP {status}，渠道要求稍后重试')
                    elif status != 200:
                        self._failed(key, 60)
                        raise SourceError(f'{key[1]} HTTP {status}')
                    else:
                        chunks, size = [], 0
                        deadline = time.monotonic() + self.timeout
                        for chunk in response.iter_content(65536):
                            self._remaining()
                            size += len(chunk)
                            if size > 8 * 1024 * 1024 or time.monotonic() > deadline:
                                raise SourceError(f'{key[1]} 响应超出大小或时间限制')
                            chunks.append(chunk)
                        import json
                        try:
                            payload = json.loads(b''.join(chunks), parse_constant=_reject_constant)
                        except (ValueError, UnicodeError):
                            raise SourceError(f'{key[1]} 返回非 JSON 数据') from None
                        if not isinstance(payload, dict) or str(payload.get('errcode')) != '0':
                            self._failed(key, 60)
                            raise SourceError(f'{key[1]} 业务状态异常，errcode={payload.get("errcode") if isinstance(payload, dict) else "invalid"}')
                        with self._lock:
                            self._failures.pop(key, None)
                        return payload
            except requests.exceptions.SSLError:
                self._failed(key, 60)
                raise SourceError(f'{key[1]} TLS 校验失败') from None
            except requests.RequestException as exc:
                # Exception strings may contain proxy credentials; only expose the class.
                error = type(exc).__name__
            if attempt < self.retries:
                self._sleep(max(delay, 0.4 * 2 ** attempt + random.uniform(0, 0.15)))
        self._failed(key)
        raise SourceError(f'{key[1]} {error}，重试已耗尽')

    def close(self):
        self._closed = True
        for session in self._sessions:
            session.close()
