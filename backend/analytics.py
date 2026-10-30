"""Descriptive rules only. Missing inputs never cause weight redistribution."""
import math


def number(v):
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def percentile(values):
    good = sorted(v for v in values if v is not None)
    def rank(v):
        if v is None:
            return None
        if len(good) <= 1:
            return 50.0
        low = sum(x < v for x in good)
        equal = sum(x == v for x in good)
        return 100 * (low + (equal - 1) / 2) / (len(good) - 1)
    return [rank(v) for v in values]


def hs_stock(row):
    return str(row['code']).startswith(('00', '30', '60', '68'))


def valid_stock(row):
    return row.get('price') is not None and row['price'] > 0 and row.get('amount') is not None and row['amount'] > 0 and row.get('change') is not None


def breadth(rows):
    unique = {r['code']: r for r in rows if hs_stock(r)}
    valid = [r for r in unique.values() if valid_stock(r)]
    normal = [r for r in valid if 'ST' not in r.get('name', '').upper()]
    st = [r for r in valid if 'ST' in r.get('name', '').upper()]
    def counts(items):
        up = sum(r['change'] > 0 for r in items)
        return dict(up=up, down=sum(r['change'] < 0 for r in items), flat=sum(r['change'] == 0 for r in items), eligible=len(items), up_ratio=up / len(items) * 100 if items else None)
    return {**counts(normal), 'st': counts(st), 'amount': sum(r['amount'] for r in valid) if valid else None,
            'total': len(unique), 'coverage': len(valid) / len(unique) if unique else None,
            'distribution': [sum(a <= r['change'] < b for r in normal) for a, b in [(-100,-7),(-7,-3),(-3,0),(0,0.00000001),(0.00000001,3),(3,7),(7,1000)]]}


def pool_stats(sealed, broken, down):
    s = {r['code']: r for r in sealed or [] if hs_stock(r) and 'ST' not in r.get('name','').upper()}
    b = {r['code'] for r in broken or [] if hs_stock(r) and 'ST' not in r.get('name','').upper()} - s.keys()
    d = {r['code'] for r in down or [] if hs_stock(r) and 'ST' not in r.get('name','').upper()}
    return dict(limit_up=len(s) if sealed is not None else None, limit_down=len(d) if down is not None else None,
                broken=len(b) if broken is not None and sealed is not None else None,
                broken_ratio=100 * len(b) / (len(s) + len(b)) if sealed is not None and broken is not None and len(s) + len(b) else None,
                max_boards=max((r.get('boards') or 1 for r in s.values()), default=0) if sealed is not None else None)


def analyze(rows, histories, archived, date, mode, calendar=None, universe_codes=None):
    """Histories: official index bars; archived: immutable member-based observations."""
    result = [dict(r) for r in rows]
    by_date = {}
    for code, bars in histories.items():
        for bar in bars:
            if bar['date'] <= date:
                by_date.setdefault(bar['date'], {})[code] = bar
    if mode == 'close':
        for r in rows:
            if r.get('change') is not None:
                by_date.setdefault(date,{})[r['code']] = {'change':r['change'], 'close':r.get('price')}
    # A rank needs the complete known board cross-section for that date.
    rank_maps = {}
    codes = universe_codes if universe_codes is not None else [r['code'] for r in rows]
    dates = sorted(d for d in by_date if d < date or mode == 'close')
    expected_days = [d for d in (calendar or dates) if d <= date and (d < date or mode == 'close')]
    for d in dates:
        vals = [by_date[d].get(c, {}).get('change') for c in codes]
        if vals and all(v is not None for v in vals):
            rank_maps[d] = dict(zip(codes, percentile(vals)))
    for r in result:
        code = r['code']
        bars = {b['date']: b for b in histories.get(code, []) if b['date'] <= date}
        previous_days = [d for d in (calendar or sorted(bars)) if d < date]
        prior = [bars.get(d) for d in previous_days]
        current = r.get('price')
        for n in (5,10,20):
            window = prior[-n:]
            r[f'return_{n}'] = ((current / window[0]['close'] - 1) * 100
                                if len(window) == n and all(b and b.get('close',0) > 0 for b in window) and current is not None else None)
        last6 = prior[-6:]
        r['prior_5'] = (last6[-1]['close'] / last6[0]['close'] - 1) * 100 if len(last6) == 6 and all(b and b.get('close',0) > 0 for b in last6) else None
        last20 = prior[-20:]
        r['drawdown'] = (current / max(b['close'] for b in last20) - 1) * 100 if len(last20) == 20 and all(b and b.get('close',0) > 0 for b in last20) and current is not None else None
        recent = expected_days[-10:]
        r['top_days_10'] = sum(rank_maps[d][code] >= 80 for d in recent) if len(recent) == 10 and all(d in rank_maps for d in recent) else None
        streak = 0
        for d in reversed(expected_days):
            if d not in rank_maps:
                streak = None
                break
            if rank_maps[d][code] < 80:
                break
            streak += 1
        r['streak'] = streak if expected_days else None
        r['matrix'] = [{'date': d, 'change': by_date.get(d, {}).get(code, {}).get('change'), 'percentile': rank_maps.get(d, {}).get(code)} for d in expected_days[-20:]]
        # Low-attention requires 60 actual daily market denominators and all five ranks.
        close_days = expected_days[-60:]
        daily_shares = {a['date']: next((x.get('amount_share') for x in a.get('rows', []) if x['code'] == code), None) for a in archived if a['date'] <= date}
        if mode == 'close':
            daily_shares[date] = r.get('amount_share')
        shares = [daily_shares.get(d) for d in close_days]
        low_attention = len(shares) == 60 and all(v is not None for v in shares) and percentile(shares)[-1] <= 20
        last5 = expected_days[-5:]
        r['cold'] = bool(low_attention and len(last5) == 5 and all(rank_maps.get(d, {}).get(code, 101) <= 30 for d in last5))
        r['labels'] = []
        r['rule_evidence'] = []
    daily = percentile([r.get('change') for r in result])
    prior = percentile([r['prior_5'] for r in result])
    broad = percentile([r.get('up_ratio') for r in result])
    for i, r in enumerate(result):
        r['daily_percentile'] = daily[i]
        r['heat'] = round(.4*daily[i]+.3*prior[i]+.3*broad[i],2) if all(v is not None for v in (daily[i],prior[i],broad[i])) else None
        r['heat_inputs'] = dict(daily=daily[i], prior_5=prior[i], breadth=broad[i])
    heat_ranks = percentile([r['heat'] for r in result])
    ordered = sorted([r for r in result if r.get('change') is not None], key=lambda r:(-r['change'],r['code']))
    rank_lookup = {r['code']: i+1 for i,r in enumerate(ordered)}
    previous_date = max((d for d in dates if d < date), default=None)
    prev_order = sorted(codes, key=lambda c:(-(by_date.get(previous_date,{}).get(c,{}).get('change') or 0),c)) if previous_date in rank_maps else []
    for i, r in enumerate(result):
        r['rank'] = rank_lookup.get(r['code'])
        r['rank_change'] = prev_order.index(r['code']) + 1 - r['rank'] if prev_order and r['rank'] else None
        if heat_ranks[i] is not None and heat_ranks[i] >= 80 and (r.get('change') or 0) > 0:
            r['labels'].append('hot')
            r['rule_evidence'].append('热点强度前20%，且当日上涨；权重40% / 30% / 30%')
        if r['cold']:
            r['labels'].append('cold')
            r['rule_evidence'].append('连续5个收盘日后30%；成交额占比处于自身60日后20%')
        if daily[i] is not None and daily[i] <= 20 and r.get('up_ratio') is not None and r['up_ratio'] <= 20 and r['drawdown'] is not None and r['drawdown'] <= -5:
            r['labels'].append('ice')
            r['rule_evidence'].append('当日涨幅后20%；上涨比例≤20%；距此前20日高点回撤≥5%')
    return sorted(result, key=lambda r: (r['heat'] is None, -(r['heat'] or 0), r['rank'] or 99999))
