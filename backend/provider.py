"""Public Eastmoney adapter. Retains timestamps/codes omitted by AKShare wrappers.

Field contracts follow the installed AKShare adapters; no positional columns.
Calendar decoding uses AKShare's bundled Sina decoder. Raw responses are hashed
and retained by Store. A valid empty pool differs from an unavailable pool.
"""
import json
import subprocess
import time
from datetime import datetime
from urllib.parse import urlencode, quote

from .analytics import number
from .store import TZ

FIELDS = 'f2,f3,f6,f8,f12,f13,f14,f18,f104,f105,f106,f124'
STOCK_FIELDS = 'f2,f3,f6,f8,f12,f13,f14,f18,f124'
INDEX_SYMBOLS = ('sh000001', 'sz399001', 'sz399006', 'sh000300', 'sh000680', 'sh000905', 'sh000852')
STOCK_FILTER = 'm:0+t:6+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2'


class DataError(RuntimeError):
    pass


def source_time(rows):
    times = sorted({r.get('source_time') for r in rows if r.get('source_time')})
    if not times:
        return None
    if len({v[:10] for v in times}) != 1:
        raise DataError('同一响应含多个交易日期，拒绝生成快照')
    return times[0]


def quote_row(r):
    timestamp = number(r.get('f124'))
    t = datetime.fromtimestamp(timestamp, TZ).isoformat(timespec='seconds') if timestamp and timestamp > 1e9 else None
    return dict(code=str(r.get('f12','')), name=str(r.get('f14','')), price=number(r.get('f2')),
                change=number(r.get('f3')), amount=number(r.get('f6')), turnover=number(r.get('f8')),
                pre_close=number(r.get('f18')), source_time=t,
                vendor_up=number(r.get('f104')), vendor_down=number(r.get('f105')),
                vendor_flat=number(r.get('f106')))


class Eastmoney:
    concept_source='eastmoney'
    def __init__(self, store, delay=.2):
        self.store = store
        self.delay = delay
        self.last_request = 0

    def text(self, url, params=None):
        # Curl honors the host network configuration and has a hard whole-call timeout.
        if params:
            url += '?' + urlencode(params, safe=':,!', quote_via=quote)
        time.sleep(max(0, self.delay - (time.monotonic()-self.last_request)))
        self.last_request = time.monotonic()
        error = None
        for attempt in range(2):
            result = subprocess.run(['curl','--fail','--silent','--show-error','--connect-timeout','6','--max-time','12',
                                     '-A','Mozilla/5.0','-e','https://quote.eastmoney.com/',url], capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout:
                return result.stdout
            error = result.stderr[:240] or 'empty response'
            if attempt == 0:
                time.sleep(1)
        raise DataError(error)

    def get(self, url, params):
        raw = self.text(url, params)
        try:
            obj = json.loads(raw)
        except ValueError as exc:
            raise DataError('来源返回非JSON内容') from exc
        self.store.observe('raw:' + url + '?' + urlencode(params), obj)
        if obj.get('rc', 0) != 0:
            raise DataError('供应商错误码: ' + str(obj.get('rc')))
        return obj

    def listing(self, fs, fields=FIELDS):
        rows, total = [], None
        seen = set()
        for page in range(1, 101):
            obj = self.get('https://push2.eastmoney.com/api/qt/clist/get',
                           dict(pn=page, pz=100, po=0, np=1, fltt=2, invt=2, fid='f12', fs=fs, fields=fields))
            data = obj.get('data')
            if not isinstance(data, dict) or 'total' not in data:
                raise DataError('列表缺少 total/data')
            reported = int(data['total'])
            if total is None:
                total = reported
            elif reported != total:
                raise DataError('分页期间列表总数改变，等待下一轮重试')
            batch = data.get('diff') or []
            if isinstance(batch, dict):
                batch = list(batch.values())
            for row in batch:
                key = str(row.get('f12'))
                if key in seen:
                    raise DataError('分页重复代码，拒绝不完整列表')
                seen.add(key)
                rows.append(row)
            if len(rows) == total:
                return rows
            if not batch or len(rows) > total:
                raise DataError('分页数量与 total 不符')
        raise DataError('列表分页超过安全上限')

    def boards(self, kind):
        raw = self.listing(('m:90+s:2' if kind == 'industry' else 'm:90+t:3') + '+f:!50')
        rows = [quote_row(r) for r in raw]
        if not rows or any(not r['code'].startswith('BK') or not r['name'] for r in rows):
            raise DataError('板块目录格式异常')
        return rows

    def stocks(self):
        return [quote_row(r) for r in self.listing(STOCK_FILTER,STOCK_FIELDS)]

    def members(self, code):
        return [quote_row(r) for r in self.listing('b:' + code + '+f:!50',STOCK_FIELDS)]

    def indices(self):
        secids = ','.join(('1.' if symbol.startswith('sh') else '0.') + symbol[2:] for symbol in INDEX_SYMBOLS)
        obj = self.get('https://push2.eastmoney.com/api/qt/ulist.np/get', dict(secids=secids, fltt=2, fields=STOCK_FIELDS))
        data = (obj.get('data') or {}).get('diff')
        if not data:
            raise DataError('指数行情为空')
        return [quote_row(r) for r in (list(data.values()) if isinstance(data,dict) else data)]

    def flows(self, kind):
        raw = self.listing('m:90+s:2' if kind == 'industry' else 'm:90+t:3', 'f12,f14,f62,f184,f164,f165,f174,f175,f124')
        return {str(r['f12']): dict(code=str(r['f12']), name=r['f14'], today=number(r.get('f62')), today_ratio=number(r.get('f184')),
                                  five=number(r.get('f164')), five_ratio=number(r.get('f165')), ten=number(r.get('f174')), ten_ratio=number(r.get('f175')),
                                  source_time=quote_row(r)['source_time']) for r in raw}

    def bars(self, code):
        data = self.get('https://push2his.eastmoney.com/api/qt/stock/kline/get',
                        dict(secid='90.'+code, klt=101, fqt=0, lmt=300, end='20500101', fields1='f1,f2,f3,f4,f5,f6', fields2='f51,f52,f53,f54,f55,f56,f57,f58,f59')) .get('data')
        if not data or data.get('code') != code:
            raise DataError('板块日线代码不匹配或无数据')
        result = []
        for text in data.get('klines', []):
            b = text.split(',')
            if len(b) < 9:
                raise DataError('日线字段不足')
            row = dict(date=b[0], open=number(b[1]), close=number(b[2]), high=number(b[3]), low=number(b[4]), amount=number(b[6]), change=number(b[8]))
            if not row['close'] or row['close'] <= 0:
                raise DataError('日线价格异常')
            result.append(row)
        if not result or len({b['date'] for b in result}) != len(result):
            raise DataError('日线为空或日期重复')
        return sorted(result, key=lambda b:b['date'])[-251:]

    def pool(self, pool_kind, date):
        route = {'up':'getTopicZTPool','broken':'getTopicZBPool','down':'getTopicDTPool'}[pool_kind]
        obj = self.get('https://push2ex.eastmoney.com/'+route, dict(ut='7eea3edcaed734bea9cbfc24409ed989', dpt='wz.ztzt', Pageindex=0, pagesize=10000, sort='fbt:asc' if pool_kind != 'down' else 'fund:asc', date=date.replace('-','')))
        data = obj.get('data')
        if data is None or 'pool' not in data:
            raise DataError('该交易日涨跌停池不可用')
        if str(data.get('qdate')) != date.replace('-',''):
            raise DataError('涨跌停池返回日期与请求日期不符')
        raw = data['pool']
        if data.get('tc') is not None and int(data['tc']) != len(raw):
            raise DataError('涨跌停池数量不完整')
        return [dict(code=str(r['c']), name=r.get('n',''), change=number(r.get('zdp')), boards=number(r.get('lbc')), first=r.get('fbt'), broken_count=number(r.get('zbc')),industry=r.get('hybk')) for r in raw]

    def calendar(self):
        # AKShare's decoding algorithm, with our bounded transport and raw audit.
        import py_mini_racer
        from akshare.stock.cons import hk_js_decode
        text = self.text('https://finance.sina.com.cn/realstock/company/klc_td_sh.txt')
        self.store.observe('calendar_raw', {'text': text})
        vm = py_mini_racer.MiniRacer()
        vm.eval(hk_js_decode)
        data = vm.call('d', text.split('=')[1].split(';')[0].replace('"',''))
        dates = sorted({str(d)[:10] for d in data})
        if not dates or len(dates) < 100:
            raise DataError('交易日历数据异常')
        return dates
