"""Download dated public inputs into resumable partitions (run off the web request path)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import os
import socket
import threading
import time

import baostock as bs
import pandas as pd
import requests

EM = "https://datacenter-web.eastmoney.com/api/data/v1/get"
BENCH = "sh.000300"
FIELDS = ["date","code","open","high","low","close","preclose","volume","amount","turn","tradestatus","pctChg","isST"]
PIT_COLS = ["SECURITY_CODE","TRADE_DATE","CLOSE_PRICE","TOTAL_MARKET_CAP","NOTLIMITED_MARKETCAP_A","PB_MRQ"]


def iter_rows(query):
    while query.next():
        yield query.get_row_data()


def frame(query):
    if query.error_code != '0':
        raise ValueError(query.error_msg)
    data = pd.DataFrame.from_records(iter_rows(query), columns=query.fields)
    if data.empty or query.error_code != '0':
        raise ValueError("incomplete BaoStock response")
    return data


def save(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    df.to_parquet(tmp, index=False)
    os.replace(tmp,path)


def bao_retry(query):
    for attempt in range(4):
        try:
            return frame(query())
        except (ValueError, OSError):
            if attempt==3: raise
            time.sleep(2**attempt)
            bs.logout()
            if bs.login().error_code!='0': raise RuntimeError("BaoStock login failed")


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--bars-after',required=True,help='last stock day verified in bulk snapshot')
    p.add_argument('--mode',choices=['calendar','values','bao','bars','industry'],required=True)
    a=p.parse_args()
    root=a.root
    if a.mode=='calendar':
        socket.setdefaulttimeout(30)
        if bs.login().error_code!='0':raise RuntimeError('BaoStock login failed')
        try:
            dates=bao_retry(lambda:bs.query_trade_dates(start_date=a.start,end_date=a.end))
            days=dates.loc[dates.is_trading_day=='1','calendar_date'].tolist()
            idx=bao_retry(lambda:bs.query_history_k_data_plus(BENCH,'date,code,open,high,low,close,preclose,pctChg',start_date=a.start,end_date=a.end,frequency='d',adjustflag='3'))
            if list(idx.date)!=days:raise ValueError('benchmark and trading calendar disagree')
            save(idx,root/'benchmark.parquet')
            print('benchmark',len(days),days[0],days[-1],flush=True)
        finally:bs.logout()
        return
    days=[d for d in pd.read_parquet(root/'benchmark.parquet').date if d<=a.end]
    if a.mode=='values':
        lock=threading.Lock();next_at=[0.]
        def fetch(day):
            path=root/'values'/f'{day}.parquet'
            if path.exists():return
            for attempt in range(5):
                try:
                    params=dict(reportName='RPT_VALUEANALYSIS_DET',columns=','.join(PIT_COLS),
                        filter=f"(TRADE_DATE='{day}')",pageSize=6000,sortColumns='SECURITY_CODE',sortTypes=1,source='WEB',client='WEB')
                    data=[];count=None
                    for page in range(1,11):
                        with lock:
                            time.sleep(max(0,next_at[0]-time.monotonic()));next_at[0]=time.monotonic()+.35
                        resp=requests.get(EM,params=dict(params,pageNumber=page),timeout=(8,35))
                        resp.raise_for_status();obj=resp.json()
                        if not obj.get('success'):raise ValueError('valuation source unavailable')
                        result=obj['result']
                        if count is not None and count!=result['count']:raise ValueError('valuation pagination changed')
                        count=result['count'];data.extend(result['data'])
                        if page==result['pages']:break
                    df=pd.DataFrame(data)
                    if len(df)!=count or len(df)<4000 or df.SECURITY_CODE.duplicated().any() or not df.TRADE_DATE.str.startswith(day).all():
                        raise ValueError('valuation date/coverage conflict')
                    save(df,path);return
                except (OSError,ValueError,KeyError,requests.RequestException):
                    if attempt==4: raise
                    time.sleep(2**attempt)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for n,_ in enumerate(pool.map(fetch,[d for d in days if d>=a.start]),1):
                if n%50==0:print('valuation partitions',n,flush=True)
        return
    socket.setdefaulttimeout(35)
    if bs.login().error_code!='0':raise RuntimeError('BaoStock login failed')
    try:
        # Weekly supplier updates: query the last trading day of each week, use returned updateDate.
        weeks={}
        for day in days:
            dt=date.fromisoformat(day)
            weeks[dt-timedelta(days=dt.weekday())]=day
        for day in weeks.values():
            if day<a.start or a.mode=='bars': continue
            path=root/'industry'/f'{day}.parquet'
            if not path.exists():
                df=bao_retry(lambda:bs.query_stock_industry(date=day))
                if len(df)<3500 or df.code.duplicated().any() or df.updateDate.max()>day or df.updateDate.nunique()!=1:
                    raise ValueError(f'invalid industry snapshot {day}')
                save(df,path)
        print('industry snapshots ready',flush=True)
        for day in days:
            if day<=a.bars_after or a.mode=='industry':continue
            path=root/'bars'/f'{day}.parquet'
            if path.exists():continue
            df=bao_retry(lambda:bs.query_daily_history_k_AStock(day))
            if len(df)<4000 or df.code.duplicated().any() or set(df.date)!={day} or set(df.adjustflag)!={'3'}:
                raise ValueError(f'invalid stock daily {day}')
            save(df[FIELDS],path)
            print('bars',day,len(df),flush=True)
    finally:bs.logout()


if __name__=='__main__':main()
