"""Run as root on Tokyo after local acceptance; preserve DB, releases and gateway auth."""
import os,sqlite3,shutil,subprocess,sys,json,pwd,grp
from pathlib import Path
RELEASE=Path('/opt/sector-dashboard/releases/20261102-news-v1')
STATE=Path('/var/lib/sector-dashboard')
CURRENT=Path('/opt/sector-dashboard/current')
CONFIG=Path('/etc/bitget-monitor-gateway/Caddyfile')
CADDY='/opt/bitget-monitor-gateway/bin/caddy'
TAG='20261102-news-v1'

def run(args):
    result=subprocess.run(args,capture_output=True,text=True)
    if result.returncode:raise RuntimeError('command failed: '+args[0]+' '+args[1]+' ('+str(result.returncode)+')')

def upgrade():
    assert os.geteuid()==0
    old=str(CURRENT.resolve());assert old!=str(RELEASE),'release already active'
    backup=STATE/('market.pre-'+TAG+'.sqlite3')
    assert not backup.exists(),'backup already exists; inspect before retry'
    with sqlite3.connect(STATE/'market.sqlite3') as src,sqlite3.connect(backup) as dst:
        src.backup(dst);assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    os.chmod(backup,0o600)
    config_backup=CONFIG.with_name('Caddyfile.pre-'+TAG)
    assert not config_backup.exists()
    shutil.copy2(CONFIG,config_backup);os.chmod(config_backup,0o600)
    stat=CONFIG.stat();text=CONFIG.read_text()
    oldpath='/fupan/api/gainers'
    assert text.count(oldpath)==1 and '/fupan/api/news' not in text
    candidate=CONFIG.with_name('Caddyfile.news-candidate')
    candidate.write_text(text.replace(oldpath,oldpath+' /fupan/api/news /fupan/api/news/*'))
    os.chmod(candidate,stat.st_mode&0o777);os.chown(candidate,stat.st_uid,stat.st_gid)
    run([CADDY,'validate','--config',str(candidate),'--adapter','caddyfile'])
    sys.path.insert(0,str(RELEASE))
    from backend.store import Store
    from backend.news import NewsStore
    store=Store(STATE/'market.sqlite3');NewsStore(store)
    # Transfer only genuine news/evidence/model-accounting records. Never replace market tables.
    with store.connect() as dst,sqlite3.connect(STATE/'news-validation.sqlite3') as src:
        tables=[r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'news_%'")]
        for table in tables:
            assert table.replace('_','').isalnum()
            assert dst.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0,(table,'not empty')
            rows=src.execute(f'SELECT * FROM {table}').fetchall()
            if rows:dst.executemany(f'INSERT INTO {table} VALUES('+','.join('?' for _ in rows[0])+')',rows)
    account=pwd.getpwnam('sector-dashboard')
    for path in STATE.glob('market.sqlite3*'):os.chown(path,account.pw_uid,account.pw_gid)
    runtime=RELEASE/'runtime'
    if not runtime.exists():runtime.symlink_to(STATE)
    shutil.copy2(RELEASE/'deploy/sector-news-worker.service','/etc/systemd/system/sector-news-worker.service')
    run(['systemctl','daemon-reload'])
    (STATE/('rollback-'+TAG+'.json')).write_text(json.dumps(dict(previous_release=old,db_backup=str(backup),gateway_backup=str(config_backup))))
    staged=CURRENT.with_name('current.news-next');staged.symlink_to(RELEASE);os.replace(staged,CURRENT)
    try:
        run(['systemctl','restart','sector-dashboard'])
        os.replace(candidate,CONFIG)
        run(['systemctl','reload','bitget-monitor-gateway'])
        run(['systemctl','enable','--now','sector-news-worker'])
    except Exception:
        subprocess.run(['systemctl','stop','sector-news-worker'],capture_output=True)
        staged.symlink_to(old);os.replace(staged,CURRENT)
        shutil.copy2(config_backup,CONFIG);os.chmod(CONFIG,stat.st_mode&0o777);os.chown(CONFIG,stat.st_uid,stat.st_gid)
        subprocess.run(['systemctl','restart','sector-dashboard'],capture_output=True)
        subprocess.run(['systemctl','reload','bitget-monitor-gateway'],capture_output=True)
        raise
    print('Release switched; consistent backup and previous release retained:',TAG)
if __name__=='__main__':upgrade()
