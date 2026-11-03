from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.lhb import collect, report_rows, period, scheduled_day
from backend.store import Store, TZ

DAY='2026-11-02'
def head(tid,reason,code='002086'):
    return dict(TRADE_DATE=DAY,TRADE_ID=tid,SECUCODE=code+'.SZ',SECURITY_CODE=code,
        MARKET='SZ',SECURITY_TYPE_CODE='058001001',SECURITY_NAME_ABBR='东方海洋',
        EXPLANATION=reason,BILLBOARD_BUY_AMT=120000000,BILLBOARD_SELL_AMT=130000000,
        BILLBOARD_NET_AMT=-10000000)

def seat(row,name,net):
    return dict(TRADE_DATE=DAY,TRADE_ID=str(row['TRADE_ID']),SECURITY_CODE=row['SECURITY_CODE'],
        SECUCODE=row['SECUCODE'],EXPLANATION=row['EXPLANATION'],OPERATEDEPT_NAME=name,
        OPERATEDEPT_CODE=name,BUY=20,SELL=20-net,NET=net)

def test_two_reasons_seats_net_and_historical_isolation(tmp_path):
    store=Store(tmp_path/'db')
    one=head(10,'日涨幅偏离值达到7%的前5只证券');three=head(20,'连续3个交易日内涨幅偏离值累计达到30%的证券')
    rows={'summary':[three,one],'buy':[seat(three,'累计买方',4),seat(one,'单日买方',3)],
          'sell':[seat(one,'单日卖方',-2),seat(three,'累计卖方',-1)]}
    fetch=lambda table,day:rows[table]
    first=collect(store,DAY,fetch);assert first['status']=='published'
    assert collect(store,DAY,fetch)['revision']==first['revision']
    with TestClient(create_app(store,collect=False)) as c:
        d=c.get('/api/lhb/002086',params={'date':DAY}).json()
        by={x['period']:x for x in d['rows']}
        assert by['single']['buy'][0]['name']=='单日买方' and by['three']['sell'][0]['net']==-1
        assert by['single']['net']==-10000000 and by['three']['net']==-10000000
        assert c.get('/api/lhb/000001',params={'date':DAY}).json()['rows']==[]
        assert c.get('/api/lhb',params={'date':'2026-10-30'}).json()['revision'] is None
        assert c.get('/api/lhb',params={'date':DAY,'asof':'2026-10-30T16:00:00+08:00'}).json()['rows']==[]
    rows['buy']=[];rows['sell']=[]
    assert collect(store,DAY,fetch)['status']=='partial'
    rows['summary']=[]
    state=collect(store,DAY,fetch);assert state['revision'] and state['status']!='pending'

def test_pending_error_partial_distinct(tmp_path):
    store=Store(tmp_path/'db')
    assert collect(store,DAY,lambda *_:[])['status']=='pending'
    def broken(*a):raise ValueError('upstream')
    assert collect(store,DAY,broken)['status']=='error'
    row=head(1,'严重异常期间累计涨幅达到100%的证券')
    state=collect(store,DAY,lambda t,d:[row] if t=='summary' else [])
    assert state['status']=='partial'
    assert period('连续10个交易日内累计达到100%')=='other' and period(row['EXPLANATION'])=='other'

def test_pages_verified_and_source_date_checked():
    row=head(1,'日换手率达到20%的前5只股票')
    def page(table,day,num):
        return {'success':True,'code':0,'result':{'pages':2,'count':2,'data':[dict(row,TRADE_ID=num)]}}
    assert len(report_rows('summary',DAY,page))==2
    with pytest.raises(ValueError):report_rows('summary',DAY,lambda *a:page('summary',DAY,1))
    with pytest.raises(ValueError):report_rows('summary','2026-10-30',page)
    with pytest.raises(ValueError):report_rows('summary',DAY,lambda *a:{'success':False,'code':500,'result':None})
    assert report_rows('summary',DAY,lambda *a:{'success':False,'code':9201,'message':'返回数据为空','result':None})==[]

def test_schedule_waits_for_verified_close():
    cal=['2026-10-30',DAY,'2026-11-03']
    assert scheduled_day(datetime(2026,11,2,15,29,tzinfo=TZ),cal) is None
    assert scheduled_day(datetime(2026,11,2,15,30,tzinfo=TZ),cal)==DAY
    assert scheduled_day(datetime(2026,11,1,18,tzinfo=TZ),cal) is None
    assert scheduled_day(datetime(2026,11,2,17,tzinfo=TZ),[]) is None


def test_related_news_respects_stock_exchange_and_historical_cutoff(tmp_path):
    from backend.gainer_news import mention
    row={'code':'000001','name':'平安银行'}
    for text in ('上证指数（000001.SH）收盘','SH000001上涨','金额（000001元）','证券代码 000001.SH'):
        assert mention(text,row) is None
    for text in ('平安银行上涨','证券（000001）公告','SZ000001公告','000001.SZ上涨'):
        assert mention(text,row)
    store=Store(tmp_path/'db')
    with TestClient(create_app(store,collect=False)) as c:
        assert c.get('/api/lhb',params={'date':'2020-01-02'}).json()['status']=='unarchived'


def test_partial_side_preserves_only_same_notice_and_stock_news(tmp_path):
    from backend.news import NewsStore
    store=Store(tmp_path/'db')
    h=head(17,'日涨幅偏离值达到7%的前5只证券')
    b=seat(h,'样本买方',3)
    records={'summary':[h],'buy':[b],'sell':[seat(h,'样本卖方',-1)]}
    fetch=lambda t,d:records[t]
    collect(store,DAY,fetch)
    records['buy']=[dict(b,EXPLANATION='另一个原因')]
    collect(store,DAY,fetch)
    n=NewsStore(store)
    stamp=DAY+'T12:00:00+08:00'
    item=dict(source='em',source_id='1',title='东方海洋（002086）披露业务进展',summary='证券代码002086',
              url='https://finance.eastmoney.com/a/1.html',published_at=stamp,region='domestic')
    event=n.ingest(item,now=stamp)
    with TestClient(create_app(store,collect=False)) as c:
        v=c.get('/api/lhb/002086',params={'date':DAY}).json()
        assert v['status']=='partial' and v['rows'][0]['buy_state']=='previous'
        assert v['rows'][0]['buy'][0]['net']==3 and v['stock']['direct'][0]['id']==event
    # A different notice cannot inherit a seat merely because its security is the same.
    records['summary']=[dict(h,TRADE_ID=18)]
    collect(store,DAY,fetch)
    assert store.snapshot('lhb',DAY,'close')['payload']['rows'][0]['buy']==[]


def test_orphan_a_share_seat_blocks_complete_label(tmp_path):
    s=Store(tmp_path/'db');a=head(5,'日换手率达到20%的前5只证券')
    rows={'summary':[a],'buy':[seat(a,'买方',8),seat(dict(a,TRADE_ID=7),'另一披露的席位',4)],
          'sell':[seat(a,'卖方',-3)]}
    result=collect(s,DAY,lambda k,d:rows[k])
    assert result['status']=='partial' and result['issues']
    by=s.snapshot('lhb',DAY,'close')['payload']['rows']
    assert len(by)==1 and [r['name'] for r in by[0]['buy']]==['买方']
