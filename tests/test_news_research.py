import json
import pytest
from backend.news import NewsStore
from backend.store import Store
from backend.news_research import same_event, detect_themes, research_priority, validate_research
from backend.news_enrichment import article_url, extract_article


def report(title, summary='', source='em', key='1', date='2026-11-02T10:00:00+08:00'):
    hosts={'em':'finance.eastmoney.com/a/','cls':'www.cls.cn/detail/','sina':'finance.sina.com.cn/7x24/'}
    return dict(source=source,source_id=key,title=title,summary=summary or title,
                url='https://'+hosts[source]+key,published_at=date,region='domestic')


def test_mixed_language_topics_and_business_context():
    assert 'ai' in detect_themes('AI漫剧与物理AI带动TPU和Google Cloud需求')
    assert 'chips' in detect_themes('DDR5和HBM存储芯片')
    assert 'consumer' not in detect_themes('招商银行零售贷款，消费电子封装胶')
    assert 'consumer' in detect_themes('白酒消费复苏')
    assert 'resources' in detect_themes('钨精矿价格与主焦煤供需')
    assert 'ai' not in detect_themes('retail sales remain stable')


def test_event_matching_requires_same_fact_and_stage():
    a=report('传Meta与傅利叶合作开发人形机器人，傅利叶称暂不方便回复')
    b=report('傅利叶回应与Meta合作人形机器人传闻：暂不方便回复',source='cls')
    assert same_event(a,b)
    assert not same_event(a,report('傅利叶否认与Meta合作开发人形机器人'))
    assert not same_event(a,report('Meta确认与傅利叶合作开发人形机器人'))
    assert same_event(report('瑞联新材：持续寻找优质投资标的 重点关注电子材料和医药领域'),report('瑞联新材称持续寻找投资标的，重点关注电子材料和医药领域'))
    assert not same_event(report('公司公告1'),report('公司公告2'))
    assert not same_event(report('英伟达发布新芯片'),report('NVIDIA announces new chip'))
    assert same_event(report('传Meta与傅利叶合作开发人形机器人'),report('Meta reportedly partners with Fourier on humanoid robot'))
    assert not same_event(report('甲公司一季度利润增长20%'),report('甲公司三季度利润增长20%'))
    assert not same_event(report('中国石化拟投资100亿元建设福建炼化项目'),report('中国石化拟投资100亿元建设湖南炼化项目'))
    assert not same_event(report('宁德时代拟建设动力电池生产基地'),report('宁德时代拟建设储能电池生产基地'))


def test_merge_is_versioned_and_corrections_stay_separate(tmp_path):
    n=NewsStore(Store(tmp_path/'db'))
    a=n.ingest(report('传Meta与傅利叶合作开发人形机器人'),now='2026-11-02T10:01:00+08:00')
    b=n.ingest(report('傅利叶与Meta合作开发人形机器人传闻',source='cls'),now='2026-11-02T10:02:00+08:00')
    n.merge_events(now='2026-11-02T11:00:00+08:00')
    assert n.list_events(asof='2026-11-02T10:30:00+08:00')['total']==2
    d=n.detail(b,asof='2026-11-02T11:01:00+08:00')
    assert len(d['reports'])==2 and d['id']==a
    assert n.list_events(asof='2026-11-02T11:01:00+08:00')['total']==1
    c=n.ingest(report('傅利叶否认与Meta合作开发人形机器人',key='3'),now='2026-11-02T11:02:00+08:00')
    n.merge_events(now='2026-11-02T11:03:00+08:00')
    assert n.detail(c,asof='2026-11-02T11:04:00+08:00')['id']==c


def test_article_security_and_extraction():
    for url in ['http://www.federalreserve.gov/x','https://127.0.0.1/x','https://www.federalreserve.gov.evil.com/x','https://www.cls.cn/api/cache','https://user@www.cls.cn/detail/1']:
        with pytest.raises(ValueError):article_url(url)
    url='https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm'
    article_url(url)
    raw=b'<html><nav>ignore navigation</nav><main id="article"><h3>FOMC statement</h3><p>The committee maintained rates at 3.75 to 4 percent.</p><p>Two members preferred a 25 basis point increase.</p></main><script>ignore()</script></html>'
    result=extract_article(url,raw)
    assert '3.75' in ' '.join(result['excerpts']) and 'ignore' not in ' '.join(result['excerpts'])
    with pytest.raises(ValueError):extract_article(url,b'<html><p>Please sign in to continue</p></html>')


def test_enrichment_cutoff_and_idempotence(tmp_path):
    n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(report('AI算力公告'),now='2026-11-02T10:01:00+08:00')
    r=n.reports(eid,'2026-11-02T03:00:00+00:00')[0]
    payload=dict(status='ok',excerpts=['新增披露的投资金额为100亿元。'],url=r['url'],content_hash='x')
    n.save_enrichment(r,payload,now='2026-11-02T12:00:00+08:00')
    n.save_enrichment(r,payload,now='2026-11-02T12:01:00+08:00')
    assert n.enrichments([r],'2026-11-02T03:00:00+00:00')==[]
    assert len(n.enrichments([r],'2026-11-02T05:00:00+00:00'))==1


def test_depth_rejects_unreferenced_facts_and_unknown_company():
    good=dict(level='focus',new_facts=[dict(text='新增投资计划',evidence_ids=['n1'])],mechanisms=[dict(text='可能增加设备需求，取决于采购落地',evidence_ids=['n1'],kind='inference')],baseline='未取得上期数据',counterarguments=['项目可能延期'],watchpoints=['关注正式采购公告'],market_context='缺少事件后行情，不能判断市场反应')
    validate_research(good,{'n1':{}})
    bad={**good,'new_facts':[dict(text='无来源事实',evidence_ids=['fake'])]}
    with pytest.raises(ValueError):validate_research(bad,{'n1':{}})
    assert research_priority([report('OpenAI拟给数据中心改名')],['ai'])['level']=='brief'
    assert research_priority([report('湖南裕能预计高端磷酸铁锂供给偏紧')],['energy'])['level']=='focus'


def test_generic_mapping_is_watch_only(tmp_path):
    n=NewsStore(Store(tmp_path/'db'))
    n.store.save('gainers','2026-11-02','close',{'rows':[dict(code='688041',name='海光信息',amount=1,change=1)]})
    with n.store.connect() as db:db.execute("UPDATE snapshots SET collected_at='2026-11-02T08:00:00+08:00'")
    b=dict(id='cpu',code='688041',name='海光信息',theme='ai',verified=True,verified_at='2026-11-02T00:00:00+00:00',event_terms=['AI'],excerpt='官网列示CPU业务',url='https://www.hygon.cn/product/cpu',business='CPU')
    with n.store.connect() as db:db.execute('INSERT INTO news_business VALUES(?,?)',('cpu',json.dumps(b)))
    r={**report('AI数据中心改名'),'revision':1}
    ctx=n.context([r],'2026-11-02T02:00:00+00:00')
    assert not ctx['allowed_links'] and not ctx['stocks']
    assert ctx['watch_candidates'][0]['code']=='688041'


def test_sina_headline_and_body_rumor_are_grouped():
    title='Meta与中国企业合作开发人形机器人？傅利叶回应'
    text='近日有消息称Meta与傅利叶合作开发人形机器人，傅利叶表示暂不方便回复。'
    assert same_event(report(title,text),report('【'+title+'】'+text,text,source='sina'))


def test_failed_enrichment_retries_after_restart(tmp_path,monkeypatch):
    from backend import news_enrichment as en
    from backend.news import normalize_time
    n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(report('重大AI订单',key='2026110201.html'),now='2026-11-02T10:01:00+08:00')
    reports=n.reports(eid,'2099-01-01T00:00:00+00:00');calls=[]
    def failed(url):calls.append(url);raise TimeoutError()
    monkeypatch.setattr(en,'fetch_article',failed)
    en.enrich_event(n,reports);en.enrich_event(n,reports)
    assert len(calls)==1
    with n.store.connect() as db:
        row=db.execute('SELECT id,payload FROM news_enrichment').fetchone();payload=json.loads(row['payload']);payload['next_attempt']=0
        db.execute('UPDATE news_enrichment SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
    n=NewsStore(Store(tmp_path/'db'));en.enrich_event(n,reports)
    assert len(calls)==2 and n.enrichments(reports,normalize_time('2099-01-01'))[0]['attempts']==2


def test_legacy_unrelated_and_exhausted_jobs_can_upgrade(tmp_path,monkeypatch):
    import threading
    from backend import news_worker as w
    from backend.store import now_iso
    n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(report('AI漫剧',date=now_iso()))
    reports=n.reports(eid,'2099-01-01T00:00:00+00:00');n.save_analysis(eid,reports,{},status='unrelated')
    with n.store.connect() as db:
        row=db.execute('SELECT payload FROM news_analysis').fetchone();p=json.loads(row[0]);p['prompt_version']='news-v2'
        db.execute('UPDATE news_analysis SET payload=?',(json.dumps(p),));db.execute('INSERT INTO news_jobs VALUES(?,3,9999999999)',(eid,))
    seen=[];monkeypatch.setattr(w,'analyze',lambda *args:seen.append(args[1]))
    assert w.analyze_batch(n,'test',1,threading.Event())==1 and seen==[eid]


def test_domestic_macro_focus_is_not_discarded(tmp_path,monkeypatch):
    from backend import news_worker as w
    from backend.store import now_iso
    n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(report('央行公布利率决议：下调政策利率',date=now_iso()))
    monkeypatch.setattr(w,'enrich_event',lambda *args:None)
    w.analyze(n,eid,'')
    assert n.detail(eid)['status']=='model_unconfigured'


def test_article_blocks_private_dns_before_network(monkeypatch):
    from backend import news_enrichment as en
    monkeypatch.setattr(en.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    monkeypatch.setattr(en.urllib3,'HTTPSConnectionPool',lambda *a,**k:pytest.fail('must not connect'))
    with pytest.raises(ValueError):en.fetch_article('https://www.cls.cn/detail/123')


def test_successful_enrichment_restarts_analysis_after_crash(tmp_path,monkeypatch):
    import threading
    from backend import news_worker as w
    from backend.store import now_iso
    n=NewsStore(Store(tmp_path/'db'));eid=n.ingest(report('AI重大订单公告',key='123.html',date=now_iso()))
    reports=n.reports(eid,'2099-01-01T00:00:00+00:00')
    n.save_analysis(eid,reports,{'priority':{'level':'focus'},'enrichment':[]},dict(title_zh='订单',summary='订单',links=[]),'analyzed')
    n.save_enrichment(reports[0],dict(status='ok',url=reports[0]['url'],excerpts=['订单明细'],content_hash='new'))
    calls=[];monkeypatch.setattr(w,'analyze',lambda *args:calls.append(args[1]))
    assert w.analyze_batch(n,'test',1,threading.Event())==1 and calls==[eid]


def test_baseline_uses_corrected_revision_only(tmp_path):
    n=NewsStore(Store(tmp_path/'db'))
    n.store.save('gainers','2026-11-02','close',{'rows':[dict(code='600001',name='样本股份',amount=1,change=1)]})
    with n.store.connect() as db:db.execute("UPDATE snapshots SET collected_at='2026-11-02T08:00:00+08:00'")
    n.ingest(report('样本股份订单100亿元',date='2026-10-31T10:00:00+08:00'),now='2026-10-31T11:00:00+08:00')
    n.ingest(report('更正：样本股份订单10亿元',date='2026-10-31T10:00:00+08:00'),now='2026-11-01T11:00:00+08:00')
    ctx=n.context([{**report('样本股份新订单',key='2'),'revision':3}],'2026-11-02T02:00:00+00:00')
    prior=[v['text'] for v in ctx['evidence'].values() if v['kind']=='prior']
    assert len(prior)==1 and '100亿元' not in prior[0] and '10亿元' in prior[0]
