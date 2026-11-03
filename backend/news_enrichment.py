"""Bounded public article excerpts. Exact hosts, pinned public IPs, no login bypass."""
import hashlib
import re
import socket
import time
from urllib.parse import urlsplit,urljoin
from bs4 import BeautifulSoup
import urllib3
from .news_network import public_addresses

PATHS={
    'www.federalreserve.gov':r'^/newsevents/pressreleases/[a-z]+\d+[a-z]?\.htm$',
    'www.cls.cn':r'^/detail/\d+$',
    'finance.eastmoney.com':r'^/a/\d+\.html$',
    'finance.sina.com.cn':r'^/(?:stock(?:/[a-z0-9_-]{1,30}){0,3}|roll|jjxw)/\d{4}-\d{2}-\d{2}/doc-[a-z0-9]+\.shtml$',
    'www.cnbc.com':r'^/\d{4}/\d{2}/\d{2}/[^/]+\.html$',
    'nvidianews.nvidia.com':r'^/news/[^/]+$',
    'blogs.nvidia.com':r'^/blog/[^/]+/?$',
}


def article_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in PATHS or p.username or p.password or p.port not in (None,443) or p.query or p.fragment or not re.fullmatch(PATHS[p.hostname],p.path):raise ValueError('article URL not allowed')
    return p


def fetch_article(url):
    for _ in range(3):
        p=article_url(url)
        addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
        ips=public_addresses(p.hostname,[x[4][0] for x in addresses])
        # Pin the checked address while retaining TLS SNI and hostname validation.
        with urllib3.HTTPSConnectionPool(ips[0],443,server_hostname=p.hostname,assert_hostname=p.hostname,cert_reqs='CERT_REQUIRED',timeout=urllib3.Timeout(connect=5,read=20)) as pool:
            response=pool.request('GET',p.path,headers={'Host':p.hostname,'User-Agent':'Mozilla/5.0','Referer':'https://'+p.hostname+'/'},redirect=False,preload_content=False,retries=False)
            try:
                if response.status in (301,302,303,307,308):url=urljoin(url,response.headers.get('Location',''));continue
                if response.status!=200:raise ValueError('article HTTP '+str(response.status))
                if 'text/html' not in response.headers.get('Content-Type',''):raise ValueError('article content type')
                raw=response.read(1_500_001)
                if len(raw)>1_500_000:raise ValueError('article too large')
                return raw,url
            finally:response.release_conn()
    raise ValueError('article redirect limit')


def extract_article(url,raw):
    p=article_url(url);soup=BeautifulSoup(raw,'html.parser')
    for node in soup(['script','style','nav','footer','header','aside','form']):node.decompose()
    selectors={
        'www.federalreserve.gov':['#article','.col-xs-12.col-sm-8.col-md-8'],
        'www.cls.cn':['.telegraph-content-box','.detail-content'],
        'finance.eastmoney.com':['#ContentBody'],
        'finance.sina.com.cn':['#artibody','#article'],
        'www.cnbc.com':['.ArticleBody-articleBody'],
        'nvidianews.nvidia.com':['.article-content','.content-container','article'],
        'blogs.nvidia.com':['.entry-content','article'],
    }
    root=next((soup.select_one(s) for s in selectors[p.hostname] if soup.select_one(s)),None)
    if root is None:raise ValueError('article body unavailable')
    text=root.get_text(' ',strip=True)
    if re.search(r'订阅后阅读|登录后阅读|付费阅读|sign in to continue|subscribe to read|访问验证|完成验证',text,re.I):raise ValueError('restricted article')
    paragraphs=[re.sub(r'\s+',' ',n.get_text(' ',strip=True)) for n in root.select('p,li,h2,h3')]
    if not paragraphs:paragraphs=[text]
    paragraphs=list(dict.fromkeys(x for x in paragraphs if len(x)>=25))
    # Select bounded evidence passages, never persist an entire HTML response.
    selected=[];size=0
    for i,line in enumerate(paragraphs):
        if i>2 and not re.search(r'\d|利率|投票|订单|产能|利润|供需|收入|预期|rate|vot|inflation|capacity|revenue|order|plan|demand',line,re.I):continue
        part=line[:900]
        if size+len(part)>6000 or len(selected)>=10:break
        selected.append(part);size+=len(part)
    if size<40:raise ValueError('insufficient article evidence')
    title=soup.title.get_text(' ',strip=True) if soup.title else ''
    return dict(status='ok',url=url,title=title[:300],excerpts=selected,content_hash=hashlib.sha256(raw).hexdigest(),scope='公开原文关键片段；非全文镜像')


def enrichment_due(n,reports):
    from .news import normalize_time
    from .store import now_iso
    saved={x['url']:x for x in n.enrichments(reports,normalize_time(now_iso()))}
    for report in reports:
        try:article_url(report['url'])
        except ValueError:continue
        old=saved.get(report['url'])
        if not old or (old['status']!='ok' and old.get('attempts',1)<3 and old.get('next_attempt',0)<=time.time()):return True
    return False


def enrich_event(n,reports):
    from .news import normalize_time
    from .store import now_iso
    cutoff=normalize_time(now_iso());saved=n.enrichments(reports,cutoff)
    byurl={x['url']:x for x in saved};attempted=0
    for report in sorted(reports,key=lambda r:r['source'] not in ('fed','nvidia')):
        if attempted>=2:break
        old=byurl.get(report['url'])
        if old and (old['status']=='ok' or old.get('attempts',1)>=3 or old.get('next_attempt',0)>time.time()):continue
        try:article_url(report['url'])
        except ValueError:continue
        attempted+=1
        try:
            raw,url=fetch_article(report['url']);payload=extract_article(url,raw);payload['url']=report['url'];payload['resolved_url']=url
        except Exception as exc:
            attempts=(old.get('attempts',1) if old else 0)+1
            payload=dict(status='unavailable',url=report['url'],excerpts=[],attempts=attempts,next_attempt=time.time()+300*2**attempts,error=type(exc).__name__+'：公开原文不可用，保留快讯证据；最多重试三次')
        n.save_enrichment(report,payload)
