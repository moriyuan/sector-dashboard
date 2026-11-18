"""Shanghai deployment maintenance; fixed allowlisted roots, no generic rm target.

Pause writers, compact to three archived sessions plus current observations,
verify a compressed backup, switch databases and restart original services.
"""
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

if __package__:
    from .retain_recent import retained_days, build_retained, prune_files
else:
    from retain_recent import retained_days, build_retained, prune_files

ROOTS=(Path('/var/lib/sector-dashboard'),Path('/var/lib/fupan-testdb'))
SERVICES=('sector-dashboard','sector-dashboard-test','sector-news-worker','sector-news-worker-test')
TIMERS=('sector-dashboard-test-update.timer','sector-resonance.timer')
ARCHIVE=Path('/var/backups/sector-retained')


def preserve_seat_disclosures(root):
    # User keeps the collected seat-stock list independently of rolling quotes.
    sys.path.insert(0,'/opt/sector-dashboard/current')
    from backend.seat_stocks import archive, current_events
    from backend.store import Store
    archive(Path('/var/lib/sector-seat-stocks/participants.sqlite3'), current_events(Store(root/'market.sqlite3')))


def command(*args):
    return subprocess.run(args,check=True,text=True,capture_output=True).stdout.strip()


def compressed_backup(source, output):
    temp=output.with_suffix('.gz.tmp');h=hashlib.sha256()
    with source.open('rb') as src, gzip.open(temp,'wb',compresslevel=1) as dst:
        while chunk:=src.read(1024*1024):h.update(chunk);dst.write(chunk)
    check=hashlib.sha256()
    with gzip.open(temp,'rb') as src:
        while chunk:=src.read(1024*1024):check.update(chunk)
    if h.digest()!=check.digest():raise ValueError('compressed backup checksum failed')
    os.chmod(temp,0o600)
    with temp.open('rb') as f:os.fsync(f.fileno())
    temp.replace(output)
    return h.hexdigest()


def main():
    if os.geteuid()!=0:raise PermissionError('maintenance requires root')
    lock=Path('/run/sector-retention.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    asof=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    active=[]
    for name in (*SERVICES,*TIMERS):
        r=subprocess.run(['systemctl','is-active',name],capture_output=True,text=True)
        if r.stdout.strip() in ('active','activating'):active.append(name)
    # Service failure should not make a scheduled maintenance restart unrelated services.
    if os.environ.get('RETENTION_RECOVER_SERVICES')=='1':active=list(dict.fromkeys([*active,*SERVICES,*TIMERS]))
    reports=[]
    try:
        command('systemctl','stop',*TIMERS)
        command('systemctl','stop',*SERVICES,'sector-resonance.service','sector-dashboard-test-update.service')
        ARCHIVE.mkdir(mode=0o700,parents=True,exist_ok=True)
        for root in ROOTS:
            path=root/'market.sqlite3';dest=root/'market.retention-new.sqlite3'
            preserve_seat_disclosures(root)
            if dest.exists():
                # An interrupted build is not a completed archive or user input.
                dest.unlink()
                Path(str(dest)+'-journal').unlink(missing_ok=True)
            with sqlite3.connect(path,timeout=60) as db:
                busy,_,_=db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                if busy:raise ValueError('writer still active')
                days=retained_days(db,3,asof)
            cutoff=min(days)
            print('building',root,days,flush=True)
            report=build_retained(path,dest,cutoff)
            metadata=path.stat();os.chown(dest,metadata.st_uid,metadata.st_gid);os.chmod(dest,metadata.st_mode&0o777)
            sha=compressed_backup(dest,ARCHIVE/(root.name+'.sqlite3.gz'))
            # New image and verified backup exist before replacing the live path.
            with dest.open('rb') as f:os.fsync(f.fileno())
            for suffix in ('-wal','-shm'):Path(str(path)+suffix).unlink(missing_ok=True)
            dest.replace(path)
            removed=prune_files(root,cutoff)
            report.update(root=str(root),kept_days=days,backup_sha256=sha,removed_files=len(removed),asof=asof)
            (root/'retention-status.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            reports.append(report);print(json.dumps(report,ensure_ascii=False),flush=True)
        compressed_backup(Path('/var/lib/sector-seat-stocks/participants.sqlite3'),ARCHIVE/'seat-stocks.sqlite3.gz')
        # The new pair of backups contain only retained dates. Remove old full-history
        # backups including the incomplete 13-Nov copy; never touch gateway/code backups.
        for folder in (Path('/var/backups/fupan-testdb'),Path('/var/backups/sector-dashboard')):
            if not folder.exists():continue
            for p in folder.rglob('*'):
                if p.is_file() and (p.name.endswith(('.sqlite3','.sqlite3.gz','.sqlite3-journal','.sqlite3-wal','.sqlite3-shm'))):p.unlink()
        print('free_bytes',shutil.disk_usage('/').free,flush=True)
    finally:
        if active:command('systemctl','start',*active)
        lock.close()


if __name__=='__main__':main()
