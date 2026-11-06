"""Recover display evidence from THS pages already captured in the requested session."""
import json
import re
from functools import lru_cache

from .provider import DataError
from .ths import detail_page, soup

DETAIL = 'https://q.10jqka.com.cn/gn/detail/code/'
FUNDS = 'https://data.10jqka.com.cn/funds/gnzjl/field/tradename/order/asc/page/'


def captures(store, prefix, start, end):
    key = 'ths_raw:' + prefix
    with store.connect() as db:
        rows = db.execute("""SELECT id,key,payload FROM observations WHERE id IN
            (SELECT MAX(id) FROM observations WHERE key>=? AND key<? AND observed_at BETWEEN ? AND ? GROUP BY key)
            ORDER BY key""", (key, key[:-1]+'0', start, end)).fetchall()
    for row in rows:
        raw = json.loads(row['payload'])
        if raw.get('source_url') == row['key'][8:] and start <= raw.get('captured_at','') <= end:
            yield row['id'], raw


@lru_cache(maxsize=32)
def directory_leaders(text):
    doc = soup(text)
    if not doc.select_one('input#baseUrl[value="funds/gnzjl"]'):
        return {}
    leaders = {}
    for tr in doc.select('table tbody tr'):
        cells = tr.find_all('td')
        if len(cells) != 11:
            continue
        concept, stock = (cells[i].find('a',href=True) for i in (1,8))
        cid = concept and re.fullmatch(r'(?:https?://q\.10jqka\.com\.cn)?/gn/detail/code/(\d{6})/',concept['href'])
        code = stock and re.fullmatch(r'https?://stockpage\.10jqka\.com\.cn/(\d{6})/?',stock['href'])
        if cid and code:
            leaders[('THS'+cid[1],concept.get_text(strip=True))] = (code[1],stock.get_text(strip=True))
    return leaders


def display_evidence(store, row, day, mode, cutoff, need_sample):
    start = day + ('T11:30' if mode == 'midday' else 'T15:00')
    end = min(cutoff or '', day + ('T12:59:59+08:00' if mode == 'midday' else 'T23:59:59+08:00'))
    result = dict(leader=None, sample=[], pages=[], total_pages=None, complete=False)
    if end < start:
        return result
    for revision, raw in captures(store, FUNDS, start, end):
        try:
            leader = directory_leaders(raw['text']).get((row['code'],row['name']))
        except DataError:
            continue
        if leader:
            result['leader'] = dict(code=leader[0],name=leader[1],url=raw['source_url'],captured_at=raw['captured_at'],revision=revision)
            break
    if not need_sample:
        return result
    cid = row['code'][3:]
    base = DETAIL+cid+'/'
    codes = {}
    identity = None
    duplicates = False
    for revision, raw in captures(store, base, start, end):
        suffix = raw['source_url'][len(base):]
        page_match = re.fullmatch(r'page/(\d+)/',suffix) if suffix else None
        if suffix and not page_match:
            continue
        number = int(page_match[1]) if page_match else 1
        try:
            page = detail_page(raw['text'], cid, row.get('expected_count'))
        except DataError:
            continue
        if page['current'] != number or number > page['pages']:
            continue
        signature = (page['index_code'],page['pages'])
        if identity is not None and signature != identity:
            continue
        identity = signature
        result['total_pages'] = page['pages']
        result['pages'].append(dict(page=number,revision=revision,captured_at=raw['captured_at'],url=raw['source_url']))
        duplicates |= bool(codes.keys() & set(page['members'])) or len(set(page['members'])) != len(page['members'])
        codes.update(zip(page['members'],page['names']))
    expected = row.get('expected_count')
    result['complete'] = bool(identity and not duplicates and len(result['pages']) == result['total_pages']
                              and (expected is None or expected == len(codes)))
    result['sample'] = [dict(code=code,name=name) for code,name in codes.items()]
    return result
