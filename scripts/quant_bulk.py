"""Extract only the necessary years of BaoStock unadjusted bars from a checksum-pinned public data archive."""
import argparse
import hashlib
import io
import tarfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from quant_history_fetch import FIELDS, save

BULK_SHA = '9e5d9d7386f695f5b255b4d44adb435aec410c5df4276f8db44be111deed0d35'


def convert(root):
    path=root/'baostock.tar'
    with path.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=BULK_SHA:
            raise ValueError('bulk archive digest mismatch')
    dates=pd.read_parquet(root/'benchmark.parquet').date
    start=dates.min()
    # Disk staging bounds memory to 200 securities during decoding.
    frames=[]
    parts=root/'bulk-parts'
    parts.mkdir(exist_ok=True)
    def flush(i):
        if frames:
            save(pd.concat(frames,ignore_index=True),parts/f'{i:03}.parquet')
            frames.clear()
    n=0
    with tarfile.open(path) as tar:
        for member in tar:
            if not member.name.startswith('raw/bars_daily/CN_') or not member.name.endswith('.parquet'):continue
            table=pq.read_table(io.BytesIO(tar.extractfile(member).read()),columns=FIELDS+["adjustflag"])
            df=table.to_pandas()
            df=df[df.date>=start].copy()
            if not df.empty:
                if df.date.duplicated().any() or df.code.nunique()!=1 or not df.adjustflag.eq('3').all():
                    raise ValueError(member.name)
                for col in FIELDS[2:]:df[col]=pd.to_numeric(df[col],errors='coerce')
                frames.append(df[FIELDS])
            n+=1
            if n%200==0:flush(n//200)
    flush(n//200+1)
    if n!=5559:raise ValueError('bulk security count mismatch')
    print('bulk securities',n,flush=True)
    # Partition one year at a time; no backfilling today's directory.
    for year in sorted(set(d[:4] for d in dates)):
        begin=max(start,year+'-01-01');end=min('2026-09-24',year+'-12-31')
        if begin>end:continue
        blocks=[pd.read_parquet(p,filters=[('date','>=',begin),('date','<=',end)]) for p in sorted(parts.glob('*.parquet'))]
        data=pd.concat(blocks,ignore_index=True)
        for day,bars in data.groupby('date',sort=True):
            if len(bars)<4000 or bars.code.duplicated().any():raise ValueError(day)
            save(bars,root/'bars'/f'{day}.parquet')
        print(year,len(data),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);convert(p.parse_args().root)
