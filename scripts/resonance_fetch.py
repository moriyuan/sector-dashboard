"""One paced five-session 1-minute history request per symbol; archive post-close raw evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
import json
import os
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

import numpy as np
import requests

URL='https://web.ifzq.gtimg.cn/appstock/app/day/query'
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.resonance import BENCHMARKS


def universe(quant,day):
    files=sorted(p for p in (quant/'inputs').glob('*.npz') if p.stem<day)[-6:]
    if not files:raise ValueError('missing dated universe')
    symbols=set()
    for path in files:
        with np.load(path,allow_pickle=False) as doc:
            for value in doc['stocks']['code']:
                code=f'{value:06d}'
                symbols.add(('sh' if code.startswith(('60','68')) else 'sz')+code)
    return sorted(symbols)


def fetch(raw,quant,day,indices_only=False):
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    if day!=now.date().isoformat() or now.strftime('%H:%M')<'15:00':
        raise ValueError('collect only after close on the stated capture day')
    folder=raw/day;folder.mkdir(parents=True,exist_ok=True)
    lock=threading.Lock();local=threading.local();tick=[0.]
    def one(symbol):
        out=folder/(symbol+'.json.gz')
        if out.exists():return 'saved'
        if not hasattr(local,'session'):local.session=requests.Session()
        for attempt in range(2):
            try:
                with lock:
                    time.sleep(max(0,tick[0]-time.monotonic()));tick[0]=time.monotonic()+.12
                r=local.session.get(URL,params={'code':symbol},timeout=(5,12))
                r.raise_for_status();obj=r.json()
                data=obj['data'][symbol]
                if obj.get('code')!=0 or not isinstance(data.get('data'),list) or not data['data']:
                    raise ValueError('empty data')
                stamp=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
                record=dict(symbol=symbol,source=URL,captured=stamp,days=data['data'],name=(data.get('qt',{}).get(symbol) or ['',''])[1])
                tmp=out.with_suffix('.tmp')
                with gzip.open(tmp,'wt',encoding='utf8',compresslevel=3) as f:json.dump(record,f,ensure_ascii=False,separators=(',',':'))
                os.replace(tmp,out);return 'ok'
            except (requests.RequestException,KeyError,ValueError,IndexError,TypeError):
                if attempt==0:time.sleep(2)
        return 'failed'
    from collections import Counter
    counts=Counter()
    symbols=[symbol for _,symbol in BENCHMARKS.values()]+([] if indices_only else universe(quant,day))
    with ThreadPoolExecutor(max_workers=6) as pool:
        for i,status in enumerate(pool.map(one,symbols),1):
            counts[status]+=1
            if i%500==0:print(day,i,dict(counts),flush=True)
    print('capture',day,len(symbols),dict(counts),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--raw',type=Path,required=True);p.add_argument('--quant',type=Path,required=True);p.add_argument('--day',required=True)
    p.add_argument('--indices-only',action='store_true');a=p.parse_args();fetch(a.raw,a.quant,a.day,a.indices_only)
