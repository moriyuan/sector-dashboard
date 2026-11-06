import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.analytics import analyze
from backend.provider import DataError, Eastmoney
from backend.public_data import PublicData
from backend.service import Collector
from backend.store import Store, TZ
from backend.ths import catalog_page, detail_page, funds_catalog_page, parse_bars, today_quote, ThsConcepts, index_code, soup

DAY='2026-10-30'


def html(rows,page=1,pages=1):
    return '<table class="m-pager-table"><tbody>'+rows+'</tbody></table><span class="page_info">'+f'{page}/{pages}'+'</span>'


def member_html(codes,page=1,pages=1,index='885611'):
    cells=''.join(f'<tr><td>{(page-1)*10+i+1}</td><td>{c}</td><td>测试</td></tr>' for i,c in enumerate(codes))
    return '<input id="requestQuery" value="code/301558"><input id="clid" value="'+index+'">'+html(cells,page,pages)


def clock(monkeypatch,hour=17,day=30):
    import backend.ths as module
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,day,hour,tzinfo=TZ)
    monkeypatch.setattr(module,'datetime',Clock)


def test_ths_directory_identity_and_member_pagination():
    page=html('<tr><td>2026-01-01</td><td><a href="https://q.10jqka.com.cn/gn/detail/code/301558/">概念甲</a></td><td>事件</td><td>--</td><td>11</td></tr>')
    assert catalog_page(page)[2][0]==dict(code='THS301558',name='概念甲',ths_cid='301558',expected_count=11,taxonomy='ths')
    parsed=detail_page(member_html(['600001'],2,2),'301558')
    assert parsed['members']==['600001'] and parsed['current']==2
    with pytest.raises(DataError,match='映射'):detail_page(member_html(['600001']),'301559')
    with pytest.raises(DataError,match='验证页'):catalog_page('<script src="chameleon.js"></script>')


def test_funds_directory_fields_pages_and_duplicate_ids(tmp_path):
    def document(page=1,pages=1,count=1):
        rows=''.join(f'<tr><td>{(page-1)*50+i+1}</td><td><a href="https://q.10jqka.com.cn/gn/detail/code/301558/">甲</a></td><td>100</td><td>1%</td><td>3</td><td>2</td><td>1</td><td>11</td><td>领涨股</td><td>10%</td><td>11</td></tr>' for i in range(count))
        return '<input id="baseUrl" value="funds/gnzjl">'+html(rows,page,pages)
    assert funds_catalog_page(document())[2][0]['expected_count']==11
    with pytest.raises(DataError,match='序号'):funds_catalog_page(document().replace('<td>1</td>','<td>7</td>',1))
    with pytest.raises(DataError,match='不是'):funds_catalog_page(document().replace('funds/gnzjl','funds/hyzjl'))
    p=ThsConcepts(Store(tmp_path/'db'));p.read=lambda *a:document(1,2,1)
    with pytest.raises(DataError,match='中间页'):p.catalog(DAY)
    p.read=lambda *a:document(1,1,2)
    with pytest.raises(DataError,match='重复'):p.catalog(DAY)


def test_catalog_uses_one_stable_sort_and_rejects_old_rank_cache(tmp_path,monkeypatch):
    import backend.ths as module
    clock(monkeypatch)
    store=Store(tmp_path/'db')
    store.observe('ths_catalog:'+DAY,[dict(code='THS999999',name='旧排序缓存')])
    store.observe('ths_catalog_checked:'+DAY,DAY+'T16:35:00+08:00')
    store.observe('ths_catalog_schema:'+DAY,2)
    requests=[]
    homepage=html('<tr><td>2026-01-01</td><td><a href="https://q.10jqka.com.cn/gn/detail/code/301558/">新增概念</a></td><td>事件</td><td>--</td><td>11</td></tr>')
    def read(url,day):
        requests.append(url)
        return homepage if url=='https://q.10jqka.com.cn/gn/' else url
    def funds(url,identities=None,identity_sources=None):
        prefix='https://data.10jqka.com.cn/funds/gnzjl/field/tradename/order/asc/page/'
        assert url.startswith(prefix)
        page=int(url[len(prefix):].strip('/'))
        rows=[dict(code=f'THS{300000+(page-1)*50+i}',ths_cid=str(300000+(page-1)*50+i),
                   name=f'概念{page}-{i}',expected_count=11,taxonomy='ths') for i in range(50)]
        return page,2,rows
    monkeypatch.setattr(module,'funds_catalog_page',funds)
    provider=ThsConcepts(store);provider.read=read
    result=provider.catalog(DAY)
    assert len(result)==101 and len({r['code'] for r in result})==101
    assert requests[:2]==[f'https://data.10jqka.com.cn/funds/gnzjl/field/tradename/order/asc/page/{p}/' for p in (1,2)]
    assert 'THS999999' not in {r['code'] for r in result}
    assert store.observed('ths_catalog_schema:'+DAY)==3


def test_missing_funds_code_requires_exact_unique_official_identity():
    document='<input id="baseUrl" value="funds/gnzjl">'+html('<tr><td>1</td><td><a href="http://q.10jqka.com.cn/gn/detail/code/ /">华为手机</a></td><td>100</td><td>1%</td><td>3</td><td>2</td><td>1</td><td>45</td><td>领涨股</td><td>10%</td><td>11</td></tr>')
    for mapping in (None,{'华为手机概念':{'THS301558'}},{'华为手机':{'THS301558','THS300001'}}):
        with pytest.raises(DataError,match='唯一核验'):funds_catalog_page(document,mapping)
    row=funds_catalog_page(document,{'华为手机':{'THS301558'}})[2][0]
    assert row['code']=='THS301558' and row['expected_count']==45
    assert '官方导航' in row['identity_source']
    with pytest.raises(DataError,match='数量异常'):
        funds_catalog_page(document.replace('<td>45</td>','<td>--</td>'),{'华为手机':{'THS301558'}})
    for bad in ('http://q.10jqka.com.cn/gn/detail/code/9999999/','/unexpected/path'):
        with pytest.raises(DataError,match='代码链接异常'):
            funds_catalog_page(document.replace('http://q.10jqka.com.cn/gn/detail/code/ /',bad),{'华为手机':{'THS301558'}})


def test_catalog_resolves_missing_code_from_captured_official_navigation(tmp_path,monkeypatch):
    clock(monkeypatch)
    store=Store(tmp_path/'db');provider=ThsConcepts(store)
    funds='<input id="baseUrl" value="funds/gnzjl">'+html('<tr><td>1</td><td><a href="http://q.10jqka.com.cn/gn/detail/code/ /">概念甲</a></td><td>100</td><td>1%</td><td>3</td><td>2</td><td>1</td><td>11</td><td>领涨股</td><td>10%</td><td>11</td></tr>')
    home=html('<tr><td>2026-01-01</td><td><a href="https://q.10jqka.com.cn/gn/detail/code/301558/">概念甲</a></td><td>事件</td><td>--</td><td>11</td></tr>')
    calls=[]
    def read(url,day):
        calls.append(url)
        return home if url=='https://q.10jqka.com.cn/gn/' else funds
    provider.read=read
    rows=provider.catalog(DAY)
    assert len(rows)==1 and rows[0]['code']=='THS301558' and rows[0]['expected_count']==11
    assert '官方导航' in rows[0]['identity_source']
    assert calls.count('https://q.10jqka.com.cn/gn/')==1


def test_old_catalog_is_only_candidate_and_requires_current_official_quote(tmp_path,monkeypatch):
    clock(monkeypatch)
    store=Store(tmp_path/'db');provider=ThsConcepts(store)
    store.observe('catalog:concept_ths',[dict(code='THS301558',name='概念甲',ths_cid='301558',expected_count=10)])
    funds='<input id="baseUrl" value="funds/gnzjl">'+html('<tr><td>1</td><td><a href="http://q.10jqka.com.cn/gn/detail/code/ /">概念甲</a></td><td>100</td><td>1%</td><td>3</td><td>2</td><td>1</td><td>11</td><td>领涨股</td><td>10%</td><td>11</td></tr>')
    home=html('<tr><td>2026-01-01</td><td><a href="https://q.10jqka.com.cn/gn/detail/code/300001/">另外概念</a></td><td>事件</td><td>--</td><td>12</td></tr>')
    provider.read=lambda url,day:home if url=='https://q.10jqka.com.cn/gn/' else funds
    for bad in ('2026-10-29T15:30:00+08:00',DAY+'T14:00:00+08:00'):
        provider.quote=lambda row,day:dict(row,source_time=bad)
        with pytest.raises(DataError,match='当日官方指数'):provider.catalog(DAY)
    provider.quote=lambda row,day:dict(row,source_time=DAY+'T16:30:00+08:00')
    rows=provider.catalog(DAY)
    resolved=next(r for r in rows if r['code']=='THS301558')
    assert resolved['expected_count']==11 and '当日指数响应' in resolved['identity_source']


def test_detail_ignores_industry_chain_promotional_table():
    page='<table class="series-table"><tbody><tr><td>其他相关股票</td></tr></tbody></table>'+member_html(['600001'])
    assert detail_page(page,'301558')['members']==['600001']


def test_single_page_without_pager_requires_independent_complete_count():
    page=member_html(['600001','600002']).replace('<span class="page_info">1/1</span>','')
    assert detail_page(page,'301558',expected_count=2)['members']==['600001','600002']
    for count in (None,3,20):
        with pytest.raises(DataError):detail_page(page,'301558',expected_count=count)
    # The validated index mapping remains usable even if member completeness fails.
    assert index_code(soup(page),'301558')=='885611'
    with pytest.raises(DataError,match='映射'):index_code(soup(page),'301559')


def test_no_independent_count_still_rejects_short_first_member_page(tmp_path,monkeypatch):
    clock(monkeypatch)
    s=Store(tmp_path/'db');s.observe('calendar',[DAY]);p=ThsConcepts(s)
    p.read=lambda *args:member_html([str(600001+i) for i in range(9)],1,2)
    with pytest.raises(DataError,match='分页序号'):
        p.members(dict(code='THS301558',ths_cid='301558',expected_count=None),DAY)
    assert s.observed('members_meta:THS301558:'+DAY) is None


def test_dated_candle_units_and_intraday_rejection():
    bars=parse_bars('cb({"data":"20261029,9,12,8,10,1000,123456.789,,,,0"})')
    assert bars[0]['amount']==123456.789 and bars[0]['high']==12 and bars[0]['close']==10
    def raw(date='20261030',dt='1530'):
        return 'cb('+json.dumps({'bk_885611':{'1':date,'7':'10','8':'12','9':'9','11':'11','19':'321000000','dt':dt,'name':'概念甲'}})+')'
    bar,stamp=today_quote(raw(),'885611','概念甲',DAY)
    assert bar['amount']==321000000 and stamp==DAY+'T15:30:00+08:00'
    with pytest.raises(DataError,match='日期'):today_quote(raw('20261029'),'885611','概念甲',DAY)
    with pytest.raises(DataError,match='源时间'):today_quote(raw(dt='1459'),'885611','概念甲',DAY)
    with pytest.raises(DataError,match='日期重复'):parse_bars('cb({"data":"20261029,9,12,8,10,1,2;20261029,9,12,8,10,1,2"})')


def test_partial_duplicate_members_never_publish(tmp_path,monkeypatch):
    clock(monkeypatch)
    s=Store(tmp_path/'db');s.observe('calendar',['2026-10-29',DAY]);p=ThsConcepts(s)
    row=dict(code='THS301558',ths_cid='301558',expected_count=11)
    first=[str(600001+i) for i in range(10)]
    p.read=lambda url,day:member_html(first,1,2) if '/page/2/' not in url else member_html(['600001'],2,2)
    with pytest.raises(DataError,match='重复'):p.members(row,DAY)
    assert s.observed('members:THS301558:'+DAY) is None
    p.read=lambda url,day:member_html(first,1,2) if '/page/2/' not in url else member_html(['600011'],2,2)
    assert len(p.members(row,DAY))==11
    clock(monkeypatch,day=31)
    s.observe('calendar',['2026-10-29',DAY,'2026-10-31'])
    with pytest.raises(DataError,match='历史归属'):p.members(row,DAY)


def test_provider_namespace_and_legacy_import_contract(tmp_path):
    s=Store(tmp_path/'db')
    class Legacy(Eastmoney):
        def boards(self,kind):return [dict(code='BK0001',name='旧概念',price=10,change=1,source_time=DAY+'T15:30:00+08:00')]
        def flows(self,kind):return {'BK0001':{'today':100,'source_time':DAY+'T15:30:00+08:00'}}
    c=Collector(s,Legacy(s));c.boards('concept');c.flow('concept')
    assert s.snapshot('concept',DAY,'close')['payload']['taxonomy']=='eastmoney'
    assert s.snapshot('concept_ths',DAY,'close') is None
    assert s.snapshot('flow_concept_ths',DAY,'close') is None
    p=PublicData(s,DAY)
    with pytest.raises(DataError,match='不拼接'):p.flows('concept')


def test_api_default_ths_and_legacy_revision_cannot_mix(tmp_path):
    s=Store(tmp_path/'db')
    def rows(code):return analyze([dict(code=code,name='同名概念',price=10,change=1)],{},[],DAY,'close')
    old=s.save('concept',DAY,'close',{'rows':rows('BK0001')})
    new=s.save('concept_ths',DAY,'close',{'rows':rows('THS301558')})
    s.observe('catalog:concept',[dict(code='BK0001',name='旧目录')])
    s.observe('bars:BK0001',[dict(date='2026-10-29',close=10,change=1,amount=100)])
    with TestClient(create_app(s,collect=False)) as client:
        got=client.get('/api/sectors',params={'kind':'concept','date':DAY}).json()
        assert got['meta']['revision']==new and got['taxonomy']=='ths'
        assert client.get('/api/sectors',params={'kind':'concept','date':DAY,'revision':old}).status_code==404
        legacy=client.get('/api/sectors',params={'kind':'concept','date':DAY,'concept_source':'eastmoney'}).json()
        assert legacy['meta']['revision']==old
        assert client.get('/api/sectors',params={'kind':'concept','date':'2026-10-29'}).json()['rows']==[]
        assert client.get('/api/sectors/THS301558',params={'kind':'concept','date':DAY}).status_code==200


def test_1630_board_refresh_uses_quote_validation(tmp_path,monkeypatch):
    clock(monkeypatch)
    s=Store(tmp_path/'db');p=ThsConcepts(s)
    s.observe('ths_quote:THS301558:'+DAY,dict(code='THS301558',price=10,captured_at=DAY+'T15:40:00+08:00',source_time=DAY+'T15:30:00+08:00'))
    p.catalog=lambda day:[dict(code='THS301558')]
    calls=[]
    def quote(row,day):
        calls.append(row['code']);return dict(row,price=11,source_time=DAY+'T16:30:00+08:00')
    p.quote=quote
    assert p.boards(DAY)[0]['price']==11 and calls==['THS301558']
    assert p.cutoff(DAY)==DAY+'T16:30'


def test_revised_catalog_quarantines_previously_verified_members(tmp_path):
    from backend.rankings import build_rankings
    s=Store(tmp_path/'db');stamp=DAY+'T16:30:00+08:00'
    s.observe('members:THS301558:'+DAY,['600001'])
    s.observe('members_meta:THS301558:'+DAY,{'verified':True,'source':'同花顺','reported_count':1,'expected_count':1})
    s.save('stocks',DAY,'close',{'rows':[dict(code='600001',name='股票',price=10,change=1,amount=100)]},stamp)
    s.save('market',DAY,'close',{'stats':{'amount':100}},stamp)
    class Provider:
        def boards(self,kind):return [dict(code='THS301558',name='概念',taxonomy='ths',expected_count=2,price=10,change=1,source_time=stamp)]
    Collector(s,Provider()).boards('concept')
    row=s.snapshot('concept_ths',DAY,'close')['payload']['rows'][0]
    assert row['membership_verified'] is False and row['up_ratio'] is None and row['amount_share'] is None
    build_rankings(s,DAY)
    assert s.snapshot('gainers',DAY,'close')['payload']['rows'][0]['concepts']==[]
    with TestClient(create_app(s,collect=False)) as client:
        assert client.get('/api/sectors/THS301558',params={'kind':'concept','date':DAY}).json()['members_available'] is False


def test_historical_member_sample_respects_session_cutoff(tmp_path):
    from backend.ths_detail import display_evidence, DETAIL
    store=Store(tmp_path/'sample.db')
    row={'code':'THS301558','name':'概念','expected_count':13}
    base=DETAIL+'301558/'
    def capture(url,clock,text):
        when=DAY+'T'+clock+'+08:00'
        obs=store.observe('ths_raw:'+url,{'captured_at':when,'text':text,'source_url':url})
        with store.connect() as db:db.execute('UPDATE observations SET observed_at=? WHERE id=?',(when,obs))
    first=[str(600001+i) for i in range(10)]
    capture(base,'12:01:00',member_html(first,1,2))
    capture(base,'15:32:00',member_html(first[::-1],1,2))
    capture(base+'page/2/','16:35:00',member_html(['600021','600022','600023'],2,2))
    info=display_evidence(store,row,DAY,'close',DAY+'T15:40:00+08:00',True)
    assert [r['code'] for r in info['sample']]==first[::-1] and len(info['pages'])==1
    assert display_evidence(store,row,DAY,'close',DAY+'T15:30:00+08:00',True)['sample']==[]
    assert len(display_evidence(store,row,DAY,'midday',DAY+'T17:00:00+08:00',True)['pages'])==1


def test_failed_concept_does_not_block_next_concept(tmp_path,monkeypatch):
    from backend.service import Collector
    import backend.service as service
    class Clock:
        @staticmethod
        def now(tz):return datetime(2026,10,30,17,0,tzinfo=TZ)
    monkeypatch.setattr(service,'datetime',Clock)
    class Provider:
        def members(self,code):
            if code=='THS300001':raise DataError('source unavailable')
            return [{'code':'600001'}]
    store=Store(tmp_path/'queue.db');store.observe('calendar',[DAY])
    col=Collector(store,Provider());col.catalog={'concept':[{'code':c,'expected_count':1} for c in ['THS300001','THS300002']]}
    assert not col.enrich_one()
    col.enrich_next=0
    assert col.enrich_one()
    assert store.observed('members:THS300002:'+DAY)==['600001']
