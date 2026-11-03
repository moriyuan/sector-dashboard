"""Read news from the closing gainers' perspective and collect stock-specific headlines."""
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from .news import AMBIGUOUS_NAMES, canonical, normalize_time
from .news_sources import SOURCES, fetch
from .store import TZ

TOP = 20
LOOKBACK = 3


def init_scans(store):
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS news_rank_scans (
            trade_date TEXT NOT NULL, code TEXT NOT NULL, checked_at TEXT NOT NULL,
            ok INTEGER NOT NULL, hits INTEGER, error TEXT, retry_at TEXT NOT NULL,
            PRIMARY KEY(trade_date,code))''')


def gainers_snapshot(store, day, cutoff):
    cutoff = min(cutoff, datetime.combine(date.fromisoformat(day), time(23,59,59), TZ))
    with store.connect() as db:
        record = db.execute("""SELECT id,source_time,payload FROM snapshots
            WHERE kind='gainers' AND mode='close' AND trade_date=? AND collected_at<=?
            ORDER BY id DESC LIMIT 1""", (day,cutoff.astimezone(TZ).isoformat(timespec='seconds'))).fetchone()
    if not record:
        return None, None, []
    return record['id'], record['source_time'], json.loads(record['payload'])['rows']


def news_picks(rows, code=None):
    names = Counter(r['name'] for r in rows)
    eligible = [r for r in rows if not r['is_st'] and r['change'] > 0]
    if code is None:
        picks = [dict(r,unique_name=names[r['name']]==1) for r in eligible[:TOP]]
    else:
        rank = next((i for i,r in enumerate(eligible,1) if r['code']==code),None)
        picks = [dict(r,unique_name=names[r['name']]==1,stock_rank=rank) for r in rows if r['code']==code]
    return picks


@lru_cache(maxsize=512)
def code_pattern(code):
    exchange='SH' if code.startswith('6') else 'SZ'
    bare=rf'{code}(?![0-9A-Za-z]|\s*\.\s*(?:SH|SZ))'
    return re.compile(rf'(?:证券代码|股票代码|代码)\s*[：:]?\s*{bare}|'
                      rf'[（(]\s*{code}\s*[)）]|(?<![a-z0-9]){exchange}\s*{bare}|'
                      rf'(?<![a-z0-9]){code}\s*[.]\s*{exchange}(?![a-z0-9])',re.I)


def mention(text, row):
    if code_pattern(row['code']).search(text):
        return '证券代码 · '+row['code']
    name = row['name']
    if row.get('unique_name',True) and name not in AMBIGUOUS_NAMES and len(name)>=3 and name in text:
        return '公司简称 · '+name
    return None


def board_terms(row):
    return [(b['name'],label+' · '+b['name']) for key,label in [('concepts','概念'),('industry','行业')]
            for b in row.get(key,[]) if len(b['name'])>=2]


def cited_hit(hit, text, basis, term):
    position = text.find(term)
    start = max(position-35,0)
    excerpt = text[start:start+150].strip()
    return dict(hit,basis=basis,excerpt=('…' if start else '')+excerpt+('…' if len(text)>start+150 else ''))


def parse_company_page(html, row, now):
    soup = BeautifulSoup(html,'html.parser')
    title = soup.title.get_text(strip=True) if soup.title else ''
    if f"({row['code']})" not in title or '个股资讯' not in title:
        raise ValueError('个股资讯页身份未核实')
    links = soup.select('.datelist a[href]')
    start = datetime.combine(now.date()-timedelta(days=LOOKBACK-1),time.min,TZ)
    items = {}
    dated = 0
    for link in links:
        stamp = re.search(r'(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)\s*$',str(link.previous_sibling))
        if not stamp:
            continue
        dated += 1
        published = datetime.fromisoformat(' '.join(stamp.groups())).replace(tzinfo=TZ)
        headline = link.get_text(' ',strip=True)
        if not start<=published<=now or not mention(headline,row):
            continue
        # The index also includes promotions and unrelated stories.
        if re.search('附走势预测|后市是否有机会|免费诊股',headline):
            continue
        url = link['href']
        p = urlsplit(url)
        if p.scheme!='https' or p.netloc!='finance.sina.com.cn' or not p.path.endswith('.shtml'):
            continue
        url = canonical(url)
        key = 'stock-'+hashlib.sha256(url.encode()).hexdigest()
        items[key] = dict(source='sina',source_id=key,title=headline,summary='',published_at=published.isoformat(),
                          url=url,region='domestic',channel='stock_headline')
    if not dated:
        raise ValueError('个股资讯列表结构未核实')
    return list(items.values())


def fetch_company(row, now):
    code = row['code']
    if not re.fullmatch(r'[036]\d{5}',code):
        raise ValueError('unsupported stock code')
    market = 'sh' if code.startswith('6') else 'sz'
    url = f'https://money.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{market}{code}.phtml'
    raw,_ = fetch(url)
    return parse_company_page(raw.decode('gb18030',errors='replace'),row,now)


def scan_one(news, now, source=fetch_company, *, enrich=None):
    """One bounded scan per iteration. Result and retry time survive restarts."""
    day = now.date().isoformat()
    _,_,market = gainers_snapshot(news.store,day,now)
    rows = news_picks(market)
    # Search other rising disclosed stocks too, including those outside the news top 20.
    if market:
        disclosure=news.store.snapshot('lhb',day,'close')
        codes={r['code'] for r in disclosure['payload']['rows']} if disclosure else set()
        codes.difference_update(r['code'] for r in rows)
        counts=Counter(r['name'] for r in market)
        rows.extend(dict(r,unique_name=counts[r['name']]==1) for r in market
                    if r['code'] in codes and not r.get('is_st') and (r.get('change') or 0)>0)
    if not rows:
        return False
    stamp = normalize_time(now)
    with news.store.connect() as db:
        due = {r['code']:r['retry_at'] for r in db.execute(
            'SELECT code,retry_at FROM news_rank_scans WHERE trade_date=?',(day,))}
    row = min((r for r in rows if due.get(r['code'],'')<=stamp),key=lambda r:due.get(r['code'],''),default=None)
    if not row:
        return False
    try:
        items = source(row,now)
        for item in items:
            news.ingest(item,now=stamp)
        ok,hits,error,delay = True,len(items),None,60
    except Exception as exc:
        ok,hits,error,delay = False,None,type(exc).__name__,15
    with news.store.connect() as db:
        db.execute('INSERT OR REPLACE INTO news_rank_scans VALUES (?,?,?,?,?,?,?)',
                   (day,row['code'],stamp,int(ok),hits,error,normalize_time(now+timedelta(minutes=delay))))
    # Article retries are independent of whether the headline search succeeded.
    if ok and enrich:enrich(news,items,now)
    return True


def rank_news(news, day, now, *, code=None, fallback_name=None, market=None):
    trade_day = date.fromisoformat(day)
    last = datetime.combine(trade_day,time(23,59,59),TZ)
    cutoff = min(last,now)
    stamp = normalize_time(cutoff)
    start = normalize_time(datetime.combine(trade_day-timedelta(days=LOOKBACK-1),time.min,TZ))
    revision,source_time,rows = market if market is not None else gainers_snapshot(news.store,day,cutoff)
    picks = news_picks(rows,code)
    if code and not picks:
        picks=[dict(code=code,name=fallback_name or code,change=None,unique_name=False,stock_rank=None)]

    with news.store.connect() as db:
        dates = [r[0] for r in db.execute("""SELECT DISTINCT trade_date FROM snapshots
            WHERE kind='gainers' AND mode='close' ORDER BY trade_date DESC LIMIT 250""")]
    result = dict(trade_date=day,revision=revision,source_time=source_time,asof=stamp,start=start,
                  top=TOP,dates=dates,rows=[])
    if not picks:
        return result
    # Corrected reports replace earlier revisions before the publication window is applied.
    with news.store.connect() as db:
        reports = db.execute('''SELECT event_id,published,payload FROM news_items
            WHERE id IN (SELECT MAX(id) FROM news_items WHERE seen<=? GROUP BY source,source_id)
            AND published BETWEEN ? AND ? ORDER BY published DESC,id DESC''',(stamp,start,stamp)).fetchall()
        scanned = {r['code']:dict(r) for r in db.execute('''SELECT code,ok,hits,error,checked_at FROM
            news_rank_scans WHERE trade_date=? AND checked_at<=?''',(day,stamp))}
    roots = news.event_roots(stamp)
    evidence = []
    market_close = datetime.combine(trade_day,time(15),TZ)
    for record in reports:
        report = json.loads(record['payload'])
        source = '新浪个股资讯' if report.get('channel')=='stock_headline' else SOURCES[report['source']]['name']
        published = datetime.fromisoformat(record['published']).astimezone(TZ)
        timing = '此前报道' if published.date()<trade_day else '收盘前' if published<market_close else '收盘后'
        text = report['title'] if report['summary']==report['title'] else report['title']+' '+report['summary']
        market_report = bool(re.search(r'午评[：:]|收评[：:]|早盘|涨停|涨超|异动|成交额|龙虎榜',report['title']))
        evidence.append((text,dict(market_report=market_report,
            id=roots.get(record['event_id'],record['event_id']),title=report['title'],
            url=report['url'],source=source,published_at=record['published'],timing=timing)))
    for rank,row in enumerate(picks,1):
        direct,sector={},{}
        terms = board_terms(row)
        for text,hit in evidence:
            key = hit['id']
            basis = mention(text,row)
            if basis:
                direct.setdefault(key,cited_hit(hit,text,basis,basis.split(' · ',1)[1]))
            else:
                match = next(((term,label) for term,label in terms if term in text),None)
                if match:
                    sector.setdefault(key,cited_hit(hit,text,match[1],match[0]))
        sector = {k:v for k,v in sector.items() if k not in direct}
        result['rows'].append(dict(rank=row.get('stock_rank',rank),code=row['code'],name=row['name'],change=row['change'],
            boards=row.get('boards'),industry=row.get('industry',[]),concepts=row.get('concepts',[]),
            scan=scanned.get(row['code']),direct=list(direct.values())[:10],sector=list(sector.values())[:5],
            direct_count=len(direct),sector_count=len(sector)))
    return result
