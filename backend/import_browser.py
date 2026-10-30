"""Validate and replay public JSON exports without cookies or network requests."""
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .provider import Eastmoney, DataError
from .service import Collector, bar_checkpoint
from .store import TZ


class BrowserArchive(Eastmoney):
    ingestion_method = 'manual_browser'

    def __init__(self, store, directory):
        super().__init__(store)
        self.entries = []
        for path in sorted(Path(directory).glob('*.json')):
            item = json.loads(path.read_text())
            url = urlparse(item['source_url'])
            if url.scheme != 'https' or url.hostname not in ('push2.eastmoney.com', 'push2his.eastmoney.com', 'push2ex.eastmoney.com') or url.username or url.password:
                raise DataError('归档来源必须为东方财富公开数据域名')
            captured = datetime.fromisoformat(item['captured_at'].replace('Z', '+00:00'))
            if captured.tzinfo is None:
                raise DataError('浏览器采集时间缺少时区')
            if item['response'].get('rc', 0) != 0:
                raise DataError('归档响应含供应商错误码')
            params = {k:v[0] for k,v in parse_qs(url.query).items()}
            if params.get('fields') and params.get('fltt', '2') != '2':
                raise DataError('归档行情必须使用 fltt=2 单位')
            self.entries.append((url.path, params, item))
        if not self.entries:
            raise DataError('目录没有公开响应归档')

    def get(self, url, params):
        path = urlparse(url).path
        keys = ('pn','fs') if path.endswith('clist/get') else ('secid',) if path.endswith('kline/get') else ('date',)
        matches = []
        for entry_path, query, item in self.entries:
            if entry_path != path:
                continue
            # The same public list response may carry quote and flow fields.
            if all(str(params.get(k, '')) == query.get(k, '') for k in keys):
                matches.append(item)
        if len(matches) != 1:
            raise DataError(f'归档分页缺失或重复: {path} {[(k, params.get(k)) for k in keys]}')
        self.store.observe('browser_raw:' + matches[0]['source_url'], matches[0])
        return matches[0]['response']

    def flows(self, kind):
        # Reuse normalized mapping; adjust only the equivalent list filter.
        original = self.listing
        self.listing = lambda fs, fields: original(fs + '+f:!50', fields)
        try:
            return super().flows(kind)
        finally:
            self.listing = original


def import_archive(store, directory):
    archive = BrowserArchive(store, directory)
    catalogs = {kind:archive.boards(kind) for kind in ('industry','concept')}
    if {r['code'] for r in catalogs['industry']} & {r['code'] for r in catalogs['concept']}:
        raise DataError('归档分类重叠')
    # Validate all lists and bars before writing derived snapshots.
    flows = {kind:archive.flows(kind) for kind in catalogs}
    known = {r['code'] for rows in catalogs.values() for r in rows}
    histories = {}
    checks = {}
    for path, query, item in archive.entries:
        if path.endswith('kline/get'):
            code = query.get('secid', '').removeprefix('90.')
            if code not in known:
                raise DataError('日线不属于本次已验证目录')
            histories[code] = archive.bars(code)
            captured=datetime.fromisoformat(item['captured_at'].replace('Z','+00:00')).astimezone(TZ)
            if captured.strftime('%H:%M') < '15:00':
                histories[code]=[b for b in histories[code] if b['date']<captured.date().isoformat()]
            checks[code]=bar_checkpoint(captured)
    collector = Collector(store, archive)
    for code, bars in histories.items():
        store.observe('bars:'+code, bars)
        store.observe('bars_checked:'+code, checks[code])
    for kind in catalogs:
        collector.flow(kind)
        collector.boards(kind)
    report = {'catalogs':{k:len(v) for k,v in catalogs.items()},'histories':len(histories),
              'ingestion_method':'manual_browser','automatic_collection_verified':False}
    store.event('browser_import',True,json.dumps(report,ensure_ascii=False))
    return report
