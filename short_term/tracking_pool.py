"""The current front-row rule, without changing recent-core membership."""

def front_codes(view):
    rows=sorted(view.get('stocks',[]),key=lambda r:(-r.get('boards',0),r.get('limit_timestamp',0),r['code']))[:8]
    return {r['code'] for r in rows} | {r['code'] for r in (view.get('tenDay') or {}).get('rows',[])}


def tracking_codes(view,registry):
    return front_codes(view) | {r['code'] for r in registry.listing()}
