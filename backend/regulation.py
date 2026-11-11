"""Evidence-bound exchange risk watch. Public disclosures are not an exhaustive blacklist."""
import argparse
import hashlib
import json
import re
import subprocess
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader
from .store import Store, TZ, now_iso
from .daily_sources import stock_code, ANN_URL

SOURCES={
 'sse':dict(name='上交所新闻发布会',tier='exchange',url='https://www.sse.com.cn/aboutus/mediacenter/conference/'),
 'szse':dict(name='深交所监管动态',tier='exchange',url='https://www.szse.cn/disclosure/supervision/dynamic/'),
 'bse':dict(name='北交所监管动态',tier='exchange',url='https://www.bse.cn/disclosure/supervise_dynamic.html'),
 'issuer':dict(name='上市公司公告（东方财富索引）',tier='issuer',url='https://data.eastmoney.com/notices/hsa/3.html'),
 'hx':dict(name='华西证券官方风险提示',tier='broker',url='https://www.hx168.com.cn/main/ljhx/gsgg/index.shtml')}
ALLOWED={urlsplit(s['url']).hostname for s in SOURCES.values()}
PARSER='regulation-v1.1'
KINDS={'stricter':'从严交易监管提示','monitor':'重点监控','severe':'严重异常波动','suspension':'停牌核查','discipline':'交易监管措施'}
DATE=r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?'


def encode(obj):return json.dumps(obj,ensure_ascii=False,sort_keys=True,allow_nan=False)
def hash_obj(obj):return hashlib.sha256(encode(obj).encode()).hexdigest()
def dates_in(text):
    out=[]
    for match in re.finditer(DATE,text):
        try:out.append(date(*map(int,match.groups())).isoformat())
        except ValueError:pass
    return out


def parse_index(source,html):
    base=SOURCES[source]['url'];soup=BeautifulSoup(html,'html.parser');links={}
    if source=='szse':
        # Static published link variables, read as data; never evaluate supplier JavaScript.
        for href,title in re.findall(r"var curHref\s*=\s*'([^']+)';.*?var curTitle\s*=\s*'([^']+)';",html,re.S):
            dates=dates_in(title)
            if href.lower().endswith('.pdf') and dates:links[urljoin(base,href)]=dict(title=title,day=dates[-1])
    else:
        for a in soup.select('a[href]'):
            text=a.get_text(' ',strip=True);href=urljoin(base,a['href'])
            if source=='hx' and not (re.search('风险提示|风险警示|重点监控',text) and re.search(r'/main/a/\d{8}/\d+[.]shtml$',href)):continue
            if source=='sse' and '新闻发布会' not in text:continue
            if source=='bse' and not re.search('监管动态',text):continue
            if href==base:continue
            parent=a
            day=None
            for _ in range(4):
                if parent is None:break
                candidates=dates_in(parent.get_text(' ',strip=True))
                if candidates:day=candidates[0];break
                parent=parent.parent
            if source=='hx':
                m=re.search(r'/a/(20\d{6})/',href)
                if m:day=datetime.strptime(m[1],'%Y%m%d').date().isoformat()
            if day:links[href]=dict(title=text,day=day)
    if not links:raise ValueError('监管索引未解析到有效日期与链接')
    return [dict(v,url=k) for k,v in links.items()]


def fetch(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in ALLOWED or p.port not in (None,443) or p.username or p.password:raise ValueError('监管来源域名不允许')
    result=subprocess.run(['curl','--fail','--silent','--show-error','--connect-timeout','6','--max-time','15','--max-filesize','5000000',
        '-A','Mozilla/5.0','-e','https://'+p.hostname+'/',url],capture_output=True,timeout=20)
    if result.returncode or not result.stdout or len(result.stdout)>5_000_000:raise ValueError('监管来源获取失败')
    return result.stdout


def document_text(raw,source):
    if raw.startswith(b'%PDF'):
        reader=PdfReader(BytesIO(raw))
        if len(reader.pages)>30:raise ValueError('监管文件页数超限')
        text='\n'.join(p.extract_text() or '' for p in reader.pages)
        if len(re.sub(r'\s','',text))<50:raise ValueError('PDF无可核验文字')
        return re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])','',text)
    soup=BeautifulSoup(raw.decode('utf-8'),'html.parser')
    if source=='hx':node=soup.select_one('.article_cont')
    else:node=soup.select_one('.article-content, .article_content, .sse_article, #zoom, .content')
    if node is None:raise ValueError('监管正文结构未核验')
    for el in node.select('script,style'):el.decompose()
    return node.get_text('\n',strip=True)


def risk_kind(text):
    if re.search('解除重点监控|移出重点监控|结束重点监控',text):return 'monitor','released'
    if re.search('核查.{0,12}(?:结束|完成)|核查.{0,12}复牌',text):return 'suspension','released'
    stricter=bool(re.search(r'(?:上交所|深交所|北交所|交易所).{0,90}(?:从严认定|从严监管)',text))
    if '重点监控' in text and re.search('证券|股票|交易所|深交所|上交所|北交所',text):
        if re.search('可能.{0,8}重点监控|或被.{0,8}重点监控',text):return None
        if re.search('列为重点监控账户',text) and not re.search('股票.{0,16}重点监控|证券.{0,16}重点监控|重点监控证券',text):return ('stricter','notice') if stricter else None
        return 'monitor','notice'
    if stricter:return 'stricter','notice'
    if '严重异常波动' in text:return 'severe','notice'
    if '停牌核查' in text and not re.search('可能.{0,8}停牌核查',text):return 'suspension','notice'
    if re.search('限制交易|暂停交易',text) and re.search('本所决定|交易所决定',text):return 'discipline','notice'
    return None


def period_bounds(text):
    start=end=None
    m=re.search(r'(?:重点监控起始日期|重点监控开始日期)(?:为|是|：|:)?\s*('+DATE+')',text)
    if m:start=dates_in(m[1])[0]
    m=re.search(r'重点监控(?:结束日期|结束时间)(?:为|是|：|:)?\s*('+DATE+')',text)
    if m:end=dates_in(m[1])[0]
    if not start and not end:
        m=re.search(r'重点监控期间(?:为|：|:)?\s*('+DATE+r')\s*(?:至|到|—|~)\s*('+DATE+')',text)
        if m:
            dates=dates_in(m[0]);start,end=dates[0],dates[-1]
    if start and end and end<start:raise ValueError('监控期间反序')
    return start,end


def extract_records(doc,text,directory):
    records=[]
    # Keep a named company and the applicable action within the same paragraph/sentence.
    paragraphs=[re.sub(r'\s+',' ',p).strip() for p in re.split(r'[\n。；]+',text) if p.strip()]
    name_codes={}
    for code,name in directory.items():name_codes.setdefault(name,[]).append(code)
    for index,paragraph in enumerate(paragraphs):
        action=risk_kind(paragraph)
        if not action:continue
        context=paragraph
        if re.search('上述|该股票|该证券',paragraph) and index>0:context=paragraphs[index-1]+'。'+paragraph
        named={}
        # Explicit codes with adjacent short names in the issuer/broker disclosure.
        for name,code in re.findall(r'[“\"「]?([*A-Za-z\u4e00-\u9fff]{2,16})[”\"」]?\s*[（(]\s*(?:证券代码[:：]?\s*)?(\d{6})\s*[)）]',context):
            if stock_code(code):named[code]=directory.get(code) or name
        # Exchange weekly reports often quote names without codes: require unambiguous directory match.
        for name in re.findall(r'[“\"「]([^”\"」]{2,20})[”\"」]',context):
            codes=name_codes.get(name,[])
            if len(codes)==1:named[codes[0]]=name
        if doc['source']=='hx' and re.search('上述',paragraph):
            prior='。'.join(paragraphs[:index])
            for name,code in re.findall(r'[“\"「]?([*A-Za-z\u4e00-\u9fff]{2,16})[”\"」]?\s*[（(]\s*(?:证券代码[:：]?\s*)?(\d{6})\s*[)）]',prior):
                if stock_code(code):named[code]=directory.get(code) or name
        if not named:continue
        start,end=period_bounds(context)
        for code,name in named.items():
            cited=context
            if code not in cited and name not in cited:
                own=next((p for p in paragraphs[:index] if code in p), '')
                cited=own+'。'+paragraph
            row=dict(code=code,name=name,kind=action[0],action=action[1],published_day=doc['day'],title=doc['title'],url=doc['url'],
                source=doc['source'],source_name=SOURCES[doc['source']]['name'],tier=SOURCES[doc['source']]['tier'],
                start=start,end=end,excerpt=cited[:1200],document_hash=doc['sha256'],captured_at=doc['captured_at'])
            row['id']=hash_obj([doc['sha256'],PARSER,code,action]);records.append(row)
    unique={}
    for r in records:
        old=unique.get(r['id'])
        if old is None or (bool(r['end']),len(r['excerpt']))>(bool(old['end']),len(old['excerpt'])):unique[r['id']]=r
    return list(unique.values())


class RegulationStore:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:db.executescript('''
        CREATE TABLE IF NOT EXISTS daily_risk_documents(id TEXT PRIMARY KEY,source TEXT,url TEXT,published_day TEXT,captured_at TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS daily_risks(id TEXT PRIMARY KEY,code TEXT,kind TEXT,published_day TEXT,captured_at TEXT,payload TEXT);
        CREATE INDEX IF NOT EXISTS daily_risk_day ON daily_risks(published_day,code);
        ''')
    def seen(self,url):
        with self.store.connect() as db:return bool(db.execute("SELECT 1 FROM daily_risk_documents WHERE url=? AND json_extract(payload,'$.parser')=?",(url,PARSER)).fetchone())
    def save(self,doc,records):
        doc=dict(doc,parser=PARSER)
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO daily_risk_documents VALUES(?,?,?,?,?,?)',(hash_obj([doc['sha256'],PARSER]),doc['source'],doc['url'],doc['day'],doc['captured_at'],encode(doc)))
            for row in records:
                db.execute('INSERT OR IGNORE INTO daily_risks VALUES(?,?,?,?,?,?)',(row['id'],row['code'],row['kind'],row['published_day'],row['captured_at'],encode(row)))
    def directory(self):
        with self.store.connect() as db:r=db.execute("SELECT payload FROM snapshots WHERE kind='stocks' ORDER BY id DESC LIMIT 1").fetchone()
        return {r['code']:r['name'] for r in json.loads(r[0]).get('rows',[])} if r else {}
    def listing(self,day):
        # Event date, not today's list membership. Later documents never leak into prior dates.
        cutoff=(date.fromisoformat(day)-timedelta(days=60)).isoformat()
        with self.store.connect() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM daily_risks WHERE published_day<=? AND published_day>=? ORDER BY published_day DESC,captured_at DESC',(day,cutoff))]
            docs=[dict(r) for r in db.execute('SELECT source,MAX(published_day) latest,COUNT(DISTINCT url) documents FROM daily_risk_documents WHERE published_day<=? GROUP BY source',(day,))]
        grouped={}
        unique={}
        for r in rows:
            k=(r['document_hash'],r['code'],r['kind'])
            old=unique.get(k)
            if old is None or (bool(r['end']),len(r['excerpt']))>(bool(old['end']),len(old['excerpt'])):unique[k]=r
        for r in unique.values():grouped.setdefault((r['code'],r['kind']),[]).append(r)
        results=[]
        for (code,kind),events in grouped.items():
            # Most recent disclosure; equal-day exchange original precedes a broker relay.
            events.sort(key=lambda r:(r['published_day'],r['tier']=='exchange',r['captured_at']),reverse=True)
            latest=events[0];start,end=latest['start'],latest['end']
            if latest['action']=='released':status='披露已解除 / 复牌；后续状态需核对'
            elif kind=='stricter':status='披露从严监管；非重点监控期限认定'
            elif kind=='severe':status='已披露严重异常波动；不等于重点监控'
            elif kind=='suspension':status='已披露停牌核查；当前交易状态待核验'
            elif start and day<start:status='披露尚未起始'
            elif end and day>end:status='已过披露期限；解除 / 续期待核验'
            elif end and (not start or day>=start):status='披露监控期限内'
            else:status='期限未披露；当前状态待核验'
            results.append(dict(latest,kind_name=KINDS[kind],status=status,events=events,backfilled=latest['captured_at'][:10]>day))
        return dict(rows=results,documents=docs,coverage='近60日已收录公开披露，不是交易所完整实时名单。普通盘口异动、龙虎榜和严重异动不自动视为重点监控。',day=day)


def hx_index(day):
    # Public list module queryArticleList (funcNo 741000), catalog 7; no login or cookies.
    cutoff=(date.fromisoformat(day)-timedelta(days=45)).isoformat();links={};total=None
    for page in range(1,11):
        params=f'funcNo=741000&catalogId=7&curtPageNo={page}&numPerPage=100&key_word='
        result=subprocess.run(['curl','--fail','--silent','--show-error','--connect-timeout','6','--max-time','15','--max-filesize','3000000',
            '-A','Mozilla/5.0','-e',SOURCES['hx']['url'],'--data',params,'https://www.hx168.com.cn/servlet/json'],capture_output=True,timeout=20)
        if result.returncode:raise ValueError('券商公告索引请求失败')
        obj=json.loads(result.stdout)
        if str(obj.get('error_no'))!='0':raise ValueError('券商公告索引失败')
        data=obj['results'][0];rows=data['data']
        if total is not None and total!=int(data['totalRows']):raise ValueError('券商公告分页变动')
        total=int(data['totalRows'])
        for r in rows:
            published=str(r['publish_date'])[:10];date.fromisoformat(published)
            if re.search('风险提示|重点监控',r['title']) and cutoff<=published<=day:
                url=urljoin(SOURCES['hx']['url'],r['url'])
                if urlsplit(url).hostname!='www.hx168.com.cn':raise ValueError('券商公告跨域链接')
                links[url]=dict(title=r['title'],day=published,url=url)
        if page>=int(data['totalPages']) or rows and min(str(r['publish_date'])[:10] for r in rows)<cutoff:break
        if not rows:raise ValueError('券商公告分页缺失')
    else:raise ValueError('券商公告超出分页上限')
    return list(links.values())


def collect_source(risk,source,day,fetcher=fetch):
    if source=='issuer':return collect_issuers(risk,day)
    if source=='hx':links=hx_index(day)
    else:
        raw=fetcher(SOURCES[source]['url']);links=parse_index(source,raw.decode('utf-8'))
    cutoff=(date.fromisoformat(day)-timedelta(days=45)).isoformat();directory=risk.directory();count=0;errors=0
    for link in links:
        if not cutoff<=link['day']<=day or risk.seen(link['url']):continue
        try:
            raw=fetcher(link['url']);text=document_text(raw,source)
            doc=dict(link,source=source,sha256=hashlib.sha256(raw).hexdigest(),captured_at=now_iso())
            records=extract_records(doc,text,directory)
            # Retain only necessary risk paragraphs plus the document fingerprint, not a news mirror.
            doc['excerpts']=[r['excerpt'] for r in records];doc['stocks']=len(records)
            risk.save(doc,records);count+=len(records)
        except Exception:errors+=1
    return dict(ok=errors==0,last_attempt=now_iso(),last_success=now_iso() if not errors else None,new_records=count,failed_documents=errors,
        index_items=len(links),coverage='已扫描当前索引，非完整名单；无法解析的原文不生成股票风险标签')


def collect_issuers(risk,day):
    from .daily_sources import DailyProvider
    provider=DailyProvider(risk.store)
    start=(date.fromisoformat(day)-timedelta(days=7)).isoformat()
    end=(date.fromisoformat(day)+timedelta(days=1)).isoformat()
    seen=set();count=0;total=None
    for page in range(1,41):
        obj=provider.get(ANN_URL,dict(sr=-1,page_size=100,page_index=page,ann_type='A',client_source='web',f_node=3,s_node=0,begin_time=start,end_time=end))
        if obj.get('success') not in (1,True):raise ValueError('风险公告接口失败')
        data=obj['data']
        if total is not None and total!=int(data['total_hits']):raise ValueError('风险公告分页变动')
        total=int(data['total_hits'])
        for item in data['list']:
            art=item['art_code'];seen.add(art);title=item['title'];kind=risk_kind(title)
            if not kind or not re.fullmatch(r'AN\d+',art):continue
            publication=str(item.get('display_time') or item['notice_date'])[:10]
            date.fromisoformat(publication)
            if publication>day:continue
            for c in item['codes']:
                code=c['stock_code']
                if not c.get('ann_type','').startswith('A') or not stock_code(code):continue
                url=f'https://data.eastmoney.com/notices/detail/{code}/{art}.html'
                if risk.seen(url):continue
                doc=dict(source='issuer',url=url,title=title,day=publication,captured_at=now_iso(),sha256=hash_obj([item,code]))
                row=dict(code=code,name=c['short_name'],kind=kind[0],action=kind[1],published_day=publication,title=title,url=url,
                    source='issuer',source_name=SOURCES['issuer']['name'],tier='issuer',start=None,end=None,
                    excerpt=title+'（仅依据公告标题分类；未据此认定为重点监控或核验当前停复牌状态。）',document_hash=doc['sha256'],captured_at=doc['captured_at'])
                row['id']=hash_obj(row);doc['stocks']=1;doc['excerpts']=[row['excerpt']];risk.save(doc,[row]);count+=1
        if page*100>=total:break
    else:raise ValueError('风险公告分页超过上限')
    if len(seen)!=total:raise ValueError('风险公告分页重复或缺失')
    return dict(ok=True,last_attempt=now_iso(),last_success=now_iso(),new_records=count,index_items=total,coverage='风险公告类别最近七日；仅按明确标题分类，不代表完整监管名单')


def collect_risk_due(ds,now=None,force=False):
    now=now or datetime.now(TZ);day=now.date().isoformat();risk=RegulationStore(ds.store)
    for source in SOURCES:
        key='risk:'+source;old=ds.states(day).get(key,{});stamp=old.get('last_attempt')
        if not force and stamp and (now-datetime.fromisoformat(stamp)).total_seconds()<3600:continue
        try:state=collect_source(risk,source,day)
        except Exception as exc:state=dict(old,ok=False,last_attempt=now_iso(),error=type(exc).__name__)
        if not state.get('last_success') and old.get('last_success'):state['last_success']=old['last_success']
        ds.state(day,key,state)


if __name__=='__main__':
    from .daily import DailyStore
    p=argparse.ArgumentParser();p.add_argument('--db',default='runtime/market.sqlite3');args=p.parse_args()
    ds=DailyStore(Store(args.db));collect_risk_due(ds,force=True)
    result=RegulationStore(ds.store).listing(datetime.now(TZ).date().isoformat())
    print(encode(dict(rows=[dict(code=r['code'],name=r['name'],kind=r['kind'],status=r['status']) for r in result['rows']],sources=ds.states(datetime.now(TZ).date().isoformat()))))
