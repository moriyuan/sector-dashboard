"""Close-only, versioned stock rankings; limit prices never use fixed percentages."""
from decimal import Decimal, InvalidOperation
from .analytics import valid_stock, hs_stock
from .taxonomy import sector_kind


def at_limit(row,side='upper_limit'):
    value=row.get(side)
    if not valid_stock(row) or value is None or value<=0:
        return None
    try:
        return Decimal(str(row['price'])).quantize(Decimal('.01'))==Decimal(str(value)).quantize(Decimal('.01'))
    except InvalidOperation:
        return None


def build_rankings(store,day):
    stocks=store.snapshot('stocks',day,'close')
    if not stocks:return None
    pools=store.snapshot('pools',day,'close')
    sealed=(pools['payload']['pools'].get('up') if pools else None)
    up={r['code']:r for r in sealed or []}
    mappings={'industry':{},'concept':{}};refs={};membership_counts={}
    for kind in mappings:
        board=store.snapshot(sector_kind(kind),day,'close')
        refs[kind]=board['id'] if board else None
        count=0
        for row in board['payload']['rows'] if board else []:
            if row.get('membership_verified') is False:continue
            inputs=board['payload'].get('input_refs',{})
            member_id=inputs.get('members',{}).get(row['code'])
            record=store.observation_record(revision=member_id) if member_id else None
            meta_id=inputs.get('members_meta',{}).get(row['code'])
            meta=store.observation_record(revision=meta_id) if meta_id else None
            if not record or (meta and not meta['payload'].get('verified')):continue
            count+=1
            for code in record['payload']:
                mappings[kind].setdefault(code,[]).append({'code':row['code'],'name':row['name']})
        membership_counts[kind]=count
    rows=[]
    for original in stocks['payload']['rows']:
        if not hs_stock(original) or not valid_stock(original):continue
        r=dict(original);code=r['code'];limit=up.get(code)
        r['is_st']='ST' in r.get('name','').upper()
        r['boards']=limit.get('boards') if limit else None
        if limit and at_limit(r) is False:
            r['limit_status']='source_conflict';r['boards']=None;r['boards_source']=None
        elif r['boards'] is not None and r['boards']>0:
            r['limit_status']='sealed';r['boards_source']='东方财富涨停池'
        elif at_limit(r) is True:
            r['limit_status']='sealed_height_unknown';r['boards_source']=None
        elif at_limit(r) is False:
            r['limit_status']='not_sealed';r['boards_source']=None
        else:
            r['limit_status']='unknown';r['boards_source']=None
        r['industry']=mappings['industry'].get(code,[])
        r['concepts']=mappings['concept'].get(code,[])
        r['pool_industry']=limit.get('industry') if limit else None
        rows.append(r)
    rows.sort(key=lambda r:(-r['change'],r['code']))
    inputs={'stocks':stocks['id'],'pools':pools['id'] if pools else None,**refs}
    previous=store.snapshot('gainers',day,'close')
    if previous and previous['payload'].get('input_refs')==inputs:
        return previous['id']
    return store.save('gainers',day,'close',{'rows':rows,'source':stocks['payload'].get('source','东方财富'),
        'input_refs':inputs,
        'membership_counts':membership_counts,'concept_source':'ths','scope':'沪深有效交易股票；行业为东方财富，概念为同花顺；默认排除ST；连板取涨停池，不以涨幅阈值推断'},stocks['source_time'])
