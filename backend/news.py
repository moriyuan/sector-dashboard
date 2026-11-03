"""Versioned public news, evidence-bound associations and conservative model accounting."""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from bs4 import BeautifulSoup
from .store import now_iso, TZ
from .news_research import ResearchStore, VERSION, detect_themes, term_match, research_priority, conflicts, validate_research

THEMES={'ai':('AI／算力',['ai','data center','data centre','人工智能','算力','大模型','英伟达','nvidia','artificial intelligence','openai']), 'chips':('半导体',['半导体','芯片','晶圆','光刻','semiconductor','chip','tsmc']), 'robot':('机器人',['具身智能','机器人','人形','robot','robots','robotics','humanoid']), 'auto':('汽车',['汽车','智能驾驶','比亚迪','electric vehicle','tesla','automotive']), 'energy':('新能源',['光伏','储能','锂电','新能源','solar','battery','renewable']), 'health':('医药',['医药','药品','临床','医疗','glp-1','pharma','clinical','fda']), 'consumer':('消费',['消费','零售','白酒','食品','retail','consumer']), 'resources':('资源品',['有色','矿业','铜','黄金','铜矿','原油','稀土','大宗商品','oil','copper','gold','commodity'])}

AMBIGUOUS_NAMES={'机器人','中国动力','中国软件','中国医药','中国卫星','中国电信','中国移动','中国联通','中国建筑','中国中铁','航天科技','航天信息','航天发展','中科信息'}


def clean(text):
 soup=BeautifulSoup(str(text or ''),'html.parser')
 for el in soup(['script','style']):el.decompose()
 return re.sub(r'\s+',' ',soup.get_text(' ',strip=True)).strip()

def normalize_time(value):
 if isinstance(value,(int,float)):dt=datetime.fromtimestamp(value,timezone.utc)
 else:
  try:dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
  except ValueError:dt=parsedate_to_datetime(str(value))
 if dt.tzinfo is None:dt=dt.replace(tzinfo=TZ)
 return dt.astimezone(timezone.utc).isoformat(timespec='seconds')

def canonical(url):
 p=urlsplit(url)
 if p.scheme not in ('http','https') or p.username or p.password:raise ValueError('invalid public link')
 return urlunsplit((p.scheme,p.netloc.lower(),p.path,urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.startswith('utm_')]),''))

def digest(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def leaders(rows):
 rows=[r for r in rows if not r.get('is_st') and 'ST' not in r.get('name','').upper() and (r.get('amount') or 0)>0]
 def top(key,positive=False):
  valid=sorted([r for r in rows if r.get(key) is not None and (not positive or r[key]>0)],key=lambda r:(-r[key],r['code']))
  return [r for r in valid if len(valid)<3 or r[key]>=valid[min(2,len(valid)-1)][key]]
 return {'boards':top('boards',True),'gainers':top('change',True),'turnover':top('amount',True),'scope':'已核验样本内排名；成交核心不等于上涨龙头'}

def validate_analysis(data,codes,evidence,allowed_links):
 if not isinstance(data,dict) or not all(isinstance(data.get(k),str) and 0<len(data[k])<=3000 for k in ('title_zh','summary')):raise ValueError('invalid summary')
 if data.get('impact') not in ('positive','negative','mixed','unknown'):raise ValueError('invalid impact')
 if not isinstance(data.get('chain'),list) or not all(isinstance(x,str) and len(x)<=1000 for x in data['chain']):raise ValueError('invalid chain')
 if not isinstance(data.get('links'),list) or len(data['links'])>30:raise ValueError('invalid links')
 for link in data['links']:
  if not isinstance(link,dict):raise ValueError('invalid link')
  refs=link.get('evidence_ids',[])
  if link.get('code') not in codes or not isinstance(refs,list) or not all(isinstance(x,str) for x in refs) or not refs or any(x not in evidence for x in refs):raise ValueError('unknown code or evidence')
  if not isinstance(link.get('reason'),str) or len(link['reason'])>1000:raise ValueError('invalid reason')
  allowed=[x for x in allowed_links if x['code']==link['code'] and x['type']==link.get('type')]
  if not allowed or not any(set(a['evidence_ids']).issubset(refs) for a in allowed):raise ValueError('unsupported relation')
 if 'research' in data:validate_research(data['research'],evidence)
 return data

class NewsStore(ResearchStore):
 def __init__(self,store):
  self.store=store
  with store.connect() as db:
   db.executescript('''
   CREATE TABLE IF NOT EXISTS news_items(id INTEGER PRIMARY KEY,event_id TEXT,source TEXT,source_id TEXT,hash TEXT,seen TEXT,published TEXT,payload TEXT);
   CREATE INDEX IF NOT EXISTS news_item_lookup ON news_items(source,source_id,id);
   CREATE INDEX IF NOT EXISTS news_event_lookup ON news_items(event_id,id);
   CREATE INDEX IF NOT EXISTS news_time_lookup ON news_items(published,seen);
   CREATE TABLE IF NOT EXISTS news_identity(source TEXT,source_id TEXT,first_seen TEXT,PRIMARY KEY(source,source_id));
   INSERT OR IGNORE INTO news_identity SELECT source,source_id,MIN(seen) FROM news_items GROUP BY source,source_id;
   CREATE TABLE IF NOT EXISTS news_analysis(id INTEGER PRIMARY KEY,event_id TEXT,input_hash TEXT,created TEXT,status TEXT,payload TEXT);
   CREATE INDEX IF NOT EXISTS news_analysis_lookup ON news_analysis(event_id,id);
   CREATE TABLE IF NOT EXISTS news_sources(source TEXT PRIMARY KEY,payload TEXT);
   CREATE TABLE IF NOT EXISTS news_raw(id INTEGER PRIMARY KEY,source TEXT,captured TEXT,body TEXT);
   CREATE TABLE IF NOT EXISTS news_budget(request_id TEXT PRIMARY KEY,day TEXT,reserved REAL,actual REAL,usage TEXT);
   CREATE TABLE IF NOT EXISTS news_jobs(event_id TEXT PRIMARY KEY,attempts INTEGER DEFAULT 0,next_attempt REAL DEFAULT 0);
   CREATE TABLE IF NOT EXISTS news_gaps(id INTEGER PRIMARY KEY,source TEXT,start TEXT,end TEXT,reason TEXT);
   CREATE TABLE IF NOT EXISTS news_worker_state(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT);
   CREATE INDEX IF NOT EXISTS news_raw_time ON news_raw(captured);
   CREATE TABLE IF NOT EXISTS news_business(id TEXT PRIMARY KEY,payload TEXT);
   ''')
  self.init_research()
  from .gainer_news import init_scans
  init_scans(store)
 def ingest(self,item,now=None):
  seen=normalize_time(now or now_iso());row=dict(item)
  row.update(title=clean(row['title'])[:500],summary=clean(row['summary'])[:6000],published_at=normalize_time(row['published_at']),url=canonical(row['url']))
  domains={'em':'eastmoney.com','cls':'cls.cn','sina':'sina.com.cn','nvidia':'nvidia.com','fed':'federalreserve.gov','cnbc':'cnbc.com'}
  host=urlsplit(row['url']).hostname or '';domain=domains.get(row['source'])
  if not domain or not (host==domain or host.endswith('.'+domain)):raise ValueError('article origin not allowed')
  if not row['title']:raise ValueError('empty title')
  if datetime.fromisoformat(row['published_at'])>datetime.fromisoformat(seen)+timedelta(minutes=10):raise ValueError('future publication')
  h=digest(row)
  with self.store.connect() as db:
   db.execute('INSERT OR IGNORE INTO news_identity VALUES(?,?,?)',(row['source'],row['source_id'],seen))
   old=db.execute('SELECT * FROM news_items WHERE source=? AND source_id=? ORDER BY id DESC LIMIT 1',(row['source'],row['source_id'])).fetchone()
   if old and old['hash']==h:return old['event_id']
   eid=old['event_id'] if old else digest([row['source'],row['source_id']])[:24]
   if not old:
    recent=db.execute('SELECT event_id,payload FROM news_items WHERE published>=? AND published<=?',(normalize_time(datetime.fromisoformat(row['published_at'])-timedelta(hours=48)),row['published_at'])).fetchall()
    for other in recent:
     r=json.loads(other['payload'])
     if r['url']==row['url'] or (r['title']==row['title'] and r['summary']==row['summary']):eid=other['event_id'];break
   db.execute('INSERT INTO news_items(event_id,source,source_id,hash,seen,published,payload) VALUES(?,?,?,?,?,?,?)',(eid,row['source'],row['source_id'],h,seen,row['published_at'],json.dumps(row,ensure_ascii=False)))
  return eid
 def reports(self,eid,asof):
  roots=self.event_roots(asof);root=roots.get(eid,eid);ids=[root]+[k for k,v in roots.items() if v==root]
  with self.store.connect() as db:
   rows=db.execute('SELECT n.*,i.first_seen FROM news_items n LEFT JOIN news_identity i USING(source,source_id) WHERE event_id IN ('+','.join('?' for _ in ids)+') AND seen<=? ORDER BY n.id',(*ids,asof)).fetchall()
  latest={};first={}
  for r in rows:
   key=(r['source'],r['source_id']);first.setdefault(key,r['first_seen'] or r['seen'])
   latest[key]={**json.loads(r['payload']),'first_seen':first[key],'revision_seen':r['seen'],'revision':r['id']}
  return list(latest.values())
 def market(self,asof):
  with self.store.connect() as db:
   row=db.execute("SELECT id FROM snapshots WHERE kind='gainers' AND mode='close' AND collected_at<=? ORDER BY trade_date DESC,id DESC LIMIT 1",(datetime.fromisoformat(asof).astimezone(TZ).isoformat(timespec='seconds'),)).fetchone()
   rev=row['id'] if row else None
   if getattr(self,'_market_id',None)!=rev or not hasattr(self,'_market'):
    snap=db.execute('SELECT * FROM snapshots WHERE id=?',(rev,)).fetchone() if rev else None
    self._market={**dict(snap),'payload':json.loads(snap['payload'])} if snap else None;self._market_id=rev
  return self._market
 def directory(self,asof):
  cutoff=datetime.fromisoformat(asof).astimezone(TZ).isoformat(timespec='seconds')
  with self.store.connect() as db:
   row=db.execute("SELECT id FROM snapshots WHERE kind='stocks' AND mode='close' AND collected_at<=? ORDER BY trade_date DESC,id DESC LIMIT 1",(cutoff,)).fetchone()
   rev=row['id'] if row else None
   if getattr(self,'_directory_id',None)!=rev or not hasattr(self,'_directory'):
    record=db.execute('SELECT payload FROM snapshots WHERE id=?',(rev,)).fetchone() if rev else None
    from .analytics import hs_stock
    self._directory=[r for r in json.loads(record[0])['rows'] if hs_stock(r) and r.get('name')] if record else []
    self._directory_id=rev
  # Older archives may only contain the already verified gainers directory.
  market=self.market(asof)
  return self._directory or (market['payload']['rows'] if market else [])
 def context(self,reports,asof):
  snap=self.market(asof)
  with self.store.connect() as db:business=[json.loads(r[0]) for r in db.execute('SELECT payload FROM news_business')]
  stocks=self.directory(asof);universe={r['code'] for r in stocks}
  text=' '.join(r['title']+' '+r['summary'] for r in reports)
  themes=detect_themes(text)
  evidence={f'n{r["revision"]}':dict(kind='news',text=r['title']+' '+r['summary'],url=r['url'],date=r['published_at']) for r in reports}
  enrichment=self.enrichments(reports,asof)
  for article in enrichment:
   for i,excerpt in enumerate(article.get('excerpts',[])):
    evidence[f'a{article["id"]}p{i}']=dict(kind='article',text=excerpt,url=article['url'],date=None,captured_at=article['captured_at'],scope='公开原文补证；发布时间沿用报道，不以采集时间替代')
  links=[];names={}
  for r in stocks:names.setdefault(r['name'],[]).append(r['code'])
  signature=(snap['id'] if snap else 0,getattr(self,'_directory_id',None))
  if getattr(self,'_matcher_id',None)!=signature:
   self._name_codes={name:codes[0] for name,codes in names.items() if len(name)>=3 and name not in AMBIGUOUS_NAMES and len(codes)==1}
   self._names_re=re.compile('|'.join(re.escape(name) for name in sorted(self._name_codes,key=len,reverse=True))) if self._name_codes else None
   self._matcher_id=signature
  mentions={}
  for eid,ev in evidence.items():
   found={self._name_codes[m.group()] for m in self._names_re.finditer(ev['text']) if m.start()==0 or not ('\u4e00'<=ev['text'][m.start()-1]<='\u9fff') or ev['text'][m.start()-1] in '据与及和对由向'} if self._names_re else set()
   pattern=r'(?:证券代码[：: ]*|股票代码[：: ]*|[（(]|\b(?:SH|SZ))(\d{6})(?!\d)|(?<!\d)(\d{6})\.(?:SH|SZ)\b'
   found.update((m.group(1) or m.group(2)) for m in re.finditer(pattern,ev['text'],re.I))
   for code in found.intersection(universe):mentions.setdefault(code,[]).append(eid)
  links=[dict(code=code,type='direct',evidence_ids=refs,reason='来源明确提及唯一证券简称或带证券语境的代码') for code,refs in sorted(mentions.items())]
  # Optional earlier disclosures supply a baseline only when actually archived.
  prior=[];start=min(r['published_at'] for r in reports)
  with self.store.connect() as db:
   for code in list(mentions)[:2]:
    name=next((r['name'] for r in stocks if r['code']==code),'')
    if not name:continue
    for row in db.execute('SELECT id,payload FROM news_items WHERE id IN (SELECT MAX(id) FROM news_items WHERE seen<=? GROUP BY source,source_id) AND published<? AND published>=? AND payload LIKE ? ORDER BY published DESC LIMIT 2',(asof,normalize_time(datetime.fromisoformat(normalize_time(start))-timedelta(hours=12)),normalize_time(datetime.fromisoformat(normalize_time(start))-timedelta(days=30)),'%'+name+'%')):
     r=json.loads(row['payload']);key=f'prior{row["id"]}'
     evidence[key]=dict(kind='prior',text=(r['title']+' '+r['summary'])[:1800],url=r['url'],date=r['published_at']);prior.append(key)
  # Mapping revisions are immutable. Only evidence already verified at this time is eligible.
  current={}
  for b in business:
   if b.get('verified') and b['verified_at']<=asof and b['code'] in universe and (datetime.fromisoformat(asof)-datetime.fromisoformat(b['verified_at'])).days<=180:
    key=b.get('base_id',b['id'])
    if key not in current or current[key]['verified_at']<b['verified_at']:current[key]=b
  valid=[];watch=[]
  for b in current.values():
   if b['theme'] not in themes:continue
   terms=b.get('event_terms',b.get('verify_terms',[]))
   refs=[k for k,v in evidence.items() if v['kind']=='news' and any(term_match(v['text'],w) for w in terms)]
   if not refs:continue
   kind=b.get('relation_type','business')
   if kind=='supply_chain' and not b.get('chain_verified'):continue
   evidence[b['id']]=dict(kind=kind,text=b['excerpt'],url=b['url'],date=b.get('source_date'),verified_at=b['verified_at'])
   # Generic business identity supports a research watchlist, not an event link.
   # Event-bound mappings need an independently reviewed subject-specific claim.
   supported=b.get('event_link_verified') and b.get('event_subjects') and any(term_match(text,w) for w in b['event_subjects']) and b.get('event_evidence')
   if not supported:
    watch.append(dict(code=b['code'],name=b['name'],business=b['business'],reason='同主题业务候选；尚无本事件影响或供货证据',evidence_ids=refs+[b['id']],url=b['url']))
    continue
   evidence[b['id']+'-event']=b['event_evidence'];refs=refs+[b['id']+'-event'];valid.append(b)
   links.append(dict(code=b['code'],type=kind,evidence_ids=refs+[b['id']],reason=b['business']+('；从机器人系统到执行器环节的推导，不证明与新闻主体有供货关系' if kind=='supply_chain' else '；同业务观察，不代表新增订单或收益预测')))
  selected={x['code'] for x in links}
  ctx=dict(themes=themes,evidence=evidence,enrichment=enrichment,watch_candidates=watch,priority=research_priority(reports,themes),conflicts=conflicts(reports),allowed_links=links,stocks=[r for r in stocks if r['code'] in selected],industry_representatives=valid,mapping_version=digest([b for b in current.values() if b['id'] in evidence]),universe_codes=sorted(universe))
  return self.refresh_quotes(ctx,asof)
 def refresh_quotes(self,ctx,asof):
  snap=self.market(asof);rows=snap['payload']['rows'] if snap else [];bycode={r['code']:r for r in rows}
  codes={x['code'] for x in ctx.get('allowed_links',[])}
  directory={r['code']:r for r in self.directory(asof)}
  ctx={**ctx,'stocks':[bycode.get(c,dict(code=c,name=directory.get(c,{}).get('name',c),change=None,amount=None,boards=None,industry=[],concepts=[],quote_status='missing')) for c in sorted(codes)]}
  grouped={}
  for theme in ctx.get('themes',[]):
   verified={b['code'] for b in ctx.get('industry_representatives',[]) if b['theme']==theme}
   grouped[theme]=leaders([bycode[c] for c in verified if c in bycode])
  ctx.update(leaders=grouped,market_date=snap['trade_date'] if snap else None,market_revision=snap['id'] if snap else None,market_refs=snap['payload'].get('input_refs',{}) if snap else {},concept_source=snap['payload'].get('concept_source','eastmoney') if snap else 'ths')
  return ctx
 def detail(self,eid,asof=None,materialize=True):
  cutoff=normalize_time(asof or now_iso());eid=self.event_roots(cutoff).get(eid,eid);reports=self.reports(eid,cutoff)
  if not reports:return None
  with self.store.connect() as db:a=db.execute('SELECT * FROM news_analysis WHERE event_id=? AND created<=? ORDER BY id DESC LIMIT 1',(eid,cutoff)).fetchone()
  if a and a['input_hash']==digest(reports):
   payload=json.loads(a['payload']);status=a['status'];analysis_id=a['id']
  else:
   payload={'context':{},'analysis':None};status='pending';analysis_id=None
   if asof is None and materialize:payload['context']=self.context(reports,cutoff)
  ctx=payload.get('context',{});analysis=payload.get('analysis') or {}
  if asof is None and payload.get('prompt_version')!=VERSION:
   if materialize:ctx=self.context(reports,cutoff)
   else:ctx={**ctx,'themes':detect_themes(' '.join(r['title']+' '+r['summary'] for r in reports))}
   if a:status='upgrade_pending'
   if analysis:
    analysis={**analysis,'links':[l for l in analysis.get('links',[]) if l['type']=='direct']}
    status='upgrade_pending'
   ctx={**ctx,'allowed_links':[l for l in ctx.get('allowed_links',[]) if l['type']=='direct'],'industry_representatives':[]}
  ctx={**ctx,'conflicts':ctx.get('conflicts',[]) if asof else conflicts(reports),'priority':ctx.get('priority') or (dict(level='brief',reason='当时版本未保存研究层级') if asof else research_priority(reports,ctx.get('themes',[])))}
  if analysis:
   published={l['code'] for l in analysis.get('links',[])}
   ctx={**ctx,'allowed_links':analysis.get('links',[]),'stocks':[r for r in ctx.get('stocks',[]) if r['code'] in published],'industry_representatives':[r for r in ctx.get('industry_representatives',[]) if r['code'] in published]}
   # Frozen market leader evidence is filtered to the finally published relation set too.
   ctx['leaders']={theme:{**board,**{k:[r for r in board.get(k,[]) if r['code'] in published] for k in ('boards','gainers','turnover')}} for theme,board in ctx.get('leaders',{}).items()}
  if asof is None and materialize and ctx:ctx=self.refresh_quotes(ctx,cutoff)
  ctx={k:v for k,v in ctx.items() if k!='universe_codes'}
  row=max(reports,key=lambda r:r['published_at'])
  return dict(id=eid,title=analysis.get('title_zh',row['title']),summary=analysis.get('summary',row['summary']),published_at=row['published_at'],first_seen=min(r['first_seen'] for r in reports),reports=reports,status=status,analysis=analysis or None,analysis_revision=analysis_id,analysis_time=a['created'] if a and analysis_id else None,prompt_version=payload.get('prompt_version'),model=payload.get('model'),late_capture=any((datetime.fromisoformat(r['first_seen'])-datetime.fromisoformat(r['published_at'])).total_seconds()>86400 for r in reports),**ctx)
 def list_events(self,date=None,region=None,source=None,theme=None,stock=None,relation=None,q='',level=None,page=1,page_size=30,asof=None,window='24h'):
  cutoff=normalize_time(asof or now_iso());end=datetime.fromisoformat(cutoff);effective=asof
  if date:
   start=datetime.fromisoformat(date).replace(tzinfo=TZ);finish=start+timedelta(days=1)
   if finish<=end:
    cutoff=min(cutoff,normalize_time(finish-timedelta(seconds=1)));effective=cutoff
  elif asof or window=='all':start=end-timedelta(days=365);finish=end
  else:start=end-timedelta(hours=24);finish=end
  with self.store.connect() as db:
   rows=db.execute('SELECT event_id,MAX(published) stamp FROM news_items WHERE seen<=? AND published>=? AND published<? GROUP BY event_id ORDER BY stamp DESC',(cutoff,normalize_time(start),normalize_time(finish))).fetchall()
  results=[];roots=self.event_roots(cutoff);seen_events=set()
  for r in rows:
   eid=roots.get(r[0],r[0])
   if eid in seen_events:continue
   seen_events.add(eid)
   # Filtering uses persisted evidence. Expensive context/quote materialization is page-bounded.
   d=self.detail(eid,asof=effective,materialize=False)
   if not d:continue
   if region and not any(x['region']==region for x in d['reports']):continue
   if source and not any(x['source']==source for x in d['reports']):continue
   if theme and theme not in d.get('themes',[]):continue
   if level and d.get('priority',{}).get('level')!=level:continue
   links=(d.get('analysis') or {}).get('links',d.get('allowed_links',[]))
   if stock and not any(x['code']==stock for x in links):continue
   if relation and not any(x['type']==relation for x in links):continue
   searchable=d['title']+' '+d['summary']+' '+' '.join(x['title']+' '+x['summary'] for x in d['reports'])
   if q and q.lower() not in searchable.lower():continue
   results.append(d)
  results.sort(key=lambda x:x['published_at'],reverse=True)
  selected=results[(page-1)*page_size:page*page_size]
  return dict(rows=[self.detail(d['id'],asof=effective) for d in selected],total=len(results),page=page,page_size=page_size,asof=cutoff,historical=effective is not None)
 def save_analysis(self,eid,reports,ctx,analysis=None,status='pending',now=None):
  with self.store.connect() as db:
   db.execute('INSERT INTO news_analysis(event_id,input_hash,created,status,payload) VALUES(?,?,?,?,?)',(eid,digest(reports),normalize_time(now or now_iso()),status,json.dumps(dict(context=ctx,analysis=analysis,prompt_version=VERSION,model='deepseek-flash'),ensure_ascii=False)))
 def reserve(self,request_id,amount,day=None):
  if not math.isfinite(amount) or amount<=0:raise ValueError('invalid reservation')
  day=day or datetime.now(TZ).date().isoformat()
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE')
   used=db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM news_budget WHERE day=?',(day,)).fetchone()[0]
   if used+amount>1 or db.execute('SELECT 1 FROM news_budget WHERE request_id=?',(request_id,)).fetchone():return False
   db.execute('INSERT INTO news_budget VALUES(?,?,?,NULL,NULL)',(request_id,day,amount));return True
 def settle(self,request_id,actual,usage):
  if not math.isfinite(actual) or actual<0:raise ValueError('invalid cost')
  with self.store.connect() as db:db.execute('UPDATE news_budget SET actual=?,usage=? WHERE request_id=?',(actual,json.dumps(usage),request_id))
 def budget(self,day=None):
  day=day or datetime.now(TZ).date().isoformat()
  with self.store.connect() as db:r=db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0),COUNT(*) FROM news_budget WHERE day=?',(day,)).fetchone()
  return dict(day=day,spent_or_reserved=r[0],requests=r[1],limit_usd=1,pricing='峰值缓存未命中价格保守计费；含未知用量预留')
 def sources(self):
  with self.store.connect() as db:
   rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM news_sources')]
   pending=db.execute("""SELECT COUNT(*) FROM (SELECT event_id,MAX(seen) seen FROM news_items GROUP BY event_id) n
    WHERE NOT EXISTS(SELECT 1 FROM news_event_links l WHERE l.child=n.event_id)
    AND NOT EXISTS(SELECT 1 FROM news_analysis a WHERE a.id=(SELECT MAX(id) FROM news_analysis WHERE event_id=n.event_id)
      AND a.status IN ('analyzed','unrelated') AND json_extract(a.payload,'$.prompt_version')=?
      AND a.created>=n.seen AND a.created>=COALESCE((SELECT MAX(created) FROM news_event_links WHERE parent=n.event_id),'') )""",(VERSION,)).fetchone()[0]
   worker=db.execute('SELECT payload FROM news_worker_state WHERE id=1').fetchone()
  for r in rows:
   r.pop('ids',None);r['stale']=not r.get('last_success') or (datetime.now(TZ)-datetime.fromisoformat(r['last_success'])).total_seconds()>r['interval']*2
  return dict(worker=json.loads(worker[0]) if worker else None,rows=rows,budget=self.budget(),pending=pending,themes=[dict(id=k,name=v[0]) for k,v in THEMES.items()])
