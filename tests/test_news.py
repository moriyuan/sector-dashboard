from datetime import datetime
import pytest
from backend.store import Store
from backend.news import NewsStore, normalize_time, clean, validate_analysis, leaders


def item(title='公司公告',source='em',key='1',published='2026-11-02T10:00:00+08:00'):
 return dict(source=source,source_id=key,title=title,summary=title,url=f'https://finance.eastmoney.com/a/{key}.html',published_at=published,region='domestic')

def test_ingest_idempotent_revisions_and_cutoff(tmp_path):
 n=NewsStore(Store(tmp_path/'db'));a=n.ingest(item(),now='2026-11-02T11:00:00+08:00')
 assert n.ingest(item(),now='2026-11-02T12:00:00+08:00')==a
 assert n.list_events(asof='2026-11-02T10:30:00+08:00')['total']==0
 n.ingest(item('更正：撤回公告'),now='2026-11-02T13:00:00+08:00')
 assert n.detail(a,asof='2026-11-02T12:00:00+08:00')['title']=='公司公告'
 assert n.detail(a,asof='2026-11-02T14:00:00+08:00')['title']=='更正：撤回公告'

def test_budget_atomic_reserve_unknown_and_day_reset(tmp_path):
 n=NewsStore(Store(tmp_path/'db'))
 assert n.reserve('a',.7,'2026-11-02')
 assert not n.reserve('b',.4,'2026-11-02')
 assert n.budget('2026-11-02')['spent_or_reserved']==pytest.approx(.7)
 assert n.reserve('b',.4,'2026-11-03')
 n.settle('a',.1,{'prompt_tokens':1});assert n.budget('2026-11-02')['spent_or_reserved']==pytest.approx(.1)

def test_unsupported_links_and_codes_rejected():
 data={'title_zh':'消息','summary':'摘要','impact':'unknown','chain':[],'links':[{'code':'999999','type':'direct','evidence_ids':['n1'],'reason':'提及'}]}
 with pytest.raises(ValueError):validate_analysis(data,{'600001'}, {'n1':{'kind':'news'}},[])
 data['links'][0]['code']='600001';data['links'][0]['evidence_ids']=['fake']
 with pytest.raises(ValueError):validate_analysis(data,{'600001'}, {'n1':{'kind':'news'}},[])
 data['links'][0]['evidence_ids']=['n1'];data['links'][0]['type']='supply_chain'
 with pytest.raises(ValueError):validate_analysis(data,{'600001'}, {'n1':{'kind':'news'}},[])

def test_time_clean_and_leader_ties():
 assert normalize_time('Sun, 01 Nov 2026 01:30:00 -0400').endswith('+00:00')
 assert '<script' not in clean('<script>bad()</script><b>公司</b>')
 rows=[dict(code=str(i),change=3,amount=100,boards=None,is_st=False) for i in range(4)]
 out=leaders(rows);assert len(out['gainers'])==4 and out['boards']==[]

def test_historical_date_freezes_updates_and_first_seen(tmp_path):
 n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(item(),now='2026-11-02T11:00:00+08:00')
 n.ingest(item('次日辟谣'),now='2026-11-03T11:00:00+08:00')
 historical=n.list_events(date='2026-11-02',asof='2026-11-04T11:00:00+08:00')
 assert historical['rows'][0]['title']=='公司公告'
 assert n.detail(eid,asof='2026-11-04T11:00:00+08:00')['first_seen']==normalize_time('2026-11-02T11:00:00+08:00')

def test_ambiguous_names_and_plain_numbers_not_direct(tmp_path):
 store=Store(tmp_path/'db');n=NewsStore(store)
 store.save('gainers','2026-11-02','close',{'rows':[dict(code='300024',name='机器人',amount=1,change=1,boards=None),dict(code='600001',name='样本公司',amount=1,change=1,boards=None)]})
 reports=[dict(title='机器人行业收入300024万元，总量600001件',summary='',revision=1,url='https://example.com',published_at='2026-11-02')]
 assert n.context(reports,'2099-01-01T00:00:00+00:00')['allowed_links']==[]
 reports[0]['title']='机器人（300024）公告'
 assert n.context(reports,'2099-01-01T00:00:00+00:00')['allowed_links'][0]['code']=='300024'

def test_page_does_not_build_every_context(tmp_path,monkeypatch):
 n=NewsStore(Store(tmp_path/'db'))
 from backend.store import now_iso
 for i in range(40):n.ingest(item(title=f'公告{i}',key=str(i),published=now_iso()))
 calls=[];original=n.context
 def count(*args):calls.append(1);return original(*args)
 monkeypatch.setattr(n,'context',count)
 # Include same-second items without using a future date filter.
 from datetime import timedelta
 result=n.list_events(page_size=1,asof=normalize_time(datetime.now().astimezone()+timedelta(seconds=2)))
 assert result['total']==40 and len(calls)<=1

def test_budget_rejects_nonfinite_and_negative(tmp_path):
 n=NewsStore(Store(tmp_path/'db'))
 for x in (float('nan'),float('inf'),-1,0):
  with pytest.raises(ValueError):n.reserve('a',x)
 with pytest.raises(ValueError):n.settle('a',-1,{})

def test_cross_language_uncertain_reports_not_conflated(tmp_path):
 n=NewsStore(Store(tmp_path/'db'))
 a=n.ingest(item('英伟达发布新芯片'),now='2026-11-02T11:00:00+08:00')
 other=item('NVIDIA announces new chip',source='nvidia');other['url']='https://nvidianews.nvidia.com/1'
 b=n.ingest(other,now='2026-11-02T11:00:00+08:00')
 assert a!=b

def test_model_failure_keeps_reserved_budget_and_retries(tmp_path,monkeypatch):
 import threading
 from backend import news_worker as w
 n=NewsStore(Store(tmp_path/'db'))
 eid=n.ingest(item('人工智能新芯片',published=datetime.now().astimezone().isoformat()))
 monkeypatch.setattr(w.requests,'post',lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
 assert w.analyze_batch(n,'test-key',1,threading.Event())==1
 assert n.detail(eid)['status']=='analysis_failed'
 assert n.budget()['spent_or_reserved']>0
 assert w.analyze_batch(n,'test-key',1,threading.Event())==0
 with n.store.connect() as db:db.execute('UPDATE news_jobs SET next_attempt=0')
 assert w.analyze_batch(n,'test-key',1,threading.Event())==1
 assert n.budget()['requests']==2

def test_rss_timezone_and_forbidden_xml():
 from backend.news_sources import parse
 rss=b'<rss><channel><item><title>News</title><description>fact</description><link>https://www.cnbc.com/1</link><pubDate>Sun, 01 Nov 2026 01:30:00 -0400</pubDate></item></channel></rss>'
 assert parse('cnbc',rss)[0]['published_at']=='2026-11-01T05:30:00+00:00'
 with pytest.raises(ValueError):parse('fed',b'<!DOCTYPE rss><rss/>')

def test_api_read_only_validation_and_empty(tmp_path):
 from fastapi.testclient import TestClient
 from backend.app import create_app
 c=TestClient(create_app(Store(tmp_path/'db'),collect=False))
 assert c.get('/api/news').json()['rows']==[]
 assert c.get('/api/news/sources').status_code==200
 assert c.get('/api/news?date=wrong').status_code==422
 assert c.get('/api/news?stock=123').status_code==422
 assert c.post('/api/news').status_code==405
 assert c.get('/api/news/'+'0'*24).status_code==404


def test_named_entity_substring_not_a_company(tmp_path):
 n=NewsStore(Store(tmp_path/'db'))
 n.store.save('gainers','2026-11-02','close',{'rows':[dict(code='000901',name='航天科技')]})
 report=dict(title='西安中科西光航天科技集团与实验室签约',summary='',revision=1,url='https://example.com',published_at='2026-11-02')
 assert n.context([report],'2099-01-01T00:00:00+00:00')['allowed_links']==[]

def test_suspended_security_still_has_direct_identity(tmp_path):
 n=NewsStore(Store(tmp_path/'db'))
 n.store.save('stocks','2026-11-02','close',{'rows':[dict(code='600001',name='样本股份',price=None,amount=None,change=None)]})
 report=dict(title='样本股份公告停牌',summary='',revision=1,url='https://example.com',published_at='2026-11-02')
 ctx=n.context([report],'2099-01-01T00:00:00+00:00')
 assert ctx['allowed_links'][0]['code']=='600001'
 assert ctx['stocks'][0]['change'] is None

def test_feed_cannot_publish_private_origin(tmp_path):
 n=NewsStore(Store(tmp_path/'db'));r=item();r['url']='http://127.0.0.1/admin'
 with pytest.raises(ValueError):n.ingest(r,now='2026-11-02T12:00:00+08:00')

def test_model_rejected_links_not_published(tmp_path):
 n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(item(),now='2026-11-02T11:00:00+08:00');cutoff=normalize_time('2026-11-02T12:00:00+08:00')
 reports=n.reports(eid,cutoff)
 n.save_analysis(eid,reports,dict(allowed_links=[{'code':'600001','type':'direct'}],stocks=[{'code':'600001'}],leaders={},industry_representatives=[]),{'title_zh':'公告','summary':'内容','links':[]},'analyzed',now=cutoff)
 assert n.detail(eid,asof=cutoff)['stocks']==[]

def test_pruning_old_revision_preserves_analysis_identity(tmp_path):
 n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(item(),now='2026-11-02T11:00:00+08:00')
 n.ingest(item('更正公告'),now='2026-11-03T12:00:00+08:00');cutoff=normalize_time('2026-11-04T12:00:00+08:00')
 reports=n.reports(eid,cutoff);n.save_analysis(eid,reports,{},dict(title_zh='更正公告',summary='更正',links=[]),'analyzed',now=cutoff)
 with n.store.connect() as db:db.execute('DELETE FROM news_items WHERE id=1')
 assert n.detail(eid,asof=cutoff)['status']=='analyzed'
 assert n.detail(eid,asof=cutoff)['first_seen']==normalize_time('2026-11-02T11:00:00+08:00')

def test_concurrent_reservation_cannot_exceed_daily_cap(tmp_path):
 from concurrent.futures import ThreadPoolExecutor
 n=NewsStore(Store(tmp_path/'db'))
 with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda key:n.reserve(key,.6,'2026-11-02'),['a','b']))
 assert sum(results)==1
 assert n.budget('2026-11-02')['spent_or_reserved']==pytest.approx(.6)

def test_verified_supply_chain_requires_business_evidence(tmp_path):
 import json
 n=NewsStore(Store(tmp_path/'db'))
 n.store.save('gainers','2026-11-02','close',{'rows':[dict(code='601689',name='拓普集团',change=2,amount=100,boards=None)]})
 b=dict(id='chain-v1',base_id='chain',code='601689',name='拓普集团',theme='robot',verified=True,verified_at='2026-11-02T00:00:00+00:00',excerpt='公司披露机器人执行器业务',url='https://basic.10jqka.com.cn/601689/operate.html',event_terms=['robot'],relation_type='supply_chain',business='执行器环节',chain_verified=False)
 # Fix snapshot time to the same observation day for this boundary test.
 with n.store.connect() as db:
  db.execute("UPDATE snapshots SET collected_at='2026-11-02T08:00:00+08:00'")
  db.execute('INSERT INTO news_business VALUES(?,?)',(b['id'],json.dumps(b)))
 r=dict(title='New humanoid robot system',summary='',revision=1,url='https://nvidianews.nvidia.com/1',published_at='2026-11-02T00:00:00+00:00')
 assert n.context([r],'2026-11-02T01:00:00+00:00')['allowed_links']==[]
 b['chain_verified']=True
 with n.store.connect() as db:db.execute('UPDATE news_business SET payload=?',(json.dumps(b),))
 # A product-level chain alone is now a watch candidate, not an event relation.
 ctx=n.context([r],'2026-11-02T01:00:00+00:00')
 assert ctx['allowed_links']==[] and ctx['watch_candidates'][0]['code']=='601689'
 b.update(event_link_verified=True,event_subjects=['New humanoid robot system'],event_evidence={'kind':'business','text':'公司公告明确为该系统提供执行器','url':'https://basic.10jqka.com.cn/601689/operate.html','date':'2026-11-02'})
 with n.store.connect() as db:db.execute('UPDATE news_business SET payload=?',(json.dumps(b),))
 ctx=n.context([r],'2026-11-02T01:00:00+00:00');link=ctx['allowed_links'][0]
 assert link['type']=='supply_chain' and link['evidence_ids']==['n1','chain-v1-event','chain-v1']
 assert ctx['leaders']['robot']['boards']==[]

def test_mapping_probe_does_not_replace_verified_seed(tmp_path,monkeypatch):
 from backend.news_worker import load_mapping,verify_mapping
 from backend import news_worker
 n=NewsStore(Store(tmp_path/'db'));load_mapping(n)
 with n.store.connect() as db:before=list(db.execute('SELECT * FROM news_business'))
 monkeypatch.setattr(news_worker,'fetch',lambda *_:(_ for _ in ()).throw(AssertionError('verified mapping must not be probed')))
 verify_mapping(n)
 with n.store.connect() as db:after=list(db.execute('SELECT * FROM news_business'))
 assert [tuple(x) for x in before]==[tuple(x) for x in after]
