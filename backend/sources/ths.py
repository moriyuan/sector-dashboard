"""同花顺涨停揭秘：涨停原因（标签式）、几天几板、板型、开板次数、封单额、封板成功率。

接口为同花顺涨停聚焦页的公开数据端点，匿名可用，支持历史交易日；
必须带 field 参数才会返回 reason_type 等原因字段。
"""
from datetime import datetime

from .base import SourceClient, hhmmss
from ..store import TZ

_URL = 'https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool'
_FIELDS = '199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004'
_PAGE_SIZE = 200


def _ts(raw) -> str | None:
    """unix 秒 → 'HH:MM:SS'（北京时间）；异常返回 None。"""
    try:
        return datetime.fromtimestamp(int(raw), TZ).strftime('%H:%M:%S')
    except (TypeError, ValueError, OSError):
        return None


class ThsSource(SourceClient):
    name = 'ths'

    def fetch_limit_pool(self, day: str) -> list[dict]:
        """返回 [{'code','name','reason','high_days','first_time','last_time','open_num',
        'order_amount','limit_up_type','seal_rate','change','turnover'}]。"""
        date = day.replace('-', '')
        pool, page = [], 1
        while True:
            payload = self.get_json(_URL, params={
                'page': page, 'limit': _PAGE_SIZE, 'field': _FIELDS,
                'filter': 'HS,GEM2STAR', 'order_field': '330324', 'order_type': 0, 'date': date})
            data = payload.get('data') or {}
            batch = data.get('info') or []
            for r in batch:
                pool.append({
                    'code': r.get('code'),
                    'name': r.get('name'),
                    'reason': (r.get('reason_type') or '').strip() or None,
                    'high_days': r.get('high_days'),
                    'first_time': _ts(r.get('first_limit_up_time')),
                    'last_time': _ts(r.get('last_limit_up_time')),
                    'open_num': r.get('open_num'),
                    'order_amount': r.get('order_amount'),
                    'limit_up_type': r.get('limit_up_type'),
                    'seal_rate': r.get('limit_up_suc_rate'),
                    'change': r.get('change_rate'),
                    'turnover': r.get('turnover_rate'),
                })
            total = int((data.get('page') or {}).get('total') or 0)
            if not batch or len(pool) >= total:
                break
            page += 1
        return [r for r in pool if r['code'] and r['name']]
