"""After-close resumable public-data update, run as a separate memory-limited service."""
import argparse
from datetime import datetime,timedelta
import fcntl
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from quant_history_build import build


def update(root, sources):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'update.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        saved=sorted((root/'results').glob('????-??-??.json'))
        if not saved:raise ValueError('validated initial history is required')
        last=saved[-1].stem
        now=datetime.now(ZoneInfo('Asia/Shanghai'))
        end=(now.date() if now.hour>=18 else now.date()-timedelta(days=1)).isoformat()
        if last>=end:return
        next_day=(datetime.fromisoformat(last).date()+timedelta(days=1)).isoformat()
        script=Path(__file__).with_name('quant_history_fetch.py')
        def fetch(mode,start):
            subprocess.run([sys.executable,str(script),'--root',str(sources),'--mode',mode,
                '--start',start,'--end',end,'--bars-after',last],check=True,timeout=3600)
        fetch('calendar',(datetime.fromisoformat(last).date()-timedelta(days=430)).isoformat())
        import pandas as pd
        dates=pd.read_parquet(sources/'benchmark.parquet').date
        if dates.max()<=last:return
        fetch('values',next_day)
        fetch('bao',next_day)
        build(sources,root,next_day,dates.max())


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True);p.add_argument('--sources',type=Path,required=True)
    a=p.parse_args();update(a.root,a.sources)
