"""Public fallback transport. Tencent prices, Sina directory, Eastmoney F10 membership.

Membership uses the same Eastmoney BOARD_CODE; never fuzzy-matches provider names.
Every successful response is retained. Undated membership is attached only to the
verified current noon session or latest completed session; capture time remains
explicitly distinct from source time.
"""
import json
import math
import re
import time
from datetime import datetime

from .analytics import hs_stock, number
from .provider import Eastmoney, DataError, INDEX_SYMBOLS, quote_row, source_time
from .store import TZ, now_iso
from .taxonomy import sector_kind
from .ths import ThsConcepts


def tencent_rows(text, symbols):
    found={}
    for symbol, raw in re.findall(r'v_((?:sh|sz|bj)\d{6})="([^"]*)"',text):
        parts=raw.split('~')
        if len(parts)<49 or parts[2]!=symbol[2:]:
            raise DataError('腾讯行情字段或代码不符')
        stamp=parts[30]
        try:
            parsed=datetime.strptime(stamp,'%Y%m%d%H%M%S').replace(tzinfo=TZ).isoformat()
        except ValueError:
            parsed=None
        # Field 57 is precise CNY ten-thousands; field 37 is rounded.
        amount=number(parts[57]) if len(parts)>57 else number(parts[37])
        found[symbol]=dict(code=parts[2],name=parts[1],price=number(parts[3]),pre_close=number(parts[4]),
            change=number(parts[32]),amount=round(amount*10000,2) if amount is not None else None,
            float_cap_yi=number(parts[44]),pb=number(parts[46]),
            turnover=number(parts[38]),source_time=parsed,high=number(parts[33]),low=number(parts[34]),
            upper_limit=number(parts[47]),lower_limit=number(parts[48]),source='腾讯财经',quote_status='received')
    missing=set(symbols)-set(found)
    if missing:
        # A transport/page omission is not evidence that a security is suspended.
        raise DataError(f'腾讯行情缺少 {len(missing)} 个请求代码: {sorted(missing)[:3]}')
    return [found[s] for s in symbols]


class PublicData(Eastmoney):
    concept_source='ths'
    def __init__(self,store,target_date=None):
        super().__init__(store,delay=.25)
        self.target_date=target_date
        self.sources={}
        self._sina_codes=None
        self._circuit={}
        self._session_day=None
        self.session_mode='close'
        self.ths=ThsConcepts(store)

    def supports_flow(self,kind):
        # The public THS cash-flow page has no independently dated flow record.
        return kind!='concept'

    def begin_session(self,day,mode='close'):
        if mode not in ('close','midday'):
            raise DataError('未知采集时段')
        if mode=='midday':
            current=datetime.now(TZ)
            if (day!=current.date().isoformat() or day not in (self.store.observed('calendar') or [])
                    or not '12:00'<=current.strftime('%H:%M')<'13:00'):
                raise DataError('午间采集仅允许当日交易日12:00至13:00前')
        if day!=self._session_day or mode!=self.session_mode:
            self._sina_codes=None
            self._circuit.clear()
            self._session_day=day
        self.target_date=day
        self.session_mode=mode
        self.ths.session_mode=mode

    def valid_source(self,stamp,day):
        if not stamp or stamp[:10]!=day:
            return False
        clock=stamp[11:16]
        return '11:25'<=clock<'13:00' if self.session_mode=='midday' else clock>='15:00'

    def latest_close(self):
        now=datetime.now(TZ)
        days=self.store.observed('calendar') or []
        eligible=[d for d in days if d<now.date().isoformat() or (d==now.date().isoformat() and now.strftime('%H:%M')>='15:30')]
        if not eligible:
            raise DataError('无已验证的收盘交易日期')
        return max(eligible)

    def public_text(self,url,params=None,encoding='utf-8',cached=False):
        # Parent transport is bytes decoded as UTF-8. Tencent needs GB18030.
        import subprocess,time
        from urllib.parse import urlencode
        if params:url+='?'+urlencode(params)
        if cached:
            old=self.store.observed('public_raw:'+url)
            if old and (datetime.now(TZ)-datetime.fromisoformat(old['captured_at'])).total_seconds()<3600:
                return old['text']
        time.sleep(max(0,self.delay-(time.monotonic()-self.last_request)))
        self.last_request=time.monotonic()
        result=subprocess.run(['curl','--fail','-sS','--connect-timeout','5','--max-time','15','-A','Mozilla/5.0',url],capture_output=True,timeout=18)
        if result.returncode:
            raise DataError(result.stderr.decode(errors='replace')[:200])
        text=result.stdout.decode(encoding)
        self.store.observe('public_raw:'+url,{'source_url':url,'captured_at':now_iso(),'text':text})
        return text

    def directory(self):
        if self._sina_codes is not None:return self._sina_codes
        base='https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.'
        total=int(json.loads(self.public_text(base+'getHQNodeStockCount',{'node':'hs_a'})))
        if not 1000<total<10000:raise DataError('新浪全市场目录总数异常')
        rows=[]
        for page in range(1,math.ceil(total/80)+1):
            batch=json.loads(self.public_text(base+'getHQNodeData',dict(page=page,num=80,sort='symbol',asc=1,node='hs_a',symbol='',_s_r_a='page')))
            if not isinstance(batch,list):raise DataError('新浪目录分页格式异常')
            rows.extend(batch)
        if len(rows)!=total or len({r['symbol'] for r in rows})!=total:
            raise DataError('新浪目录数量或分页去重校验失败')
        if int(json.loads(self.public_text(base+'getHQNodeStockCount',{'node':'hs_a'})))!=total:
            raise DataError('新浪目录采集期间总数变化')
        self._sina_codes=[r['symbol'] for r in rows if hs_stock({'code':r['code']}) and r['symbol'].startswith(('sh','sz'))]
        self.store.observe('sina_universe:'+now_iso()[:10],self._sina_codes)
        return self._sina_codes

    def quotes(self,symbols):
        rows=[]
        for start in range(0,len(symbols),80):
            batch=symbols[start:start+80]
            raw=self.public_text('https://qt.gtimg.cn/q='+','.join(batch),encoding='gb18030')
            rows.extend(tencent_rows(raw,batch))
        return rows

    def stocks(self):
        # Direct public fallback is preferred while the primary list circuit is down.
        day=self.target_date or self.latest_close()
        symbols=set(self.directory())
        for kind in ('industry','concept'):
            for board in self.store.observed('catalog:'+sector_kind(kind)) or []:
                for code in self.store.observed('members:'+board['code']+':'+day) or []:
                    if hs_stock({'code':code}):symbols.add(('sh' if code.startswith(('60','68')) else 'sz')+code)
        rows=self.quotes(sorted(symbols))
        same=[r for r in rows if self.valid_source(r.get('source_time'),day)]
        if not same or len(same)/len(rows)<.95:
            raise DataError('腾讯行情日期、时段或覆盖率不符，保留原快照')
        # Explicit stale/invalid quotes stay available for coverage, never in breadth.
        valid_codes={r['code'] for r in same}
        for r in rows:
            if r['code'] not in valid_codes:
                r.update(price=None,change=None,amount=None,quote_status='stale_or_unavailable')
        self.sources['stocks']='腾讯财经行情 / 新浪财经目录'
        return rows

    def indices(self):
        rows=self.quotes(INDEX_SYMBOLS)
        stamp=source_time(rows)
        if not all(self.valid_source(r.get('source_time'),self.target_date or self.latest_close()) for r in rows):
            raise DataError('腾讯指数未对应所选交易日及采集时段')
        self.sources['indices']='腾讯财经'
        return rows

    def boards(self,kind):
        if kind=='concept':
            rows=self.ths.boards(self.target_date or self.latest_close(),fetch=not getattr(self,'ths_cached_only',False))
            self.sources['board_concept']='同花顺概念指数'
            return rows
        try:
            if time.monotonic()-self._circuit.get('boards:'+kind,float('-inf'))<300:raise DataError('主行情列表本轮已失败')
            rows=super().boards(kind)
            if not all(self.valid_source(r.get('source_time'),self.target_date or self.latest_close()) for r in rows):raise DataError('板块日期或时段不符')
            self.sources['board_'+kind]='东方财富行情'
            return rows
        except Exception as exc:
            if time.monotonic()-self._circuit.get('boards:'+kind,float('-inf'))>=300:self._circuit['boards:'+kind]=time.monotonic()
            day=self.target_date or self.latest_close()
            snap=self.store.snapshot(kind,day,self.session_mode)
            if not snap:
                return self.board_quotes(kind,day)
            self.sources['board_'+kind]='东方财富同日'+('午间' if self.session_mode=='midday' else '收盘')+'存档（报价未更新）'
            self.store.event('board_transport_'+kind,False,str(exc))
            return [dict(r) for r in snap['payload']['rows']]

    def board_quotes(self,kind,day):
        # A working individual quote endpoint can refresh an existing directory.
        # It cannot discover new boards; directory vintage remains explicit.
        catalog=self.store.observation_record('catalog:'+kind)
        if not catalog:raise DataError('没有板块目录，无法使用逐板块备用报价')
        rows=[]
        for old in catalog['payload']:
            code=old['code']
            raw=self.get('https://push2.eastmoney.com/api/qt/stock/get',dict(secid='90.'+code,
                fields='f57,f58,f43,f170,f48,f168,f60,f86',fltt=2)).get('data')
            if not raw or raw.get('f57')!=code or raw.get('f58','').strip()!=old['name'].strip():
                raise DataError('单板块报价代码或名称不匹配')
            row=quote_row(dict(f12=raw['f57'],f14=raw['f58'],f2=raw.get('f43'),f3=raw.get('f170'),
                f6=raw.get('f48'),f8=raw.get('f168'),f18=raw.get('f60'),f124=raw.get('f86')))
            if not self.valid_source(row['source_time'],day):
                raise DataError('单板块报价不是所选收盘日或午间时段')
            row['catalog_observed_at']=old.get('catalog_observed_at') or catalog['observed_at']
            rows.append(row)
        self.sources['board_'+kind]='东方财富逐板块'+('午间' if self.session_mode=='midday' else '收盘')+'报价 / 已存目录（新增板块待目录恢复）'
        return rows

    def flows(self,kind):
        if kind=='concept':
            raise DataError('同花顺概念资金流尚无可验证日期；不拼接东方财富资金')
        try:
            if time.monotonic()-self._circuit.get('flow:'+kind,float('-inf'))<300:raise DataError('资金列表本轮已失败')
            rows=super().flows(kind)
            if not rows or not all(self.valid_source(r.get('source_time'),self.target_date or self.latest_close()) for r in rows.values()):raise DataError('资金日期或时段不符')
            self.sources['flow_'+kind]='东方财富资金流'
            return rows
        except Exception as exc:
            if time.monotonic()-self._circuit.get('flow:'+kind,float('-inf'))>=300:self._circuit['flow:'+kind]=time.monotonic()
            snap=self.store.snapshot('flow_'+kind,self.target_date or self.latest_close(),self.session_mode)
            if not snap:raise
            self.sources['flow_'+kind]='东方财富同日资金存档（未更新）'
            self.store.event('flow_transport_'+kind,False,str(exc))
            return snap['payload']['rows']

    def membership_batch(self,catalog,day):
        if catalog and all(r['code'].startswith('THS') for r in catalog):
            verified=0
            for row in catalog:
                self.ths.members(row,day);verified+=1
            return {'boards':len(catalog),'verified':verified}
        current=datetime.now(TZ)
        midday=(self.session_mode=='midday' and day==current.date().isoformat()
                and day in (self.store.observed('calendar') or []) and '12:00'<=current.strftime('%H:%M')<'13:00')
        if not midday and day!=self.latest_close():
            raise DataError('F10 归属无历史日期接口，拒绝重建更早历史成分')
        cal=self.store.observed('calendar') or []
        if current.date().isoformat()>day and current.date().isoformat() in cal and current.strftime('%H:%M')>='09:15':
            raise DataError('当前归属已进入下一交易时段，不能回填上一交易日成分')
        expected={str(int(r['code'][2:])):r for r in catalog}
        rows=[];seen=set();total=None;pages=1;page=1
        while page<=pages:
            params=dict(reportName='RPT_F10_CORETHEME_BOARDTYPE',columns='SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_CODE,BOARD_NAME,IS_PRECISE,BOARD_RANK',
                source='WEB',client='WEB',filter='(BOARD_CODE in ("'+'","'.join(expected)+'"))',pageNumber=page,pageSize=500,
                sortColumns='BOARD_CODE,SECURITY_CODE',sortTypes='1,1')
            obj=json.loads(self.public_text('https://datacenter-web.eastmoney.com/api/data/v1/get',params,cached=True))
            result=obj.get('result')
            if not obj.get('success') or not result:raise DataError('F10 成分报告不可用')
            if total is None:total=int(result['count']);pages=int(result['pages'])
            if int(result['count'])!=total or int(result['pages'])!=pages or pages>1000:
                raise DataError('F10 分页总数改变或超限')
            for r in result['data']:
                board=str(int(r['BOARD_CODE']));code=str(r['SECURITY_CODE']);key=(board,code)
                if board not in expected or r['BOARD_NAME'].strip()!=expected[board]['name'].strip() or key in seen:
                    raise DataError('F10 代码、名称或分页唯一性不符')
                seen.add(key);rows.append(r)
            page+=1
        if len(rows)!=total:raise DataError('F10 成分分页数量不完整')
        grouped={r['code']:[] for r in catalog}
        for r in rows:grouped[expected[str(int(r['BOARD_CODE']))]['code']].append(str(r['SECURITY_CODE']))
        for board,members in grouped.items():
            record=next(r for r in catalog if r['code']==board)
            vendor=[record.get('vendor_'+k) for k in ('up','down','flat')]
            expected_count=sum(vendor) if all(x is not None for x in vendor) else None
            # Validate against same-session supplier universe before scoring.
            verified=(len(members)==expected_count if expected_count is not None else current.date().isoformat()==day)
            validation='目录数量核对' if expected_count is not None else '同日完整分页（无独立目录计数）'
            self.store.observe('members:'+board+':'+day,sorted(members))
            self.store.observe('members_meta:'+board+':'+day,dict(source='东方财富 F10',source_time=None,captured_at=now_iso(),
                session_date=day,session_mode=self.session_mode,classification='东方财富原始 BOARD_CODE',reported_count=len(members),expected_count=expected_count,verified=verified,validation=validation))
        self.sources['members']='东方财富 F10'
        return {'boards':len(grouped),'memberships':len(rows),'verified':sum((self.store.observed('members_meta:'+c+':'+day) or {}).get('verified',False) for c in grouped)}

    def members(self,code):
        day=self.target_date or self.latest_close()
        catalog=[r for kind in ('industry','concept') for r in self.store.observed('catalog:'+sector_kind(kind)) or [] if r['code']==code]
        if not catalog:raise DataError('没有已验证的成分目录')
        self.membership_batch(catalog,day)
        return [dict(code=c) for c in self.store.observed('members:'+code+':'+day)]

    def bars(self,code):
        if code.startswith('THS'):
            day=self.target_date or self.latest_close()
            row=next((r for r in self.ths.catalog(day) if r['code']==code),None)
            if not row:raise DataError('同花顺概念代码不在目录中')
            self.ths.quote(row,day)
            return self.store.observed('bars:'+code) or []
        return super().bars(code)
