from datetime import datetime
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.news import NewsStore
from backend.store import Store, TZ
from backend.move_news import related_moves
from backend.gainer_news import gainers_snapshot, scan_one

DAY='2025-02-12'
FACT='北京市商务局印发《智能眼镜购置补贴实施细则》，每件最高补贴500元'
def at(t):return datetime.fromisoformat(DAY+'T'+t).replace(tzinfo=TZ)
def q(code,name,change=10,**kw):
    return dict(code=code,name=name,change=change,is_st=False,concepts=[],industry=[],**kw)
def save(store,kind,rows,stamp):
    rev=store.save(kind,DAY,'close',{'rows':rows})
    with store.connect() as db:
        db.execute('UPDATE snapshots SET collected_at=? WHERE id=?',(stamp.isoformat(timespec='seconds'),rev))
    return rev
def story(n,uid,body,t='10:05',seen=None,title='异动报道',source='cls'):
    return n.ingest(dict(source=source,source_id=uid,title=title,summary=body,region='domestic',
        url=('https://www.cls.cn/detail/' if source=='cls' else 'https://finance.eastmoney.com/a/')+uid,
        published_at=at(t).isoformat()),now=(seen or at(t)).isoformat())
def test_catalyst_links_unlisted_cross_industry_peers_only_top100(tmp_path):
    store=Store(tmp_path/'s');n=NewsStore(store)
    board=dict(code='BK1',name='计算机')
    rows=[q('000001','风险警示公司'),q('300001','星海科技'),q('300002','光学股份'),
          q('300003','其他软件'),q('300004','芯片公司'),q('300005','风险警示乙'),q('300006','芯云科技')]
    rows[0]['is_st']=rows[5]['is_st']=True
    rows[1]['industry']=rows[3]['industry']=[board]
    rows += [q(str(600100+i),str(600100+i)) for i in range(94)]
    rows += [q('300100','风光股份'),q('300101','博纳股份'),q('300102','收跌股份',-3)]
    rev=save(store,'gainers',rows,at('15:10'))
    rate='智能眼镜概念走高，星海科技、光学股份封板，风光股份、博纳股份、风险警示乙跟涨。消息面上，'+FACT+'。'
    orig=story(n,'11',rate)
    peer_report=story(n,'12','智能眼镜概念反弹，芯云科技上涨。消息面上，'+FACT+'。',source='em')
    story(n,'13','智能眼镜概念震荡走高，芯片公司涨停。消息面上，公司公布第三季度业绩大幅增长。')
    story(n,'14','计算机行业走高，其他软件涨停，星海科技同步上行。')
    story(n,'15','受公司收购矿山资产获批影响，星海科技上涨。')
    story(n,'16','报道引用光学股份，其他软件发布业绩报告，净利润增长。')
    notices=[dict(code='300006',period=i) for i in ['single','three','three']]
    m=gainers_snapshot(store,DAY,at('17:00'))
    prof=related_moves(n,DAY,at('17:00'),m,notices,{'300001','300003','300102','300005','300099'})
    p=prof['300001']
    assert m[0]==rev and p['state']=='ok'
    assert [(r['name'],r['rank']) for r in p['rows']]==[('光学股份',2),('芯云科技',5),('风光股份',100)]
    assert p['rows'][0]['periods']==[] and p['rows'][1]['periods']==['single','three']
    assert p['rows'][1]['matches'][0]['evidence']['id']==peer_report
    assert {(c['level'],c['topic']) for c in p['causes']}=={('context','智能眼镜'),('attributed',None)}
    assert next(c for c in p['causes'] if c['topic'])['evidence']['id']==orig
    assert prof['300003']['state']=='unverified' and prof['300003']['rows']==[]
    assert prof['300102']['state']=='not_rising' and prof['300099']['state']=='missing_quote'

def test_time_corrections_and_reason_vs_coincidence(tmp_path):
    store=Store(tmp_path/'s');n=NewsStore(store)
    rows=[q('300001','星海科技'),q('300002','光学股份'),q('300003','其他软件')]
    def get(t):
        tm=at(t);return related_moves(n,DAY,tm,gainers_snapshot(store,DAY,tm),[],{'300001'})['300001']
    save(store,'gainers',rows,at('15:05'))
    real='智能眼镜概念走高，星海科技、光学股份涨停。消息面上，'+FACT+'。'
    r=story(n,'1',real,seen=at('16:05'))
    story(n,'2','昨日星海科技、光学股份涨停。消息面上，'+FACT+'。')
    story(n,'3','星海科技、其他软件上涨。消息面上，该题材人气火热。')
    story(n,'4','今日星海科技涨停。消息面上，公司盘后公告拟收购矿山资产。','16:00')
    story(n,'5','智能眼镜概念走高，光学股份涨停；软件概念拉升，星海科技、其他软件上涨。')
    story(n,'6','成分股星海科技上涨10%，其他软件上涨7%。',title='北京市召开智能眼镜补贴政策会议，ETF上涨5%')
    story(n,'17','成分股星海科技上涨。',title='北京市召开智能眼镜补贴政策会议，指数拉升',source='em')
    story(n,'7','星海科技、其他软件涨停。消息面上，'+FACT+'。','08:50')
    story(n,'8','微软签约星海科技，其他软件上涨。消息面上，公司公布海外订单增长。')
    assert get('15:01')['state']=='unavailable'
    early=get('16:00');assert early['state']=='unverified' and not early['rows'] and len(early['leads'])==1
    middle=get('16:06');assert len(middle['causes'])==1 and middle['rows'][0]['code']=='300002'
    assert middle['causes'][0]['evidence']['id']==r
    story(n,'1','更正：实际报道的是其他公司。',seen=at('16:10'))
    assert get('16:12')['state']=='unverified'
    story(n,'9','今日星海科技因收购矿山资产获批涨停。','17:00',title='收盘复盘')
    late=get('17:01')
    assert late['causes'][0]['level']=='attributed' and late['causes'][0]['evidence']['timing']=='盘后回顾' and late['total']==0
    assert late['causes'][0]['evidence']['excerpt']=='今日星海科技因收购矿山资产获批涨停'

def test_two_themes_ambiguous_names_and_security_codes(tmp_path):
    store=Store(tmp_path/'s');n=NewsStore(store)
    rows=[q('300001','星海科技'),q('300002','光学股份'),q('300003','机器人'),q('300004','同名公司'),q('300005','同名公司')]
    m=(7,None,rows)
    story(n,'1','软件概念走高，星海科技涨停；眼镜概念拉升，光学股份涨停。消息面上，'+FACT+'。')
    story(n,'2','机器人、同名公司涨停。消息面上，国务院发布人形机器人专项补贴。')
    story(n,'3','受国务院发布人形机器人专项补贴影响，300003.SH涨停。')
    story(n,'4','收盘回顾：星海科技可能涨停。消息面上，国务院发布人形机器人专项补贴。')
    check=lambda:related_moves(n,DAY,at('18:00'),m,[],{r['code'] for r in rows})
    out=check()
    assert out['300002']['state']=='ok' and out['300002']['rows']==[]
    assert all(out[c]['state']=='unverified' for c in ('300001','300003','300004','300005'))
    story(n,'5','受国务院发布人形机器人专项补贴影响，300003.SZ涨停。')
    assert check()['300003']['state']=='ok'

def test_list_detail_news_same_cutoff_and_market_revision(tmp_path):
    store=Store(tmp_path/'s');n=NewsStore(store)
    target='300001';peer='300002'
    quotes=[q(peer,'光学股份'),q(target,'星海科技')]
    save(store,'lhb',[dict(id='1',code=target,name='星海科技',period='single',buy=[],sell=[],buy_state='reported',sell_state='reported')],at('15:08'))
    rev=save(store,'gainers',quotes,at('15:10'))
    story(n,'1','光学概念走高，光学股份、星海科技封板。消息面上，'+FACT+'。',seen=at('16:00'))
    tomorrow=datetime(2025,2,13,8,tzinfo=TZ)
    save(store,'gainers',[q(peer,'光学股份',-5),q(target,'星海科技')],tomorrow)
    story(n,'1','勘误：并无上述事件。',seen=tomorrow)
    with TestClient(create_app(store,collect=False)) as c:
        params={'date':DAY}
        d=c.get('/api/lhb/'+target,params=params).json()
        listing=c.get('/api/lhb',params=params).json()
        news=next(x for x in c.get('/api/news/gainers',params=params).json()['rows'] if x['code']==target)
        assert news['attribution']==d['related']==listing['related_by_code'][target]
        assert d['gainers_revision']==listing['gainers_revision']==rev and d['related']['rows'][0]['code']==peer
        assert c.get('/api/lhb/'+target,params={**params,'asof':at('15:55').isoformat()}).json()['related']['state']=='unverified'
        assert c.get('/api/lhb/'+target,params={'date':'2025-02-11'}).json()['related']['state']=='unavailable'

def test_disclosed_risers_outside_top20_get_targeted_news(tmp_path):
    store=Store(tmp_path/'s');n=NewsStore(store)
    rows=[q(str(300001+i),'样本'+str(i)) for i in range(21)]+[q('300100','下跌股票',-3)]
    save(store,'gainers',rows,at('15:10'))
    save(store,'lhb',[{'code':r['code']} for r in rows[-2:]],at('15:30'))
    called=[]
    def fetch(row,now):called.append(row['code']);return []
    for _ in range(21):assert scan_one(n,at('16:00'),fetch)
    assert called[-1]=='300021' and not scan_one(n,at('16:00'),fetch)

def test_article_evidence_only_after_capture_and_only_exact_revision(tmp_path):
    from backend.news import normalize_time
    from backend.news_enrichment import article_url, extract_article
    import pytest
    from html import escape
    n=NewsStore(Store(tmp_path/'s'));m=(1,None,[q('300001','星海科技'),q('300002','光学股份')])
    url='https://finance.sina.com.cn/stock/s/2025-02-12/doc-ina12345.shtml'
    for unsafe in (url+'?next=1',url.replace('sina.com.cn','sina.com.cn.invalid'),url.replace('/stock/s/','/private/')):
        with pytest.raises(ValueError):article_url(unsafe)
    line='智能眼镜概念走高，星海科技、光学股份涨停。消息面上，'+FACT+'。'
    raw=('<html><meta charset="utf-8"><article id="artibody"><p>'+escape(line)+'</p></article></html>').encode()
    article=extract_article(url,raw);assert article['excerpts']==[line]
    stub=dict(source='sina',source_id='1',title='个股异动报道',summary='',url=url,region='domestic',published_at=at('10:00').isoformat())
    n.ingest(stub,now=at('11:00').isoformat())
    n.save_enrichment(stub,article,now=at('16:04').isoformat())
    check=lambda t:related_moves(n,DAY,at(t),m,[],{'300001'})['300001']
    assert check('16:00')['state']=='unverified'
    c=check('16:05')['causes'][0]
    assert c['evidence']['origin']=='原文补证' and c['evidence']['captured_at']==normalize_time(at('16:04'))
    n.ingest(dict(stub,summary='修订后的摘要。'),now=at('16:10').isoformat())
    assert check('16:11')['state']=='unverified'

def test_independent_drivers_in_same_sentence_do_not_join(tmp_path):
    n=NewsStore(Store(tmp_path/'s'));m=(1,None,[q('300001','星海科技'),q('300002','光学股份')])
    story(n,'1','受新产品获得认证并正式投产影响，星海科技涨停，受药物临床试验获批影响，光学股份上涨。')
    out=related_moves(n,DAY,at('16:00'),m,[],{'300001','300002'})
    assert all(p['state']=='ok' and len(p['causes'])==1 and not p['rows'] for p in out.values())
    assert '投产' in out['300001']['causes'][0]['evidence']['fact']
    assert '药物' in out['300002']['causes'][0]['evidence']['fact']

def test_recap_narrows_to_specific_theme_and_labels_post_close(tmp_path):
    n=NewsStore(Store(tmp_path/'s'))
    m=(1,None,[q('300001','星海科技'),q('300002','光学股份'),q('300003','其他软件')])
    story(n,'1','今日软件板块走高，其他软件上涨，其中智能眼镜最为强势，星海科技、光学股份涨停，消息面上，'+FACT+'。','16:00',title='今日收盘复盘')
    out=related_moves(n,DAY,at('17:00'),m,[],{x['code'] for x in m[2]})
    assert out['300003']['state']=='unverified'
    c=out['300001']['causes'][0]
    assert c['topic']=='智能眼镜' and c['evidence']['timing']=='盘后回顾'
    assert [p['code'] for p in out['300001']['rows']]==['300002']
