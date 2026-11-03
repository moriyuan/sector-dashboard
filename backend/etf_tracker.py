"""Post-close broad ETF monitoring. Primary-market estimates are investor-anonymous."""
from collections import defaultdict
from datetime import date, datetime
import fcntl
import json
import math
import re
import subprocess
from urllib.parse import urlencode

from fastapi import APIRouter
from .store import TZ

KIND = 'broad_etf'
RULE = 'etf-flow-v1'
BASE = 20
HEAVY = 1.5
FUNDS = {
    '510300': ('沪深300', '华泰柏瑞'), '510310': ('沪深300', '易方达'),
    '510330': ('沪深300', '华夏'), '159919': ('沪深300', '嘉实'),
    '510050': ('上证50', '华夏'), '510500': ('中证500', '南方'),
    '512100': ('中证1000', '南方'), '588000': ('科创50', '华夏'),
    '159915': ('创业板指', '易方达'), '159338': ('中证A500', '国泰'),
}
SSE = 'https://www.sse.com.cn/market/funddata/volumn/etfvolumn/'
SZSE = 'https://www.szse.cn/market/fund/volume/etf/index.html'
HOLDING = dict(fund='510300', report_date='2025-12-31', published='2026-03-31',
    report='华泰柏瑞沪深300ETF 2025年年度报告 §9.2',
    url='https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-03-31/510300_20260331_W6CZ.pdf#page=75',
    holders=[dict(name='中央汇金资产管理有限责任公司', shares=37858474974, pct=42.62),
             dict(name='中央汇金投资有限责任公司', shares=35654598859, pct=40.14)])

def number(value):
    if value is None or value in ('','-','--'):return None
    n=float(str(value).replace(',',''))
    if not math.isfinite(n) or n<0:raise ValueError('无效非负数值')
    return n

def get(url, referer, params=None):
    if params:url+='?'+urlencode(params)
    r=subprocess.run(['curl','--fail','--silent','--show-error','--compressed','--max-time','14',
        '--connect-timeout','5','-A','Mozilla/5.0','-e',referer,url],capture_output=True,timeout=17)
    if r.returncode:raise ValueError(f'公开接口请求失败 ({r.returncode})')
    return r.stdout.decode('utf-8-sig')

def sse_shares(day):
    sql='COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L'
    params={'sqlId':sql,'STAT_DATE':day,'isPagination':'true','pageHelp.pageSize':2000,'pageHelp.pageNo':1}
    obj=json.loads(get('https://query.sse.com.cn/commonQuery.do',SSE,params))
    rows=obj['result']; page=obj['pageHelp']
    if obj['sqlId']!=sql or not rows or int(page['total'])!=len(rows):
        raise ValueError('沪市份额未发布或分页不全')
    result={}
    for row in rows:
        if row['STAT_DATE']!=day:raise ValueError('沪市份额日期不符')
        code=row['SEC_CODE']
        if code in result:raise ValueError('重复基金份额')
        size=number(row['TOT_VOL'])
        result[code]=None if size is None else size*10000
    return result

def sz_shares(code, start, end):
    params={'SHOWTYPE':'JSON','CATALOGID':'scsj_fund_jjgm','TABKEY':'tab1','txtDm':code,
            'jjlb':'ETF','txtStart':start,'txtEnd':end}
    rows={}
    for page in range(1,25):
        data=json.loads(get('https://www.szse.cn/api/report/ShowReport/data',SZSE,{**params,'PAGENO':page}))[0]
        meta=data['metadata']
        if meta['catalogid']!=params['CATALOGID'] or int(meta['pageno'])!=page:raise ValueError('深市份额页码不符')
        for row in data['data']:
            day=row['size_date']
            if row['fund_code']!=code or not start<=day<=end or day in rows:raise ValueError('深市份额日期或代码不符')
            size=number(row['current_size'])
            rows[day]=None if size is None else size*10000
        if page>=int(meta['pagecount']):
            if len(rows)!=int(meta['recordcount']):raise ValueError('深市份额分页不全')
            return rows
    raise ValueError('深市份额超出分页范围')

def nav_history(code, start, end):
    text=get(f'https://fund.eastmoney.com/pingzhongdata/{code}.js','https://fund.eastmoney.com/')
    if not re.search(r'\bfS_code\s*=\s*"'+code+'"',text):raise ValueError('净值基金代码不符')
    m=re.search(r'\bData_netWorthTrend\s*=\s*(\[.*?\])\s*;',text,re.S)
    if not m:raise ValueError('未取得净值序列')
    nav={}
    for row in json.loads(m[1]):
        day=datetime.fromtimestamp(row['x']/1000,TZ).date().isoformat()
        if start<=day<=end:
            if day in nav:raise ValueError('重复净值日')
            nav[day]={'nav':number(row['y']), 'event':str(row.get('unitMoney') or '')}
    return nav

def quotes(code, start, end):
    ticker=('sz' if code.startswith('1') else 'sh')+code
    obj=json.loads(get('https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get',
                       'https://gu.qq.com/',{'param':f'{ticker},day,{start},{end},250,'}))
    bars={}
    for row in obj['data'][ticker]['day']:
        day=row[0]
        if not start<=day<=end:continue
        if day in bars or len(row)<9:raise ValueError('成交序列不完整')
        hands=number(row[5]); wan=number(row[8])
        if wan is None or hands is None:continue
        amt=wan*10000
        if hands:
            vwap=amt/(hands*100)
            if not float(row[4])-.005<=vwap<=float(row[3])+.005:raise ValueError('成交量额单位校验失败')
        elif amt:raise ValueError('无量成交')
        bars[day]=dict(amount=amt,volume=hands*100)
    return bars

def metrics(today, previous, baseline):
    shares,prev=today.get('shares'),previous.get('shares')
    delta=shares-prev if shares is not None and prev is not None else None
    nav=today.get('nav'); prev_nav=previous.get('nav')
    suspect=bool(re.search(r'拆分|拆细|合并|折算',today.get('event') or ''))
    if nav and prev_nav and abs(nav/prev_nav-1)>.30:suspect=True
    mean=None
    if len(baseline)==BASE and all(v is not None for v in baseline):
        mean=sum(baseline)/BASE
    value=today.get('amount')
    multiple=value/mean if value is not None and mean and mean>0 else None
    return dict(share_delta=delta,net=delta*nav if delta is not None and nav and not suspect else None,
                adjustment=suspect,mean20=mean,multiple=multiple,heavy=None if multiple is None else multiple>=HEAVY)

def summary(rows):
    net=[r['net'] for r in rows if r['net'] is not None]
    valid_vol=[r['heavy'] for r in rows if r['heavy'] is not None]
    return dict(net=sum(net) if net else None,net_count=len(net),count=len(rows),
        inflow=sum(x for x in net if x>0) if net else None,outflow=sum(-x for x in net if x<0) if net else None,
        heavy_count=sum(valid_vol) if valid_vol else None,volume_count=len(valid_vol))

def collect(store, end, count=1, now=None):
    now=now or datetime.now(TZ)
    cal=sorted(set(store.observed('calendar') or []))
    if end not in cal or end>now.date().isoformat() or (end==now.date().isoformat() and now.strftime('%H:%M')<'15:30'):
        raise ValueError('只采集交易日已收盘数据')
    if not 1<=count<=60:raise ValueError('一次限 1–60 个交易日')
    ix=cal.index(end)
    days=cal[max(0,ix-count+1):ix+1]
    start=cal[max(0,cal.index(days[0])-BASE)]
    share_days=cal[max(0,cal.index(days[0])-1):ix+1]
    with open(str(store.path)+'.etf.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _collect(store,cal,days,start,end,share_days,now)

def _collect(store, cal, days, start, end, share_days, now):
    shares=defaultdict(dict); prices={}; navs={}; errors={}
    def fetch(label,fn,*args):
        try:return fn(*args)
        except (KeyError,IndexError,TypeError,ValueError,OSError,subprocess.SubprocessError) as exc:
            errors[label]=str(exc)[:130];return {}
    for day in share_days:
        for code,v in fetch('沪市份额 '+day,sse_shares,day).items():
            if code in FUNDS:shares[code][day]=v
    for code in FUNDS:
        if code.startswith('1'):
            shares[code]=fetch('深市份额 '+code,sz_shares,code,share_days[0],end)
        prices[code]=fetch('成交 '+code,quotes,code,start,end)
        navs[code]=fetch('净值 '+code,nav_history,code,start,end)
    results=[]
    for day in days:
        ix=cal.index(day)
        if ix==0:continue
        prev=cal[ix-1]; base_days=cal[max(ix-BASE,0):ix]
        old=store.snapshot(KIND,day,'close')
        old_rows={r['code']:r for r in old['payload']['rows']} if old else {}
        next_day=cal[ix+1] if ix+1<len(cal) else None
        provisional=not next_day or now.strftime('%Y-%m-%dT%H:%M')<next_day+'T09:00'
        rows=[]
        for code,(index,manager) in FUNDS.items():
            old_row=old_rows.get(code,{})
            retained=[]
            def field(key,value):
                if value is None and old_row.get(key) is not None:
                    retained.append(key);return old_row[key]
                return value
            current_shares=shares[code].get(day)
            current=dict(shares=field('shares',current_shares),**{k:field(k,navs[code].get(day,{}).get(k)) for k in ('nav','event')},
                         **{k:field(k,prices[code].get(day,{}).get(k)) for k in ('amount','volume')})
            prev_shares=field('prev_shares',shares[code].get(prev))
            prev_nav=field('prev_nav',navs[code].get(prev,{}).get('nav'))
            old_base=old_row.get('baseline',{})
            baseline={d:prices[code].get(d,{}).get('amount') for d in base_days}
            for d,val in baseline.items():
                if val is None and old_base.get(d) is not None:
                    baseline[d]=old_base[d]
                    if 'baseline' not in retained:retained.append('baseline')
            mt=metrics(current,{'shares':prev_shares,'nav':prev_nav},list(baseline.values()))
            reference=(old_row.get('provisional',False) if current_shares is None else provisional) if code.startswith('1') else False
            rows.append(dict(code=code,index=index,manager=manager,**current,prev_shares=prev_shares,prev_nav=prev_nav,
                    baseline=baseline,provisional=reference,retained=retained,**mt))
        result=summary(rows)
        issues={k:v for k,v in errors.items() if not k.startswith('沪市份额') or k.endswith((day,prev))}
        payload=dict(rows=rows,summary=result,previous=prev,issues=issues,rule=RULE)
        if old and old['payload']==payload:revision=old['id']
        else:revision=store.save(KIND,day,'close',payload)
        results.append(dict(day=day,revision=revision,**result))
    return results

def due_session(now,calendar):
    today=now.strftime('%Y-%m-%d');clock=now.strftime('%H:%M')
    if not calendar or today>calendar[-1] or today not in calendar:return None
    if clock>='19:30':return today,'晚间复核'
    if clock>='16:30':return today,'盘后'
    if clock>='09:00':
        i=calendar.index(today)
        if i:return calendar[i-1],'次晨确认'
    return None

def run_scheduler(store, stop):
    while not stop.is_set():
        now=datetime.now(TZ)
        try:
            target=due_session(now,sorted(store.observed('calendar') or []))
            if target:
                day,batch=target
                key='etf-check:'+day+':'+batch
                state=store.observed(key) or {}
                if not state.get('ok') and now.timestamp()>=state.get('retry_at',0):
                    result=collect(store,day)[-1]
                    ok=result['net_count']==result['count']==result['volume_count']
                    snap=store.snapshot(KIND,day,'close',result['revision'])
                    ok=ok and not snap['payload']['issues'] and not any(r['retained'] for r in snap['payload']['rows'])
                    store.observe(key,dict(ok=ok,retry_at=now.timestamp()+1800))
                    store.event('宽基ETF',ok,f"{day} {batch} · 净申赎覆盖 {result['net_count']}/{result['count']}")
        except BlockingIOError:pass
        except Exception as exc:
            store.event('宽基ETF',False,'采集异常 · '+type(exc).__name__);stop.wait(300)
        stop.wait(60)

def day_view(store,day):
    snap=store.snapshot(KIND,day,'close')
    disclosures=[HOLDING] if HOLDING['published']<=day else []
    result=dict(day=day,revision=None,collected_at=None,status='unarchived',rows=[],summary=None,
        trend=[],issues={},rule=RULE,disclosures=disclosures)
    if not snap:return result
    payload=snap['payload'];stats=payload['summary']
    complete=stats['count']==stats['net_count']==stats['volume_count']
    status='partial'
    if complete and not payload['issues'] and not any(r['retained'] for r in payload['rows']):
        status='provisional' if any(r['provisional'] for r in payload['rows']) else 'complete'
    result.update(revision=snap['id'],collected_at=snap['collected_at'],summary=stats,previous=payload['previous'],
        rows=[{k:v for k,v in row.items() if k not in ('baseline','event','prev_nav')} for row in payload['rows']],
        issues=payload['issues'],status=status)
    with store.connect() as db:
        history=db.execute("""SELECT trade_date,payload FROM snapshots WHERE id IN (
            SELECT MAX(id) FROM snapshots WHERE kind=? AND mode='close' AND trade_date<=?
            GROUP BY trade_date ORDER BY trade_date DESC LIMIT 20) ORDER BY trade_date""",(KIND,day)).fetchall()
    result['trend']=[{'day':r['trade_date'],**json.loads(r['payload'])['summary']} for r in history]
    return result

def router(store):
    api=APIRouter()
    @api.get('/api/etf-tracker')
    def read(date:date):
        return day_view(store,date.isoformat())
    return api
