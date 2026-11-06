import json
from datetime import datetime
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from backend.store import Store, TZ
from backend.daily_sources import parse_notices, report_period, financial_metrics, DailyProvider
from backend.daily import DailyStore, collect_changes, collect_announcements, enrich_one, validate_analysis, analyze_one, evidence, SECTIONS, VERSION
from backend.news import NewsStore

@pytest.fixture
def ds(tmp_path):return DailyStore(Store(tmp_path/'market.sqlite3'))

def notice(day='2026-11-05'):
 return dict(id='AN202611051234:600519',day=day,code='600519',name='贵州茅台',period='2026-09-30',kind='report',title='2026年第三季度报告',notice_date=day,published_at=day+'T18:00:00+08:00',url='https://data.eastmoney.com/notices/detail/600519/AN202611051234.html')

def payload(n):
 return dict(notice=n,period=n['period'],basis='结构化财务数据',backfilled=False,url=n['url'],metrics=financial_metrics(dict(TOTALOPERATEREVE=1e9,PARENTNETPROFIT=2e8,NETCASH_OPERATE_PK=3e8,CURRENCY='CNY')),industry=None)

def test_disclosure_publication_not_notice_date_and_filter_dividends():
 rows=[dict(art_code='AN123',title='2026年第三季度报告',display_time='2026-11-05 19:00:01:200',notice_date='2026-11-06 00:00:00',codes=[dict(stock_code='600519',short_name='测试',ann_type='A,SHA')]),dict(art_code='AN124',title='2026年前三季度权益分派公告',notice_date='2026-11-05',codes=[])]
 result=parse_notices(rows,'2026-11-05')
 assert len(result)==1 and result[0]['notice_date']=='2026-11-06' and result[0]['period']=='2026-09-30'
 assert parse_notices(rows,'2026-11-06')==[]
 assert report_period('2026年年度报告')=='2026-12-31'
 assert report_period('关于上市公告') is None

def test_money_percent_currency_and_zero_denominator():
 rows={r['id']:r for r in financial_metrics(dict(TOTALOPERATEREVE=1e9,PARENTNETPROFIT=0,NETCASH_OPERATE_PK=1,XSMLL=25,CURRENCY='USD'))}
 assert rows['TOTALOPERATEREVE']['value']==1e9 and rows['TOTALOPERATEREVE']['currency']=='USD'
 assert rows['XSMLL']['value']==25 and rows['cash_profit']['value'] is None
 assert rows['KCFJCXSYJLR']['value'] is None

def test_dedup_source_separation_and_historical_isolation(ds):
 event=dict(c='600519',n='测试',tm=93001,t=8201,i='1,2,3')
 ds.add_changes('2026-11-05','eastmoney',[event,event]);ds.add_changes('2026-11-05','eastmoney',[event])
 ds.add_changes('2026-11-05','ths',[dict(code='600519',name='测试',date='2026-11-05',time='09:30:01',event='急速拉升',detail='')],{'sha256':'abc'})
 rows=ds.listing('2026-11-05')['anomalies'];assert len(rows)==2 and all(r['count']==1 for r in rows)
 assert ds.listing('2026-11-04')['anomalies']==[]
 with pytest.raises(ValueError):ds.add_changes('2026-11-05','ths',[],None)
 with pytest.raises(ValueError):ds.add_changes('2026-11-04','ths',[dict(date='2026-11-05')],{'file':'x'})

def test_no_weekend_or_historical_changes_fetch(ds):
 calls=[];p=SimpleNamespace(changes=lambda:calls.append(1))
 ds.store.observe('calendar',['2026-11-05'])
 assert not collect_changes(ds,'2026-11-05',p,datetime(2026,11,6,tzinfo=TZ))
 assert not collect_changes(ds,'2026-11-07',p,datetime(2026,11,7,tzinfo=TZ))
 assert calls==[]

def test_failed_refresh_retains_archive(ds):
 n=notice();ds.add_notices([n]);ds.state(n['day'],'announcements',dict(ok=True,last_success='2026-11-05T20:00:00+08:00'))
 def fail(_):raise TimeoutError()
 collect_announcements(ds,n['day'],SimpleNamespace(announcements=fail))
 assert len(ds.listing(n['day'])['notices'])==1
 assert not ds.states(n['day'])['announcements']['ok']
 assert ds.states(n['day'])['announcements']['last_success']

def test_metrics_survive_industry_outage(ds):
 n=notice();ds.add_notices([n])
 def fail(_):raise TimeoutError()
 enrich_one(ds,SimpleNamespace(financials=lambda _:payload(n),peers=fail),datetime(2026,11,5,20,tzinfo=TZ))
 assert ds.detail(n['id'])['financials']['metrics']
 assert ds.job('finance:'+n['id'])['status']=='awaiting_industry'

def analysis():
 return dict(sections=[dict(key=k,kind='limitation',text='当前提供的财务指标只支持有限判断，尚缺完整业务分部与管理层解释，需要核对公告原文后再评价增长的可持续性。',evidence_ids=['notice']) for k in SECTIONS])

def test_model_evidence_contract():
 inputs=evidence(payload(notice()));valid=analysis();assert validate_analysis(valid,inputs)==valid
 invalid=analysis();invalid['sections'][0]['evidence_ids']=['fake']
 with pytest.raises(ValueError):validate_analysis(invalid,inputs)
 invalid=analysis();invalid['sections'][0]['text']+='利润增长100%'
 with pytest.raises(ValueError):validate_analysis(invalid,inputs)
 invalid=analysis();invalid['sections'][3]['kind']='fact'
 with pytest.raises(ValueError):validate_analysis(invalid,inputs)

def test_timeout_budget_and_restart_resume(ds):
 n=notice();ds.add_notices([n]);fid=ds.add_finance(n,payload(n));news=NewsStore(ds.store);now=datetime(2026,11,5,20,tzinfo=TZ)
 def fail(*a,**kw):raise TimeoutError()
 assert analyze_one(ds,news,'dummy',now,post=fail)
 assert news.budget()['spent_or_reserved']>0
 assert ds.job('analysis:'+VERSION+':'+str(fid))['status']=='analysis_failed'
 reopened=DailyStore(ds.store)
 assert not analyze_one(reopened,news,'dummy',now,post=lambda *a,**k:pytest.fail('early retry'))

def test_budget_exhaustion_missing_key_and_valid_result(ds):
 n=notice();ds.add_notices([n]);fid=ds.add_finance(n,payload(n));news=NewsStore(ds.store)
 now=datetime(2026,11,5,20,tzinfo=TZ)
 assert not analyze_one(ds,news,'',now)
 response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:dict(model='deepseek-flash',usage=dict(prompt_tokens=100,completion_tokens=200),choices=[dict(finish_reason='stop',message=dict(content=json.dumps(analysis())))]))
 assert analyze_one(ds,news,'dummy',now,post=lambda *a,**k:response)
 assert ds.detail(n['id'])['status']=='analyzed'
 assert ds.detail(n['id'])['analysis']['input_hash']
 assert not analyze_one(ds,news,'dummy',now)
 # A different immutable revision needs its own analysis and cannot bypass the shared cap.
 revised=payload(n);revised['basis']='修订';fid2=ds.add_finance(n,revised)
 assert news.reserve('fill',.999)
 assert not analyze_one(ds,news,'dummy',now,post=lambda *a,**k:pytest.fail('over budget'))
 assert ds.job('analysis:'+VERSION+':'+str(fid2))['status']=='budget_paused'

def test_reject_later_restatement(ds):
 p=DailyProvider(ds.store);p.table=lambda *a:[dict(SECURITY_CODE='600519',REPORT_DATE='2026-09-30',UPDATE_DATE='2026-11-06')]
 assert p.financials(notice()) is None

def test_readonly_api_and_date_validation(ds):
 from fastapi import FastAPI
 from backend.daily import router
 app=FastAPI();app.include_router(router(ds.store));client=TestClient(app)
 assert client.get('/api/daily?day=2026-11-05').json()['notices']==[]
 assert client.get('/api/daily?day=wrong').status_code==422
 assert client.get('/api/daily/reports/wrong').status_code==422
 assert client.post('/api/daily').status_code==405
 assert NewsStore(ds.store).budget()['requests']==0


def test_industry_median_is_explicit_and_mean_claim_rejected():
 p=payload(notice());p['industry']=dict(name='半导体',count=5,period=p['period'],url='https://data.eastmoney.com',metrics=[dict(id='peer_YSTZ',label='营收同比',value=10,unit='%',n=5)])
 items=evidence(p);peer=next(e for e in items if e['id']=='peer_YSTZ')
 assert peer['aggregation']=='median' and '中位数' in peer['label']
 invalid=analysis();invalid['sections'][3]['text']+='同行平均值较高。'
 with pytest.raises(ValueError):validate_analysis(invalid,items)


def test_known_report_dates_allowed_but_invented_dates_rejected():
 inputs=evidence(payload(notice()));a=analysis();a['sections'][0]['text']+='报告期2026年9月30日。'
 assert validate_analysis(a,inputs)
 a['sections'][0]['text']+='其他日期2027年9月30日。'
 with pytest.raises(ValueError):validate_analysis(a,inputs)
