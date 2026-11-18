"""Read-only Shanghai and Shenzhen K-line data from TDX standard quote servers."""
import argparse
import json
import math
import os
import re
from datetime import datetime

from .base import SourceClient, SourceError
from ..store import TZ, now_iso

MARKETS = {'sz': 0, 'sh': 1}
PERIODS = ('day', '1m')
DEFAULT_SERVERS = '101.35.121.35:7709,180.153.18.170:7709,101.33.225.16:7709'


def _bars(raw, period, count):
    if not isinstance(raw, list) or not raw or len(raw) > count:
        raise SourceError('通达信 K 线响应为空或数量异常')
    rows = []
    seen = set()
    for item in raw:
        try:
            stamp = datetime(item.year, item.month, item.day, item.hour, item.minute, tzinfo=TZ)
            bar = {field: float(getattr(item, field)) for field in ('open', 'high', 'low', 'close', 'amount')}
            bar['volume'] = float(item.vol)
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise SourceError('通达信 K 线字段格式异常') from exc
        key = stamp.date().isoformat() if period == 'day' else stamp.isoformat(timespec='minutes')
        valid_prices = 0 < bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']
        if (key in seen or not all(math.isfinite(value) for value in bar.values())
                or not valid_prices or bar['amount'] < 0 or bar['volume'] < 0):
            raise SourceError('通达信 K 线时间重复或量价异常')
        seen.add(key)
        bar['date'] = stamp.date().isoformat()
        if period == '1m':
            bar['timestamp'] = key
        if hasattr(item, 'up_count'):
            bar.update(up_count=item.up_count, down_count=item.down_count)
        rows.append(bar)
    return sorted(rows, key=lambda row: row.get('timestamp', row['date']))


class TdxSource(SourceClient):
    name = 'tdx'

    def __init__(self, servers=None):
        super().__init__()
        servers = servers if servers is not None else os.environ.get('SECTOR_TDX_SERVERS', DEFAULT_SERVERS)
        self.servers = []
        for entry in servers.split(','):
            host, sep, port = entry.strip().rpartition(':')
            if not sep or not re.fullmatch(r'[A-Za-z0-9.-]+', host) or not port.isdecimal() or not 1 <= int(port) <= 65535:
                raise ValueError('通达信节点格式应为 host:port，多个节点用逗号分隔')
            self.servers.append((host, int(port)))

    def fetch_bars(self, symbol, *, period='day', count=20, offset=0):
        """Return one page of source-tagged, unadjusted bars in chronological order."""
        symbol = symbol.lower()
        if not re.fullmatch(r'(sh|sz)[0-9]{6}', symbol):
            raise ValueError('请使用 sh 或 sz 加六位代码，例如 sh600519 或 sz000001')
        if period not in PERIODS or not 1 <= count <= 800 or not 0 <= offset <= 65535:
            raise ValueError('周期须为 day 或 1m，条数为 1–800，偏移量为 0–65535')
        try:
            from xmtdx import KlineCategory, Market, TdxClient, TdxError
        except ImportError as exc:
            raise SourceError('无法加载 xmtdx，请安装 requirements-tdx.txt 中的通达信依赖') from exc

        category = KlineCategory.DAY_ALT if period == 'day' else KlineCategory.MIN_1_ALT
        market = Market(MARKETS[symbol[:2]])
        failures = []
        for host, port in self.servers:
            self._throttle()
            endpoint = f'{host}:{port}'
            client = TdxClient(host, port, timeout=5, auto_reconnect=False, max_attempts=1)
            try:
                client.connect()
                raw = client.get_bars(market, symbol[2:], category, offset, count)
                rows = _bars(raw, period, count)
            except (SourceError, TdxError, OSError) as exc:
                failures.append(f'{endpoint} {exc}')
                continue
            finally:
                client.close()
            return {'source': self.name, 'endpoint': endpoint, 'captured_at': now_iso(), 'volume_unit': 'vendor',
                    'symbol': symbol, 'period': period, 'adjustment': 'none', 'requested_count': count,
                    'offset': offset, 'rows': rows}
        raise SourceError('通达信行情不可用：' + '；'.join(failures))


def main():
    parser = argparse.ArgumentParser(description='读取通达信沪深标准行情，输出 JSON')
    parser.add_argument('symbol', help='证券代码，例如 sh600519')
    parser.add_argument('--period', choices=PERIODS, default='day')
    parser.add_argument('--count', type=int, default=20)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--servers', help='自定义节点，格式 host:port[,host:port]')
    args = parser.parse_args()
    try:
        result = TdxSource(args.servers).fetch_bars(
            args.symbol, period=args.period, count=args.count, offset=args.offset,
        )
    except (SourceError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
