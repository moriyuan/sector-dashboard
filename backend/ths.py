"""Public THS concept pages and dated index candles. No login/cookie extraction."""
import json
import re
import subprocess
import threading
import time
from datetime import datetime

from bs4 import BeautifulSoup

from .analytics import number
from .provider import DataError
from .store import TZ, now_iso


def soup(text):
    page = BeautifulSoup(text, 'html.parser')
    if not page.select_one('table, #clid, #gnSection'):
        raise DataError('同花顺返回验证页或非行情页面；本轮停止，不绕过验证')
    return page


def pagination(page):
    el = page.select_one('.page_info')
    match = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', el.text) if el else None
    if not match or not 1 <= int(match[1]) <= int(match[2]) <= 1000:
        raise DataError('同花顺分页信息缺失或异常')
    return int(match[1]), int(match[2])


def catalog_page(text):
    page = soup(text)
    current, total = pagination(page)
    rows = []
    for tr in page.select('table tbody tr'):
        cells = tr.find_all('td')
        if len(cells) != 5:
            raise DataError('同花顺概念目录字段变化')
        link = cells[1].find('a', href=True)
        match = re.search(r'/gn/detail/code/(\d{6})/', link['href']) if link else None
        count = number(cells[4].get_text(strip=True))
        if not match or count is None or count < 0 or count != int(count):
            raise DataError('同花顺概念代码或成分数量异常')
        rows.append(dict(code='THS'+match[1], name=link.get_text(strip=True),
                         ths_cid=match[1], expected_count=int(count), taxonomy='ths'))
    if not rows:
        raise DataError('同花顺概念目录为空')
    return current, total, rows


def index_code(page, cid):
    query = page.select_one('#requestQuery')
    clid = page.select_one('#clid')
    if not query or query.get('value') != 'code/'+cid or not clid or not re.fullmatch(r'\d{6}', clid.get('value','')):
        raise DataError('同花顺概念与指数代码映射不符')
    return clid['value']


def detail_page(text, cid, expected_count=None):
    page = soup(text)
    index = index_code(page, cid)
    single_page = not page.select_one('.page_info') and expected_count is not None and 1 <= expected_count <= 10
    current, total = (1, 1) if single_page else pagination(page)
    members = []
    ordinals = []
    for tr in page.select('table.m-pager-table tbody tr'):
        cells = tr.find_all('td')
        if len(cells) < 3:
            raise DataError('同花顺成分表字段缺失')
        code = cells[1].get_text(strip=True)
        ordinal = number(cells[0].get_text(strip=True))
        if not re.fullmatch(r'\d{6}',code) or ordinal is None or int(ordinal) != ordinal:
            raise DataError('同花顺成分代码或序号异常')
        members.append(code); ordinals.append(int(ordinal))
    if not members or len(members)>10 or (current<total and len(members)!=10) or ordinals != list(range((current-1)*10+1,(current-1)*10+1+len(members))):
        raise DataError('同花顺成分分页序号不连续')
    if single_page and len(members)!=expected_count:
        raise DataError('同花顺无分页成分数量与目录不符')
    infos = {}
    for dl in page.select('.board-infos dl'):
        dt, dd = dl.find('dt'), dl.find('dd')
        if dt and dd: infos[dt.get_text(strip=True)] = dd.get_text(' ',strip=True)
    return dict(index_code=index, current=current, pages=total, members=members, infos=infos)


def funds_catalog_page(text):
    """The public concept funds directory lists 50 concepts per page, by CID."""
    page=soup(text)
    base=page.select_one('#baseUrl')
    if not base or base.get('value')!='funds/gnzjl':raise DataError('不是同花顺概念目录')
    current,total=pagination(page)
    rows=[]
    for i,tr in enumerate(page.select('table tbody tr')):
        cells=tr.find_all('td')
        if len(cells)!=11:raise DataError('同花顺概念目录字段变化')
        link=cells[1].find('a',href=True)
        match=re.search(r'/gn/detail/code/(\d{6})/',link['href']) if link else None
        count=number(cells[7].get_text(strip=True))
        if not match or count is None or count<0 or count!=int(count):raise DataError('同花顺目录代码或数量异常')
        if number(cells[0].get_text(strip=True))!=(current-1)*50+i+1:raise DataError('同花顺概念目录序号不连续')
        rows.append(dict(code='THS'+match[1],name=link.get_text(strip=True),ths_cid=match[1],
                         expected_count=int(count),taxonomy='ths'))
    if not rows:raise DataError('同花顺概念目录为空')
    return current,total,rows


def jsonp(text):
    match = re.fullmatch(r'\s*[A-Za-z_$][\w$]*\s*\((\{.*\})\)\s*;?\s*',text,re.S)
    if not match:
        raise DataError('同花顺指数响应不是预期 JSONP')
    try: return json.loads(match[1])
    except ValueError as exc: raise DataError('同花顺指数 JSON 无效') from exc


def parse_bars(text):
    obj = jsonp(text)
    result = []
    for line in obj.get('data','').split(';'):
        if not line: continue
        fields = line.split(',')
        if len(fields)<7: raise DataError('同花顺日线字段不足')
        try: day=datetime.strptime(fields[0],'%Y%m%d').date().isoformat()
        except ValueError as exc: raise DataError('同花顺日线日期无效') from exc
        values=[number(v) for v in fields[1:7]]
        if any(v is None for v in values) or min(values[:4])<=0 or min(values[4:])<0:
            raise DataError('同花顺日线值无效')
        # v4 columns: date, open, high, low, close, shares, CNY amount.
        result.append(dict(date=day,open=values[0],high=values[1],low=values[2],close=values[3],amount=values[5]))
    if not result or len({r['date'] for r in result})!=len(result):
        raise DataError('同花顺日线为空或日期重复')
    return sorted(result,key=lambda r:r['date'])


def today_quote(text,index_code,name,day):
    obj=jsonp(text).get('bk_'+index_code)
    if not obj or obj.get('name')!=name:
        raise DataError('同花顺指数代码或名称不符')
    if str(obj.get('1'))!=day.replace('-',''):
        raise DataError('同花顺指数实际日期与目标日不符')
    clock=str(obj.get('dt',''))
    if not re.fullmatch(r'\d{4}',clock) or not '1500'<=clock<='2359' or int(clock[2:])>=60:
        raise DataError('同花顺指数尚无可验证的收盘源时间')
    vals={key:number(obj.get(field)) for key,field in [('open','7'),('high','8'),('low','9'),('close','11'),('amount','19')]}
    if any(v is None for v in vals.values()) or min(vals[k] for k in ('open','high','low','close'))<=0 or vals['amount']<0:
        raise DataError('同花顺收盘行情字段无效')
    return dict(date=day,**vals),day+'T'+clock[:2]+':'+clock[2:]+':00+08:00'


class ThsConcepts:
    def __init__(self,store):
        self.store=store
        self.lock=threading.Lock()
        self.last=0.0

    @staticmethod
    def cutoff(day):
        now=datetime.now(TZ)
        return day+('T16:30' if now.date().isoformat()>day or now.strftime('%H:%M')>='16:30' else 'T15:30')

    def read(self,url,day):
        old=self.store.observed('ths_raw:'+url)
        # A capture after today's close may be reused; never morning/yesterday.
        if old and old['captured_at'][:10]==day and old['captured_at']>=self.cutoff(day):
            return old['text']
        with self.lock:
            time.sleep(max(0,.2-(time.monotonic()-self.last)))
            self.last=time.monotonic()
        result=subprocess.run(['curl','--noproxy','*','--fail','--silent','--show-error','--location',
            '--connect-timeout','5','--max-time','15','-A','Mozilla/5.0','-e','https://q.10jqka.com.cn/',url],capture_output=True,timeout=18)
        if result.returncode: raise DataError(result.stderr.decode(errors='replace')[:200])
        text=result.stdout.decode('gb18030',errors='replace')
        if 'chameleon' in text and len(text)<2000:
            raise DataError('同花顺访问验证页；保留旧快照并退避')
        self.store.observe('ths_raw:'+url,dict(source_url=url,captured_at=now_iso(),text=text))
        return text

    def catalog(self,day):
        cached=self.store.observed('ths_catalog:'+day)
        checked=self.store.observed('ths_catalog_checked:'+day)
        if cached and checked and checked>=self.cutoff(day) and self.store.observed('ths_catalog_schema:'+day)==2:return cached
        rows=[];pages=None
        for page in range(1,1001):
            url='https://data.10jqka.com.cn/funds/gnzjl/' if page==1 else f'https://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/page/{page}/'
            current,total,batch=funds_catalog_page(self.read(url,day))
            if current!=page or (pages is not None and total!=pages):
                raise DataError('同花顺目录分页改变或返回了错误页面')
            pages=total
            if page<pages and len(batch)!=50:raise DataError('同花顺目录中间页不完整')
            rows.extend(batch)
            if page==pages:break
        if len({r['code'] for r in rows})!=len(rows):raise DataError('同花顺目录重复概念代码')
        # The funds directory omits some concepts (e.g. no fund-flow coverage).
        # Merge only official CID links, never fuzzy names or Eastmoney mappings.
        homepage=self.read('https://q.10jqka.com.cn/gn/',day)
        home=soup(homepage)
        mapping={r['code']:r for r in rows}
        for link in home.select('.cate_inner a[href]'):
            match=re.search(r'/gn/detail/code/(\d{6})/',link['href'])
            if not match:raise DataError('同花顺目录导航代码异常')
            code='THS'+match[1]
            mapping.setdefault(code,dict(code=code,name=link.get_text(strip=True),ths_cid=match[1],expected_count=None,taxonomy='ths'))
        for row in catalog_page(homepage)[2]:
            if row['code'] not in mapping or mapping[row['code']]['expected_count'] is None:mapping[row['code']]=row
        rows=list(mapping.values())
        self.store.observe('ths_catalog:'+day,rows)
        self.store.observe('ths_catalog_checked:'+day,now_iso())
        self.store.observe('ths_catalog_schema:'+day,2)
        self.store.observe('catalog:concept_ths',rows)
        return rows

    def detail(self,row,day,page=1):
        cid=row['ths_cid']
        url=f'https://q.10jqka.com.cn/gn/detail/code/{cid}/'+(f'page/{page}/' if page>1 else '')
        result=detail_page(self.read(url,day),cid,row.get('expected_count'))
        if result['current']!=page:raise DataError('同花顺成分返回了错误页码')
        return result

    def quote(self,row,day):
        cached=self.store.observed('ths_quote:'+row['code']+':'+day)
        if cached and cached.get('captured_at','')>=self.cutoff(day) and cached.get('name')==row['name']:return dict(cached,**row)
        page=soup(self.read(f"https://q.10jqka.com.cn/gn/detail/code/{row['ths_cid']}/",day))
        index=index_code(page,row['ths_cid'])
        bar,stamp=today_quote(self.read(f'https://d.10jqka.com.cn/v4/line/bk_{index}/01/today.js',day),index,row['name'],day)
        recent=self.read(f'https://d.10jqka.com.cn/v4/line/bk_{index}/01/last.js',day)
        history=parse_bars(recent)
        years=jsonp(recent).get('year',{})
        if len(history)<251:
            for year in (int(day[:4])-1,int(day[:4])):
                if str(year) in years:
                    try:history.extend(parse_bars(self.read(f'https://d.10jqka.com.cn/v4/line/bk_{index}/01/{year}.js',day)))
                    except DataError as exc:self.store.event('ths_history:'+row['code'],False,str(exc))
        bars={b['date']:b for b in history if b['date']<=day};bars[day]=bar
        bars=sorted(bars.values(),key=lambda b:b['date'])
        for i,b in enumerate(bars):b['change']=(b['close']/bars[i-1]['close']-1)*100 if i else None
        previous=[b for b in bars if b['date']<day]
        if not previous:raise DataError('同花顺缺少前收盘点位')
        cal=self.store.observed('calendar') or []
        expected=max((d for d in cal if d<day),default=None)
        if expected and previous[-1]['date']!=expected:raise DataError('同花顺前收盘日不连续')
        self.store.observe('bars:'+row['code'],bars[-251:])
        result=dict(row,index_code=index,price=bar['close'],change=bar['change'],amount=bar['amount'],captured_at=now_iso(),
                    pre_close=previous[-1]['close'],turnover=None,source_time=stamp,source_time_precision='minute',
                    vendor_up=None,vendor_down=None,vendor_flat=None,quote_status='received')
        self.store.observe('ths_quote:'+row['code']+':'+day,result)
        return result

    def members(self,row,day):
        now=datetime.now(TZ)
        calendar=self.store.observed('calendar') or []
        eligible=[d for d in calendar if d<now.date().isoformat() or (d==now.date().isoformat() and now.strftime('%H:%M')>='15:30')]
        if not eligible or day!=max(eligible) or (now.date().isoformat()>day and now.date().isoformat() in calendar and now.strftime('%H:%M')>='09:15'):
            raise DataError('同花顺无历史成分接口，拒绝重建历史归属')
        old=self.store.observed('members_meta:'+row['code']+':'+day)
        if old and old.get('verified') and old.get('expected_count')==row['expected_count']:
            return self.store.observed('members:'+row['code']+':'+day)
        first=self.detail(row,day);members=list(first['members'])
        for page in range(2,first['pages']+1):
            batch=self.detail(row,day,page)
            if batch['pages']!=first['pages'] or batch['index_code']!=first['index_code']:
                raise DataError('同花顺成分分页总数或指数映射变化')
            if page<first['pages'] and len(batch['members'])!=10:raise DataError('同花顺成分中间页不足')
            members.extend(batch['members'])
        if (row['expected_count'] is not None and len(members)!=row['expected_count']) or len(set(members))!=len(members):
            raise DataError('同花顺成分数量与目录不符或分页重复')
        self.store.observe('members:'+row['code']+':'+day,sorted(members))
        self.store.observe('members_meta:'+row['code']+':'+day,dict(source='同花顺概念成分',source_time=None,
            captured_at=now_iso(),session_date=day,classification='同花顺概念 CID',reported_count=len(members),
            expected_count=row['expected_count'],verified=True,validation='完整分页、唯一代码'+('及同日概念目录数量核对' if row['expected_count'] is not None else '；无独立目录数量')))
        return members

    def boards(self,day,fetch=True):
        rows=[]
        for row in self.catalog(day):
            try:
                quote=self.quote(row,day) if fetch else self.store.observed('ths_quote:'+row['code']+':'+day)
                if not quote:raise DataError('同花顺该概念报价未采集')
                quote=dict(quote,**row)
                if not fetch and quote.get('captured_at','')<self.cutoff(day):quote['quote_status']='stale'
                rows.append(quote)
            except Exception as exc:
                self.store.event('ths_quote:'+row['code'],False,str(exc))
                old=self.store.observed('ths_quote:'+row['code']+':'+day)
                if old and old.get('source_time','').startswith(day+'T'):
                    rows.append(dict(old,quote_status='stale'))
                else:
                    rows.append(dict(row,price=None,change=None,amount=None,pre_close=None,turnover=None,
                        source_time=None,vendor_up=None,vendor_down=None,vendor_flat=None,quote_status='missing'))
        if not any(r['source_time'] for r in rows):raise DataError('同花顺概念收盘报价全部不可用')
        return rows
