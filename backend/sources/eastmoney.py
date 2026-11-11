"""东方财富涨停池：每日涨停股票名单、连板数、首封/末封时间、炸板次数、封单资金。

接口为行情中心涨停板行情的公开数据端点，匿名可用，支持任意历史交易日。
"""
from .base import SourceClient, SourceError, hhmmss

_URL = 'https://push2ex.eastmoney.com/getTopicZTPool'
_UT = '7eea3edcaed734bea9cbfc24409ed989'
_PAGE_SIZE = 500


class EastmoneySource(SourceClient):
    name = 'eastmoney'

    def fetch_limit_pool(self, day: str) -> list[dict]:
        """返回当日涨停池 [{'code','name','boards','board_label','first_time','last_time',
        'open_num','seal_fund','change','turnover','amount','industry'}]，按首封时间升序。"""
        date = day.replace('-', '')
        pool, page = [], 0
        while True:
            payload = self.get_json(_URL, params={
                'ut': _UT, 'dpt': 'wz.ztzt', 'Pageindex': page,
                'pagesize': _PAGE_SIZE, 'sort': 'fbt:asc', 'date': date})
            data = payload.get('data') or {}
            batch = data.get('pool') or []
            for r in batch:
                stat = r.get('zttj') or {}
                boards = int(r.get('lbc') or 1)
                days = int(stat.get('days') or boards)
                pool.append({
                    'code': r.get('c'),
                    'name': r.get('n'),
                    'boards': boards,
                    'board_label': f'{days}天{stat.get("ct", boards)}板' if days != boards else f'{boards}连板',
                    'first_time': hhmmss(r.get('fbt')),
                    'last_time': hhmmss(r.get('lbt')),
                    'open_num': int(r.get('zbc') or 0),
                    'seal_fund': r.get('fund'),
                    'change': r.get('zdp'),
                    'turnover': r.get('hs'),
                    'amount': r.get('amount'),
                    'industry': r.get('hybk'),
                })
            total = int(data.get('tc') or 0)
            if not batch or len(pool) >= total:
                break
            page += 1
        if not pool and total:
            raise SourceError('eastmoney 涨停池分页异常')
        return [r for r in pool if r['code'] and r['name']]
