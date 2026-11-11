"""Daily PE history. Compare each index only with its own unchanged PE series."""
import json
import math
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup

from .store import TZ

INDICES = {
    '000001': '上证指数', '399001': '深证成指', '399006': '创业板指',
    '000300': '沪深300', '000680': '科创综指',
    '000905': '中证500', '000852': '中证1000',
}
CSI = 'https://www.csindex.com.cn/csindex-home/perf/index-perf'
MIN_DAYS = 120


def source(code):
    if code.startswith('399'):
        return 'ETF.run', '等权 PE-TTM', f'https://www.etf.run/index/SZSE/{code}'
    return '中证指数', '滚动市盈率', f'https://www.csindex.com.cn/#/indices/family/detail?indexCode={code}'


def parse_etf(html, code):
    # Read the public chart's JSON data without executing provider scripts.
    prefix = 'self.__next_f.push('
    for script in BeautifulSoup(html, 'html.parser').find_all('script'):
        body = (script.string or '').strip()
        if 'compressedIndexDaily' not in body or not body.startswith(prefix):
            continue
        packet = json.loads(body[len(prefix):].removesuffix(';')[:-1])[1]
        _, _, payload = packet.partition(':')
        props = json.JSONDecoder().raw_decode(payload)[0][3]
        if props['indexInfo']['innerCode'] != code + '.SZSE':
            raise ValueError('估值指数身份不符')
        series = props['compressedIndexDaily']
        fields = series['fieldNames']
        day, pe = fields.index('date'), fields.index('equalWeightedPeTtm')
        return [(r[day], r[pe]) for r in series['values']]
    raise ValueError('缺少公开估值图表')


def fetch(code, end):
    if code.startswith('399'):
        r = requests.get(source(code)[2], timeout=(5, 20))
        r.raise_for_status()
        observations = parse_etf(r.content, code)
    else:
        r = requests.get(CSI, params=dict(indexCode=code, startDate='20150101',
                                         endDate=end.replace('-', '')), timeout=(5, 20))
        r.raise_for_status()
        observations = []
        for row in r.json()['data']:
            if row['indexCode'] != code:
                raise ValueError('估值指数身份不符')
            day = datetime.strptime(row['tradeDate'], '%Y%m%d').date().isoformat()
            observations.append((day, row['peg']))
    rows = {}
    for day, value in observations:
        day = date.fromisoformat(day).isoformat()
        if day > end:
            continue
        if day in rows:
            raise ValueError('估值日期重复')
        value = float(value) if value is not None else None
        rows[day] = value if value is not None and math.isfinite(value) else None
    if not any(v is not None and v > 0 for v in rows.values()):
        raise ValueError('缺少有效市盈率')
    return rows


def collect_one(store, code, end):
    key = 'index-pe:v1:' + code
    rows = fetch(code, end)
    old = store.observed(key) or {}
    if old and max(rows) < max(old):
        raise ValueError('估值来源日期回退')
    # Preserve earlier history if a provider shortens its response window.
    store.observe(key, {**old, **rows})
    return max(rows)


def summarize(rows, day, mode):
    dates = sorted(d for d in rows if d < day or (d == day and mode == 'close'))
    result = dict(date=None, pe=None, percentile=None, count=0, start=None)
    if not dates:
        return result
    last = dates[-1]
    end = date.fromisoformat(last)
    try:
        start = end.replace(year=end.year - 5)
    except ValueError:  # February 29
        start = end.replace(year=end.year - 5, day=28)
    sample = [d for d in dates if d > start.isoformat() and rows[d] is not None and rows[d] > 0]
    value = rows[last]
    result.update(date=last, pe=value, count=len(sample), start=sample[0] if sample else None)
    if value is not None and value > 0 and len(sample) >= MIN_DAYS:
        result['percentile'] = round(100 * sum(rows[d] < value for d in sample) / len(sample), 1)
    return result


def day_view(store, day, mode):
    cutoff = day + ('T12:00:00+08:00' if mode == 'midday' else 'T23:59:59+08:00')
    result = []
    with store.connect() as db:
        for code, name in INDICES.items():
            key = 'index-pe:v1:' + code
            record = db.execute('SELECT observed_at,payload FROM observations WHERE key=? AND observed_at<=? ORDER BY id DESC LIMIT 1',
                                (key, cutoff)).fetchone()
            if not record:
                record = db.execute('SELECT observed_at,payload FROM observations WHERE key=? ORDER BY id LIMIT 1', (key,)).fetchone()
            row = summarize(json.loads(record['payload']) if record else {}, day, mode)
            provider, method, url = source(code)
            result.append(dict(row, code=code, name=name, provider=provider, method=method, url=url,
                               backfilled=bool(record and record['observed_at'] > cutoff)))
    return result


def run_scheduler(store, stop):
    while not stop.is_set():
        now = datetime.now(TZ)
        today = now.date().isoformat()
        calendar = store.observed('calendar') or []
        eligible = [d for d in calendar if d < today or (d == today and now.hour >= 17)]
        if eligible:
            target = max(eligible)
            for code in INDICES:
                if stop.is_set():
                    return
                key = 'index-pe:check:' + code
                previous = store.observed(key) or {}
                if previous.get('latest', '') >= target or now.timestamp() < previous.get('retry', 0):
                    continue
                try:
                    latest = collect_one(store, code, target)
                    store.event('指数估值 ' + code, latest >= target, '源日期 ' + latest)
                except Exception as exc:
                    latest = previous.get('latest', '')
                    store.event('指数估值 ' + code, False, str(exc))
                store.observe(key, dict(latest=latest, retry=now.timestamp()+3600))
        stop.wait(300)
