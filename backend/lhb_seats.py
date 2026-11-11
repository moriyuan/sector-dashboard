"""Versioned public-report associations. Brokerage seats can serve multiple clients."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from .store import TZ

VERSION = 'public-seat-research-2026-11-08'
REVIEWED_AT = '2026-11-08T12:00:00+08:00'
NOTE = '公开报道关联，实际账户归属待核实。营业部可服务多位客户；“顶级”采用媒体描述。'


def evidence(url, title, basis):
    return dict(url=url, title=title, basis=basis, reviewed_at=REVIEWED_AT)


OPENING = evidence(
    'https://www.jiemian.com/article/13845917.html',
    '界面新闻 · 2026 年开年知名游资动向',
    '报道将南京太平南路、上海长宁区江苏路、宁波桑田路和西安朱雀大街分别关联到作手新一、章盟主、桑田路和方新侠。')
PROFILES = [
    dict(name='章盟主',level='媒体称顶级游资',seat_code='10025080',
         names=['国泰海通证券股份有限公司上海长宁区江苏路证券营业部',
                '国泰海通证券股份有限公司长宁区江苏路证券营业部'],
         sources=[OPENING, evidence(
             'https://www.jiemian.com/article/14163905.html',
             '界面新闻 · 章建平与作手新一布局立讯精密',
             '报道称章建平为顶级游资，并将上海长宁区江苏路列为其常用席位。')]),
    dict(name='作手新一',level='媒体称顶级游资',seat_code='10025054',
         names=['国泰海通证券股份有限公司南京太平南路证券营业部'],
         sources=[OPENING,evidence(
             'https://www.cnfin.com/stock-xh08/a/20210429/1985062.shtml',
             '中国金融信息网 · 南京太平南路营业部报道（2021-04-29）',
             '报道记载市场将原国泰君安南京太平南路与作手新一关联，并使用顶级游资描述；现名称另由 2026 年报道核对。')]),
    dict(name='宁波桑田路',level='知名游资席位',seat_code='10456710',
         names=['国盛证券股份有限公司宁波桑田路证券营业部'],
         sources=[OPENING,evidence(
             'https://www.jiemian.com/article/11731788.html',
             '界面新闻 · 桑田路、呼家楼、方新侠交易报道',
             '报道将国盛证券宁波桑田路列为知名游资常用席位，并说明该营业部有多路资金常驻。')]),
    dict(name='方新侠',level='媒体称顶级游资',seat_code='10453418',
         names=['中信证券股份有限公司西安朱雀大街证券营业部'],
         sources=[OPENING,evidence(
             'https://www.jiemian.com/article/11731788.html',
             '界面新闻 · 桑田路、呼家楼、方新侠交易报道',
             '报道将中信证券西安朱雀大街关联到方新侠，并将其称为顶级游资。')]),
]



for original in PROFILES:
    original['reviewed_at'] = REVIEWED_AT
    original['version'] = VERSION
EXPANDED_VERSION = 'public-seat-research-2026-11-08-v2'
with Path(__file__).with_name('lhb_seat_profiles.json').open() as source:
    expanded = json.load(source)
for profile in expanded:
    profile['version'] = EXPANDED_VERSION
PROFILES.extend(expanded)
LATEST_REVIEW = max(profile['reviewed_at'] for profile in PROFILES)


def available_profiles(cutoff):
    if cutoff is None:
        return PROFILES
    boundary = cutoff.astimezone(TZ)
    return [profile for profile in PROFILES
            if datetime.fromisoformat(profile['reviewed_at']) <= boundary]


def association(seat, cutoff=None):
    for profile in available_profiles(cutoff):
        if seat['name'] not in profile['names']:
            continue
        code = seat.get('seat_code')
        expected_code = profile['seat_code']
        if code and expected_code and code != expected_code:
            continue
        verified_code = bool(code and expected_code)
        return dict(
            name=profile['name'], level=profile['level'],
            sources=deepcopy(profile['sources']),
            match='席位代码与名称' if verified_code else '完整名称（席位代码未独立核验）',
            confidence='公开报道关联，实际账户归属待核实',
            version=profile['version'], reviewed_at=profile['reviewed_at'])
    return None


def annotate(result, cutoff=None):
    """Use only associations available by the selected research cutoff."""
    result = deepcopy(result)
    profiles = available_profiles(cutoff)
    reviewed = max((p['reviewed_at'] for p in profiles), default=REVIEWED_AT)
    version = EXPANDED_VERSION if reviewed == LATEST_REVIEW else VERSION
    result['seat_research'] = dict(
        version=version, reviewed_at=reviewed, available=bool(profiles),
        profile_count=len(profiles),
        note=(NOTE + f' 已核验 {len(profiles)} 组关联。') if profiles
        else '所选截止时间早于本次席位资料核验时间。')
    for row in result['rows']:
        found = {}
        observations = {}
        for side in ('buy', 'sell'):
            for seat in row.get(side, []):
                match = association(seat, cutoff)
                seat['association'] = match
                if not match:
                    continue
                key = (match['name'], seat['name'])
                entry = found.setdefault(key, dict(
                    name=match['name'], level=match['level'], seat=seat['name'],
                    sides=[], states={}, buy=seat['buy'], sell=seat['sell'], net=seat['net'],
                    version=match['version'], reviewed_at=match['reviewed_at']))
                entry['sides'].append(side)
                entry['states'][side] = row.get(side + '_state')
                observations.setdefault(key, []).append(seat)
        for key, entry in found.items():
            if len(entry['sides']) < 2:
                continue
            # Some Shanghai reports split buy/sell amounts across the two lists.
            # Combine complementary amounts once, within this disclosure only.
            values = observations[key]
            for field in ('buy', 'sell'):
                known = {s[field] for s in values if s[field] is not None}
                entry[field] = next(iter(known)) if len(known) == 1 else None
            entry['net'] = (entry['buy'] - entry['sell']
                            if entry['buy'] is not None and entry['sell'] is not None else None)
        row['known_seats'] = list(found.values())
    return result


def stock_summary(rows):
    """Deduplicate disclosures without adding overlapping seat amounts or periods."""
    stocks = {}
    for row in rows:
        for seat in row.get('known_seats', []):
            stock = stocks.setdefault(row['code'], dict(
                code=row['code'], name=row['name'], change=row.get('change'), periods={}))
            period = stock['periods'].setdefault(row['period'], {})
            key = (seat['name'], seat['seat'])
            entry = period.setdefault(key, dict(
                name=seat['name'], seat=seat['seat'], values=set(), disclosure_ids=set(), previous=False))
            entry['values'].add(seat['net'])
            entry['disclosure_ids'].add(row['id'])
            entry['previous'] |= 'previous' in seat.get('states', {}).values()
    for stock in stocks.values():
        for period, entries in stock['periods'].items():
            output = []
            for entry in entries.values():
                values = entry.pop('values')
                entry['conflict'] = len(values) > 1
                entry['net'] = next(iter(values)) if len(values) == 1 else None
                entry['disclosure_ids'] = sorted(entry['disclosure_ids'])
                output.append(entry)
            stock['periods'][period] = sorted(output, key=lambda s: (s['name'], s['seat']))
    return sorted(stocks.values(), key=lambda s: s['code'])
