"""Shanghai close worker. Publish only completed minutes, retain published sessions on failure."""
import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

from resonance_build import build
from resonance_fetch import fetch
from resonance_members import known_boards


def update(data_dir):
    root=data_dir/'resonance';root.mkdir(parents=True,exist_ok=True)
    now=datetime.now(ZoneInfo('Asia/Shanghai'));day=now.date().isoformat()
    if now.strftime('%H:%M')<'15:10' or (root/'days'/day/'summary.json').exists():return
    with (root/'update.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with sqlite3.connect('file:'+str(data_dir/'market.sqlite3')+'?mode=ro',uri=True) as db:
            row=db.execute("select payload from observations where key='calendar' order by id desc limit 1").fetchone()
            if not row:raise ValueError('trading calendar missing')
            cal=sorted(set(json.loads(row[0])))
            if not cal or day>cal[-1]:raise ValueError('trading calendar stale')
            if day not in cal:return
            prior=cal[cal.index(day)-1];universe=known_boards(db,day,prior)
        quant=data_dir/'quant-price-v2'
        if not (quant/'inputs'/f'{prior}.npz').is_file():raise ValueError('previous close missing')
        with tempfile.TemporaryDirectory(dir=root,prefix='.capture-') as temp:
            fetch(Path(temp),quant,day)
            build(root,quant,Path(temp)/day,day,universe)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,required=True)
    update(p.parse_args().data_dir)
