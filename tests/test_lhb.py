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
        listing=c.get('/api/lhb',params={'date':DAY}).json()
        assert len(listing['rows'])==2 and all(r['change'] is None for r in listing['rows'])
        assert listing['quote_date'] is None and listing['quote_source_time'] is None
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


def test_listing_daily_change_covers_all_stocks_and_periods_without_cross_date(tmp_path,monkeypatch):
    store=Store(tmp_path/'db')
    clock=[DAY+'T16:00:00+08:00']
    monkeypatch.setattr('backend.store.now_iso',lambda:clock[0])
    notices=[head(i+1,'日换手率达到20%的前5只证券',code) for i,code in enumerate(['002086','000001','000002','000003','000004'])]
    notices.append(head(9,'连续3个交易日内涨幅偏离值累计达到30%的证券'))
    collect(store,DAY,lambda table,day:notices if table=='summary' else [])
    def quote(code,change,is_st=False):
        return dict(code=code,name='样本'+code,change=change,is_st=is_st,industry=[],concepts=[],boards=None,price=10,amount=1e8)
    rows=[quote(str(600100+i),20) for i in range(101)]
    rows.extend([quote('002086',10),quote('000001',-3.2),quote('000002',0),quote('000004',-5,True)])
    revision=store.save('gainers',DAY,'close',{'rows':rows},DAY+'T15:00:00+08:00')
    store.save('gainers',DAY,'midday',{'rows':[quote('000003',88)]},DAY+'T11:30:00+08:00')
    store.save('gainers','2026-11-03','close',{'rows':[quote('000003',99)]},'2026-11-03T15:00:00+08:00')
    with TestClient(create_app(store,collect=False)) as client:
        result=client.get('/api/lhb',params={'date':DAY,'asof':DAY+'T16:30:00+08:00'}).json()
        by={r['code']:r for r in result['rows']}
        assert by['002086']['change']==10 and by['000001']['change']==-3.2
        assert by['000002']['change']==0 and by['000003']['change'] is None
        assert by['000004']['change']==-5
        assert {r['change'] for r in result['rows'] if r['code']=='002086'}=={10}
        assert result['quote_date']==DAY and result['gainers_revision']==revision
        assert result['quote_source_time']==DAY+'T15:00:00+08:00'
        clock[0]=DAY+'T17:00:00+08:00'
        later=store.save('gainers',DAY,'close',{'rows':[quote('002086',11)]},DAY+'T15:00:00+08:00')
        prior=client.get('/api/lhb',params={'date':DAY,'asof':DAY+'T16:30:00+08:00'}).json()
        assert prior['gainers_revision']==revision and prior['rows'][0]['change']==result['rows'][0]['change']
        latest=client.get('/api/lhb',params={'date':DAY}).json()
        assert latest['gainers_revision']==later
        assert all(r['change']==11 for r in latest['rows'] if r['code']=='002086')


def test_three_day_compounding_calendar_missing_and_asof(tmp_path,monkeypatch):
    from backend.lhb_api import three_day_changes
    from backend.gainer_news import gainers_snapshot
    s=Store(tmp_path/'db'); clock=['2026-10-29T18:00:00+08:00']
    monkeypatch.setattr('backend.store.now_iso',lambda:clock[0])
    days=['2026-10-29','2026-10-30','2026-11-02']
    s.observe('calendar',days)
    def rows(price):
        return [dict(code='000001',price=price,pre_close=10),dict(code='000002',price=10,pre_close=10),dict(code='000003',price=10,pre_close=0)]
    for d,p in zip(days,[11,11,9]):
        clock[0]=d+'T16:00:00+08:00'
        s.save('gainers',d,'close',{'rows':rows(p)})
    cutoff=datetime.fromisoformat(DAY+'T16:30:00+08:00')
    market=gainers_snapshot(s,DAY,cutoff)
    values,window,revisions=three_day_changes(s,DAY,cutoff,market,{'000001','000002','000003','000004'})
    assert values['000001']==pytest.approx(8.9)
    assert values['000002']==0 and '000003' not in values and '000004' not in values
    assert window==days and len(revisions)==3
    from datetime import timezone
    assert three_day_changes(s,DAY,cutoff.astimezone(timezone.utc),market,{'000001'})[0]=={'000001':values['000001']}
    clock[0]=DAY+'T17:00:00+08:00'
    s.save('gainers','2026-10-30','close',{'rows':rows(20)})
    assert three_day_changes(s,DAY,cutoff,market,{'000001'})[0]['000001']==pytest.approx(8.9)
    # A missing exchange trading day cannot be replaced by an older available quote.
    clock[0]=DAY+'T16:00:00+08:00'
    s.observe('calendar',['2026-10-30','2026-10-31',DAY])
    assert three_day_changes(s,DAY,cutoff,market,{'000001'})[0]=={}
