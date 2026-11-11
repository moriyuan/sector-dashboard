from datetime import datetime
import pytest
from backend.store import Store
from backend.regulation import RegulationStore,extract_records,period_bounds,risk_kind,parse_index,fetch


def doc(day='2026-10-26'):
 return dict(source='hx',day=day,title='关于凡拓数创的风险提示',url='https://www.hx168.com.cn/main/a/20261026/99131.shtml',sha256='abc',captured_at='2026-11-05T19:00:00+08:00')
TEXT='“凡拓数创”（301313）被纳入重点监控证券名单，重点监控起始日期为2026-10-26，重点监控结束日期为2026-11-06。'


def test_periods_are_explicit_not_ten_day_guess():
 rows=extract_records(doc(),TEXT,{})
 assert len(rows)==1 and rows[0]['code']=='301313' and rows[0]['tier']=='broker'
 assert (rows[0]['start'],rows[0]['end'])==('2026-10-26','2026-11-06')
 assert period_bounds('交易所对该股票重点监控。')==(None,None)
 assert risk_kind('该股票可能被重点监控') is None
 assert risk_kind('账户列为重点监控账户') is None
 assert risk_kind('股票交易严重异常波动')==('severe','notice')
 assert risk_kind('可能申请停牌核查') is None


def test_industry_environment_monitor_not_stock_risk():
 assert not extract_records(doc(),'公司属于化工重点监控企业（301313）。',{})
 assert not extract_records(doc(),'对账户采取监管措施，华西证券（002926）提醒投资者。',{})


def test_ambiguous_names_not_bound_and_codes_not_funds():
 text='对“测试”股票重点监控。'
 assert extract_records(doc(),text,{'000001':'测试','000002':'测试'})==[]
 assert len(extract_records(doc(),text,{'000001':'测试'}))==1
 assert not extract_records(doc(),'“纳斯达克”（513300）被纳入重点监控证券。',{})


def test_historical_date_expiry_and_release(tmp_path):
 risk=RegulationStore(Store(tmp_path/'risk.sqlite3'));risk.save(doc(),extract_records(doc(),TEXT,{}))
 assert risk.listing('2026-10-25')['rows']==[]
 row=risk.listing('2026-11-05')['rows'][0]
 assert row['status']=='披露监控期限内'
 assert risk.listing('2026-10-30')['rows'][0]['backfilled']
 assert '待核验' in risk.listing('2026-11-07')['rows'][0]['status']
 released=doc('2026-11-08');released['sha256']='def';released['url']+='?release'
 risk.save(released,extract_records(released,'“凡拓数创”（301313）已解除重点监控。',{}))
 assert '待核验' in risk.listing('2026-11-07')['rows'][0]['status']
 assert '已解除' in risk.listing('2026-11-08')['rows'][0]['status']


def test_source_index_and_network_allowlist():
 rows=parse_index('szse',"var curHref='./P020261030.pdf'; var curTitle='深市监管动态（2026年10月26日-2026年10月30日）';")
 assert rows[0]['day']=='2026-10-30' and rows[0]['url'].startswith('https://www.szse.cn/')
 with pytest.raises(ValueError):parse_index('szse','验证码')
 for url in ('http://127.0.0.1/','https://www.szse.cn.evil.test/','https://u:p@www.szse.cn/'):
  with pytest.raises(ValueError):fetch(url)


def test_broker_shared_action_binds_all_named_stocks_and_deduplicates():
 text='“新华传媒”（600825）发布交易风险提示。\n“天普股份”（605255）发布交易风险提示。\n上交所将对上述股票上的异常交易行为进行进一步从严认定，并视情况从重采取列为重点监控账户等措施。'
 rows=extract_records(doc(),text,{})
 assert {r['code'] for r in rows}=={'600825','605255'}
 assert all(r['kind']=='stricter' and r['start'] is None and r['end'] is None for r in rows)
 assert all(r['code'] in r['excerpt'] for r in rows)
 rows=extract_records(doc(),TEXT+'\n深交所对上述证券重点监控。',{})
 assert len(rows)==1 and rows[0]['end']=='2026-11-06'
