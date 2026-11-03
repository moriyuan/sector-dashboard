"""Bounded public THS recovery, three workers with globally paced requests."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from .rankings import build_rankings


def collect_ths(collector,limit=0,members=True):
    p=collector.provider
    day=p.latest_close();p.begin_session(day)
    catalog=p.ths.catalog(day)
    selected=catalog[:limit] if limit else catalog
    print(f'同花顺 {day}：目录 {len(catalog)}，本轮 {len(selected)}，采集成分 {members}',flush=True)
    def one(row):
        result=[]
        for stage,fn in [('quote',lambda:p.ths.quote(row,day))]+([('members',lambda:p.ths.members(row,day))] if members else []):
            try:
                fn();collector.store.event('ths_'+stage+':'+row['code'],True,'采集完成');result.append(stage)
            except Exception as exc:
                collector.store.event('ths_'+stage+':'+row['code'],False,str(exc))
        return result
    counts={'quote':0,'members':0}
    with ThreadPoolExecutor(max_workers=3) as workers:
        futures=[workers.submit(one,row) for row in selected]
        for i,future in enumerate(as_completed(futures),1):
            for stage in future.result():counts[stage]+=1
            if i%10==0 or i==len(selected):print(i,counts,flush=True)
    p.ths_cached_only=True
    try:collector.boards('concept')
    finally:p.ths_cached_only=False
    print('gainers revision',build_rankings(collector.store,day),flush=True)
    print('本轮成功',counts,flush=True)
