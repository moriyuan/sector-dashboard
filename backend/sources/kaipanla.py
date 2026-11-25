"""开盘啦公开渠道。入口参考用户提供的 SDK 0.1.0，严格验证日期与分页。
来源日期只能证明交易日；没有行情时间戳的响应不宣称秒级新鲜。
"""
import math
import re
import uuid
from datetime import datetime
from functools import wraps

from .base import SourceClient, SourceError
from .kpl_transport import KplTransport
from ..store import TZ

_HIS = 'https://apphis.longhuvip.com/w1/api/index.php'
_RT = 'https://apphwhq.longhuvip.com/w1/api/index.php'
_HQ = 'https://apphq.longhuvip.com/w1/api/index.php'


def checked(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (KeyError, IndexError, TypeError, ValueError, OverflowError, OSError) as exc:
            raise SourceError(f'{fn.__name__} 数据结构或字段异常 ({type(exc).__name__})') from exc
    return call


def source_date(value, expected):
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if str(value) == expected.replace('-', ''):
        value = expected
    if value != expected:
        raise SourceError(f'来源日期 {value!r} 与请求日期 {expected} 不符')


def number(value):
    value = float(value)
    if not math.isfinite(value):
        raise SourceError('数值不是有限数')
    return value


def code_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{6}', value):
        raise SourceError('代码必须为六位数字')
    return value


def streak(label):
    if label == '首板':
        return 1
    match = re.fullmatch(r'([0-9]+)(?:连板|板)', label)
    if match:
        return int(match[1])
    match = re.fullmatch(r'([0-9]+)天([0-9]+)板', label)
    return int(match[1]) if match and match[1] == match[2] else None


class KaipanlaSource(SourceClient):
    name = 'kaipanla'
    ua = 'Dalvik/2.1.0 (Linux; U; Android 14; V2178A Build/UP1A.231005.007)'
    page_size = 60
    max_pages = 30

    def __init__(self, timeout=None, transport=None):
        super().__init__(timeout)
        self.transport = transport or KplTransport(timeout=timeout or 8)
        self.device_id = uuid.uuid4().hex

    def close(self):
        self.transport.close()

    def _post(self, url, payload):
        data = {'PhoneOSNew': '1', 'DeviceID': self.device_id,
                'VerSion': '6.2.20.2', 'apiv': 'w47', 'Red': '0', **payload}
        headers = {'User-Agent': self.ua, 'Accept': 'application/json',
                   'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'}
        if data.get('c') == 'HisLimitResumption':
            data.update(PhoneOSNew='2', VerSion='5.21.0.5', apiv='w42')
            headers.update({'User-Agent': 'lhb/5.21.5 (com.kaipanla.www; build:0; iOS 18.6.2) Alamofire/4.9.1',
                            'Accept-Language': 'zh-Hans-CN', 'X-Requested-With': 'X_Requested_With'})
        return self.transport.post(url, data, headers)

    def _app(self, controller, action, **params):
        return self._post(_RT, {'c': controller, 'a': action, **params})

    @checked
    def _pages(self, fetch, extract, *, total_field=None, page_count=False):
        rows, seen, offset, expected_total = [], set(), 0, None
        for _ in range(self.max_pages):
            payload = fetch(offset)
            page = extract(payload)
            if not isinstance(page, list):
                raise SourceError('分页列表结构异常')
            if total_field and payload.get(total_field) is None:
                raise SourceError('分页缺少数量字段，无法证明完整性')
            if total_field:
                count = int(payload[total_field])
                if count < 0:
                    raise SourceError('分页数量异常')
                if page_count:
                    if count != len(page):
                        raise SourceError('分页返回数量不一致')
                else:
                    if expected_total is not None and count != expected_total:
                        raise SourceError('分页期间总数变化，等待下一轮重新采集')
                    expected_total = count
            for row in page:
                if not isinstance(row, list) or not row:
                    raise SourceError('分页行结构异常')
                code = code_value(row[0])
                if code in seen:
                    raise SourceError('分页出现重复代码，无法证明完整性')
                seen.add(code)
                rows.append(row)
            offset += len(page)
            if expected_total is not None:
                if offset > expected_total or (not page and offset < expected_total):
                    raise SourceError('分页结果与总数不一致')
                if offset == expected_total:
                    return rows
            elif len(page) < self.page_size:
                return rows
        raise SourceError('分页超过安全上限，结果未完整')

    @checked
    def fetch_strength_board(self, day, *, include_raw=False):
        historical = day < datetime.now(TZ).date().isoformat()
        def load(host, history):
            def fetch(index):
                return self._post(host, {'c': 'ZhiShuRanking', 'a': 'RealRankingInfo',
                    'Date': day, 'st': self.page_size, 'Index': index, 'Order': 1, 'ZSType': 7,
                    'Type': 5 if history else 1, 'apiv': 'w41' if history else 'w21',
                    'VerSion': '5.20.0.2', 'RStart': '0925', 'REnd': '1500'})
            def extract(payload):
                source_date(payload.get('Day'), day)
                return payload['list']
            return self._pages(fetch, extract, total_field='Count', page_count=history)
        try:
            raw = load(_HIS if historical else _HQ, historical)
        except SourceError:
            raw = load(_HQ if historical else _RT, False)
        rows = [{'code': r[0], 'name': r[1], 'strength': number(r[2]),
                 'change': number(r[3]), **({'raw': r} if include_raw else {})} for r in raw]
        rows.sort(key=lambda r: (-r['strength'], r['code']))
        return [{**r, 'rank': i} for i, r in enumerate(rows, 1)]

    @checked
    def fetch_sector_strength(self, code, day):
        payload = self._post(_HIS, {'a': 'GetPlate_Info_QJ', 'c': 'ZhiShuRanking',
                                   'apiv': 'w42', 'Date': day, 'PlateID': code_value(code)})
        source_date(payload.get('Date'), day)
        row = payload['List']
        if not row:
            return None
        return {'code': code, 'rank': int(row[0]), 'strength': number(row[1]), 'date': day}

    @checked
    def fetch_resumption(self, day):
        # Realtime can return yesterday's `date` alongside Day=[today]. Trust only `date`.
        errors = []
        for host, controller, date_key in ((_HIS, 'HisLimitResumption', 'Date'),
                                           (_RT, 'DailyLimitResumption', 'Day')):
            try:
                payload = self._post(host, {'c': controller, 'a': 'GetPlateInfo_w38',
                                           date_key: day, 'st': 100, 'Index': 0, 'apiv': 'w42'})
                source_date(payload.get('date'), day)
                return self._parse_resumption(payload, day)
            except SourceError as exc:
                errors.append(str(exc))
        raise SourceError('复盘不可用: ' + ' / '.join(errors))

    @checked
    def _parse_resumption(self, payload, day):
        nums = payload['nums']
        if not isinstance(nums, dict) or not isinstance(payload['list'], list):
            raise SourceError('复盘统计或题材列表结构异常')
        stats = {'limit_up': int(nums['ZT']), 'limit_down': int(nums['DT']),
                 'broken_ratio': nums.get('ZBL'), 'up': int(nums['SZJS']), 'down': int(nums['XDJS'])}
        themes, unique = [], set()
        for t in payload['list']:
            if not isinstance(t, dict) or not isinstance(t.get('StockList'), list):
                raise SourceError('复盘题材结构异常')
            stocks, seen = [], set()
            if 'num' in t and int(t['num']) != len(t['StockList']):
                raise SourceError('复盘题材股票数量不完整')
            for s in t['StockList']:
                code = code_value(s[0])
                if code in seen or len(s) < 18:
                    raise SourceError('复盘股票重复或字段不完整')
                seen.add(code)
                unique.add(code)
                stamp = datetime.fromtimestamp(int(s[6]), TZ) if s[6] else None
                if stamp:
                    source_date(stamp.date().isoformat(), day)
                stocks.append({'code': code, 'name': s[1],
                    'change': number(s[4]) if s[4] not in (None, '') else None,
                    'first_time': stamp.strftime('%H:%M:%S') if stamp else None,
                    'boards': streak(str(s[9] or '')), 'seal_fund': s[8], 'board_label': s[9] or None,
                    'tags': [x for x in str(s[11] or '').split('、') if x],
                    'reason_full': str(s[17] or s[16] or '').strip() or None})
            themes.append({'code': t['ZSCode'], 'name': t['ZSName'],
                           'explain': (t.get('TCExplain') or '').strip() or None, 'stocks': stocks})
        if len(unique) != stats['limit_up']:
            raise SourceError(f'复盘覆盖不足：{len(unique)}/{stats["limit_up"]}')
        return {'date': day, 'stats': stats, 'themes': themes, 'complete': True}

    @checked
    def fetch_sector_ladder(self, day):
        payload = self._post(_HIS, {'a': 'GetYTFP_BKHX', 'c': 'FuPanLa', 'apiv': 'w42', 'Date': day})
        source_date(payload.get('Date'), day)
        themes = []
        for t in payload['List']:
            stocks = []
            for tier in t.get('TD') or []:
                for s in tier.get('Stock') or []:
                    stocks.append({'code': code_value(s['StockID']), 'name': s['StockName'],
                                   'board_label': (s.get('Tips') or '').strip() or None})
            if len({s['code'] for s in stocks}) != len(stocks) or int(t.get('Count', len(stocks))) != len(stocks):
                raise SourceError('历史梯队数量或重复校验失败')
            themes.append({'code': t['ZSCode'], 'name': t['ZSName'], 'count': len(stocks),
                           'explain': (t.get('Reason') or '').strip() or None, 'stocks': stocks})
        return themes

    @checked
    def fetch_emotion(self, day):
        row, payload = self._dated_info(day, 'ChangeStatistics', lambda r: r['Day'], 100)
        source_date(row.get('Day'), day)
        highest, total, temperature = int(row['lbgd']), int(row['ztjs']), int(row['strong'])
        if not 0 <= highest <= 30 or total < 0 or not 0 <= temperature <= 100:
            raise SourceError('情绪指标超出有效范围')
        return {'highest': highest, 'limit_up': total, 'temperature': temperature,
                'drawdown_count': int(row['df_num']), 'raw': payload}

    @checked
    def fetch_market_mood(self, day):
        payload = self._app('MarketMood', 'MoodNumCount')
        if not isinstance(payload['list'], dict):
            raise SourceError('市场概况结构异常')
        return {'values': payload['list'], 'date_validation': 'observation_only_no_source_date', 'raw': payload}

    @checked
    def fetch_rise_fall(self, day):
        row, payload = self._dated_info(day, 'RiseFallAnalysis', lambda r: r[6], 40)
        source_date(row[6], day)
        rate = number(row[4])
        if not 0 <= rate <= 100:
            raise SourceError('炸板率超出有效范围')
        return {'limit_up': int(row[0]), 'limit_down': int(row[1]), 'natural_limit_up': int(row[2]),
                'touched_limit_down': int(row[3]), 'break_rate': rate, 'failed_limit': int(row[5]), 'raw': payload}

    def _dated_info(self, day, action, row_date, page_size):
        historical = day < datetime.now(TZ).date().isoformat()
        seen = set()
        for index in range(0, page_size * self.max_pages, page_size):
            payload = self._post(_HIS if historical else _RT, {
                'c': 'HisHomeDingPan' if historical else 'HomeDingPan', 'a': action,
                'Date': day, 'Index': index, 'st': page_size,
                'apiv': 'w43' if historical else 'w47', 'VerSion': '5.22.0.2' if historical else '6.2.20.2'})
            rows = payload['info']
            dates = [row_date(row) for row in rows]
            if day in dates:
                return rows[dates.index(day)], payload
            if not historical or not rows or len(rows) < page_size or any(d < day for d in dates):
                break
            if seen.intersection(dates):
                raise SourceError('历史情绪分页重复')
            seen.update(dates)
        raise SourceError('情绪历史中未找到指定交易日')

    @checked
    def fetch_break_rate(self, day):
        payload = self._app('HomeDingPan', 'ZhangTingPoBan', st=20)
        source_date(payload['info']['Day'], day)
        return {'raw': payload}

    @checked
    def fetch_drawdowns(self, day):
        pages = []
        def fetch(index):
            payload = self._app('HomeDingPan', 'SharpWithdrawalList', Index=index,
                                st=self.page_size, Order=0, Type=5)
            pages.append(payload)
            return payload
        def extract(payload):
            source_date(payload['date'], day)
            return payload['info']
        raw = self._pages(fetch, extract, total_field='num')
        return {'rows': [{'code': r[0], 'name': r[1], 'drawdown': number(r[5]),
                          'change': number(r[6])} for r in raw], 'raw': pages}

    @checked
    def fetch_limit_pool(self, day, level):
        if not 1 <= level <= 30:
            raise SourceError('梯队级别超出有效范围')
        historical = day < datetime.now(TZ).date().isoformat()
        def fetch(index):
            return self._post(_HIS if historical else _RT, {'c': 'HisHomeDingPan' if historical else 'HomeDingPan',
                'a': 'DailyLimitPerformance', 'Day': day, 'Index': index, 'st': self.page_size,
                'Order': 0, 'Type': 4, 'PidType': level,
                'apiv': 'w31' if historical else 'w47', 'VerSion': '5.7.0.12' if historical else '6.2.20.2'})
        def extract(payload):
            source_date(payload['info'][1], day)
            return payload['info'][0]
        raw = self._pages(fetch, extract)
        rows = []
        for r in raw:
            if len(r) < 16 or int(r[15]) != level:
                raise SourceError('涨停股所属梯队不符')
            stamp = datetime.fromtimestamp(int(r[4]), TZ)
            source_date(stamp.date().isoformat(), day)
            rows.append({'code': r[0], 'name': r[1], 'boards': level,
                         'limit_time': stamp.strftime('%H:%M:%S'),
                         'limit_timestamp': int(r[4]), 'theme': r[5], 'tags': r[12], 'raw': r})
        rows.sort(key=lambda r: (r['limit_timestamp'], r['code']))
        return rows

    @checked
    def fetch_quote(self, code, day):
        payload = self._app('StockL2Data', 'GetStockPanKou', StockID=code_value(code))
        source_date(payload['day'], day)
        if payload.get('code') != code:
            raise SourceError('盘口返回代码不符')
        real = payload['real']
        clock = str(int(real['time'])).zfill(9)
        stamp = datetime.strptime(day + clock, '%Y-%m-%d%H%M%S%f').replace(tzinfo=TZ)
        return {'code': code, 'name': payload['name'], 'price': number(real['last_px']),
                'change': number(real['px_change_rate']), 'amount': number(real['total_turnover']),
                'source_time': stamp.isoformat(), 'reason': payload.get('ZTReason'), 'raw': payload}

    @checked
    def fetch_sector_trend(self, code, day):
        payload = self._app('ZhiShuL2Data', 'GetTrendIncremental', StockID=code_value(code), Day=day.replace('-', ''))
        source_date(payload['day'], day)
        if payload.get('code') is not None and str(payload['code']) != code:
            raise SourceError('板块分时返回代码不符')
        if not isinstance(payload['trend'], list) or not payload['trend']:
            raise SourceError('板块分时为空')
        return {'code': code, 'raw': payload}
