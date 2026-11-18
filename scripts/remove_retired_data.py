"""Offline targeted cleanup. Writers must be stopped; original survives validation.

Unlike rolling retention, this preserves every date and every unrelated table.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

FILTERS = {
    'snapshots': "kind='broad_etf'",
    'observations': "key LIKE 'index-pe:%' OR key LIKE 'financing:%' OR key LIKE 'etf-check:%'",
    'events': "job IN ('宽基ETF','融资余额') OR job LIKE '指数估值 %'",
}


def clean(path):
    path = Path(path).resolve(strict=True)
    destination = path.with_name(path.name + '.remove-extras-new')
    if destination.exists():
        raise ValueError('unfinished destination exists; inspect before retry')
    with closing(sqlite3.connect(path, timeout=60)) as source:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        removed = {table: source.execute(f'SELECT count(*) FROM "{table}" WHERE {where}').fetchone()[0]
                   for table, where in FILTERS.items() if table in tables}
        if not any(removed.values()):
            return dict(path=str(path), removed=removed)
        busy, _, _ = source.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if busy:
            raise ValueError('database still has active readers/writers')
        counts = {table: source.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            target.execute('PRAGMA journal_mode=DELETE')
            with target:
                for table, where in FILTERS.items():
                    if table in tables:
                        target.execute(f'DELETE FROM "{table}" WHERE {where}')
            target.execute('VACUUM')
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('integrity check failed; original unchanged')
            for table, count in counts.items():
                assert target.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == count - removed.get(table, 0)
    metadata = path.stat()
    os.chmod(destination, metadata.st_mode & 0o777)
    if os.geteuid() == 0:
        os.chown(destination, metadata.st_uid, metadata.st_gid)
    with destination.open('rb') as handle:
        os.fsync(handle.fileno())
    for suffix in ('-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    destination.replace(path)
    Path(str(path) + '.etf.lock').unlink(missing_ok=True)
    return dict(path=str(path), removed=removed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', type=Path, nargs='+')
    for path in parser.parse_args().paths:
        print(json.dumps(clean(path), ensure_ascii=False), flush=True)
