"""Idempotent removal overlay for the independently updated Shanghai test branch.

Run before building a release. Only the three retired modules are changed.
Unknown remaining references abort before writing files.
"""
from pathlib import Path
import re
import sys


def apply(root):
    root = Path(root)
    files = ('backend/app.py', 'backend/service.py', 'backend/cli.py',
             'frontend/src/main.tsx', 'frontend/src/ResearchLayout.tsx')
    updated = {}
    for name in files:
        if name.startswith('frontend/') and not (root / name).exists() and (root / 'frontend/dist/index.html').exists():
            # Production packages contain compiled assets only, replaced separately.
            continue
        value = (root / name).read_text()
        if name.endswith('app.py'):
            value = '\n'.join(line for line in value.split('\n') if not any(token in line for token in (
                'from .etf_tracker import', 'app.include_router(etf_router',
                'from .index_pe import', 'from .financing import',
                "'index_pe':index_pe_view", "'financing':financing_view")))
        elif name.endswith('service.py'):
            value = re.sub(r'        from \.etf_tracker import run_scheduler as run_etf\n.*?daemon=True, name=\x27index-pe\x27\)\.start\(\)\n', '', value, flags=re.S)
        elif name.endswith('cli.py'):
            value = value.replace(",'collect-etf'", '')
            value = re.sub(r'^    parser.add_argument\(\x27--days\x27.*\n', '', value, flags=re.M)
            value = re.sub(r"    if args.command=='collect-etf':\n.*?(?=    if args.command=='collect-lhb':)", '', value, flags=re.S)
        elif name.endswith('main.tsx'):
            value = '\n'.join(line for line in value.split('\n') if not any(token in line for token in (
                'import EtfTracker', 'import IndexPe', 'import MarginTrend',
                '<IndexPe ', '<MarginTrend ', '<EtfTracker ')))
            value = value.replace(';index_pe?:IndexPeRow[];financing?:Financing', '')
            value = value.replace('龙虎榜及 ETF 份额资金数据', '龙虎榜数据')
        else:
            value = value.replace("['national-team','宽基ETF'],", '')
            value = value.replace("(id!=='national-team'&&id!=='lhb')", "id!=='lhb'")
        if re.search(r'etf_tracker|index_pe|financing|IndexPe|MarginTrend|EtfTracker|national-team|collect-etf', value):
            raise ValueError('Unrecognized retired-module reference: ' + name)
        updated[name] = value
    for name, value in updated.items():
        (root / name).write_text(value)
    for name in ('backend/etf_tracker.py', 'backend/index_pe.py', 'backend/financing.py',
                 'frontend/src/EtfTracker.tsx', 'frontend/src/etf-tracker.css',
                 'frontend/src/IndexPe.tsx', 'frontend/src/MarginTrend.tsx',
                 'frontend/src/margin-trend.css', 'tests/test_etf_tracker.py',
                 'tests/test_index_pe.py', 'tests/test_financing.py'):
        (root / name).unlink(missing_ok=True)
    for stem in ('etf_tracker', 'index_pe', 'financing'):
        for path in (root / 'backend/__pycache__').glob(stem + '.*.pyc'):
            path.unlink()


if __name__ == '__main__':
    apply(sys.argv[1])
