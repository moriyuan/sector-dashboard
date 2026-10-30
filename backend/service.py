import threading
import time
from datetime import datetime

from .analytics import analyze, breadth, hs_stock, pool_stats, valid_stock
from .provider import Eastmoney, DataError, source_time
from .public_data import PublicData
from .rankings import build_rankings, at_limit
from .schedule import phase
from .store import TZ, now_iso


def fresh_input(snapshot, board_time, max_seconds, mode):
    if not snapshot or not snapshot.get('source_time'):
        return False
    if mode == 'close':
        return snapshot['source_time'][:10] == board_time[:10] and snapshot['source_time'][11:16] >= '15:00'
    return abs((datetime.fromisoformat(board_time)-datetime.fromisoformat(snapshot['source_time'])).total_seconds()) <= max_seconds


def bar_checkpoint(current):
    clock = current.strftime('%H:%M')
    return current.date().isoformat()+(' / 1630' if clock >= '16:30' else ' / 1530' if clock >= '15:30' else ' / intraday')


def close_session(stamp):
    if not stamp or stamp[11:16] < '15:00':
        raise DataError('仅采集收盘数据：来源尚未收盘，保留已有归档')
    return stamp[:10], 'close'


class Collector:
    def __init__(self, store, provider=None):
        self.store = store
        self.provider = provider or PublicData(store)
        self.stop = threading.Event()
        self.thread = None
        self.last = {}
        self.catalog = {}
        self.failures = {}
        self.status = 'idle'
        self.enrich_failures = 0
        self.enrich_next = 0
        self.archive_phase = None
        self.pending_publish=set()

    def job(self, name, fn, interval=300):
        previous = self.last.get(name, 0)
        if name in self.last and time.monotonic() - previous < interval * min(8, 2 ** self.failures.get(name,0)):
            return None
        self.last[name] = time.monotonic()
        try:
            result = fn()
            self.store.event(name, True, '采集完成')
            self.failures[name] = 0
            return result
        except Exception as exc:
            self.failures[name] = self.failures.get(name,0)+1
            self.store.event(name, False, f'{type(exc).__name__}: {exc}')
            return None

    def calendar(self):
        cal = self.provider.calendar()
        self.store.observe('calendar', cal)
        return cal

    def market(self):
        stocks = self.provider.stocks()
        valid = [r for r in stocks if valid_stock(r)]
        stamp = source_time(valid)
        if not stamp:
            raise DataError('全市场缺少交易日期，保留原快照')
        day, mode = close_session(stamp)
        # Exclude old/non-session quotes from the denominator.
        same_day = [r for r in stocks if r.get('source_time','') and r['source_time'][:10] == day and r['source_time'][11:16]>='15:00']
        stats = breadth(same_day)
        stats['total']=len({r['code'] for r in stocks if hs_stock(r)})
        stats['coverage']=(stats['eligible']+stats['st']['eligible'])/stats['total'] if stats['total'] else None
        normal=[r for r in same_day if valid_stock(r) and hs_stock(r) and 'ST' not in r.get('name','').upper()]
        stats['quote_limit_up']=sum(at_limit(r) is True for r in normal) if normal and all(at_limit(r) is not None for r in normal) else None
        stats['quote_limit_down']=sum(at_limit(r,'lower_limit') is True for r in normal) if normal and all(at_limit(r,'lower_limit') is not None for r in normal) else None
        stats['limit_price_coverage']=sum(at_limit(r) is not None and at_limit(r,'lower_limit') is not None for r in normal)/len(normal) if normal else None
        stats['quote_coverage'] = len(same_day) / len(stocks) if stocks else None
        source=getattr(self.provider,'sources',{}).get('stocks','东方财富')
        self.store.save('stocks', day, mode, {'rows':stocks,'source':source}, stamp)
        self.store.save('market', day, mode, {'stats':stats,'source':source}, stamp)
        return day, mode

    def indices(self):
        rows = self.provider.indices()
        stamp = source_time(rows)
        if not stamp:
            raise DataError('指数缺少交易日期')
        day, mode = close_session(stamp)
        self.store.save('indices',day,mode,{'rows':rows,'source':getattr(self.provider,'sources',{}).get('indices','东方财富')},stamp)
        return day, mode

    def pools(self, day, mode):
        if mode != 'close':
            raise DataError('仅采集收盘情绪池')
        pools = {}
        for kind in ('up','broken','down'):
            pools[kind] = self.job('pool_'+kind, lambda k=kind:self.provider.pool(k,day))
        if any(x is None for x in pools.values()) and self.store.snapshot('pools',day,mode):
            self.store.event('pools',False,'部分情绪池未成功，保留上次可用快照及其采集时间')
            return
        if any(x is not None for x in pools.values()):
            self.store.save('pools',day,mode,{'pools':pools,'stats':pool_stats(pools['up'],pools['broken'],pools['down']),
                'source':'东方财富涨跌停池','scope':'供应商池覆盖沪深非ST，科创板及未开板新股可能未收录；非全市场涨跌停普查'})
            self.store.event('pools',True,'情绪池已归档')

    def flow(self, kind):
        rows = self.provider.flows(kind)
        stamp = source_time(list(rows.values()))
        if not stamp:
            raise DataError('资金数据缺少交易日期，无法绑定行情快照')
        day, mode = close_session(stamp)
        self.store.save('flow_'+kind,day,mode,{'rows':rows,'source':getattr(self.provider,'sources',{}).get('flow_'+kind,'东方财富')},stamp)

    def boards(self, kind):
        rows = self.provider.boards(kind)
        other = self.catalog.get('concept' if kind=='industry' else 'industry',[])
        if {r['code'] for r in rows} & {r['code'] for r in other}:
            raise DataError('行业与概念代码重叠，分类过滤未通过校验')
        stamp = source_time(rows)
        if not stamp:
            raise DataError('板块行情缺少交易日期，拒绝绑定采集日期')
        day, mode = close_session(stamp)
        self.catalog[kind] = rows
        self.store.observe('catalog:'+kind, rows)
        market = self.store.snapshot('market',day,mode)
        stock_snapshot = self.store.snapshot('stocks',day,mode)
        stocks_fresh = fresh_input(stock_snapshot,stamp,120,mode)
        market_fresh = fresh_input(market,stamp,120,mode)
        stock_map = {r['code']:r for r in stock_snapshot['payload']['rows']} if stocks_fresh else {}
        market_amount = market['payload']['stats']['amount'] if market_fresh else None
        flow = self.store.snapshot('flow_'+kind,day,mode)
        flow_rows = flow['payload']['rows'] if fresh_input(flow,stamp,360,mode) else {}
        histories = {}
        refs = {'stocks':stock_snapshot['id'] if stock_snapshot else None,'bars':{},'members':{},'members_meta':{}}
        pool_snapshot = self.store.snapshot('pools',day,mode)
        refs['pools'] = pool_snapshot['id'] if pool_snapshot else None
        previous = self.store.history(kind,day)
        for r in rows:
            code = r['code']
            member_record = self.store.observation_record('members:'+code+':'+day)
            member_codes = member_record['payload'] if member_record else None
            refs['members'][code] = member_record['id'] if member_record else None
            member_meta=self.store.observation_record('members_meta:'+code+':'+day)
            refs['members_meta'][code]=member_meta['id'] if member_meta else None
            eligible_codes = [c for c in (member_codes or []) if hs_stock({'code':c})]
            members = [stock_map[c] for c in eligible_codes if c in stock_map]
            member_stats = breadth(members)
            r['coverage'] = len(members)/len(eligible_codes) if eligible_codes else None
            r['member_count'] = len(eligible_codes) if member_codes is not None else None
            r['eligible_count'] = member_stats['eligible'] if member_codes is not None else None
            # A missing quote is not a flat/declining constituent. Require all mapped quotes.
            r['up_ratio'] = member_stats['up_ratio'] if r['coverage'] == 1 else None
            r['membership_verified']=member_meta['payload']['verified'] if member_meta else member_codes is not None
            if not r['membership_verified']:
                r['up_ratio']=None
            r['valid_coverage']=member_stats['coverage']
            r['membership_source']=member_meta['payload']['source'] if member_meta else None
            member_amounts = [m.get('amount') for m in members if valid_stock(m)]
            complete_amount = r['membership_verified'] and bool(eligible_codes) and r['coverage']==1 and all(m.get('amount') is not None and m.get('quote_status')!='stale_or_unavailable' for m in members)
            r['amount_share'] = sum(member_amounts)/market_amount*100 if complete_amount and market_amount else None
            prev = next((a for a in reversed(previous) if a['date'] < day), None)
            prev_share = next((v.get('amount_share') for v in prev['rows'] if v['code']==code), None) if prev else None
            r['share_change'] = r['amount_share']-prev_share if r['amount_share'] is not None and prev_share is not None else None
            r['flow'] = flow_rows.get(code)
            r['flow_collected_at'] = flow['collected_at'] if flow else None
            bar_record = self.store.observation_record('bars:'+code)
            refs['bars'][code] = bar_record['id'] if bar_record else None
            histories[code] = bar_record['payload'] if bar_record else []
        cal = self.store.observed('calendar') or []
        analyzed = analyze(rows,histories,previous,day,mode,cal)
        self.store.save(kind,day,mode,{'rows':analyzed,'scope':'东方财富板块指数；成分广度统计沪深非ST有效交易股票',
                                     'ingestion_method':getattr(self.provider,'ingestion_method','automatic'),
                                     'source':getattr(self.provider,'sources',{}).get('board_'+kind,'东方财富'),
                                     'input_refs':refs,
                                     'components':{'stocks':metadata(stock_snapshot,day,mode),'market':metadata(market,day,mode),'flow':metadata(flow,day,mode)},
                                     'history_coverage':sum(bool(h) for h in histories.values())/len(rows),
                                     'flow_source_time':flow['source_time'] if flow else None},stamp)
        self.store.event('board_'+kind,True,f'{len(rows)} 个板块，历史覆盖 {sum(bool(h) for h in histories.values())}/{len(rows)}')
        return day, mode

    def enrich_one(self):
        # Bounded work between quote cycles, no huge foreground backfill.
        if time.monotonic() < self.enrich_next:
            return False
        current = datetime.now(TZ)
        if not phase(current,self.store.observed('calendar') or []).startswith('archive'):
            return False
        day = current.date().isoformat()
        checkpoint = bar_checkpoint(current)
        for kind in ('industry','concept'):
            rows = self.catalog.get(kind) or self.store.observed('catalog:'+kind) or []
            for r in rows:
                code = r['code']
                member_key = 'members:'+code+':'+day
                if hasattr(self.provider,'members') and self.store.observed(member_key) is None:
                    if self.failures.get(member_key,0) and time.monotonic()-self.last.get(member_key,0)<600:
                        continue
                    def load_members():
                        members = self.provider.members(code)
                        self.store.observe(member_key,sorted({m['code'] for m in members}))
                    self.job(member_key,load_members)
                    return self.enrich_result(member_key)
                bars_key = 'bars:'+code
                if self.store.observed('bars_checked:'+code) != checkpoint:
                    if self.failures.get(bars_key,0) and time.monotonic()-self.last.get(bars_key,0)<600:
                        continue
                    def load_bars():
                        bars=self.provider.bars(code)
                        # An unfinished daily candle is never archived as a close.
                        if current.strftime('%H:%M') < '15:00':
                            bars=[b for b in bars if b['date'] < day]
                        self.store.observe(bars_key,bars)
                        self.store.observe('bars_checked:'+code,checkpoint)
                    self.job(bars_key,load_bars)
                    return self.enrich_result(bars_key)
        return False

    def enrich_result(self, key):
        failed = self.failures.get(key,0)>0
        self.enrich_failures = self.enrich_failures+1 if failed else 0
        self.enrich_next = time.monotonic() + (min(600,30*2**min(self.enrich_failures,5)) if failed else 1)
        return not failed

    def cycle(self, initial=False):
        self.job('calendar',self.calendar,86400)
        cal = self.store.observed('calendar') or []
        current = datetime.now(TZ)
        self.status = phase(current,cal)
        day = current.date().isoformat()
        archive_key = 'archive:'+day+':'+self.status
        need_archive = self.status.startswith('archive') and self.store.observed(archive_key) is None
        active = self.status.startswith('archive') and (initial or need_archive)
        if active:
            if hasattr(self.provider,'begin_session'):
                self.provider.begin_session(day)
            if self.archive_phase != self.status:
                # The 16:30 verification must not inherit 15:30 retry cooldowns.
                for name in ('indices','market','pool_up','pool_broken','pool_down','flow_industry','flow_concept','board_industry','board_concept'):
                    self.last.pop(name,None)
                if hasattr(self.provider,'_circuit'):self.provider._circuit.clear()
                self.archive_phase = self.status
            index_date = self.job('indices',self.indices)
            market = self.job('market',self.market)
            refreshed=bool(index_date or market)
            if market or index_date:
                self.pools(*(market or index_date))
            for kind in ('industry','concept'):
                self.job('flow_'+kind,lambda k=kind:self.flow(k))
                refreshed=bool(self.job('board_'+kind,lambda k=kind:self.boards(k))) or refreshed
            if refreshed:build_rankings(self.store,day)
            if need_archive:
                snapshots = [self.store.snapshot(k,day,'close') for k in ('industry','concept','market','indices','pools','flow_industry','flow_concept')]
                complete_pools=bool(snapshots[4]) and all(v is not None for v in snapshots[4]['payload']['pools'].values())
                fresh_sources=all('存档' not in s['payload'].get('source','') for s in snapshots if s)
                if complete_pools and fresh_sources and all(s and s['collected_at'][:10]==day and s['collected_at'][11:16] >= ('16:30' if self.status=='archive_1630' else '15:30') for s in snapshots):
                    self.store.observe(archive_key,{'revisions':[s['id'] for s in snapshots]})
        return active

    def run(self):
        try:
            self.cycle(initial=True)
        except Exception as exc:
            self.store.event('scheduler',False,f'{type(exc).__name__}: {exc}')
        while not self.stop.is_set():
            try:
                self.cycle()
                changed = self.enrich_one()
                # Keep unpublished input revisions pending across cooldown ticks.
                if changed:self.pending_publish.update(('industry','concept'))
                if self.status.startswith('archive'):
                    for kind in tuple(self.pending_publish):
                        if self.job('board_'+kind,lambda k=kind:self.boards(k)):
                            build_rankings(self.store,datetime.now(TZ).date().isoformat())
                            self.pending_publish.discard(kind)
            except Exception as exc:
                self.store.event('scheduler',False,f'{type(exc).__name__}: {exc}')
            self.stop.wait(1)

    def start(self):
        self.thread = threading.Thread(target=self.run,daemon=True,name='sector-collector')
        self.thread.start()

    def shutdown(self):
        self.stop.set()


def metadata(snapshot, date, mode):
    if not snapshot:
        return dict(trade_date=date,mode=mode,source_time=None,collected_at=None,revision=None,rule_version='sector-v1.0',coverage=None,status='missing')
    return dict(trade_date=date,mode=mode,source_time=snapshot['source_time'],collected_at=snapshot['collected_at'],revision=snapshot['id'],
                rule_version=snapshot['rule_version'],coverage=snapshot['payload'].get('history_coverage'),status='available',sha256=snapshot['sha256'],
                ingestion_method=snapshot['payload'].get('ingestion_method','automatic'),source=snapshot['payload'].get('source','东方财富'))
