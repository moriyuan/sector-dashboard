"""One-time reversible upgrade; run as root on the existing Tokyo host."""
from pathlib import Path
import os,sqlite3,subprocess
base=Path('/opt/sector-dashboard');current=base/'current'
old=base/'releases/20261102-news-v2';new=base/'releases/20261102-news-v3'
state=Path('/var/lib/sector-dashboard');backup=state/'market.pre-20261102-news-v3.sqlite3'
assert current.resolve()==old and new.is_dir() and not backup.exists()
subprocess.run(['systemctl','stop','sector-news-worker'],check=True)
try:
 with sqlite3.connect(state/'market.sqlite3') as src,sqlite3.connect(backup) as dst:
  src.backup(dst);assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 os.chmod(backup,0o600)
 subprocess.run(['sudo','-u','sector-dashboard',str(base/'venv/bin/python'),'-c',"import sys;sys.path.insert(0,'"+str(new)+"');from backend.store import Store;from backend.news import NewsStore;NewsStore(Store('/var/lib/sector-dashboard/market.sqlite3'))"],check=True)
 stage=base/'current.news-v3-next';stage.symlink_to(new);os.replace(stage,current)
 subprocess.run(['systemctl','restart','sector-dashboard'],check=True)
except Exception:
 stage=base/'current.news-v3-rollback';stage.symlink_to(old);os.replace(stage,current)
 subprocess.run(['systemctl','restart','sector-dashboard','sector-news-worker'])
 raise
print('news-v3 activated; database backup retained; news worker paused for bounded source/model acceptance',flush=True)
