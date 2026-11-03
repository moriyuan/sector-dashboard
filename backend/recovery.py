"""Bounded recovery of the latest completed session from public sources."""
from datetime import datetime
from .provider import DataError
from .store import TZ
from .rankings import build_rankings
from .taxonomy import sector_kind


def recover(collector,limit=0):
    provider=collector.provider
    day=provider.latest_close()
    now=datetime.now(TZ)
    calendar=collector.store.observed('calendar') or []
    if now.date().isoformat()>day and now.date().isoformat() in calendar and now.strftime('%H:%M')>='09:15':
        raise DataError('下一交易时段已开始；当前成分不能用于回填上一交易日')
    provider.begin_session(day)
    def step(name,fn):
        result=collector.job(name,fn,0)
        print(name,'失败' if collector.failures.get(name) else result or '完成',flush=True)
        return result
    for kind in ('industry','concept'):
        step('board_'+kind,lambda k=kind:collector.boards(k))
        snapshot=collector.store.snapshot(sector_kind(kind,getattr(provider,'concept_source','ths')),day,'close')
        if snapshot:
            step('members_'+kind,lambda s=snapshot:provider.membership_batch(s['payload']['rows'],day))
    step('market',collector.market)
    step('indices',collector.indices)
    collector.pools(day,'close')
    attempted=0
    for kind in ('industry','concept'):
        for row in collector.catalog.get(kind,[]):
            if collector.store.observed('bars:'+row['code']):continue
            if limit and attempted>=limit:break
            code=row['code'];attempted+=1
            def load():
                bars=provider.bars(code)
                collector.store.observe('bars:'+code,[b for b in bars if b['date']<=day])
            step('bars:'+code,load)
            # Stop this transport after its first failure; do not hammer every board.
            if collector.failures.get('bars:'+code):break
        if attempted and collector.failures.get('bars:'+code):break
    for kind in ('industry','concept'):
        step('flow_'+kind,lambda k=kind:collector.flow(k))
        step('board_'+kind,lambda k=kind:collector.boards(k))
    print('gainers',build_rankings(collector.store,day),flush=True)
