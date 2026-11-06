"""Stage missing benchmark partitions from frozen stock minutes and that day's own membership."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from resonance_build import BENCHMARKS, VERSION, read_capture, write_replay


def stage(days,raw,output):
    for folder in sorted(days.glob('????-??-??')):
        day=folder.name
        meta=json.loads((folder/'summary.json').read_text())
        uni=json.loads((folder/'universe.json').read_text())
        minutes=folder/'minutes.npz'
        if not (meta['version']==VERSION and meta['day']==uni['day']==day and meta['prior']==uni['prior']<day
                and hashlib.sha256(minutes.read_bytes()).hexdigest()==meta['minute_sha256']):
            raise ValueError(f'{day}: inconsistent frozen inputs')
        target=output/day
        target.mkdir(parents=True,exist_ok=False)
        with np.load(minutes,allow_pickle=False) as src:
            prices,amounts=src['prices'],src['amounts']
            codes,names=src['codes'].tolist(),src['names'].tolist()
        for tag,(_,symbol) in BENCHMARKS.items():
            px,_,ref,_,captured=read_capture(raw/(symbol+'.json.gz'),day)
            if not np.isfinite(px[-1]) or np.isfinite(px).sum()<239:raise ValueError(f'{symbol} incomplete')
            write_replay(target,tag,px,ref,captured,day,prices,amounts,codes,names,uni['boards'])


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['days','raw','output']:p.add_argument(name,type=Path)
    args=p.parse_args();stage(args.days,args.raw,args.output)
