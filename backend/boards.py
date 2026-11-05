"""最高板走势：每日涨停池中连板数最高的三个层级及对应股票名单。
数据来自每日 pools 快照（东财涨停池），历史深度取决于池快照的留存/回补范围。"""
from .analytics import hs_stock

TIERS = 3


def history(store, limit=60):
    """按日期升序返回 [{'date','tiers':[{'boards','names'}...]}]，最多三级（最高/次高/第三）。"""
    days = sorted({d['trade_date'] for d in store.dates() if d['mode'] == 'close'})
    rows = []
    for day in days:  # 只处理有池快照的日期；数量少，直接全扫再截尾
        snap = store.snapshot('pools', day, 'close')
        if not snap:
            continue
        sealed = (snap['payload'].get('pools') or {}).get('up') or []
        by_boards = {}
        for r in sealed:
            if not hs_stock(r) or 'ST' in (r.get('name') or '').upper():
                continue
            boards = int(r.get('boards') or 1)
            by_boards.setdefault(boards, []).append(r.get('name') or r['code'])
        if not by_boards:
            continue
        top = sorted(by_boards.items(), reverse=True)[:TIERS]
        rows.append({'date': day,
                     'tiers': [{'boards': b, 'names': sorted(names)} for b, names in top]})
    return rows[-limit:]
