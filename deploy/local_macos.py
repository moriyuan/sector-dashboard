"""Install the two per-user launch agents, or run their news worker."""
import os
import plistlib
import re
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(ROOT / '.venv/bin/python')


def start_news():
    if not os.environ.get('DEEPSEEK_API_KEY'):
        rc = Path.home() / '.zshrc'
        if rc.is_file():
            # Read only the designated literal assignment. Never execute shell settings.
            values = {}
            with rc.open() as file:
                for line in file:
                    match = re.match(r'^\s*(?:export\s+)?(DEEPSEEK_API_KEY|DEEPSEKK_API_KEY)=(.*)$', line)
                    if match:
                        parts = shlex.split(match[2], comments=True)
                        if len(parts) == 1 and re.fullmatch(r'[A-Za-z0-9._-]+', parts[0]):
                            values[match[1]] = parts[0]
            os.environ['DEEPSEEK_API_KEY'] = values.get('DEEPSEEK_API_KEY') or values.get('DEEPSEKK_API_KEY', '')
    os.execv(PYTHON, [PYTHON, '-m', 'backend.news_worker'])


def install():
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise SystemExit('Run as the logged-in macOS user.')
    agents = Path.home() / 'Library/LaunchAgents'
    logs = ROOT / 'runtime/local-service'
    logs.mkdir(mode=0o700, exist_ok=True)
    agents.mkdir(mode=0o700, exist_ok=True)
    domain = f'gui/{os.getuid()}'
    commands = {
        'local.fupan.dashboard': ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1',
                                  '--port', '8788', '--workers', '1', '--no-proxy-headers'],
        'local.fupan.news': [str(Path(__file__).resolve()), 'worker'],
    }
    for label, args in commands.items():
        env = {'TZ': 'Asia/Shanghai', 'PYTHONUNBUFFERED': '1', 'SECTOR_DB': str(ROOT / 'runtime/market.sqlite3')}
        if label == 'local.fupan.news':
            env['SECTOR_NEWS_PUBLIC_DNS'] = '1'
        path = agents / f'{label}.plist'
        config = plistlib.dumps(dict(Label=label, ProgramArguments=[PYTHON, *args],
            WorkingDirectory=str(ROOT), EnvironmentVariables=env, RunAtLoad=True, KeepAlive=True,
            ThrottleInterval=20, ExitTimeOut=100, Umask=0o077,
            StandardOutPath=str(logs / (label + '.log')), StandardErrorPath=str(logs / (label + '.err'))))
        if path.exists() and path.read_bytes() != config:
            raise SystemExit(f'Existing launch agent differs, review before replacing: {path}')
        if not path.exists():
            with path.open('xb') as file:
                file.write(config)
            path.chmod(0o600)
        running = subprocess.run(['launchctl', 'print', f'{domain}/{label}'], capture_output=True)
        if running.returncode:
            subprocess.run(['launchctl', 'enable', f'{domain}/{label}'], check=True)
            subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
        print(label)


if __name__ == '__main__':
    if sys.argv[1:] == ['worker']:
        start_news()
    elif sys.argv[1:] == ['install']:
        install()
    else:
        raise SystemExit('Usage: local_macos.py install | worker')
