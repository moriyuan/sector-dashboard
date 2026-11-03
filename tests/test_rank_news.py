from datetime import datetime
import pytest

from backend.news import NewsStore
from backend.store import Store, TZ
from backend.gainer_news import rank_news, parse_company_page, scan_one


def market(n, day, rows, stamp):
    rev = n.store.save('gainers', day, 'close', {'rows':rows})
    with n.store.connect() as db:
        db.execute('update snapshots set collected_at=? where id=?',(stamp,rev))
    return rev


def stock(code, name, pct=10, **kw):
    return dict(code=code,name=name,change=pct,is_st=False,industry=[],concepts=[],boards=None,**kw)


def item(n, uid, title, stamp):
    return n.ingest(dict(source='em',source_id=uid,title=title,summary=title,
        url=f'https://finance.eastmoney.com/a/{uid}.html',published_at=stamp,region='domestic'),now=stamp)


def test_rank_date_evidence_and_corrections(tmp_path):
    n=NewsStore(Store(tmp_path/'db'));day='2026-11-02'
    a=stock('300085','银之杰');a['industry']=[{'code':'BK1','name':'计算机'}]
    rows=[dict(a,code='000001',name='ST例',is_st=True),a,stock('300999','机器人'),stock('300333','下跌股',-1)]
    market(n,day,rows,day+'T15:35:00+08:00')
    one=item(n,'1','银之杰公告订单',day+'T10:00:00+08:00')
    item(n,'2','计算机行业政策发布',day+'T15:20:00+08:00')
    item(n,'3','机器人产业大会召开',day+'T12:00:00+08:00')
    item(n,'4','银之杰签约',day+'T11:00:00+08:00')
    item(n,'4','更正：签约主体为其他公司',day+'T12:00:00+08:00')
    late=item(n,'5','银之杰次日消息','2026-11-03T08:00:00+08:00')
    view=rank_news(n,day,datetime(2026,11,3,18,tzinfo=TZ));first,ambiguous=view['rows']
    assert [x['code'] for x in view['rows']]==['300085','300999']
    assert [r['id'] for r in first['direct']]==[one] and first['direct'][0]['timing']=='收盘前'
    assert first['sector'][0]['timing']=='收盘后' and first['sector'][0]['basis']=='行业 · 计算机'
    assert not ambiguous['direct'] and late not in {r['id'] for r in first['direct']}
    assert rank_news(n,'2026-11-01',datetime(2026,11,3,18,tzinfo=TZ))['revision'] is None


def test_targeted_news_real_title_time_and_external_links():
    r=stock('300085','银之杰')
    html='''<title>银之杰(300085)个股资讯</title><div class="datelist"><ul>
2026-11-02&nbsp;10:45&nbsp;<a href="https://finance.sina.com.cn/stock/2026-11-02/doc-a.shtml">银之杰披露新产品</a><br/>
2026-11-02 11:11 <a href="https://finance.sina.com.cn/stock/2026-11-02/doc-b.shtml">计算机行业回顾</a><br/>
2026-11-02 10:45 <a href="https://evil.com/doc-c.shtml">银之杰公告</a></ul></div>'''
    stamp=datetime(2026,11,2,16,tzinfo=TZ)
    hits=parse_company_page(html,r,stamp)
    assert len(hits)==1 and hits[0]['published_at'].endswith('10:45:00+08:00')
    with pytest.raises(ValueError):parse_company_page(html.replace('银之杰(300085)','另一公司(300086)'),r,stamp)


def test_targeted_scan_restarts_without_duplicate_reads(tmp_path):
    n=NewsStore(Store(tmp_path/'db'));at=datetime(2026,11,2,16,tzinfo=TZ)
    r=stock('300085','银之杰');market(n,'2026-11-02',[r],at.isoformat());calls=[]
    def fetch(stock,now):
        calls.append(stock['code'])
        return [dict(source='sina',source_id='rank-a',title='银之杰公告',summary='',url='https://finance.sina.com.cn/doc-a.shtml',published_at=now.isoformat(),region='domestic')]
    assert scan_one(n,at,fetch) and not scan_one(NewsStore(n.store),at,fetch)
    view=rank_news(n,'2026-11-02',at);assert calls==['300085']
    assert view['rows'][0]['scan']['ok'] and len(view['rows'][0]['direct'])==1
