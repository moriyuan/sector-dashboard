"""Fixed public source adapters. No browser credentials, arbitrary URLs, or full-text crawl."""
import json,os,re,socket
from urllib.parse import urlsplit,urljoin
from xml.etree import ElementTree as ET
import requests
from .news import clean, normalize_time
from .news_network import pinned_feed, public_addresses

SOURCES={
 'cls':dict(name='财联社电报',region='domestic',interval=120,url='https://www.cls.cn/api/cache?name=telegraph'),
 'em':dict(name='东方财富快讯',region='domestic',interval=120,url='https://np-weblist.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize=200&req_trace=1710315450384'),
 'sina':dict(name='新浪财经',region='domestic',interval=120,url='https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=100&zhibo_id=152&tag_id=0&dire=f&dpc=1&pagesize=100&type=1'),
 'cnbc':dict(name='CNBC World',region='overseas',interval=600,url='https://www.cnbc.com/id/100727362/device/rss/rss.html'),
 'nvidia':dict(name='NVIDIA 新闻室',region='overseas',interval=600,url='https://nvidianews.nvidia.com/releases.xml'),
 'fed':dict(name='美联储公告',region='overseas',interval=600,url='https://www.federalreserve.gov/feeds/press_all.xml')}
ALLOWED={urlsplit(x['url']).hostname for x in SOURCES.values()}|{'basic.10jqka.com.cn','money.finance.sina.com.cn'}
REFERERS={'www.cls.cn':'https://www.cls.cn/telegraph',
          'np-weblist.eastmoney.com':'https://kuaixun.eastmoney.com/'}

def fetch(url,headers=None):
 for _ in range(4):
  p=urlsplit(url)
  if p.scheme!='https' or p.hostname not in ALLOWED or p.username or p.port not in (None,443):raise ValueError('source URL not allowed')
  if p.hostname=='money.finance.sina.com.cn' and (p.query or p.fragment or not re.fullmatch(r'/corp/go[.]php/vCB_AllNewsStock/symbol/(?:sh6|sz[03])[0-9]{5}[.]phtml',p.path)):raise ValueError('stock listing URL not allowed')
  addresses=socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
  ips=public_addresses(p.hostname,[x[4][0] for x in addresses])
  request_headers={'User-Agent':'Mozilla/5.0',**(headers or {})}
  if p.hostname in REFERERS:request_headers['Referer']=REFERERS[p.hostname]
  if os.getenv('SECTOR_NEWS_PUBLIC_DNS')=='1':
   status,response_headers,body=pinned_feed(url,ips[0],request_headers,3_000_000)
   if status in (301,302,303,307,308):url=urljoin(url,response_headers['Location']);continue
   return body,dict(response_headers)
  with requests.get(url,headers=request_headers,timeout=(5,20),allow_redirects=False,stream=True) as r:
   if r.status_code in (301,302,303,307,308):url=urljoin(url,r.headers['Location']);continue
   if r.status_code==304:return None,dict(r.headers)
   r.raise_for_status();parts=[];size=0
   for part in r.iter_content(65536):
    size+=len(part)
    if size>3_000_000:raise ValueError('source too large')
    parts.append(part)
   return b''.join(parts),dict(r.headers)
 raise ValueError('redirect limit')

def parse(source,raw):
 cfg=SOURCES[source];out=[]
 def add(key,title,summary,date,url):
  out.append(dict(source=source,source_id=str(key),title=clean(title) or clean(summary)[:90],summary=clean(summary),published_at=normalize_time(date),url=url,region=cfg['region']))
 if source=='em':
  data=json.loads(raw)
  if str(data.get('code'))!='1':raise ValueError('Eastmoney source error')
  for r in data['data']['fastNewsList']:add(r['code'],r['title'],r['summary'],r['showTime'],f"https://finance.eastmoney.com/a/{r['code']}.html")
 elif source=='cls':
  data=json.loads(raw)
  if data.get('errno')!=0:raise ValueError('CLS source error')
  for r in data['data']['roll_data']:add(r['id'],r['title'],r['content'],r['ctime'],f"https://www.cls.cn/detail/{r['id']}")
 elif source=='sina':
  for r in json.loads(raw)['result']['data']['feed']['list']:add(r['id'],r.get('title',''),r['rich_text'],r['create_time'],f"https://finance.sina.com.cn/7x24/?tag=0&id={r['id']}")
 else:
  if b'<!ENTITY' in raw.upper() or b'<!DOCTYPE' in raw.upper():raise ValueError('DTD not allowed')
  root=ET.fromstring(raw)
  for r in root.findall('.//item'):
   url=r.findtext('link');add(r.findtext('guid') or url,r.findtext('title'),r.findtext('description'),r.findtext('pubDate'),url)
 if not out:raise ValueError('empty or changed source format')
 return out
