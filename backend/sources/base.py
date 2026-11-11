"""渠道服务基类：统一限速、重试与异常语义。

每个数据源一个子类，对外暴露语义化方法（如 fetch_limit_pool），
返回已解析的 dict/list；网络或结构异常一律抛 SourceError，
由聚合层决定降级策略（单源失败不影响其他源）。
"""
import threading
import time

import requests

from ..provider import DataError


class SourceError(DataError):
    """数据源请求失败或响应结构异常。"""


class SourceClient:
    name = 'base'
    min_interval = 0.8   # 相邻请求最小间隔（秒），礼貌限速
    timeout = 15
    retries = 2          # 网络层错误重试次数
    ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

    def __init__(self, timeout: float | None = None):
        self.timeout = timeout or self.timeout
        self._lock = threading.Lock()
        self._last = 0.0

    def _throttle(self):
        with self._lock:
            wait = self._last + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def _request(self, method: str, url: str, *, headers=None, params=None, data=None, json_body=None):
        """带限速与重试的 JSON 请求；非 200 或 JSON 解析失败抛 SourceError。"""
        last_err = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                resp = requests.request(method, url, headers={'User-Agent': self.ua, **(headers or {})},
                                        params=params, data=data, json=json_body, timeout=self.timeout)
                if resp.status_code != 200:
                    raise SourceError(f'{self.name} HTTP {resp.status_code}')
                return resp.json()
            except SourceError as e:
                last_err = e
            except (requests.RequestException, ValueError) as e:
                last_err = SourceError(f'{self.name} 请求失败: {e}')
            if attempt < self.retries:
                time.sleep(1.2 * (attempt + 1))
        raise last_err

    def get_json(self, url, *, headers=None, params=None):
        return self._request('GET', url, headers=headers, params=params)

    def post_json(self, url, *, headers=None, params=None, data=None, json_body=None):
        return self._request('POST', url, headers=headers, params=params, data=data, json_body=json_body)


def hhmmss(raw) -> str | None:
    """东财式整数时分（92500 / 101345）→ 'HH:MM:SS'；异常返回 None。"""
    try:
        text = str(int(raw)).zfill(6)
        h, m, s = int(text[:2]), int(text[2:4]), int(text[4:6])
        if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
            return None
        return f'{h:02d}:{m:02d}:{s:02d}'
    except (TypeError, ValueError):
        return None
