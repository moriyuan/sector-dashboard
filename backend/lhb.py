"""Close-only, date-bound A-share public trading disclosures (龙虎榜)."""
import fcntl
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

from .schedule import phase
from .store import TZ, now_iso

REPORTS = {'summary':'RPT_DAILYBILLBOARD_DETAILSNEW', 'buy':'RPT_BILLBOARD_DAILYDETAILSBUY',
           'sell':'RPT_BILLBOARD_DAILYDETAILSSELL'}
SOURCE = '东方财富 · 每日龙虎榜'


def period(reason):
    if re.search(r'(?<![零一二三四五六七八九十百\d])(?:3|三)个?交易日',reason):
        return 'three'
    if re.search(r'累计|期间|连续',reason):
        return 'other'
    if re.search(r'当日|首日|单日|日(?:收盘|涨幅|跌幅|振幅|价格|换手)',reason):
        return 'single'
    return 'other'


def read_page(table, day, page):
    cols = 'TRADE_ID' if table=='summary' else f'TRADE_ID,{table.upper()},OPERATEDEPT_CODE,OPERATEDEPT_NAME'
    params = dict(reportName=REPORTS[table],columns='ALL',pageSize=300,pageNumber=page,
                  filter=f"(TRADE_DATE='{date.fromisoformat(day)}')",sortColumns=cols,
                  sortTypes='1' if table=='summary' else '1,-1,1,1',source='WEB',client='WEB')
    with requests.get('https://datacenter-web.eastmoney.com/api/data/v1/get',params=params,
            headers={'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/stock/tradedetail.html'},
            timeout=(5,15),allow_redirects=False) as response:
        if response.status_code!=200:
            raise ValueError(f'东财 HTTP {response.status_code}')
        return response.json()


def report_rows(table, day, fetch_page=read_page):
    rows,seen = [],{}
    page,pages,total = 1,1,None
    while page<=pages:
        obj = fetch_page(table,day,page)
        if page==1 and obj.get('code')==9201 and obj.get('message')=='返回数据为空' and not obj.get('result'):
            return []
        result = obj.get('result')
        if obj.get('success') is not True or obj.get('code')!=0 or not isinstance(result,dict):
            raise ValueError('东财报表响应异常')
        if page==1:
            pages,total = result['pages'],result['count']
        if not 1<=pages<=50 or (result['pages'],result['count'])!=(pages,total) or not result.get('data'):
            raise ValueError('东财分页数量异常')
        for row in result['data']:
            if str(row.get('TRADE_DATE',''))[:10]!=day or not row.get('TRADE_ID'):
                raise ValueError('披露日期或编号异常')
            key = (str(row['TRADE_ID']),)
            if table!='summary':
                key += tuple(row.get(k) for k in ('OPERATEDEPT_CODE','OPERATEDEPT_NAME','BUY','SELL'))
            # Anonymous institutions can occupy distinct ranked slots with identical
            # amounts. Preserve those slots within a page. Cross-page overlap remains
            # ambiguous and must not silently pass the pagination integrity check.
            anonymous = (table!='summary' and row.get('OPERATEDEPT_NAME')=='机构专用'
                         and str(row.get('OPERATEDEPT_CODE')) in ('0','None',''))
            if key in seen and not (anonymous and seen[key]==page):
                raise ValueError('东财分页重复')
            seen[key]=page
            rows.append(row)
        page+=1
    if len(rows)!=total:
        raise ValueError('东财分页不完整')
    return rows


def amount(value):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('金额异常')
    return value


def notices(rows):
    by_id={}
    for row in rows:
        code,market=row['SECURITY_CODE'],row['MARKET']
        if market not in ('SH','SZ') or row['SECURITY_TYPE_CODE']!='058001001':
            continue
        if not re.fullmatch(r'[036]\d{5}',code) or row['SECUCODE']!=code+'.'+market:
            raise ValueError('证券标识不匹配')
        key=str(row['TRADE_ID'])
        reason=row['EXPLANATION']
        if not reason or key in by_id:
            raise ValueError('披露原因或编号异常')
        by_id[key]=dict(id=key,code=code,name=row['SECURITY_NAME_ABBR'],market=market,
                        period=period(reason),reason=reason,buy_total=amount(row.get('BILLBOARD_BUY_AMT')),
                        sell_total=amount(row.get('BILLBOARD_SELL_AMT')),net=amount(row.get('BILLBOARD_NET_AMT')))
    return by_id


def seats(rows, heads, side, excluded):
    result=defaultdict(list)
    orphans=set()
    for row in rows:
        key=str(row['TRADE_ID'])
        if key not in heads:
            security=row.get('SECUCODE','')
            if key not in excluded and re.fullmatch(r'(?:00[0-3]\d{3}|30[01]\d{3})[.]SZ|(?:60[0135]\d{3}|68[89]\d{3})[.]SH',security):
                orphans.add(key)
            continue
        head=heads[key]
        if (head['code']!=row['SECURITY_CODE'] or row['EXPLANATION']!=head['reason']
                or row['SECUCODE']!=head['code']+'.'+head['market'] or not row.get('OPERATEDEPT_NAME')):
            raise ValueError('席位与披露编号、证券或原因不匹配')
        result[key].append(dict(name=row['OPERATEDEPT_NAME'],seat_code=str(row.get('OPERATEDEPT_CODE') or ''),buy=amount(row.get('BUY')),
                                sell=amount(row.get('SELL')),net=amount(row.get('NET'))))
    for entries in result.values():
        entries.sort(key=lambda x:(x[side] is None,-(x[side] or 0),x['name']))
    return result,orphans


def collect(store, day, fetch=report_rows):
    date.fromisoformat(day)
    with open(str(store.path)+'.lhb.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return collect_locked(store,day,fetch)


def collect_locked(store, day, fetch):
    old=store.snapshot('lhb',day,'close')
    revision=old['id'] if old else None
    previous={r['id']:r for r in old['payload']['rows']} if old else {}
    issues=[]
    try:
        summary=fetch('summary',day)
        heads=notices(summary)
        excluded={str(row['TRADE_ID']) for row in summary}-heads.keys()
        if not heads:
            status='stale' if old else 'pending'
            if old:issues.append('本次来源为空，显示已保存的修订')
        else:
            partial=False
            for side in ('buy','sell'):
                try:
                    groups,orphans=seats(fetch(side,day),heads,side,excluded)
                    if orphans:
                        partial=True
                        issues.append('席位报表出现尚未匹配的沪深 A 股披露编号：'+', '.join(sorted(orphans)))
                except (ValueError,requests.RequestException,KeyError,TypeError) as exc:
                    groups={}
                    issues.append(('买入' if side=='buy' else '卖出')+'席位采集异常 · '+type(exc).__name__)
                for key,head in heads.items():
                    entries=groups.get(key,[])
                    prior=previous.get(key,{})
                    state='reported' if entries else 'missing'
                    if not entries:
                        partial=True
                        if (prior.get('code'),prior.get('reason'))==(head['code'],head['reason']) and prior.get(side):
                            entries,state=prior[side],'previous'
                    head[side],head[side+'_state']=entries,state
            payload={'rows':sorted(heads.values(),key=lambda h:(h['code'],h['id'])),'source':SOURCE}
            if not old or old['payload']!=payload:
                revision=store.save('lhb',day,'close',payload)
            status='partial' if partial else 'published'
    except (ValueError,requests.RequestException,KeyError,TypeError) as exc:
        issues.append('榜单读取或核验失败 · '+type(exc).__name__)
        status='stale' if old else 'error'
    check=dict(status=status,revision=revision,checked_at=now_iso(),issues=issues)
    store.observe('lhb-check:'+day,check)
    return check


def scheduled_day(now, calendar):
    if phase(now,calendar).startswith('archive'):
        return now.astimezone(TZ).date().isoformat()
    return None


def run_scheduler(store, stop):
    while not stop.is_set():
        try:
            now=datetime.now(TZ)
            day=scheduled_day(now,store.observed('calendar') or [])
            if day:
                check=store.observed('lhb-check:'+day) or {}
                interval=30 if check.get('status')=='published' else 5
                if not check or now>=datetime.fromisoformat(check['checked_at'])+timedelta(minutes=interval):
                    collect(store,day)
        except Exception as exc:
            store.event('lhb',False,'调度异常 · '+type(exc).__name__)
        stop.wait(30)


def view(store, day, asof=None):
    boundary=asof.astimezone(TZ).isoformat(timespec='seconds') if asof else None
    check=store.observed('lhb-check:'+day,before=boundary) or {}
    with store.connect() as db:
        sql="SELECT id FROM snapshots WHERE kind='lhb' AND trade_date=? AND mode='close'"
        args=[day]
        if boundary:
            sql+=' AND collected_at<=?'
            args.append(boundary)
        version=db.execute(sql+' ORDER BY id DESC LIMIT 1',args).fetchone()
    snap=store.snapshot('lhb',day,'close',version['id']) if version else None
    default='unarchived' if day<datetime.now(TZ).date().isoformat() else 'pending'
    status=check.get('status',default)
    if snap and check.get('revision')!=snap['id']:
        status='partial' if any(r[s+'_state']!='reported' for r in snap['payload']['rows'] for s in ('buy','sell')) else 'published'
    return dict(trade_date=day,status=status,revision=snap['id'] if snap else None,
                collected_at=snap['collected_at'] if snap else None,checked_at=check.get('checked_at'),
                issues=check.get('issues',[]),source=SOURCE,rows=snap['payload']['rows'] if snap else [])
